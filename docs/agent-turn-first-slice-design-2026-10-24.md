# Agent Turn 首个切片设计（2026-10-24）

本次只做设计，生产源码未改。结论：**不执行“首刀净移至少 300 行”的抽取；先保留此设计，等待 Phase 0 最终基线通过。** 本轮检查的自然边界不足以证明 300 行净减量可以安全实现。以下给出小切片的具体先例，以及后续较大抽取应完成的所有权设计。这不是已完成 Phase 1，也不把若干不相关的小改累计成一次 300 行验收。

## 测量基准与决策口径

依据 [D1 状态矩阵](agent-turn-state-ownership-2026-10-24.md)，源码 `minicode/agent_loop.py` SHA-256 为 `ee82348ae566289c950ca35ec9fd6abf36b03ca97cec3c839d2f6764af6207fe`。下列行号和 AST 长度均对应这一版本。

`run_agent_turn` 位于 2103–7423 行，共 5321 个物理行、54 个参数。闭包捕获 92 个不同的 turn 绑定，其中跨职责组共享 28 个，比例为 30.43%。**没有触发 >60% 阈值**。设计暂不实施的原因是具体边界和调用顺序尚未满足抽取验收，不是阈值失败。

“净移 300 行”的验收应是 `run_agent_turn` 同一 AST 节点的前后物理行数差，扣除新增的委托、组装参数、状态同步和兼容代码；不统计新文件新增量、格式压缩、空行删减或把函数改名。下表是候选的毛长度上界，不能当作已实现的净减量。嵌套 `closing_failed` 和 `retain_migration_checkpoint` 已包含在外层函数内，不重复计数。

| 候选边界 | 当前函数与位置 | 毛长度 | 约束与判断 |
| --- | --- | ---: | --- |
| 强制加载已路由 Skill | `enforce_skill_load_before_final`，2407–2429 | 23 | 4 个 turn 捕获、1 个 nonlocal；最小可观察状态转换，可作为安全边界先例，但达不到行数门槛 |
| 子代理最终答复屏障 | `enforce_subagent_completion_before_final`，2457–2544 | 88 | 5 个捕获；屏障快照、进度消息、trace、交付确认必须保持顺序 |
| 工作流交付辅助逻辑 | 3106–3180 中 5 个函数 | 71 | 观察 4、剩余项 16、收尾 23、恢复回执 14、中断消息 14；依赖真实提交回执与 transcript，不等同于审批 |
| 有限收尾 | `_attempt_max_steps_soft_landing` 3416–3593 + `finish_bounded_completion` 3595–3636 | 220 | 前者捕获 23 个 turn 绑定，涉及唯一模型调用、预算预约/结算、取消、消息投影；后者还优先交付已提交工作流 |
| 原生完成审查 | `enforce_implementation_completion_before_final`，3686–3915 | 230 | 43 个 turn 捕获、10 个 nonlocal 重绑；已有能力对象外还有 turn 状态和交付副作用，不宜机械搬到一个参数包 |

230 + 88 = 318 或 220 + 88 = 308 都仅是毛长度，而且把“源码和测试证据能否批准当前候选”或“有限收尾”与“异步子代理结果是否已展示”绑成一个新职责。工作流回执不授权宣布任意用户目标完成；模型的部分总结也不授权批准候选。不能为了满足行数门槛合并这些状态机。

本结论限于上述已检查候选，不声称整个函数不存在任何可安全抽取的 300 行边界。下一步需要的是可独立审查的状态所有权方案与绿色行为基线，而不是一个拥有 43 个依赖的万能 `TurnContext`。

## 最小先例：已有 Skill 加载约束的判定与呈现分离

这一先例是备选设计，不作为 300 行验收，也不新增 gate、controller、配置项或模块。沿用现有强制加载规则，把其纯判定放到已有 `minicode/skill_router.py`，不要放进负责事件投影的 `run_events.py`。

建议的显式接口形状：

```python
# 仅两个字段的不可变状态；不是服务容器。
@dataclass(frozen=True)
class RequiredSkillCompletionState:
    required: frozenset[str]
    nudged: bool = False

# 返回新状态、现有三种动作、需要呈现的文本。
def evaluate_required_skill_completion(
    state: RequiredSkillCompletionState,
    loaded_names: frozenset[str],
) -> tuple[RequiredSkillCompletionState, Literal["ok", "retry", "failed"], str | None]:
    ...
```

也可以使用等价的命名元组；不要求为数据载体再开模块。这里抽出的是已有规则，不能增加一层第二 gate 或让判定器调用模型、工具、事件、时钟或权限服务。

| 状态或效果 | 所有者 | 读写契约 |
| --- | --- | --- |
| 规范化的 required 名称集合、是否已提示 | 一个 turn 的 `RequiredSkillCompletionState` 值 | 创建时规范化一次；判定返回新值；不跨 turn 复用 |
| 已成功加载的名称 | 现有 `SkillUsageTracker`，`run_events.py:214` | 使用 `loaded_names()` 的不可变快照；只能来自真实加载，不从模型文本恢复 |
| 完整 transcript、assistant/progress 回调、turn outcome | `run_agent_turn` | 判定器不持有这些对象；loop 在原调用点应用返回值 |
| discovery 是否已提示 | 原有 discovery 路径 | 保持单独的 advisory-once 规则，不与强制加载终止规则合并 |
| cancellation、deadline、预算 | 当前 turn 的原对象 | 不转移到 Skill 判定状态；判定本身不消耗模型额度 |

两个真实调用点是 `agent_loop.py:5095` 与 `:5204`。必须同时迁移并保留各自现有周边顺序，不能把第二个分支的呈现提前/延后顺便“统一”。第一个分支的顺序为：子代理 finalization barrier → Skill 强制加载判定 → discovery 提示 → 实现完成审查 → cancellation 检查 → 最终呈现和 trace。第二个分支前面已有自己的内容呈现逻辑，此次不改变它。

状态转换保持三种：集合已满足即 `ok`；首次缺少时返回 `retry`、置 nudged 并产生原提示；再次缺少时返回 `failed` 和原失败文本。`retry` 的 user 消息由 loop 追加。终止时仍先检查 cancellation，再按原顺序调用回调、追加 assistant 消息并设置 failed outcome。不能因为抽取把“仅提示一次”变成“每次 final 都刷新额度”。

预期这里只能减少约十余行主函数代码；确切净值需实际 diff 和 AST 复测确认，**不能承诺达到 300 行**。其价值是先验证“数据状态 + 明确输入/输出 + loop 保留副作用”的形式，而非移走一个同样庞大的闭包。

保留真实 seam 测试：

- `tests/test_agent_loop.py::test_routed_skill_must_be_loaded_before_final_is_accepted`：真实 `load_skill`、先拒绝提前 final、再放行；不能把加载器 mock 成直接改 tracker。
- `tests/test_agent_loop.py::test_routed_skill_refusal_is_not_reported_as_success`：重复 final 必须以 failed 收束。
- `tests/test_skill_load_batch_barrier.py`：一次响应中其他调用必须等实际 Skill 加载后才能执行。
- 实施时补齐第二调用分支、两个分支共用同一 nudge 状态、取消优先于失败呈现的行为用例；先确认相邻测试是否已有覆盖，避免只镜像纯函数实现。

## ToolContext：只迁移子代理会话存储这一域

首个域选择 `_subagent_sessions`，不同时迁移 mailbox、生命周期、预算和 permissions。`_subagent_mailbox` 还承载 Executor/Reviewer 的身份绑定通道；简单重命名会掩盖权威关系。生命周期另有 owner shutdown 与异步 worker 不得等待自身的契约，首刀也不顺带改它。

现有所有权与调用点：

- `agent_loop.py:2263` 创建当前 loop 的 `SubagentSessionStore`；串行/并行工具调用共享同一个实例，子 loop 的既有创建行为不变。
- `_execute_single_tool` 的显式参数 `subagent_sessions` 位于 `agent_loop.py:873`，在 `:994` 绑定到 `ToolContext`。不改成自动查找全局对象。
- 私有字段声明为 `Any | None`，位于 `tooling.py:925`。
- `tools/task.py:2173` 读取存储；`2185–2218` 在昂贵子代理设置之前处理不可用、未知 ID、类型不一致；`3086–3100` 仅保存非 scheduler 的 explore/plan 结果。
- `subagent_sessions.py:31` 的现有 `SubagentSessionStore` 负责锁、8 项默认上限和 FIFO 淘汰；`SubagentSession` 负责已完成会话数据。

建议在现有 `ToolContext` 增加一个只读 property 和一个显式绑定方法：

```python
@property
def subagent_sessions(self) -> SubagentSessionStore | None:
    ...

def with_subagent_sessions(
    self, store: SubagentSessionStore | None,
) -> ToolContext:
    ...  # dataclasses.replace(self, _subagent_sessions=store)
```

以上为接口草图。类型可放在 `TYPE_CHECKING` 导入内，避免增加运行期循环依赖。property 只返回原引用；方法返回浅复制后的新 context，不修改调用者，不创建 store、不复制锁、不重建预算或取消对象。不允许通用 `set_private(name, value)`、依赖字典或透传 `**kwargs`。

生产调用迁移为 `context.subagent_sessions`，构造处用 `ToolContext(...).with_subagent_sessions(subagent_sessions)`。存储的 `get/save/retained_ids` 继续使用已有显式方法，不加同义包装层。私有初始化参数暂保留兼容已有 embedder/测试，阶段内新增生产代码不得直接读写它；未检查外部兼容性前不直接删除 dataclass 字段。

必须保留的行为：`None` 仍明确禁用 continuation，未知 ID 不能静默启动新会话；类型不匹配不能复用；scheduler 的冻结 Assignment 不进入父会话存储；只保存已完成 explore/plan 会话；共享 budget、cancellation token、permissions、mailbox 和 lifecycle 都必须保持对象身份，不能因 context 复制获得新的额度或越过授权。

针对这一域只选 `tests/test_subagent_continuation.py`、`tests/test_subagent_concurrent_context.py`、`tests/test_subagent_context_delivery_regression.py`。在现有行为测试中补显式绑定方法的非突变和共享对象身份断言。保留 `tests/test_subagent_lifecycle.py::test_async_task_worker_does_not_join_its_own_lifecycle_job` 作为邻接回归，不修改它的 lifecycle 前提。

## 大切片继续设计的真实边界

若 Phase 1 仍要求单个切片净移至少 300 行，优先继续设计已有 `ImplementationCompletionReview` 周边的“当前候选完成评估事务”，而不是新增总控制器。当前 230 行闭包只是核心，尚未形成满足门槛的完整边界。必须先明确哪些状态确实归完成评估所有，哪些仅是 loop 协作。

候选 typed state 只能包含 `review`、`attempt`、`accepted/classification`、`limits` 及其一次初始化标记、`migration_checkpoint`；可放在已有 `implementation_completion_review.py`。不得把 `model/tools/messages/context_manager/event_sink/permissions/budget` 一起塞成字段。它们是现有能力参数或逐次调用输入，且 transcript 和 presentation 仍归 loop。存储格式兼容和已保存 continuation 的恢复路径需要单独审查，不能只替换局部变量。

返回值应显式给出 `ok/retry/stop`、当前候选绑定的 decision/check receipts、评估状态更新及需要交付的 feedback 数据。loop 决定消息插入位置、trace 和最终呈现；评估能力不得返回“执行任意回调列表”把顺序藏回去。`direct_implementation_batch_status` 与 phases 的验证观察当前跨 tools/completion 共享，不能仅因它们出现在完成审查里就改归评估对象。

必须保留的副作用顺序来自 `agent_loop.py:3686–3915`：

1. 完成并交付待决写入批次反馈；已有控制异常不得被 cleanup 覆盖（`3654–3672`）。
2. 检查取消与父 deadline，再执行真实当前候选 checks；候选变化立即使迁移 checkpoint 失效。
3. 同一 turn 只初始化一次 native review allowance，保留父关闭预算；重复 final 不刷新 allowance。
4. 必要时先迁移历史探针，再运行独立 review/inspection/coverage。保留同一 Executor、原始证据与候选摘要绑定。
5. 预算耗尽时保留已提交的负面证据，绝不推导成功；`finally` 恢复父 budget/deadline。父取消和父 deadline 仍优先传播。
6. 再检查取消/deadline与当前候选摘要。之后才发布 completion_decided 和 trace。
7. 批准时只把实际 verifier 回执计入 verification；恢复审查若推翻旧修复，先撤销旧 active feedback 权威，再呈现新状态。
8. 拒绝时保持原 Executor，替换 active feedback、保留历史、维护 task ledger，最后同步 context manager。模型的未通过 final 不得作为完成交付。

工作流交付是另一条不可混入的链：真实回执先进入 transcript，再确认 delivery，才允许 `TurnWorkflowDelivery.observe` 消费对应 invocation；已提交回执在取消、压缩和异常时仍可恢复。`finish_bounded_completion` 先尝试交付这些回执，再考虑一次无工具总结。只有 lifecycle 所有者负责 shutdown（`6737`、`6853`）。这些 ordering 不由完成评估 typed state 接管。

较大抽取的必要回归包括当前候选变更、原生审查预算耗尽、旧负面证据重验证、inspection 的实际读取、同一执行者反馈/续作、提交后取消与回执交付。复用 `test_completion_review_budget_result.py`、`test_implementation_completion_review.py`、`test_implementation_continuation.py`、`test_independent_coverage_inspection.py`、`test_semantic_supersession_continuation.py`、`test_submission_feedback_continuation.py`、`test_workflow_delivery_summary_safety.py`，按最终触及边界再缩小。不能用一个 mock review 返回 approved 的测试替代这些。

## 交付与停止条件

1. 先完成 Phase 0 最终冻结基线；本设计不声称全套已绿，也未为设计运行广测。
2. 审阅本次对 300 行首刀的设计暂停结论。如果接受较小先例，应明确其不满足原数字验收，而不是暗改统计口径。
3. 独立迁移上述一个 ToolContext 域，验证现有 continuation、并发身份及禁用行为；不要顺便批量改其他私有字段。
4. 若接受 Skill 判定先例，再单独实现并在两个原调用点验证。两项各自可回滚，不共同包装成一次 300 行抽取。
5. 继续补完成评估事务的所有权/接口和 continuation 格式设计。实际源码 diff 能证明净移至少 300 行、没有宽上下文参数包、原 seam 回归通过后，才可以宣布较大切片满足 Phase 1。

本文没有新增运行时模块、controller、gate、memory 模块或环境变量。只做源码读取和 AST 长度核对；生产抽取与测试执行须等待 Phase 0 结果及后续实施决定。
