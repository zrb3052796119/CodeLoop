# Phase 2 本地修复与验证

日期：2026-10-23。前置工程门：gen-2总5/6（single3/3、explicit2/3），结果提交94c06e26。本报告记录随后本地改动；真实新八题已完成且未过门，最新结论见 [evaluation-report.md](evaluation-report.md)。

## 已完成

主提示与task工具描述统一为任务形状：独立调查问题用explore，明确设计疑点用plan，带授权范围和可执行检查的隔离实现用workflow，已知位置的单点修复和单事实查找用直接工具。每类提供简短正反例，强调明确交接物、共享预算与时间；文件数量只作辅助。两处均说明已有`@file:relative/path`完整正文通道、3800字符内联上限与800字符摘要，没有增加角色或协议字段。

原进度反馈会向无task的实现者重复建议workflow。现改为：在默认连续执行模式下，顶层Agent具备当前启用的task、不是plan或原生结果提交角色、未发生编辑、已有至少8次成功读取批次且不处于现有预算/时间压力时，追加一次条件性的委派建议。工具是否可用每次请求重新检查；模型仍按任务形状决定是否委派，宿主不会自动发起。编辑、验证或子结果送达不会补充建议次数；普通进度、预算和截止提醒配额保持原值。

“8”是成功读取**批次**，不是工具调用次数。显式旧`bounded_implementation_phases=True`模式不追加新建议，保留原提醒和新版系统指引；有限共享预算本身不代表启用该模式。原phase定价块逐字节保持一致，没有新控制器、准入层或runtime开关。

## 验证

最终联合回归：**16文件、251 passed in 14.07s**，退出码0；`git diff --check`通过。覆盖主提示、task指引、文件任务书、实际loop进度反馈、只读与工具能力、预算/截止升级、持久日志、阶段控制、恢复上下文投影和连续Executor工作流。具体命令与完整输出见 [final-joint-regression.txt](final-joint-regression.txt)。

新增实际loop测试包含于上述251项，不另外累加：18项通过。对保存的修前loop与tracker源码，使用相同公开API和同字节测试，两个目标断言失败；修后两项通过，没有用TypeError或导入失败充当反例。旧源码复现无task时12批读取收到3次委派指令。证据见 [progress-counterexample.json](progress-counterexample.json) 和 [最终收据](progress-final-receipt.json)。这些模型决策均为本地脚本，不涉及真实provider调用。

扩展检查发现的5项恢复测试失败在修前源码上同样存在：其fixture使用默认continuous，却断言旧bounded阶段配额。已只给该fixture显式传`bounded=True`，保留全部断言，未改变生产controller；该文件58项通过。修前/修后同失败证据见 [基线诊断](progress-existing-recovery-baseline.json)，纠正后日志见 [recovery-fixture-correction.txt](recovery-fixture-correction.txt)。

独立复核无阻断，核对源码哈希、能力边界、一次性建议状态和原phase计价块一致性。精确变更见 [phase2.patch](phase2.patch)：以gen-2执行宿主为生产基线，包含4个生产文件、2个测试文件，排除原有memory修改；[source-hashes.json](source-hashes.json)记录前后字节。实现留在当前工作区，未把其他用户修改一并提交。

## 后续效果验收

旧合成八题与auto轨迹已删除。存活报告能证明历史没有发生委派，不能证明模型看到了哪条动态建议而拒绝采纳。合成八题的旧分母是0/24，真实八题才是0/16；已修正方案中的混用，详见 [取证笔记](notes.md)。

用户随后授权新八题及端点数据发送，trigger-gen2先冻结并提交e85a9580，再固定执行八格各一次。独立终审8/8有效：真实触发0/8、两负例零启动完整，6/8候选通过产物验收，但13次请求用量未知，Phase2未过门。5格硬截止、1格partial、末2格首请求TLS失败；未补跑、未改变gen-2注册文件，正式48次停止。独立窗口完成AgentRouter缺候选fail-closed与Windows限制说明，控制器行为保留。

## 最终回归命令

```sh
python3 -m pytest tests/test_task_tool_guidance.py tests/test_prompt.py tests/test_workflow_prompt_criteria.py tests/test_verification_prompt_guidance.py tests/test_workflow_brief.py tests/test_coding_progress.py tests/test_coding_progress_safety.py tests/test_progress_deadline.py tests/test_progress_escalation.py tests/test_progress_journal_persistence.py tests/test_progress_delegation.py tests/test_implementation_phases.py tests/test_implementation_recovery.py tests/test_implementation_progress_projection.py tests/test_continuous_execution_runtime.py tests/test_continuous_executor_workflow.py -q
```
