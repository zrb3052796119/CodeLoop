# Agent Turn 首个切片设计与候选实现（2026-10-24）

本候选把“上下文压缩结果的安装与权威消息保留”移到现有 `agent_loop.py` 的模块级操作。`run_agent_turn` 同一 AST 节点从 **5321 行降为 4824 行，净减 497 行**；4 个目标闭包全部迁出，新增函数最多 11 个参数。局部行为对照已通过；**P0 完整验收已通过并推送 main，首次 P1 全量有 1 项失败，后续组合候选的最终全量与原生 Windows CI 仍待，Phase 1 go 未成立**。这不是本切片的合入或发布结论。

原设计只检查若干命名闭包，暂时未找到自然的 300 行边界；后续独立复审补查了主循环中较大的同职责应用段。依据 [D1 状态矩阵](agent-turn-state-ownership-2026-10-24.md)，92 个捕获绑定中 28 个跨职责组共享，占 30.43%，**没有触发 >60% no-go**。此前暂停源于等待基线与补齐所有权设计的时序约束，不需要另设人工批准点。

## 基准与实际减量

基准提交：`9f9369421486f018c837f84d21bd727aed6ab326`；原 `agent_loop.py` SHA-256：`ee82348ae566289c950ca35ec9fd6abf36b03ca97cec3c839d2f6764af6207fe`。以下原行号均对应该提交。

净减量按同名 `run_agent_turn` 的 `end_lineno - lineno + 1` 计算，包含新增委托、参数组装、状态同步与控制流；没有把新文件新增量、无关小改、格式压缩或重命名计作减量。嵌套的所有命名函数由 30 个降为 26 个。这里只减少主函数范围；模块总行数因显式签名、类型和文档增加，不能据此声称整个代码库缩小。

| 原区域 | 原行 | 候选归属与边界 |
| --- | --- | --- |
| 预请求应用 | 2874–3097 | `_prepare_turn_context`；原 cost → compactor budget 联动一起保留 |
| API overflow 恢复 | 4738–4873 | `_recover_overflow_context`；分类和 loop 的 `continue` 留在调用者 |
| 高水位应用体 | 6355–6468 | `_apply_high_water_compaction`；原 `step % 10`、阈值触发条件留在 loop |
| `reconcile_task_ledger` | 2233–2250 | `ContextRetention.reconcile`，含 completion feedback 的位置规则 |
| `observable_message_count` | 2252–2253 | `_observable_message_count`，保持排除 task-ledger 的口径 |
| `_revalidate_model_visible_memory` | 3182–3197 | `_revalidate_prompt_memory`，仍在两处原模型调用前执行 |
| `_predictive_compaction` | 4137–4157 | `_apply_predictive_compaction`；显式 action 分支代替闭包/回调字典 |

四个旧闭包名不再出现在 `run_agent_turn` 的任何词法嵌套层。其他修复请求定价、工具结果投影、完成审查闭包不属于本切片，仍保留原位置。没有新增 controller、cybernetic、memory_* 模块、gate、配置或模型重试。

## 显式接口与所有者

所有新接口复用已有 `agent_loop.py`；它已经拥有 Memory 提交、finalize 与事件辅助函数，避免给 `context_compactor.py` 引入反向依赖。

```python
@dataclass(frozen=True, slots=True)
class ContextRetention:
    ledger: TaskLedger | None
    active_completion_feedback: ChatMessage | None
    def reconcile(self, candidate: list[ChatMessage]) -> list[ChatMessage]: ...

@dataclass(frozen=True, slots=True)
class ContextRecovery:
    messages: list[ChatMessage]
    result: CompactionResult
    context_operation_id: str | None
    messages_before: int | None
    kind: Literal["cybernetic", "compactor"]
```

`ContextRetention` 只借用当前两个权威对象，不拥有它们的生命周期。它没有 model、tools、runtime、budget、permissions 或 callbacks 字段；`TaskLedger` 本身仍由原 turn 维护。`ContextRecovery` 只携带这次成功恢复的 list、结果和观测事实；失败返回 `None`。loop 必须先重绑 canonical list，再把该 record 交给成功观察函数，不让可中断的 manager 同步或事件提前发生。

| 接口 | 参数数 | 责任 |
| --- | ---: | --- |
| `_prepare_turn_context` | 11 | 原预请求优化、Memory 安装/回滚、同步与观测 |
| `_apply_high_water_compaction` | 7 | 已触发高水位的结果应用，保留原异常策略 |
| `_recover_overflow_context` | 7 | 已分类 overflow 的恢复，保留开始/失败事件，返回成功 record |
| `_complete_context_recovery` | 4 | loop 重绑之后同步 manager、发成功事件并记录日志 |
| `_revalidate_prompt_memory` | 3 | 在原两处模型调用前检查最新 Memory 权威 |
| `_apply_predictive_compaction` | 4 | 原 proactive `compact_messages(force=True)` |
| `_observable_message_count` | 1 | 原压缩事件消息计数 |

所有服务依赖都标注已有类型、显式传入；`MemoryPipeline` 仅作类型导入。原有 materialize/rollback lambda 在操作内部以明确传入的目标 list 和候选 list 创建，不让调用者传入任意回调字典或宽 `TurnContext`。

| 对象或效果 | 所有者 | 抽取后的权限 |
| --- | --- | --- |
| `current_messages` 引用、continue/return、turn outcome | `run_agent_turn` | proactive 借用并原地修改；reactive 由 loop 重绑 |
| ledger 事实、当前 completion feedback 的替换/清空 | 原 turn 和完成审查 | retention 恢复投影，不产生新事实或批准 |
| compactor/PID/dedup/session cache | 原会话控制器实例 | 调用原方法，不创建第二份状态 |
| Memory 收据验证、安装、回滚和版本 | 原 MemoryPipeline 与已有提交 helper | 同样的 guard、fallback 和 finalize 顺序 |
| review、checks、预算、取消、deadline、权限 | 各原 owner | 不转移到新状态；摘要调用继续使用原共享预算 |
| operation ID、事件 path、消息计数 | 当前上下文操作 | 保持原时点及失败观测差异 |

## Fresh retention 的计数与边界

原源码 AST 有 **14 个 `reconcile_task_ledger(...)` 调用**；文本匹配中的第 15 处是函数定义。候选有 **9 个即时 `ContextRetention(task_ledger, implementation_completion_feedback)` 构造点**：

- pre-request 一次调用内原有 4 处 reconcile，共享这次同步操作的快照。
- high-water 一次调用内原有 2 处 reconcile，共享这次同步操作的快照。
- reactive 两个互斥分支原有 2 处 reconcile，共享这次调用的快照。
- 初始 transcript、宿主完成反馈更新、repair request 压缩、predictive 压缩、feedback 强制压缩、最终 feedback 压缩各在原位置重新构造，共 6 处。

没有 turn 开始创建后跨请求缓存的 retention。宿主完成审查可在后续迭代替换或清空 feedback；下一次 helper/直接 reconcile 总是使用当前绑定。操作内部多次 reconcile 之间没有宿主完成审查或模型请求，不增加新的 feedback owner。原“反馈已存在则不挪动；缺失才移除过时同类反馈并插入全部 system 消息之后”的规则保持不变。

## 三路径差异与失败契约

| 维度 | 预请求 | 高水位 | reactive |
| --- | --- | --- | --- |
| 触发 | 原 context manager 条件 | 原每 10 步及 threshold 条件 | 原 overflow 分类 |
| 执行 | cybernetics 优先，否则 compactor | `process_request(enable_tool_budget=False)` | 原 `try_reactive_recover` / `reactive_recover` |
| canonical list | 原地 slice 安装 | 原地 slice 安装 | 返回成功 record；loop 先重绑，再同步/观测并继续 |
| Memory guard | 保留提交、回滚、memory-free fallback | 保留相同既有机制 | 原来没有该 guard，纯重构未新增 |
| manager 同步 | 原 list 引用 | 原 list 引用 | 新绑定 list 引用 |
| cancel/deadline | 原取消传播、deadline 转换 | 取消传播、deadline 转换 | 原异常原样传播 |
| 普通异常 | 原样传播 | warning 并继续 | 原样传播 |
| 失败事件计数 | 原 compaction failure | 原 compaction failure，带 step | cyber 分支计候选长度；直接 compactor 分支计原长度 |

guard 拒绝后仍先安装 memory-free prefix、finalize 控制器状态、同步 manager，再发对应失败事件。成功的实际物化与 provenance 仍由原提交 helper 负责。predictive 仍使用 proactive 方法，不消耗 reactive 重试额度；两个非压缩 predictive 动作仍只记录原日志。Memory revalidation 仍采用原地更新 transcript、给 manager 列表副本的既有差异。

这些是行为保持范围，不是对历史路径的重新背书。reactive 尚无 proactive 路径的 Memory guard；本切片不混入这类策略变更。泛化所有压缩路径成同一种状态机，会丢掉上述真实差异。

## 确定性证据

解释器使用 root 的 `.venv-acceptance/bin/python`，但每次执行显式设置目标 `PYTHONPATH`，打印并校验 `minicode.agent_loop.__file__`；隔离 HOME，清除 provider 凭证和 `MINI_CODE_*`，seed 固定为 `20261024`。测试使用本地模型和实际本地验证命令，没有调用 live API。

精确基线源从 `git archive 9f936942` 提取到临时目录；没有修改当时执行 Phase 0 全量的冻结 clone。六个测试文件在两边字节相同：

- `test_memory_compaction_feedback.py`：真实 Memory guard 拒绝、回调后失败/回滚、counter、provenance、控制态确认。
- `test_task_ledger.py`：ledger 连续性和新反馈重绑定场景。
- `test_context_compaction_single_path.py`、`test_compaction_summary_continuity.py`：单路径与摘要连续性。
- `test_memory_p2_lifecycle_red_regressions.py`、`test_context_observation_events.py`：取消/deadline/普通错误、恢复与事件顺序、predictive 配额。

| 执行 | 结果 | 证据文件 |
| --- | --- | --- |
| 原 9f936942 源 + 最终测试字节 | 95 passed，6.62s | `before-final-tests.log` |
| 最终修复后源 + 相同 6 文件/命令/测试字节 | 95 passed，6.56s | `after-final-identical-tests.log` |
| 初始抽取快照 + 最终 8 个 observation 中断用例 | 8 failed，全部在保留新 context 的断言 | `introduced-interrupt-before-fix.log` |
| 最终源的 synthetic fault：仅把 reactive 当前 feedback 入参换为 None | 1 failed，第二次请求错误保留 attempt 1 | `final-fault-injection.log` |

新用例 `test_reactive_compaction_retains_latest_completion_decision_after_rebinding` 通过真实 `write_file` 形成两个候选，执行两轮实际 pytest checks，独立保留 passed + 非空 + digest-bound receipt；语义边界只拒绝完成。两次 overflow 都经过原恢复入口，第二个 compressor 故意返回第一轮反馈。循环外断言下一请求只有当前 attempt、ledger 目标/约束仍存活、canonical list 两次重绑、manager 同步，以及中断后的 partial host receipt。断言不藏在会被 agent loop 捕获的 model callback 中。

独立复审确实发现并修复了一项初始抽取引入的回归：成功恢复的 helper 原先先同步 manager/发事件，返回后 loop 才重绑 canonical transcript。如果事件接收器抛 `KeyboardInterrupt` 或 `SystemExit`，finally 会重新写入旧 list。新增 `test_recovery_rebinds_canonical_context_before_interruptible_observation` 覆盖 cyber/direct 两路 × `context.compacted`/`recovery.completed` 两处 × 两种中断。相同 8 个测试字节在原 9f 源绿、保存的初始抽取快照红、最终候选绿；异常对象身份和事件顺序也必须一致。修复采用上述 typed 成功 record 和重绑后观察，不吞 `BaseException`。这是本次候选引入回归的真实对照，不是旧仓库的历史缺陷。

最终候选与原源码是纯重构的 **before/after 均绿** 证明。synthetic fault 仅验证测试能抓住丢失当前 feedback 的错误，**不宣称历史缺陷 PROVEN**。日志、输入哈希、AST 统计与运行脚本保存在 `/private/tmp/codeloop-governance-20261024/phase1-implementation/`；该本机证据目录不是新评测 gate。

最终候选重新执行上述 95 项与另 8 个邻接文件，合计 **336 passed，44.24s**，证据为 `after-final-tests.log`。邻接文件为：`test_agent_loop.py`、`test_agent_loop_work_chain_disabled.py`、`test_context_compactor.py`、`test_context_cybernetics.py`、`test_implementation_continuation.py`、`test_requirement_completion_revalidation.py`、`test_request_schema_soft_landing_review.py`、`test_bounded_completion_entrypoints.py`。初始抽取的 87 项与 241 项日志保留作过程证据，不作为最终版本验收。`git diff --check` 及本次三个 Python 文件按仓库 CI 口径的 Ruff（`--select=E,F --ignore=E501`）均通过。最终文件哈希与统计见同目录 `implementation-report.json`。局部绿色不替代候选完整随机顺序套件；P0 已通过，后续组合候选的最终验收仍待。

## ToolContext：只迁移会话存储这一域

同一 Phase 1 候选的独立域变更由另一实现者负责。保留原 `SubagentSessionStore` 和锁/8 项 FIFO 上限，在现有 `ToolContext` 增加 typed 只读 `subagent_sessions` property 与 `with_subagent_sessions(store) -> ToolContext`。后者只用 `dataclasses.replace` 返回浅复制，不创建 store，不复制锁，不重建预算、取消、权限、mailbox 或 lifecycle。

`agent_loop.py` 的原构造点改为 `ToolContext(...).with_subagent_sessions(subagent_sessions)`；`tools/task.py` 读 property。原私有 dataclass 初始化字段保留兼容，新增生产路径不继续私有读写。显式 None 仍禁用 continuation；未知 ID/类型不匹配不能静默开启新会话；scheduler Assignment 不进入父存储；只有完成的 explore/plan 会话可保存。

会话域三文件及 lifecycle 邻接共 23 项已通过，包含真实并行工具观察共享对象 identity 的断言。它与本切片共用同一候选 `agent_loop.py` 构造调用；该小域不计入 497 行主函数净减量，也不扩展本次 compaction 状态对象。

定向原始证据已保存为仓库内的 [Phase 1 证据包](../artifacts/governance-trial-2026-10-24/phase1-proofs/README.md)，包含逐文件 SHA、独立审查及被否决抽取的恢复材料。

## 候选验收状态（2026-10-25）

P0 `9f936942` 已取得 14,923 passed / 8 skipped 的完整随机结果，随后 P0 文档/证据序列随 `ff2f18ea` 原子推送 main 与开发分支。Phase 2 冻结记录 `ab17ca7e` / `ea31eed2` 已推送开发分支，仍为 0 API / 0 results；不改变本切片的实验或验收结论。

首次 P1/P3 组合 `eb17c62a` 的全量已结束：14,932 passed / 1 failed / 8 skipped / 3 warnings，2889.76 秒。唯一失败是 hot-store 的 `200.36325 ms < 200 ms` 墙钟断言，完整来源身份见 [release baseline](release-baseline-2026-10-24.md#首次-p1-全量与后续修复)。`71347fd5` 修正该计时承诺与测试，生产 AST 不变；累计预算、真实 worker 和迟到命令拒绝的对照及独立审查见 [hot-store 证据包](../artifacts/governance-trial-2026-10-24/hot-store-budget-proofs/README.md)。

`d0f728e0` 修复既有 wheel 检查器的 editable 来源误判；另已独立以 `-I -S` 验证精确 `9f936942` / `eb17c62a` 实际 wheel 的各 9 个指定模块来源，[证据与独立审查](../artifacts/governance-trial-2026-10-24/wheel-import-proofs/README.md)已归档。原 CI run `40111546762` 两个 Windows job 的失败为既存 portable 临时句柄未关闭，`37761457` 的最小修复及同字节反例见 [Windows 证据包](../artifacts/governance-trial-2026-10-24/windows-portable-publication-proofs/README.md)。同一 CI 的 Ubuntu 3.11 / macOS 3.11 内部错误同因于既有测试全局猴补 `os.name`；真实 Python 3.11.15 复现 WindowsPath/pytest INTERNALERROR。`7b8157e7` 仅以模块局部 `os`/`sys`/`subprocess` 代理隔离测试，生产不变；Python 3.11/3.12 各 124 项相关通过，最终同字节目标测试在 exact P0 1 passed，2 个合成坏 flags/session 反例正常失败。[fixture 证据](../artifacts/governance-trial-2026-10-24/run-command-fixture-proofs/README.md)已归档。

封存候选 `3cadcb7584526b41af7cc928d6c58d5bb19d8ea5` 的本机完整随机套件仍在运行，已知 v40 closure 失败；[草稿 PR #2](https://github.com/zrb3052796119/CodeLoop/pull/2) 的原生 [CI run `40114448888`](https://github.com/zrb3052796119/CodeLoop/actions/runs/40114448888) 同样运行中。以下跟进修正尚未提交发布，不能用于覆盖该封存候选的失败或宣称 Phase 1 go。

旧 CI `40111546762` 的 Ubuntu 3.12 完整结果为 14,911 passed / 2 failed / 18 skipped；两项失败分别源于 fixture 依赖目录枚举顺序、在真实 outer Future 完成前过早发出信号。修正仅调整测试前提，相关 60 项（seed `20261024`）与 124 项（seed `42`）通过，但范围有重叠、不相加。另将已审查的 Windows publisher 生产改动补入未冻结 v41 的精确 staged source 声明：相同最终测试旧声明 15 failed / 19 passed，新声明 34 passed，相关 179 项通过；40 份历史 pin 字节不变，不激活 v41。原始失败、独立审查及边界见[跟进证据包](../artifacts/governance-trial-2026-10-24/ci-followup-proofs/README.md)。 **Phase 1 go 继续 pending。** 不将首次候选的通过部分、定向回归或 P0 结果拼成该候选的完整通过。
