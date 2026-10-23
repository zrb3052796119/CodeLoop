# 多 Agent 协作模块实用性修复方案

日期：2026-10-22（v1.1，吸收同日审阅修正）
状态：Phase 0/1实现与本地验证完成（1579项），gen-2六格5/6通过继续门；Phase2本地251项通过，但新八题真实触发0/8，未过门，停止扩大对照。产物验收6/8不等于完整交付，13次用量未知；两负例零误触发证据完整。Phase3独立路由清理18项通过、Windows限制已说明。尚无协作优越性结论。
范围：`minicode/tools/task.py`、`minicode/central_scheduler.py`、`minicode/workflow_runner.py`、`minicode/subagent_*`、`minicode/prompt.py`、`minicode/task_deadline.py` 及相关测试
审阅依据：`artifacts/multi-agent-route-review-2026-10-22/review.md`

## 0. 背景与问题定义

当前证据（`docs/multi-agent-effectiveness.md`，2026-10-14 至 2026-10-17）：

- 48 次正式对照：单 Agent / 自动调度 / 显式协作三组成功率 **0/16**；显式协作平均耗时为单 Agent 的 **3.11 倍**（p=0.047）。
- 自动调度 **16/16 + 24/24 次零委派**。
- **Plan＋Analyst 合计**占 279/406 次模型请求，Executor 仅 46/406。注意这是两类角色的合计，不等于"非 Executor 占比"基线，后续开销统计须分开报告 Plan 与 Analyst。
- 历史失败包含传输与准入问题（3800 字符 prompt、闭合 schema、预算预留），也包含候选质量和最终交接问题，不能将失败全部归为协议。20KB 控制消息截断回写的缺陷**已修复**，不再列为待修项。

**问题不是机制缺失，而是协议经济学：协作协议的成本结构性地超过它所委派的工作，且系统内无任何机制度量这一点。**

### 本方案证明什么（重要，防止过度解读）

当前 Verifier 是宿主确定性验证（`workflow_runner.py:4837`），因此 **E→V 是两个阶段，不是两个 LLM 角色**。最小闭环若成功，证明的是：

> "委派执行 ＋ 隔离确定性验证 ＋ 事务回写"这条产品链路有交付价值。

它**不**证明多模型互补收益或"多 Agent 智能"。该解释边界写入所有后续报告。

### 反过度防御约束（全程有效）

本项目的历史失败模式是"为可审计优化到不可交付"。因此以下约束优先级高于本方案其他条款：

1. **不加新 schema 字段、不加新回执类型、不加新角色、不加新门禁。** 缺上下文就复用 `inputRefs`/handoff（`multi_agent_contracts.py:717`），缺通道就复用快照工作区文件。
2. **准入检查只做 fail-fast，不做预留层。** 进入第一个付费阶段前查一次资源，不足即拒；通过后不再层层扣除。
3. **文件通道的完整性 = 一次 sha256 + 原子写。** 不为它发明新的回执词表或校验链。
4. **Phase 1 期间冻结一切无关行为变更**（含 orchestrator、smart_router、压缩策略），保证归因干净。清理类改动只能并行准备、单独窗口落地。
5. 任何"加固"提案默认拒绝，除非它直接对应一个已复现的交付失败。

目标（按优先序）：
1. 显式协作在 3 题工程集上达到继续门槛（见 Phase 1 验收门）。
2. Executor 获得真实编辑机会的试次占比显著上升（现在 Executor 是链路中最稀缺的产物）。
3. 自动触发从"从不发生"变为"在跨文件调查任务上可复现发生"。

非目标：
- 不追求四阶段全链路先出成绩；不新增门禁、控制器、schema 版本。
- 不动 memory/skill 等其他模块。
- 不在本轮声称多 Agent 优越性——优越性留待正式对照。

---

## Phase 0：守护修复（先稳住地基，~1 天）

HEAD 上已有 2 个失败测试（审阅复现确认）。这些不修，后续所有对照结果都不可信。

| # | 改动 | 位置 | 说明 |
|---|---|---|---|
| 0.1 | 修复 mailbox 并行测试失败 | `tests/test_subagent_mailbox.py::test_workflow_parallel_explore_writes_mailbox_and_feeds_plan` | 断言还停在"各 phase 共享同一 budget attribution view"，与按子代理归因的重构脱节。按新归因语义重写断言，不改生产代码迁就旧断言。 |
| 0.2 | 修复 scheduler 路由测试失败 | `tests/test_central_multi_agent_scheduler.py::test_clear_low_risk_change_skips_unnecessary_analysis_and_review` | `route.scoped_change` 自 policy v4 起恒含 REVIEWER，原测试预期落后于实现。历史版本断言保留，新默认行为由 policy v6 测试锁定。 |
| 0.3 | 死线默认值接通解析 | `minicode/task_deadline.py:47` | **只改常量不生效**：`task_timeout_seconds` 在未配置时直接返回 `None`，从不引用 `DEFAULT_TASK_TIMEOUT_SECONDS`。修法：未配置（env 与 runtime 均无值）时回退到常量，常量设为 900s；`MINICODE_TASK_TIMEOUT_SECONDS` 写入 `.env.example` 与 README。改后用三组用例验证：默认回退、显式 0/None 仍表示无限、祖先 deadline 取 min 语义不变。注释"ancestor deadlines always remain authoritative"必须保持成立。 |
| 0.4 | 消灭字符串计数契约 | `minicode/workflow_runner.py:1469-1477` | 用正则从 ToolResult 人类可读 header 解析 "Model turns: N (tool calls: M)"。改为 ToolResult 携带结构化 usage 字段，header 渲染从同一数据源生成。 |

**验收门**：`pytest tests/test_subagent_mailbox.py tests/test_central_multi_agent_scheduler.py tests/test_multi_agent_v2_recovery.py -q` 全绿；`.env.example` 含新变量；0.3 三组用例通过。

---

## Phase 1：砍协议税（把交付率从 0 抬起来，最高杠杆）

### 1.0 入口资源准入（自 Phase 3 并入，fail-fast）

- 位置：`_build_worker_assignment`（`workflow_runner.py:702-799`）之后、**第一个付费阶段启动之前**。
- 改法：检查三类资源的入口余量——模型请求（起点 6 次，待验证，不是定论）、token（含父交接地板与收尾预留）、时间（含收尾窗口）。任一不足 → workflow **在入口即拒**（`pre_flight_insufficient`），不再像现在那样烧完上游才在 `executor_budget_insufficient`（`workflow_runner.py:3755`）处 blocked。
- 约束：这是 fail-fast 检查，不是新的预留层；通过后按现有预算视图执行，不加第二层扣除。

### 1.1 默认路由改为真两阶段 `verified_change`

- 位置：`minicode/central_scheduler.py:395-435`。
- 事实修正：`scoped_change` 在 policy v4+ 恒含 REVIEWER（:421-431）；**真 E→V 是 `verified_change`**（:408-415，当前要求 v5 且 `not independent_review_required and not complex`）。
- 改法：当 workflow_request 完整（有 write_paths + 可执行 verification_commands）且无敏感路径命中时，默认路由 = `verified_change`。保留 ANALYST 的显式触发：
  - 风险画像 HIGH/CRITICAL（人工门不动）；
  - request 显式声明需要分析；
  - 同一 workflow 内 Executor 首次失败后的重试前（此时才付分析成本，失败证据真实存在）。
- 需要 `independent_review_required`/`complex` 的判定与"request 完整"联动，避免完整请求被保守画像（`tests/test_multi_agent_v2_workflow_request.py:229-249`）重新推回四阶段。
- policy 版本 v5→v6 冻结，重放语义沿用现有版本化机制。

### 1.2 Analyst unknowns 不终结 workflow

- 位置：`minicode/workflow_runner.py:2865-2870` → `central_scheduler.py:1004-1008`。
- 现状：Analyst 报告含任何 unknowns → `inconclusive` → 整个 workflow 终结，候选不交接，父只能重开 workflow 再付一轮。
- 改法：unknowns **通过 Assignment 已有的 `inputRefs`/handoff 字段传递给 Executor**（`multi_agent_contracts.py:717`，不加新字段），只有 scope 无法确定、没有可执行候选时才 blocked。候选始终保留交接。

### 1.3 正文文件通道（传输层去瓶颈，最小实现）

原则：**transport 只做完整性（一次 digest、原子写），不做语义截断**。不做新回执词表。

| 通道 | 现状 | 改法 |
|---|---|---|
| workflow prompt ≤3800 字符 | `tools/task.py:294`，超限直接拒，20/36 历史请求中招 | 任务书正文写入快照工作区文件（如 `_task_brief.md`），tool call 传持久路径 + ≤800 字符摘要；3800 上限仅约束 inline 用法。 |
| 大段正文（分析报告、诊断、review 说明） | 20KB 通道超限即截断/拒收 | 同上走文件通道：落盘 + sha256，父按需读取。**信封上限保持现状不放宽**——20KB diff 截断缺陷已修复，盲目扩容无证据收益。 |
| 父收到的 workflow 输出 8000 字符 | `workflow_finalization.py:46` | 保持 8000，内容改为"结论 + 关键风险 + 证据文件路径"。 |

### 1.4 Reviewer 不再饿死

- 位置：`minicode/workflow_runner.py:689-701`，首审 deadline 只给剩余窗口 75%，V31 到截止无判定。
- 改法：Reviewer 窗口在**入口预留**（剩余时间的 50% 且不少于 120s）；重试可顺延但**不得突破全局截止**。Reviewer prompt 内联实际 `review_submit` 工具的最小合法示例；Assignment 标识、patch digest 等信封字段由宿主绑定，不要求模型把完整 ReviewVerdict.v1 当作工具参数提交。契约不变。

**验收门（工程六格 = 3 题 × 2 组，共 6 次；每组 3 次，继续/停止判定，不判优越性）**：
- 组别：单 Agent vs 两阶段（Executor→Verifier，Verifier 为确定性代码）。
- 门槛沿用旧门：**总成功 ≥4/6、每组 ≥2/3、每题 ≥1/2**，全部按既有审计标准执行，失败原样保留。
- 同时报告（作为开销指标，非门槛）：Executor 实际收到编辑请求的试次数、Plan/Analyst/Executor 分开的绝对请求数、token、耗时、非 Executor 请求占比。占比只作观察，不独自代表协议税（Executor 无效阅读越多占比可能越低）。
- 两组即使同分（如 3/3 vs 3/3），只要过门即**继续**；优越性判定留给正式对照，不在六格上要求"严格高于"。
- 若不过门 → 止损（见下）。

---

## Phase 2：修触发（让功能存在）

Phase 1 过门后才做。

### 2.0 先回答"为什么不采纳"

现有进度反馈源码**已包含主动建议委派的措辞**；原计划先从既有journal/transcript核对实际呈现与后续行为。2026-10-23取证确认旧auto轨迹已删除，不能从历史零委派反推出模型看到并拒绝了建议。本地只能确认无task角色收到不适用建议等呈现问题；新八题的动态建议也未实际呈现，因此没有建立措辞、时机或激励的因果结论。

### 2.1 工具描述改为"任务形状匹配"

- 位置：`minicode/prompt.py:254-273`。
- 把 "Do NOT..." 劝退清单压缩为一行，替换为正向形状表，主信号是任务语义形状：**可独立调查的问题、明确的交接产物、剩余预算是否支持委派**。文件数、读取次数只作辅助信号，不作硬条件。每条配正/反微型示例。

### 2.2 宿主侧进度触发

- 同turn内成功读取批次≥8且无编辑仅作为辅助信号；具备当前task能力且预算/时间允许时，每turn至多给一次可选建议，不强制。此实现修正可证的能力与呈现边界，不声称已经查明历史模型不采纳的原因。新八题未达到该呈现条件，效果未被验证。

**验收门**：合成 8 题 auto 组，委派触发率 ≥25%；非触发任务（单文件缺陷）误触发 0。历史口径分别是合成八题 auto 0/24、真实八题 auto 0/16，不能混用。2026-10-23取证确认旧合成题包和auto原始轨迹也已删除；随后已按e85a9580独立冻结并执行trigger-gen2新八题，与历史不可比。实际预注册门固定全部8格为分母、至少2格真实子代理执行并送达；负例任何启动请求（含准入拒绝）均算误触发，须零启动且分类完整；还须8格终态、独立审计有效及用量/范围/清理完整。真实结果0/8触发且13次用量未知，门失败；两负例零启动完整成立。不能称旧题重跑。

---

## Phase 3：清欠账（原 Phase 4 重排；预算项已并入 Phase 1）

原预算经济学两要点去向：入口准入并入 1.0；父交接地板（`agent_loop.py:796-850`，现为"上下文估算＋回执估算＋2048"的准入地板）**维持现状不动**——它已有明确语义（"admission floor, not a second reservation"），重设为小额+比例可能重新造成收尾失败。仅当 Phase 1 六格中出现收尾失败证据时再议。

清欠账按"删除可并行准备、行为变更独立冻结"执行：

| # | 对象 | 处置 |
|---|---|---|
| 3.1 | `minicode/agent_protocol.py`（422 行，零调用方） | 删除。可立即执行（零行为面）。 |
| 3.2 | `minicode/context_isolation.py`（零调用方） | 删除。真实隔离是 workspace 事务。可立即执行。 |
| 3.3 | `agent_router.py` | 默认 tier 硬编码过时模型名（`claude-haiku-3-20240307`）且缺模型时静默 fallback；先把 fallback 改 fail-closed，再评估去留。 |
| 3.4 | `SmartRouter` | **保留**。有真实生产调用（`agent_loop.py:2564,2593-2605` `route_and_switch`）。 |
| 3.5 | `CyberneticOrchestrator` | **Phase 1 期间一行不动**。它不是纯遥测：其 smart_router 参与模型切换，输出影响压缩、预算、并发、超时路径。任何清理放在 Phase 1 对照结束后的独立窗口，单独评审。禁止新增控制器。 |
| 3.6 | Windows 进程树清理 no-op（`background_tasks.py:384-386`） | 文档标注 Windows 为二等公民，或接入 `taskkill /T`。低优先。 |

**红线**：3.3–3.5 的一律不得在 Phase 1 执行窗口内落地，否则污染归因。

---

## 不做清单（同等重要）

- 不新增任何角色、门禁、schema 字段、回执类型、schema 版本——协议已过剩、交付为零。
- 不做跨 workflow 的持久化 checkpoint/重放——父 turn 崩溃丢 in-flight 工作是已知取舍，等两阶段路线证明价值后再评估。
- 不用 GLM-5.3-Flash 之外的模型重跑对照来"救数据"——模型与协议变量一次只动一个。
- 不挑较好轮次替代失败记录（沿用 `multi-agent-effectiveness.md` 纪律）。
- 不把六格结果解释为多模型互补收益（Verifier 是确定性代码）。

## 里程碑与总验收

| 阶段 | 门 | 判定 |
|---|---|---|
| Phase 0 | 指定测试全绿 + 死线默认生效（三组用例） | 必过，不设回退 |
| Phase 1 | 六格旧门：总 ≥4/6、每组 ≥2/3、每题 ≥1/2 | 过 → 进 Phase 2；正式对照另行预注册；不过 → 止损（见下） |
| Phase 2 | auto 触发率 ≥25% 且零误触发 | 过 → 可准备另行预注册的正式对照；不过 → 停止扩展 |
| Phase 3 | 独立窗口落地，单独回归 | — |

**止损措辞（预注册，防止事后归因漂移）**：若 Phase 1 不过门，结论只能是：

> 当前模型、任务集、预算和协作策略组合，尚未证明收益。

不能推出"瓶颈在模型、不在协议"——历史记录中零 Plan/Analyst 的直接 Executor 路线也失败过，且存在候选质量、上下文、父最终交接等多个混杂因素。Agent 评测测量的是模型与宿主的组合，失败不能单独识别模型原因。止损后保留的产品能力：只读子代理＋快照回写事务；续接机制的局部证据不等于整体协作收益已成立。

## 实施与评测状态（2026-10-23）

- 实施日志、初始工作区快照和本地回归结果保存在 `artifacts/multi-agent-practicality-2026-10-22/`。原有 memory 等工作区修改不属于本次修复。
- 最终联合回归 `1579 passed in 275.36s`，`git diff --check` 与修改生产模块的编译检查通过。完整范围、基线与验收限制见该目录 `implementation-report.md`；测试使用脚本化模型，不代表真实模型收益。
- 文件任务书沿用 `prompt` 字段：首行 `@file:docs/task-brief.md`，后接不超过 800 字符摘要。宿主校验原始读权限，将完整 UTF-8 字节原子保存到 `.workflow-briefs/<sha256>.md`，在快照中读取；未增加工具或角色 schema 字段。
- 两个无引用根包模块单独核对并删除；`py-src` 副本保留。模型路由、控制器和压缩策略的清理仍按独立窗口处理。
- 六格的原冻结输入 `artifacts/three-arm-real-2026-10-13/tasks.json` 和 `manifest.json` 已在 10 月 19 日有记录的清理中删除，当前 Git 历史与剩余归档未找到副本。2026-10-23 用户明确授权改为 **gen-2 新题集**：从 10 月 13 日之后的真实修复提交选 3 个不同于历史题的缺陷；冻结公开题目、真实修复前基线、隐藏 oracle、参考修复、模型、预算及审计纪律，并在任何模型评测之前提交。工程门不变，与全部历史数字不可比。准备记录见 `artifacts/multi-agent-gen2-2026-10-23/`；本地脚本测试仍不代表模型效果验收。
- gen-2 预注册已提交为 `f131d7f3f9cc47dc00d7e64a977c466ec4804237`，随后固定六格各执行一次。总5/6、single3/3、explicit2/3；独立终审6/6有效、0错误、1个由HTTP前预算拒绝解释的计数警告。全部100次物理请求用量完整，没有补抽或替换失败。协作组Plan/Analyst均0、Executor29/主Agent9次请求，非Executor占比23.7%，三个Executor均编辑并完成workflow送达。唯一失败为摘要预算题的隐藏行为验收；新增测试通过不覆盖此失败。
- 六格结束时继续门通过，Phase 2进入取证；当时auto组与新正式48次均未运行。此结果只支持继续开发，不证明优于单Agent或历史收益。完整结果及证据边界见 `artifacts/multi-agent-gen2-2026-10-23/report.md`。
- Phase 2随后完成任务形状指引、文件正文说明和当前task能力感知的单次可选建议；最终16文件251项本地测试通过，独立复核无阻断。新建议用于默认连续父Agent；显式旧bounded配额模式保留原提醒和系统指引，原阶段计价代码未改。旧auto轨迹已删除，不能声称查明模型不采纳的历史原因；用户随后授权“冻结新八题，继续 Phase 2”及新八题的数据发送；预注册e85a9580后固定执行八格，真实触发0/8、两负例零误触发完整、6/8候选产物通过，但13请求用量未知，Phase2未过门。证据终审8/8有效，不等于通过效果门。5格硬截止、1格partial、末2格TLS证书失败；没有重跑，正式48次停止。最终结果见`artifacts/multi-agent-trigger-2026-10-23/evaluation-report.md`。见 `artifacts/multi-agent-trigger-2026-10-23/report.md` 与 `docs/2026-10-23--multi-agent-trigger-preregistration.md`。

- Phase3在八格宿主冻结后独立完成AgentRouter缺候选fail-closed：None/空目录区分、仅声明候选fallback、未登记显式模型拒绝；18项回归通过，同字节真实旧源码5项目标失败、修后通过。README说明Windows直接子进程/裸PID限制。SmartRouter保留，CyberneticOrchestrator独立审阅后保留行为，未新增控制器。其中AgentRouter修复和Windows文档在冻结后完成，未进入本轮受测宿主；此前两个死模块删除已包含在冻结宿主中。见`artifacts/multi-agent-practicality-2026-10-22/phase3/router-summary.md`。

**优越性判定**：留待 48 次正式对照，任务级配对 + Holm 校正；六格只回答"是否值得继续"。

## 风险

- 1.1 改默认路由会让 scheduler 测试大面积预期失效——Phase 0 先修测试纪律正是为此；policy v6 冻结 + 重放版本化。
- 1.0 入口准入若校准过严会拒绝本来可行的运行——所以三类资源阈值都标注"待验证起点"，六格数据回来后校准，不预设为定论。
- 1.3 文件通道若长出新校验体系就违背了本方案初衷——约束：一次 digest + 原子写，不加回执词表。
- Phase 1 若仍不过门，混杂因素（候选质量、上下文、父交接）仍在——止损措辞已预注册，防止把组合失败归因到单一变量。

- 后续原生审查 R1/R2 验证：裁决域按 A 修复，精确宿主 713 项通过，TLS 保持校验；源码冻结 `c9002748`，两格预注册 `0b6dc796`。stock/config 各一次均在 review 90 秒耗尽，未进入 inspection；产物通过但 accepted final 均为 false。终审 2/2 有效不等于行为门通过。按门停止 gen-2 六格 bounded 扩跑，R3 不激活，保留显式 E→V 工程能力；不作模型单因归责。实测参数与结论见 `docs/2026-10-23--native-review-economics-repair.md` §4、§7 及 `artifacts/native-review-acceptance-2026-10-23/evaluation-report.md`。
