# 五阶段多 Agent 修复路线审阅

日期：2026-10-22。范围：当前工作区与现有评测文档；本轮未改实现、未跑付费模型。

结论：支持“减少默认分析、保持候选连续、先证明最小交付闭环”的方向。它有望提高委派执行的可靠性与预算利用率，但尚不能保证提高任务成功率，更不能由失败推出模型是唯一瓶颈。建议修正现状、验收分母和阶段依赖后实施。

## 先澄清实验在证明什么

当前 Executor→Verifier 是两个阶段：Verifier 是宿主确定性检查，见 [run_verifier](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/workflow_runner.py:4837>)。这组实验首先检验“父协调＋委派执行＋隔离验证/回写”是否比直接单 Agent 值得使用，不直接检验两个模型角色的互补。

不跑四阶段对照、固定模型、不挑轮次，均应保留。但 Phase 1 同时调整路由、传输、交接和预算，属于一组策略改动；若要隔离路由收益，应先把双方适用的共同修复落地，再冻结，只改变委派策略。否则准确表述为“整套策略相对单 Agent 的效果”，不能把收益归给某一个改动。

## 各阶段判断与修正

### Phase 0：应先做，需验证真正生效

- 两个指定测试已复现：`2 failed in 0.71s`。mailbox 失败是对象 identity 断言，应改成共享总账＋独立阶段归因＋合计一致；scheduler 失败是预期缺少 Reviewer，应按实际 policy 分支验证，而非只改一行结果。
- [task_deadline.py:40](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/task_deadline.py:40>) 未配置时直接返回 None。只改 `DEFAULT_TASK_TIMEOUT_SECONDS` 不会生效；应区分未配置、显式无限和显式数值，保留祖先 deadline。900 秒是合理的产品默认候选，不是模型能力改进；它也会恢复 10 月 17 日明确解除的总时长限制。正式对照须显式冻结同一时限，不让默认值变化偷偷进入实验。
- 人类 header 正则计数确实存在于 [workflow_runner.py:1454](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/workflow_runner.py:1454>)。结构化宿主计数应覆盖子进程、进程内和失败出口，header 仅作展示，避免容器与叶子重复计费。

### Phase 1：收益最可能来自候选连续和减少不必要交接

1. **路由名与行为不一致。** [central_scheduler.py:406](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/central_scheduler.py:406>) 的 `verified_change` 才是 E→V；`scoped_change` 自 policy v4 起是 E→V→R。应明确改风险默认还是独立审查豁免条件，保留历史版本含义。默认免 Analyst 可以与独立审查策略分开处理。
2. **普通 unknowns 应作为输入。** 当前先持久化 AnalystHandoff，再因任何 unknowns 终结，确有浪费。但 Assignment 没有 `context_risks` 字段；`risks` 属于 AnalystReport。优先复用 handoff/inputRefs，在 Executor prompt 中投影未决事项；保留明确 blocked、权限缺失、人类授权和风险重分类语义。保留失败候选不等于批准回写。
3. **失败后分析应按失败类型触发。** 编辑锚点、schema、传输和额度错误由宿主/原 Executor 纠正；有真实反例且根因不明时再用 Analyst。保留同 ExecutorSession、候选 transaction、scope 与预算。如果实验允许此升级，就应称“按需升级策略”，不能声称每次都是严格两阶段。
4. **文件通道正确，统一扩容不是核心。** [scheduler_artifacts.py:124](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/scheduler_artifacts.py:124>) 已允许校验完整的宿主 diff 超过 20KB，仍受 4MiB 总限；此前补丁缺陷不能写成尚未修复。正文落盘、摘要＋路径＋digest 有价值，但 256KB 信封不应全部灌入模型上下文。父无法预知临时快照路径，应在快照前准备可读 brief，或由宿主管理输入 artifact；返回证据需持久化到审计目录，不能指向即将清理的临时快照。保留字节限制、路径权限和原子性属于完整性与资源管理；超限应显式失败，不能静默截断。
5. **Reviewer 预算需提前分配。** 临到审查才给“剩余 50% 且至少120秒”不保证改善，甚至比75%更少。应在 workflow 入口为适用路由预留审查、修复、验证回写和父交接窗口，重试不越过全局 deadline。当前实际提交是 `review_submit`，内联示例应对应其现行语义 schema。若本轮只评 E→V，这部分可作为独立修复，不计作两阶段的收益。

历史 279/406 是 Plan＋Analyst 合计，不是 Analyst 单独占用，也不是“失败率”。[effectiveness.md:176](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/docs/multi-agent-effectiveness.md:176>) 已有零 Plan/Analyst 仍失败的记录。应先用当前版本分账，不能把历史约69%当成今天可回收的预算。

### Phase 2：保留后置，但优化委派适配度

[coding_progress.py:202](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/coding_progress.py:202>) 已建议 coordinator 直接委派 workflow，主循环也已接线；prompt 同样已有正向用途说明。当前零触发不能直接归因为“无触发机制”或两句劝退文案。

任务形状应围绕可独立调查的问题、明确交接产物、上下文隔离价值和充足剩余预算。文件数≥4、读取次数≥8只能是观察信号，不能直接决定委派价值。复用现有事件记录“满足条件→提议已呈现→实际委派→完整交付”。25%只作覆盖指标，同时看质量与成本；“零误触发”应限定为预注册负例集上的观察结果，不声称总体误触发率为零。

### Phase 3：将入口准入提前，避免盲目缩小交接预算

入口资源准入应并入 Phase 1，放在第一个付费阶段前，进入 Executor 时再复核。2→6是待验证的默认候选，这里计的是模型请求，不是工具调用；还需同时估算下一次输入 token、时间及后续交接需求。历史已有21/40调用但累计 token 接近耗尽的记录，单改请求下限无法解决。

[agent_loop.py:796](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/agent_loop.py:796>) 的父交接 floor 当前是上下文估算＋回执估算＋2048，并非百分比。应先缩小父最终答复所需的上下文与返回投影，再按实际输入成本预留。直接替换成固定小额＋比例可能再次产生“代码已完成，父无法完整交付”的故障。

### Phase 4：分开死代码删除和运行行为变更

根包 `agent_protocol.py`、`context_isolation.py` 无外部使用，删除方向成立；旧 `py-src` 副本仍有调用，范围需明确。

SmartRouter 有生产调用，而且包裹 AgentRouter，不是四套彼此独立的选择权威。ModelSelectionController 在当前主路径主要是日志推荐，不能直接视为已有主模型绑定权威。应先明确父/子模型的实际绑定点，并保证冻结模型不会静默 fallback。

CyberneticOrchestrator 不是 log-only：[agent_loop.py:3968](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/agent_loop.py:3968>) 执行预测压缩，[:6420](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/agent_loop.py:6420>) 消费控制信号，后者还会改变步数、预算、并发和 timeout。降成遥测属于行为变更，应在 Phase 1 冻结后单独提交、验证。死代码删除可并行准备；禁止新增控制器和避免大拆分值得保留。

## 验收口径必须修正

- 现有工程六格是3题×2组，每组只有3次。不能要求协作组4/6或其编辑试次5/6。沿用既有六格应是总成功≥4/6、每组≥2/3、每题≥1/2，并满足原审计。若同时坚持协作严格高于 single，实际上只允许3/3对2/3；两组都3/3会被拒绝。建议工程门检验可交付性，优越性留正式对照；若确实要协作组≥4/6，则需预先改成每组6次、共12次，并同步更新 runner/auditor。
- “非Executor请求/总请求≤40%”可保留为角色开销代理指标，不能等同净协议税。Executor 多做无效阅读也能让它下降；它也把有用审查计入开销，并漏掉 Executor 内部 schema/交接成本。伴随报告各角色绝对调用、token、耗时、格式纠错开销与完整成功率，不加新的运行时门禁。
- 历史约69%基线是Plan＋Analyst占比，与非Executor占比不是同一口径。应重新冻结同定义的基线，不做跨口径下降结论。
- 判定依赖真实完成的编辑、验收、回写、父最终交付，不能只看模型发出编辑请求。隐藏验收和新增测试纪律应保留；缺失用量维持未知。
- 保持模型、任务信息、总预算、授权范围和裁判一致；不能一边把时限改900秒，一边沿用600秒旧结果比较。

## 止损结论

支持门不过就冻结默认写路径协作，这是一项投入决策。但准确结论是“当前模型、任务集、预算和协作策略组合未证明收益”。候选质量、上下文重复、宿主流程、评测覆盖与模型能力仍可能共同影响结果。评测本来就在测模型与宿主的组合，不能通过一次失败排除其他因果解释，参见 [Anthropic 的 Agent 评测方法](https://www.anthropic.com/research/demystifying-evals-for-ai-agents)。

只读子代理＋由父执行者通过既有事务完成写入，可以作为退路；现有脚本续接节省不应扩大成已证实的真实模型平均收益。多 Agent 更适合可以分离的并行调查，这也与 [Anthropic 多 Agent 工程记录](https://www.anthropic.com/research/multi-agent-research-system) 的适用边界一致，但不能代替本项目对照。

建议顺序：Phase 0 → Phase 1（包含前置资源准入）→ 工程门 → Phase 2 → 正式对照。Phase 4 死代码可并行准备；模型绑定和控制器行为变更另行冻结验证。阶段后移不等于加角色或加门禁。

## 本轮验证范围

运行了两个指定测试，在当前工作区复现失败；不是全量测试或干净 HEAD 运行。历史统计来自 effectiveness 文档，部分链接的归档产物本地缺失，未声称重新审计历史原始记录。未执行真实模型试次，未修改原提案或生产代码。
