# 多 Agent 实用性修复实施记录

实施：2026-10-22 至 2026-10-23。起始 HEAD：`e092a9af`。

**后续状态（2026-10-23）**：下文保留初始实现时点的范围与验证记录。用户随后授权gen-2新题集，先提交预注册f131d7f3，再在明确授权端点执行固定六格：5/6（single3/3、explicit2/3），独立final审计6/6有效，继续门通过。未替换失败格或改变模型；Phase2随后完成新八题，但真实触发0/8且用量不完整，未过门；独立路由fail-closed与Windows文档已收尾（见文末），其他控制器行为未改。详见 [gen-2终态报告](../multi-agent-gen2-2026-10-23/report.md)。

## 范围与状态

已实现方案 v1.1 的 Phase 0 和 Phase 1 代码，以及明确允许立即删除的两个根包死模块。Phase 1 的模型效果验收尚未进行，Phase 2 和模型路由/控制器行为清理未启动。没有切换模型、调用付费模型评测、创建提交或 PR。

初始工作区已有 memory、agent_loop 等修改，保存在 `initial-status.txt` 与 `preexisting.diff`；本次未覆盖这些工作。实际工作区差异同时包含原有修改，不能把整个 `git diff` 当作本次修复。

## 实现

- 未配置 Task 超时回退到 900 秒；显式 `None/0` 保持无限本地期限，祖先期限取最早值。
- `ToolUsageCounts` 在宿主内部传递真实调用数；显示文本和终态观察不再靠正则反推。计入被后续重试覆盖的阶段结果和同 Executor 续修。
- 调度 policy 升为 v6。完整、非敏感请求默认 E→V；显式分析触发 A→E→V，显式审查仍触发 Reviewer；实际验证失败可在原 Executor 续修前诊断一次。保留 v2–v5 回放语义。
- Analyst 的普通 unknowns 经原有结果 artifact/inputRefs 交接，保留原 scope、ImplementationOrder 与风险门。
- 第一次付费阶段前做一次资源可行性检查：Executor 最少 6 次调用、token 交接地板和时间窗口；不新增预算预留层。这是可行性下限，不保证任务必定能在预算内完成。
- 长任务书通过现有 `prompt` 的 `@file:相对路径` 约定传输，摘要最多 800 字符，普通 inline 仍最多 3800。原文权限先校验，精确 UTF-8 字节原子保存至 `.workflow-briefs/<sha256>.md`，快照校验一致性；正文中的显式分析/审查指令也进入路由。
- 工作流阶段原文在子代理返回时保全，最终摘要引用源工作区持久证据文件；子进程大正文同样采用文件加摘要，不扩大控制信封。最终收尾投影为证据路径与权威结果预留 8000 字符内的空间。
- 有 Reviewer 的路线在入口为其留出至少 120 秒或剩余时间 50% 的窗口；重试延长受原全局期限约束。提示提供实际 `review_submit` 最小合法示例。
- 删除无调用方的根包 `agent_protocol.py`、`context_isolation.py`；保留仍有引用的 `py-src` 副本。模型路由和 CyberneticOrchestrator 未改。

独立审查和真实本地链路还修复了动态诊断风险裁决的 trace 回放、验证失败误投影为证据不足、子目录路径重复拼接、目录误用新文件豁免，以及正式候选 receipt 混入动态 Reviewer probe 导致拒绝的问题。已有 scope、候选绑定和独立 probe 审查未放宽。

## 验证边界

本地集成测试使用脚本化模型控制行为，实际运行文件读取/编辑、pytest、快照、Verifier、审计、预算账本与父交付链。它们证明宿主执行链可用，不能证明模型质量或协作收益。

最终联合回归：**99 个测试文件，1579 passed，275.36 秒，退出码 0**。完整日志为 `final-regression.txt`，精确文件列表为 `final-regression-files.txt`。覆盖 workflow/scheduler/subagent、角色契约、Reviewer、正文传输、Task 超时、共享预算及子进程 lease；包括 Phase 0 指定验收组。`git diff --check` 和本次修改生产模块的 `compileall` 均通过。未安装 Ruff，因此未运行 Ruff。

失败的首轮输出仍保留于 `broad-regression.txt`，没有用较好试次替换原记录。分组回归有重叠，不累加成另一测试总数。此前旧 Reviewer 前缀和 checked-candidate 测试在纯 HEAD 也失败；已按当前真实准入与独立 probe 行为修正 fixture，并保留预算、权限和回写断言。相关基线见 `reviewer-prefix-head.txt` 与 `checked-candidate-baseline.md`。

入口报价计入已有 subprocess lease 占用、本地预算视图及交接地板，保留可选并行探索并报价其最低开销。超时/隔离失败和提交后兜底结果保留此前已收到的 Worker 调用数；中断且未收到返回的调用不推算为已知用量。

## 工程六格缺失输入

原脚本需要 `artifacts/three-arm-real-2026-10-13/tasks.json` 和 `manifest.json`。它们已在 10 月 19 日有记录的清理中删除；当前 Git 历史与剩余归档未找到原包。依据见 `docs/archive/multi-agent-retired-2026-10-19/README.md` 及其清理记录。

已向用户请求原副本路径。取得原包后，才能冻结此次实现并沿原脚本跑 3 题×2组共6次，保持固定模型、预算、审计和全部失败记录。门仍为总成功≥4/6、每组≥2/3、每题≥1/2；E→V 的 V 是确定性代码，不作多模型互补或优越性结论。缺少输入不是验收失败，也不是过门。

## Phase 3 独立清理窗口（2026-10-23）

Phase 1 工程门通过、Phase 2 的执行宿主已冻结并提交后，在当前源码工作区独立完成模型目录耗尽修复。运行中的八格继续使用已归档宿主，本次改变不属于它的受测条件，也不用于改善八格成绩。

AgentRouter仅从当前tier主模型和声明fallback依次选择；候选全缺时抛现有RuntimeError。显式空目录保持空，None仍使用默认目录；未登记的force_model同样失败，已登记自定义模型可用。现有SmartRouter在切换前传播失败，Agent Loop及Orchestrator保留当前模型。仅验证目录成员资格，不验证凭证或远端可用性。18项router/smart_router回归通过；同字节反例在真实旧源码5失败/6通过、修后11通过，失败均为未抛预期异常。独立复核无阻断。详见[独立证据](phase3/router-summary.md)。

README补充Windows真实边界：有Popen句柄时只尝试终止直接子进程，不能保证清理后代；仅持PID的兼容路径不终止Windows进程，cancelled状态不等于整树退出。此处采用方案允许的文档处置，未宣称获得Windows递归清理能力。

SmartRouter保留；CyberneticOrchestrator的路由、压缩、预算、并发和超时影响已独立审阅，保留现有行为。未将它错误地视为纯遥测，未接入当前未连接的ModelSwitcher或新增控制器。默认tier名称仍是现有目录，未改变模型选择策略来影响评测。
