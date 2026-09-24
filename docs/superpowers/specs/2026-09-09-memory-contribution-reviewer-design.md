# 记忆贡献评审器（Memory Contribution Reviewer）设计

日期：2026-09-09
分支：codex/multi-agent-v2

## 1. 背景与缺口

持久化记忆子系统已经具备完整的注入/记账/反馈骨架：

- 注入侧有防篡改回执 `MemoryPromptReceipt`（`minicode/memory_pipeline.py:74`），
  精确记录实际渲染进提示词的记忆 ID、权威版本（`rendered_versions`）与逐字文本
  （`rendered_fragments`）。
- 轨迹侧有 Trace Contract v2 的 `execution_trace`（`tool_call`/`tool_result`/
  `task_result` 事件，已脱敏限幅，`minicode/agent_loop.py:285`）。
- 反馈侧有两个通道：普通整轮反馈 `record_feedback`（`minicode/memory.py:7950`）
  与佐证反馈 `record_corroborated_feedback`（`minicode/memory.py:8038`，连续 2 次
  `independent_verification` 失败自动隔离）。

**缺口**：整轮反馈把"任务成功"均摊给所有本轮渲染的记忆——这正是"不能只因为
任务成功就判断所有记忆都有帮助"。佐证通道支持 `verification_memory_ids` 绑定，
但规范 Agent 路径从不提供该绑定（`MemoryPipeline.feedback` docstring 自述，
`minicode/memory_pipeline.py:3066-3074`），因此逐条记忆的因果归因在生产上
从未发生。也没有任何组件回看"本轮注入的记忆在轨迹中扮演了什么角色"。

## 2. 目标

任务结束后增加一个"记忆评审器"：

1. 读取：当前任务描述、实际注入的记忆（来自 prompt receipt）、工具调用参数与
   执行结果（来自 execution trace）。
2. 对每条注入记忆给出**带证据引用**的贡献判断。
3. LLM 判断必须通过确定性的"证据引用门"校验，防止无证据的归因。
4. 判断结果是带置信度的推断；低置信度一律不加减分。

非目标：

- 不证明反事实（"没有这条记忆模型是否会出错"）——单次执行无法证明，评审器
  只对"观察到的证据"下结论。
- 不改动整轮反馈的既有语义（`pipeline.feedback` 保持原样，评审是新增的独立通道）。
- 不新增持久化 schema（复用 entry 计数字段与观察回执）。

## 3. 判定规则（用户规格）

| 观察到的证据 | 结论 | 反馈策略 |
|---|---|---|
| 记忆给出路径 A，模型直接读取 A 并成功 | 路径有效，记忆可能提供了帮助 | 小幅正反馈（普通通道） |
| 记忆给出路径 A，读取返回文件不存在 | 路径主张在当前环境失效 | 负反馈或进入修正流程（佐证通道，2 次隔离） |
| 轨迹中没有对应操作，或关联不明确 | 无法判断贡献 | 不加减分 |

三值枚举：`helped` / `invalidated` / `undetermined`。

## 4. 证据引用门（关键约束，代码强制而非仅靠提示词）

LLM 返回的每条 `helped`/`invalidated` 判定必须同时满足，否则**降级为
`undetermined`**（记录 `downgrade_reason`，不加减分）：

1. `memory_quote` 非空且与该记忆的实际渲染片段有实质重叠（归一化子串或
   词元重叠率 ≥ 0.6）；
2. `tool_event_ids` 非空、且每个 ID 都存在于本次提供的 trace 事件索引中；
3. `invalidated` 至少引用一个 `is_error=True` 的结果事件；
4. `helped` 至少引用一个非错误状态的结果事件；
5. `confidence ≥ min_confidence`（默认 0.6）。

任务成功本身不是任何一条判定的证据。

## 5. 模块设计

新模块 `minicode/memory_contribution_reviewer.py`：

### 数据结构

- `ContributionReviewConfig`：`mode ∈ {"off","rule","llm"}`（默认 `"rule"`，
  环境变量 `MINI_CODE_MEMORY_CONTRIBUTION_REVIEW`）、`timeout_seconds=15`、
  `max_output_tokens=1500`、`max_input_bytes=24576`、`min_confidence=0.6`、
  `max_memories=8`、`max_related_events_per_memory=6`。`from_runtime()` 全部限幅。
- `RelatedToolEvent`：`event_id / tool_name / call_summary / result_status /
  result_summary / result_event_ids`。
- `MemoryEvidencePack`：`memory_id / authority_version / fragment_text /
  related_events`。
- `MemoryContributionVerdict`：`memory_id / outcome / confidence / memory_quote /
  tool_event_ids / reasoning / basis("llm"|"rule") / downgrade_reason`。
- `MemoryContributionReview`：`receipt_id / operation_id / task_summary /
  verdicts / review_source("llm"|"rule_fallback"|"rule"|"no_evidence"|"off") /
  failure_code / model_called / latency_ms`。

### 确定性证据绑定（评审前置，也是 rule 模式与回退路径）

- `extract_path_claims(text)`：从记忆文本提取路径样词元（含扩展名或含 `/` 的
  词元），过滤过短/噪声。
- `build_evidence_packs(receipt, trace)`：按 `call_id` 索引 trace；对每条渲染
  记忆，找出输入序列化文本中包含其路径主张的 `tool_call` 事件，配对其
  `tool_result`（成功/失败 + 摘要）。
- rule 判定：相关事件中存在失败读取 → `invalidated`（confidence 0.9）；
  全部成功 → `helped`（confidence 0.7，"可能提供了帮助"）；无相关事件 →
  `undetermined`。

### LLM 评审（`mode="llm"`）

- 客户端复用 `StructuredGenerationClient` 协议
  （`minicode/reflection_llm.py:1124`）；生产侧用
  `ModelAdapterStructuredGenerationClient(model_adapter)` 包装管线在
  `initialize()` 收到的适配器（与 Curator 同一先例）；测试侧注入 duck-typed
  fake `generate_json`。
- 提示词包含：任务描述、task_result 概要、每条记忆的精确渲染片段与确定性
  相关事件、以及全部有界工具事件索引（event_id/工具/输入输出摘要）。系统提示
  写明判定表、引用要求与"任务成功不是证据、helped 只是可能帮助"的约束。
- 输出 JSON：`{"verdicts": [{memory_id, outcome, confidence, memory_quote,
  tool_event_ids, reasoning}]}`，逐条过证据引用门。
- 任何 LLM 失败（超时/解析/预算）→ rule 回退（`review_source="rule_fallback"`），
  绝不让评审失败影响任务终结路径。

### 反馈应用（`apply_contribution_feedback`）

- 版本栅栏：取 receipt 的 `rendered_versions` 过滤到判定涉及的 ID，防止对已
  变更版本误记账。
- `helped` → `MemoryManager.record_feedback([id], True,
  source="contribution_review", observation_id=op+":positive:"+id,
  expected_versions=…)`。需在 `memory.py` 的
  `_ORDINARY_FEEDBACK_SOURCES` 增加 `"contribution_review"`。
- `invalidated` → `MemoryManager.record_corroborated_feedback([id], False,
  source="independent_verification", observation_id=op+":verification:"+id,
  expected_versions=…)`（进入既有修正流程：连续 2 次失败隔离）。
- `undetermined` → 不调用任何记账。
- 所有调用 best-effort（try/except + warning），观察回执保证幂等。

## 6. 集成点

1. `MemoryPipeline.initialize()`：保存 `model_adapter`，构造
   `MemoryContributionReviewer`（配置来自 `ContributionReviewConfig.from_runtime`）。
2. `MemoryPipeline.review_injected_memories(task_description, execution_trace, *,
   operation_id, agent_budget, event_sink, cancellation_token, deadline_monotonic)`：
   读取 `last_committed_prompt_receipt`，构建证据包，评审，应用反馈，发
   `memory.contribution_reviewed` 事件。无回执/未初始化/mode=off → no-op。
   生命周期与 `_feedback_recorded` 同步：`inject()` 时复位，评审后置位。
3. `CyberneticOrchestrator.review_memory_contribution(...)`：转发到管线
   （镜像 `reflect_on_task`）。
4. `agent_loop._deliver_memory_quality_feedback`：签名扩展
   `task_description` 与 `execution_trace`，在整轮反馈**之前**调用评审
   （best-effort）。两个终结路径（短路径 `agent_loop.py:4992` 与完整路径
   `agent_loop.py:5200`）都传入各自可得的任务描述与轨迹。

## 7. 测试计划（TDD）

`tests/test_memory_contribution_reviewer.py`：

1. 路径主张提取与证据包构建（成功/失败配对、无关记忆零事件）。
2. rule 判定三值行为。
3. LLM 判定与引用门：合法 JSON 保留；未知 event_id / 引文不匹配 / helped 无
   事件 / 低置信度 → 降级 undetermined；坏 JSON / 超时 → rule 回退。
4. 提示词包含记忆片段与事件索引（fake client 捕获）。
5. 反馈应用：helped → success_count+1（source=contribution_review）；
   invalidated → corroborated_failure_count+1，两次独立观察 → 隔离；
   undetermined → 不变；版本不匹配 → 跳过；同 operation 重放 → 幂等。
6. 管线端到端（真实 MemoryManager + tmp_path + fake client）。
7. agent loop 集成：`_deliver_memory_quality_feedback` 在 feedback 之前触发
   评审并传递轨迹（fake orch/pipeline 捕获调用序）。

回归：`tests/test_memory_feedback_*.py`、`test_memory_corroborated_feedback.py`、
`test_reflection_*`、`test_agent_flow.py` 必须保持通过。

## 8. 错误处理与默认值

- 默认 `mode="rule"`：无 LLM 成本，确定性证据（路径主张↔工具事件）仍可评审；
  `llm` 经环境变量显式开启；`off` 关闭。
- 评审永不抛出：所有路径 try/except 包裹，失败只记日志/事件。
- 幂等：per-verdict `observation_id` + 记忆侧观察回 receipts。
