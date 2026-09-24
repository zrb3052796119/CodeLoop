# 多 Agent 协作审查（2026-09-08）

审查范围是当前工作区根目录 `minicode` 的实际代码，包括未提交修改。[pyproject.toml](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/pyproject.toml>) 的打包入口指向这里，`py-src`、`ts-src` 的历史实现不计入当前运行时。此次只审查并保存报告、离线探针，没有修改业务代码，也没有调用真实模型或做实时成本基准。

判断：角色数量足够；实现、验证、审查和回写的权力分离有明确基础，但还不能称为边界完全干净。优先修复裁决者的权限/证据交接、父任务结束条件和并发回写的一致性，再补失败修复循环和统一委派入口。增加更多专职 Agent 不是当前优先事项。

**究竟有几个 Agent**

这些是按需创建的角色，并非固定常驻进程。

| 分类 | 名称 | 职责与当前实现 |
| --- | --- | --- |
| 独立只读子 Agent | Explore | 搜索、定位、仓库探索；允许同步调用和异步 spawn/poll/cancel，默认 12 轮。 |
| 独立只读子 Agent | Plan | 深入分析和设计建议；与 Explore 的工具和任务范围高度接近，多一个 code_review，默认 8 轮。 |
| Workflow 内部模型角色 | Analyst | 调查事实、给出风险和实现顺序候选；使用 plan 模型路由。 |
| Workflow 内部模型角色 | Executor | 按锁定任务和路径修改隔离快照；使用 general 模型路由，默认 15 轮；无命令执行和自行提交权限。 |
| Workflow 内部模型角色 | Reviewer | 对冻结 diff 和验证证据作审查判断；使用 plan 路由，默认 8 轮。 |
| Workflow 内部模型角色 | Adjudicator | 冲突出现时按需裁决；使用 plan 路由，默认 8 轮。当前实现存在下文的边界缺口。 |

因此按“编码协作中的模型职责”数是 **2 个独立角色 + 4 个 workflow 内部角色，共 6 种**。按对外生产 task 入口数是 **explore、plan、workflow 三个**。配置仍保留第四个 `general`，但生产 Agent Loop 拒绝直接使用，它作为兼容入口和 Executor 的底层模型路由存在。

`workflow` / `CentralScheduler` 是确定性编排代码，`Verifier` 是确定性检查执行器，均不应再算成一个 LLM 子 Agent。`MemoryCuratorAgent` 是另一条记忆维护链路，可能用模型合成经验，独立于编码任务委派；`AgentRouter` 选择模型，`CyberneticOrchestrator` 调整运行参数，也不是可领取编码任务的同事。[agent_protocol.py](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/agent_protocol.py>) 中的 TeamRegistry/AgentRole 在当前 minicode 运行模块内未发现外部引用，不应把那组 enum 再加到 Agent 数量里。

角色声明见 [minicode/subagent_types.py:14](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/subagent_types.py:14>)、[minicode/multi_agent_contracts.py:255](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/multi_agent_contracts.py:255>)；生产 general 拒绝见 [minicode/tools/task.py:2753](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/tools/task.py:2753>)；角色模型/轮数映射见 [minicode/workflow_runner.py:539](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/workflow_runner.py:539>) 和 [minicode/config.py:854](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/config.py:854>)。

**现在的调度方式**

这是父 Agent 委派加受控流水线，主要协作方向是父子交接，不是子 Agent 自由协商的网络。

- 简单读取：父 Agent 直接完成；较大的只读工作可委派 Explore / Plan，异步池默认 4 个 worker，每轮累计最多 16 个 job。
- 小范围低风险修改：父 Agent 有默认启用的直接编辑通道，最多 4 个文件、每个 diff 200 行、每轮 400 行，具体敏感路径仍受限制。
- 明确、低风险、范围窄且有声明验证覆盖的 workflow：Executor → Verifier → 回写。
- 普通复杂修改：Analyst → Executor → Verifier → Reviewer → 回写。
- 高风险且需要人类确认、或 critical 修改：Analyst → Human → Executor → Verifier → Reviewer → 回写。
- 冲突：必要时插入 Adjudicator；未解决则停止或交还。

规则见 [central_scheduler.py:306](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/central_scheduler.py:306>)、[multi_agent_contracts.py:560](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/multi_agent_contracts.py:560>)；直接修改通道见 [permissions.py:235](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/permissions.py:235>)、[prompt.py:181](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/prompt.py:181>)。注意这里的 commit/promotion 指把已验证的快照修改回写到父工作区，并不等同于 git commit。

**优先处理的确定问题**

1. **[P1] Adjudicator 未被识别为受限的 scheduler 只读角色。** [tools/task.py:1857-1860](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/tools/task.py:1857>) 只识别 analyst/reviewer，实际 `_run_adjudicator` 在 [workflow_runner.py:3341-3354](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/workflow_runner.py:3341>) 派发 plan/adjudicator。结果是它走普通 Plan 的工具、Skill 处理和 `PermissionManager(context.cwd)`，而不是 `SchedulerWorkerReadPermissions`。指定的 read_roots/forbidden_paths 没有进入同一套权限执行。这会允许读取任务授权范围之外、但仍在工作区内的材料，污染裁决输入。它仍无普通文件写工具，`_workflow_phase` 也仍会禁用 memory，不能夸大为任意写入或记忆泄漏。应以封闭角色表统一定义 prompt、工具、权限和模型路由，未知角色直接拒绝。

2. **[P1] Adjudicator 拿不到要求它评判的冻结证据。** [workflow_runner.py:829-873](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/workflow_runner.py:829>) 构造的 claims 包含 Analyst 证据和 WorkflowRequest 的 evidenceRefs；但 `3330-3334` 只授权读取 conflict artifact 和 assignment artifact。证据引用不是证据正文，也不会自动赋予读取权限。应由 scheduler 验证引用后，将这批冻结证据显式加入 assignment/channel；裁决测试必须实际读取两方证据，不能只模拟一个合法 select_b 返回值。此问题与上一条独立：修好权限分类后，缺失的证据授权仍须补齐。

3. **[P1] 子 workflow 完成被当作整个父任务完成。** [workflow_finalization.py:1158](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/workflow_finalization.py:1158>) 把成功回写标为 `_must_deliver`；[agent_loop.py:4146-4188](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/agent_loop.py:4146>) 见该标记后立即 return，不再进入父模型下一轮或普通异步 finalization barrier。探针得到 parent_model_calls=1、async_barrier_ready_at_parent_return=false、pending_subagent_count=1。若用户要求先完成 A 再完成 B，或旁边还有调查结果待汇总，A 提交后父任务就提前结束；父循环持有生命周期时还会在 finally 中 shutdown 剩余子任务。应拆分“提交结果必须原样保留”与“整个用户目标已完成”：前者是不可变回执，后者由父任务账本和全部子任务状态决定，同时保留取消/提交状态未知时的权威结果投递保护。

4. **[P1，并发条件下] 不相交的写路径不能证明任务彼此独立。** [workflow_runner.py:1557-1565](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/workflow_runner.py:1557>) 只按 write_paths 准入，`1652-1660` 也只把写集交给基线检查；[workflow_workspace.py:794-820](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/workflow_workspace.py:794>) 因而忽略被读取的其他文件是否已变化。两个快照各自通过验证、分别修改不同文件，合并后仍可能破坏同一个跨文件约束。适用情形是同工作区的并发 workflow/多会话调用者，单个父循环串行跑 workflow 不触发此反例。应记录实际读集及版本，在晋升时复核；短期可以保守串行化同工作区修改，或在最新父状态上重建集成快照并重验后再原子晋升。

5. **[P2] 同步与异步委派没有共享失败熔断。** [tools/task.py:2766-2792](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/tools/task.py:2766>) 只给 action=run 检查/更新计数；spawn 的 runner 在 `2805-2834` 直接调用 `_run`。探针先制造两次同步失败，使下一次 run 返回 delegation_circuit_open，再用同上下文 spawn，仍能成功准入；异步失败也不更新计数。应把准入、终态结算放到一个共同入口，按任务/回合定义熔断范围，并处理并发更新。

附带发现：[agent_runtime.py:358-365](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/agent_runtime.py:358>) 无条件优先返回历史最后一条 assistant。已有上一轮答案、当前轮以 workflow tool_result 结束时，可能显示旧答案。探针已复现。这与第 3 条的最终交付协议应一并修复。

**职责与调度上的改进空间（不是违反当前合同的 bug）**

- **分析职责重叠。** Explore / Plan 的差别主要是深度、提示词和 code_review 工具；Analyst 又承担调查和规划。推荐先把内部实现收敛成 Analyst 的 discover / design / prepare_change 模式，保留 Explore/Plan 对外别名兼容。明确交付物：discover 给事实和位置，design 给备选方案及取舍，prepare_change 给受范围约束的实现顺序，避免主 Agent 探索一次、Plan 再探索一次、workflow Analyst 再探索一次。
- **执行者缺少修复反馈循环。** Executor 无 run_command 是合理的权限取舍，但后续 Verifier 失败一律 retryable=False（[workflow_runner.py:3746-3757](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/workflow_runner.py:3746>)），Reviewer changes_required 也直接终止（`3239-3245`，[central_scheduler.py:927-947](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/central_scheduler.py:927>)）。当前重试主要是阶段调用/协议重试，不是改代码后重新验证。建议只由 scheduler 增加 1–2 次修复预算：失败证据 → Executor → 新 patchDigest → Verifier → 必要时 Reviewer，范围不变；安全拒绝、范围扩张、需求不清仍应停止。
- **并发上限分散。** AsyncSubagentLifecycle 的默认 4 worker 和同步只读调用所在的 mc-tool 线程池独立。反馈控制器降低 `_force_max_workers` 只影响后者，不能压低已经 spawn 的异步队伍。未发现代码承诺“总共最多 4 个”，但统一回合级 admission/semaphore 才能让负载反馈真正约束全部委派。
- **能力配置没有完全跟随业务角色。** Analyst/Reviewer/Adjudicator 都使用 plan 的模型/8 轮限制。可以支持按 logical role 配置预算和模型，让廉价探索、实现和高风险审查各自调优；同一模型审查不同上下文不等于完全独立的判断，换不同模型也不自动保证独立，应以实测判断收益。
- **工具限制应保留必要能力。** 结构化只读角色仅有 list/read/AST/notes，缺少经过 scope 审计的 grep_files；Executor 也缺 grep。大型仓库调查会退化为逐文件读取。可增加受统一路径权限约束的搜索/符号检索，不需要放开任意命令。
- **兼容层与生产入口还未收敛。** README.zh-CN.md:195 起仍把 general 写成生产同步写 Agent；task schema 同时包含已弃用类型和 parallel_explore，并宣称所有修改都必走 workflow，而 prompt 已允许 parent fast edit。建议从同一角色/路由定义生成生产 schema、prompt 和文档，把 legacy 参数留在兼容 adapter 中。这样可以减少父模型选错入口和开发者维护漏项，Adjudicator 的遗漏正说明手工多处分支的成本。

**建议的落实顺序**

先修角色映射和冻结证据可读性、父任务结束协议、并发读依赖检查及统一熔断。这些是正确性修复。随后补有界 Executor 修复循环，统一回合并发额度。最后再合并分析实现、分角色配置模型和预算、简化 schema/提示词/文档；暂不扩展为更多常驻专职 Agent 或自由互聊网络。

保留目前有效的机制：封闭 Assignment/result 合同、分配级只读 artifact channel 和单个结果写入权、隔离快照、patchDigest 绑定、Verifier 与 Reviewer 独立、共享预算和稳定身份、受控深度，以及失败时不回写。这里的“独立”主要指职责和证据权威，不代表所有生产 Worker 都在独立 OS 进程中：当前 scheduler Executor 仍走进程内执行，强杀子进程路径主要覆盖 legacy general（[tools/task.py:2061-2071](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/tools/task.py:2061>)）。

**验证与证据范围**

本次运行 22 个相关测试文件，合计 **490 passed**（311 + 179），日志在本目录 tests-contracts.txt 和 tests-integration.txt。绿色测试证明现有合同受到保护，不证明跨角色权限、跨 workflow 组合和实际模型协作质量已经充分覆盖。

本目录保存独立离线探针：父任务提前结束/旧答案投影、异步熔断、裁决角色权限/证据访问、并发 write skew。探针用确定性模型或内存 fixture 隔离边界，文件事务探针在临时目录执行。没有据此声称当前真实模型成功率、token 节省比例或并行速度提升。

**可直接查看的复现产物**

- [probe_parent_completion.py](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/multi-agent-review-2026-09-08/probe_parent_completion.py>) · [运行结果](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/multi-agent-review-2026-09-08/probe_parent_completion.json>)
- [reproduce_role_boundaries.py](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/multi-agent-review-2026-09-08/reproduce_role_boundaries.py>) · [运行结果](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/multi-agent-review-2026-09-08/role-boundaries-result.json>)
- [probe_async_circuit.py](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/multi-agent-review-2026-09-08/probe_async_circuit.py>) · [运行结果](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/multi-agent-review-2026-09-08/probe_async_circuit_result.json>)
- [probe_concurrent_write_skew.py](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/multi-agent-review-2026-09-08/probe_concurrent_write_skew.py>) · [运行结果](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/multi-agent-review-2026-09-08/probe_concurrent_write_skew_result.md>)
