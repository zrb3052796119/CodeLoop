# 恢复经验模块实施复审

日期：2026-09-09。依据 `implementation.md` 和当前工作区代码审查。未修改生产代码或原实施记录，未调用真实模型或写入正式记忆。

## 结论

实现方向符合上次建议：保留唯一持久化入口、复用无工具 LLM 客户端、抽取共享校验、提供独立恢复片段模块。原始三个误判反例及独立无关 verification 案例已有回归覆盖。

**目前建议继续修正后再接入生产。** 两类问题影响现有共享路径：摘录通道造成证据状态升级漏洞，目标身份与验证范围匹配仍会错误确认恢复。新 Agent 还存在证据切片与多结果接口缺口。

文档准确披露了 RecoveryLessonAgent 尚未接入 `agent_reflection` 的生产选择路径、真实模型 shadow 评测尚未进行。因此本次不把“尚未生产接线”本身当作隐藏缺陷，也不能认为运行中的 Agent 已获得按恢复片段学习的能力。即使新 Agent 未接线，已修改的共用 schema/validator 在现有 `mode=llm` 路径仍然生效。

## 高优先级问题

### F1 [P1] 真实摘录与正文改写可把 inferred 来源提升为 confirmed

位置：[reflection_synthesis.py:1629](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_synthesis.py:1629>)；关联 [reflection_synthesis.py:1710](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_synthesis.py:1710>)。

摘录分支允许正文不再包含原句，但 `_source_statuses()` 仍以原句和正文的包含关系决定是否继承 DecisionEvidence 的状态。正文改写后，实际引用的 inferred decision 被排除出状态检查。

已从真实 TraceEvidenceExtractor 构造的 assistant 因果判断复现：

| 同一 inferred 来源的候选 | 最终结果 |
|---|---|
| 原句 + confirmed | 拒绝：epistemic_status_overclaim |
| 改写 + confirmed，无摘录 | 拒绝：claim_statement_not_grounded |
| 改写 + confirmed，加真实摘录 | 通过，进入可持久化候选 |

还用脚本客户端经过 `LLMReflectionSynthesizer → ReflectionEngine.reflect(mode=llm, gap_fill)` 复现：没有测试或其他验证，最终 `selection_source=llm_gap_fill`、claim 状态为 confirmed。root_cause 类型同样可将 inferred decision 提升，并获得 `confirmed_error_recovery_verified` 信号。

这是可由程序确定的新增回归，独立于文档已承认的“无法用字符串判断任意语义推广”边界。修正应依据被引用/摘录实际绑定的记录继承状态，不能再依据正文是否复述原句。

### F2 [P1] 完整路径匹配仍会把不同操作对象认作同一次恢复

位置：[reflection_evidence.py:848](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:848>)、[reflection_evidence.py:882](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:882>)、[reflection_evidence.py:2027](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:2027>)。

使用当前工具 validator 和真实 trace emitter 的三项新反例，都生成 confirmed recovery 并通过 value gate：

- 同一个 `cwd=/repo/workspace`，auth 测试失败，billing 测试通过：正则把 cwd 本身收集为资源，交集命中工作目录。
- `cwd=/repo/backend` 与 `cwd=/repo/frontend` 分别运行 `tests/test_config.py`：相对路径没有先按 cwd 解析，两个不同文件被认作同目标。
- 同一 workspace 下 `tests/test_auth.py` 与 `backend/tests/test_auth.py`：没有重命名或基准目录证据，仅凭路径后缀就当成同文件。

`_invocation_path_resources()` 还去掉绝对路径开头的 `/`，后续函数无法区分绝对/相对关系。新增测试将 `src/auth.py` 与 `backend/src/auth.py` 直接断言为等价，事实上两文件可以同时存在。

建议以结构化调用构建目标身份：cwd 是解析基准，不能作为共同业务资源；相对路径需锚定 workspace/cwd；路径后缀或潜在重定位只可提出候选关系，不能直接确认恢复。

### F3 [P2] “相同验证范围”的判定仍不完整

位置：[reflection_evidence.py:2336](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:2336>)、[reflection_synthesis.py:519](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_synthesis.py:519>)。

两个不同入口的最小反例：

1. `pytest tests/test_auth.py -k=expired` 失败 → 编辑 README → `-k=valid` 通过。`_call_target()` 丢弃所有 flag，选择范围消失，生成 confirmed 的 README 修复经验。
2. 已有 auth RecoveryEvidence，auth 测试先失败、billing 测试后通过。共享红绿检查只比较 `command_kind == test`，没有核对目标或关联错误，仍接受 confirmed。去掉此前 failed verification，反而会正确拒绝同一候选。

第二项属于原校验缺陷的修复遗漏：提取为共享函数使双方判定一致，但并没有使弱匹配本身正确。应共享经过校验的验证目标/测试范围身份；测试类型、共词或文件重叠不能证明失败检查已经复跑。无法确定时保持候选关系。

## 新 Agent 接入前需修正的问题

### F4 [P2] 无关成功调用会抢占真正的恢复尝试

位置：[recovery_lesson.py:253](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/recovery_lesson.py:253>)。

candidate feed 使用第一个同 tool_name、输入不同且成功的调用，并立即将该 error 标记为已处理。

复现：`python scripts/report.py (timeout: 1)` 失败 → `git status` 成功 → `python scripts/report.py (timeout: 120)` 成功。片段选择 `git status`，真正的 timeout 调整没有进入子证据。虽然片段保守标记 candidate，不会自动证明因果，但模型已失去正确输入，无法学习这次调整。

建议按候选操作对象筛选，或保留有界的多个后续尝试让模型判断，不要凭“同属 run_command”绑定唯一后继。

### F5 [P2] 子证据投影删掉父证据成立所需的验证记录

位置：[recovery_lesson.py:302](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/recovery_lesson.py:302>)。

`episode.verifications` 只保留已匹配的 passed checks，投影没有保留早先 failed checks。父证据靠红绿复跑支持的同一正确恢复，在片段中失去证明关系所需的先前失败：父 validator 接受 1 条，标记 verified 的子片段 validator 接受 0 条，报 `confirmed_recovery_verification_unrelated`。

子投影也未保留 referenced_files 等可能参与相关性判断的上下文。应保留验证关系的最小完整证据链及其引用对象，而非只拷贝最后成功节点。验收需检查投影前后同一候选的准入一致性。

### F6 [P2] 跨片段 claim ID 重复，聚合结果无法直接安全提交

位置：[recovery_lesson.py:397](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/recovery_lesson.py:397>)；生成位置 [reflection_llm.py:953](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_llm.py:953>)。

每次 LLM 响应从 `llm-claim-000001` 编号。两个不同 episode 各接受一条不同 semantic_key 的 lesson，聚合后 ID 仍完全相同。

当前 [memory_pipeline.py:2027](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/memory_pipeline.py:2027>) 用 claim_id 建字典并去重；未来若直接沿用这条接口，一个结果会被覆盖或跳过。目前模块未接线，未发生由本模块导致的生产丢失。应在聚合前加 episode 命名空间，并明确维护 episode 与 lesson 对应关系。

### F7 [P2] 新保留的调用输入不能作为合法证据摘录

位置：[reflection_llm.py:693](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_llm.py:693>)、[reflection_synthesis.py:1634](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_synthesis.py:1634>)。

LLM envelope 展示 `ToolEvidence.invocation`，但 parser 和 validator 的摘录来源均没有 tool invocation。真实的 `python scripts/report.py (timeout: 120)` 被模型引用时，报 `evidence_quote_not_grounded`。validator 也没有对应纯 tool 事件的来源类型索引。

这使步骤 2 新保留的参数变化无法通过步骤 3 的摘录合同引用。需要将可见输入统一到带 call/event ID 的证据来源映射，同时复用 F1 的来源状态检查，避免扩展输入时再次失去证据约束。

## 较低优先级与已确认边界

- 接受了 lesson 后没有填充 `EpisodeLessonOutcome.persistable_claims`，逐片段结果一直为空。
- `attempted=True` 发生在预算和调用前检查之前；预算拒绝时真实客户端调用 0 次，`llm_called` 却为 1。建议区分尝试次数和真实模型调用次数。
- 单次 candidate warning 被 value gate 拒绝是保守持久化边界，本次不将其列为缺陷；若希望保留非持久化 observation，应另行定义返回字段。
- 摘录来自另一条未引用 decision 时，parser 虽放行，但最终 validator 正确拒绝；未发现这项绕过。
- 文档已披露任意正文推广尚无语义审查、非计数 error 格式尚未完全覆盖。这些仍是启用前的评估边界，本次不冒充新发现。

## 测试与复现

本次独立重跑 16 个相关模块：**337 passed**（第一组 142，第二组 195）。覆盖新增模块、原恢复测试、LLM/parser/evidence/claim selection/precision，以及批次持久化、operation identity 和写入合同。没有重跑全项目约一万项测试，不能以收集成功替代执行结果。

探针均用合成轨迹/脚本模型响应，只经过真实代码处理，没有执行其中的命令、编辑或正式记忆写入：

- [identity_probe.py](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/recovery-rule-review-2026-09-09/implementation-review/identity_probe.py>) → [identity_probe.json](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/recovery-rule-review-2026-09-09/implementation-review/identity_probe.json>)：4 项错误确认。
- [quote_probe.py](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/recovery-rule-review-2026-09-09/implementation-review/quote_probe.py>) → [quote_probe.json](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/recovery-rule-review-2026-09-09/implementation-review/quote_probe.json>)：状态升级、生产选择路径、弱红绿匹配及拒绝对照。
- [probe_recovery_episode.py](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/recovery-rule-review-2026-09-09/implementation-review/probe_recovery_episode.py>) → [probe_recovery_episode.txt](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/recovery-rule-review-2026-09-09/implementation-review/probe_recovery_episode.txt>)：片段错配、验证链丢失、ID 碰撞、调用摘录及诊断计数。

先修 F1、F2 和 F3，再补齐片段证据与结果合同；随后接入 shadow 路径，才有条件评估不同语言、路径、命令包装及跨任务场景下的泛化收益。当前通过的测试证明所列案例得到覆盖，尚不能证明误归因风险和片段学习合同已经闭合。
