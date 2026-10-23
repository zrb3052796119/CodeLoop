# 宿主完成审查链经济学修复方案

日期：2026-10-23
状态：R1、R2 已实现并完成针对性本地验证；完整回归门尚有 4 项修前失败；R3 暂缓
前置依据：
- `artifacts/multi-agent-trigger-2026-10-23/native-review-diagnostic.md`（stock/config 两格逐请求诊断）
- `artifacts/multi-agent-trigger-2026-10-23/evaluation-report.md`（Phase 2 八格终局）
- `docs/2026-10-22--multi-agent-practicality-repair-plan.md`（v1.1，含反过度防御约束）

## 0. 问题定义

Phase 2 八格中，stock 格 **92 秒**即产出通过公开检查和新增回归的候选；review 腿的 acceptance probe 到约 **230 秒**才通过。宿主原生完成审查链（三个阶段，全部 `max_steps=None`）消耗其后的 508 秒、15/19 请求、265k/319k 已知 token，在证据核对最后一个请求上被 600 秒硬截止杀掉——**审查无终态、无批准、零代码修补，交付未形成**。诊断确认的四项缺陷：

1. `review_test` 的 runtime 强制 `sourceEvidence/correctsProbeId/correctionReason` 三者全有或全无（冻结宿主 `implementation_completion_review.py:1078`），工具 schema 与说明均未暴露 → 请求 8 格式重试。
2. inspection 的 `ready_for_evidence` 要求 findings 为空（`completion_coverage_inspection.py:71-73`，当前源码已核实），schema 里 findings 是任意字符串数组（`:29`）且 description 未说明 → 请求 15 重试。
3. **原生批准路径无阶段经济边界**：修前 `completion_coverage_audit.py:1002/1039` 的 `max_steps=None, budget_exhausted_policy="raise"` 仅继承共享全局死线；stock 在公开检查及 review probe 通过后继续独立读取、challenge、证据核对，但没有到达独立覆盖裁决。
4. 可观测性缺口：transcript 仅在正常返回时落盘，SIGKILL 丢失终态（本方案不处理，见 §5 范围外）。

**本方案目标与证据边界**：让审查链在预算内收束；只有独立审查批准后才形成 accepted final。本地构造测试验证边界和接受语义，不能证明真实模型在新时限内必能形成裁决。不证明三个审查阶段任何一个可删除（诊断明确：无对照实验，不能证明移除任一步仍有相同正确率）；不证明协作收益；不解决 config 格的父侧延迟问题。

## 1. 范围

**范围内**：完成测试审查（completion test review）、独立覆盖检查（coverage inspection）、证据核对（evidence reconciliation）三阶段的 schema 对齐与预算边界。证据短路已暂缓，缩窄为"独立检查结果复用"设计冻结（见 R3），按数据门激活。

**范围外（不做，防 scope creep）**：
- workflow 内的 Reviewer 角色（已有入口预留窗口，Phase 1.4 已覆盖，不相干）；
- TLS 证书失败修复（独立 infra 问题，是任何真实重跑的前置，但与本链无关）；
- config 格的父侧延迟（167 秒 `stopReason=length` 空响应、网络重试）——本方案对这类格无帮助，如实声明；
- 触发门重测、题集重设计；
- transcript 逐步落盘（诊断缺陷 #4，独立评估）。

## 2. 反过度防御约束（继承 v1.1，全程有效）

1. 本方案是**给无边界机制加边界 + 给全量流程加短路**，方向是减负而非加固。禁止顺手"增强"审查（加探针、加检查项、加回执类型）。
2. schema 对齐**只改描述与错误信息，不改 runtime 校验语义**（诊断原话："候选修复范围是描述/schema 对齐，不是放宽结果验收"）。
3. 收尾预留只做一次、只在完成门入口算一次；审查阶段只能在剩余空间内消费，不新增第二层扣除。
4. 短路条件必须是"证据已在手 + 候选未变"，不得是"时间不够了就跳过"。

## 3. 修复项

### R1 Schema 对齐（先做，风险最低）

| # | 改动 | 位置 |
|---|---|---|
| R1.1 | `review_test` 工具 schema：optional 字段 `sourceEvidence/correctsProbeId/correctionReason` 的 description 写明"三者必须同时提供或同时省略"；runtime 拒绝时的错误信息列出缺失的具体字段名（参照 `:905` 处 `sourceEvidence accepts only path/startLine/endLine` 的既有风格——请求 16 的行号报错就是好例子，请求 8 的报错不是） | `implementation_completion_review.py` runtime 校验器 + 对应工具 schema 定义（grep `review_test` schema 字典） |
| R1.2 | inspection 提交 schema（当前 `:29`）的 `findings` description 写明"当 `status=ready_for_evidence` 时必须为空数组"；schema 层面可直接加条件约束或仅描述（描述即可，runtime `:71-73` 已有明确报错 `ready_requires_no_findings`） | `completion_coverage_inspection.py:29` |

**验收门**：针对两处约束的构造用例——合法提交通过、违约提交被拒且错误信息包含约束说明；既有完成审查回归全绿。

### R2 经济边界

**R2.1 收尾预留先行**。完成门入口（`agent_loop.py:3745` 事件发出处）先计算并冻结父收尾预留：

```
closing_reserve_requests = 2            # 收尾政策预留量，并非已证明的最少请求数
closing_reserve_seconds  = max(60, 剩余时间 × 15%)
```

审查链可用预算 = 剩余预算 − 收尾预留。收尾预留不参与审查消费，审查花超不得侵占。

有限 token 预算同时保留这两次收尾的输入、输出及反馈估计；只在链视图保护一次，不预扣第二份账。估计不保证 provider 实际 token 或成功响应。原最终输出已经存在时，宿主可以直接批准或附上 partial 回执，不必再发出两次请求。

**R2.2 三阶段各自上限**（起点值，用 stock 轨迹校准后冻结；stock 实际消耗：审查 6 请求/184s、检查 7 请求/261s、核对 2 请求/58s）：

| 阶段 | 请求上限 | 墙钟上限 |
|---|---:|---:|
| 完成测试审查 | 8 | 90s |
| 独立覆盖检查 | 8 | 150s |
| 证据核对 | 4 | 60s |
| **链级合计** | — | **≤ 完成门入口剩余时间的 50%，且 ≤ 扣除收尾预留后的全部** |

实现方式：阶段 `max_steps` 使用剩余额，真正准入复用现有 budget view，包含嵌套探针裁决；仅修改 `max_steps` 不足以限制总请求。bounded 原生调用使用既有 `max_retries_override=0`，OpenAI/Anthropic 适配器内部重发也遵守该值；不支持此接口的第三方 adapter 只能保证宿主准入次数。墙钟沿用现有绝对 deadline，工具和嵌套调用共享阶段上下文。一次父 turn 首次进入完成门时冻结链及收尾预留，阶段第一次启动时冻结本阶段截止和额度，重入不刷新。三个阶段是同一链的兄弟阶段；没有有限父截止时链至多 300 秒，仍保留各阶段上限。`full` 不新增这些本地限额。

**R2.3 模式开关**：

```
MINI_CODE_COMPLETION_REVIEW_MODE = bounded | full    # 默认 bounded
```

- `bounded`：适用上述上限；链在任一上限耗尽仍无决定时，发出 `implementation.completion_review_budget_exhausted` 事件，**候选与全部证据照常返回父，`requirementsAccepted` 保持 `false`**。`completionReview` 为三值闭集：`approved | rejected | budget_exhausted`，`requirementsAccepted=true` 当且仅当 `approved`。
- **语义不变量**：`requirementsAccepted` 的含义在两种模式下完全一致（= 独立审查已批准）。模式开关只改变审查能消耗的预算，不改变"什么算接受"。"耗尽也 accepted"被否决：审查被砍正是因为预算耗尽，此时让生产者自我认证恰是最不可信的时刻，且下游（评测门、父模型、用户）将无法区分"审过批准"与"没耐心了"。
- `budget_exhausted` 在审计与统计中单列：不并入 `rejected`（夸大失败），不并入 `approved`（夸大成功）。
- `completionReview` 只在已有对应分类的终态事件/回执出现；非预算原因的 `inconclusive` 保留原原因，省略该可选字段，不能伪造为三类之一。认证的负裁决及续审反馈字节不为该展示字段改变。
- 耗尽不等于作废：pending coverage audit 状态按 `candidateDigest` 绑定（`implementation_completion_review.py:232-234`），后续 turn 可从断点续审，不从头重审。
- `full`：现状行为（`budget_exhausted_policy="raise"`，无 accepted final 则交付失败）。评测需要严格审查语义时用此档。

**验收门**：构造用例覆盖——上限内正常收束；审查耗尽 → bounded 返回候选且 `requirementsAccepted=false`、`completionReview=budget_exhausted` / full 维持拒绝；收尾预留不被审查侵占（审查阶段拿到的准入视图里看不到预留份额）。stock 入口约剩 508 秒，链上限因此约为 254 秒。离线时间线只能验证预算可容纳性；修前 review 实耗约 184 秒，不能据此证明新 90 秒上限下会批准。完整验收仍要求既有完成审查回归全绿；修前失败须单列并留存证据，不能算作本轮新增回归，也不能据此宣称已过全绿门。

### R3 独立检查结果复用（暂缓；设计冻结如下，按数据门激活）

**暂缓理由**：R2 单独即可限制链的消耗，但是否能在限额内形成独立裁决还需真实验证；R3 的两个候选触发场景——run 内超预算、跨 turn 续审——均无对应优化收益的复现数据。按 v1.1 约束"提案默认拒绝，除非对应已复现交付失败"，待 R1+R2 真实验证数据后再决定是否实现。

**初稿缺陷记录（防复活时重蹈）**：初稿以"审查 approved + 独立 acceptance probe 通过"作为跳过独立覆盖检查的条件——不成立。review 腿内的 probe 由审查链上下文产出，仍属生产侧证据；以它替代独立检查腿违反"测试作者不能自行批准其测试的语义充分性"（`:1746`）的独立性不变量。该方案作废。

**冻结后的缩窄设计（激活时按此实现）**：仅复用**已完成且有效**的独立覆盖检查上下文——候选 digest 与证据绑定未变（`implementation_completion_review.py:232-234` 既有机制）时，续审/重入不重做 inspection，直接进入轻量核对（≤4 请求、≤60s，核对既有证据集）。任何生产侧产物（review approved、review 腿 probe）**不构成**跳过独立检查的理由。绑定失效即全量重做。

**激活条件（满足其一）**：① R1+R2 两格真实验证中链在预算内仍未到达终态决定，且耗时分布显示 inspection 是瓶颈；② 跨 turn 续审场景真实出现且重复 inspection 的成本可测。

## 4. 预期与校准

- stock 形态（工作快、审查慢）格：预算内收束，交付形成。这是本方案的主要收益对象。
- config 形态（父侧延迟：length 截断空响应、网络重试）：**无改善**，如实声明。
- 阶段上限的起点值来自单格轨迹，样本量 1——首次真实验证后按实际分布校准一次，然后冻结写回本文档，不再逐轮调参。

**2026-10-23 首次 bounded 实测与冻结**（预注册 `0b6dc796`，完整审计 2/2 有效）：

| 格 | 父工作至 review 入口（约） | review 活跃墙钟 / 请求 | inspection | 核对 | 全格时间 / 最终决定 |
| --- | ---: | ---: | --- | --- | --- |
| stock | 78.77s | 90.026s / 4 | 未进入 | 未进入 | 168.808s / budget_exhausted |
| config | 396.455s | 90.032s / 3 | 未进入 | 未进入 | 486.501s / budget_exhausted |

阶段墙钟按 observer 的首次 phase_selected 到终态，包含工具和本地工作；约 26/32ms 的截止后收尾不改变失败性质。两格均未在限额内形成 approved/rejected，虽候选 oracle 与新增回归通过，`requirementsAccepted` 仍为 false。父时间预留实际为 stock 78.207s、config 60s；都正常返回 incomplete，未触达 600s 硬截止。

**参数冻结不变**：review 8/90s、inspection 8/150s、核对 4/60s，链级 ≤剩余 50%，先保留 2 请求及 max(60s,剩余15%)。本次 review 在上限处尚未完成，另两阶段无新样本，不能估算完整阶段分布；不事后延长参数来救本门，也不追加试次。预算安全退出已验证，预算内形成独立决定的目标未达到。R1 的旧 schema 拒绝分支未在本轮真实执行到，不能用“无 schema 错误”宣称其收益已实测。

**R3 结论：两个激活条件均未满足，继续暂缓。** 两格均在首次 review 耗尽，尚未进入 inspection；既有 full 轨迹未见同绑定的已完成 inspection 被重复执行。详细证据见 `artifacts/native-review-acceptance-2026-10-23/evaluation-report.md` 和 `full-trace-comparison.md`。

## 5. 真实验证门（预注册后执行）

1. 前置：TLS 证书问题单独修复（infra，不在本方案）。
2. 两格 bounded 真实验证（stock 形态 ×1、config 形态 ×1），预注册题目、预算、门槛后执行：
   - 门：stock 形态格的审查链在预算内到达终态决定（`approved` 或 `rejected`），`approved` 时形成 accepted final；`budget_exhausted` 视为该格**未过门**（保底路径是安全网，不是目标）。config 形态格只记录不设门。
3. 两格过门后，才考虑 gen-2 六格在 bounded 模式下的重跑（另预注册）；不过门则回 `full` 模式分析轨迹，不追加试次。
4. 全部失败原样保留；不换模型、不补跑、不挑轮次。

## 6. 不做清单

- 不删除任何审查阶段（无对照证据，诊断明令禁止此归因）；
- 不以任何生产侧证据（review approved、review 腿 probe）替代独立覆盖检查——独立性不变量永久有效；
- 不放宽任何 runtime 验收校验；
- 不给快速路径新增探针/检查项；
- 不做 transcript 逐步落盘（独立评估）；
- 不动触发门与题集（独立决策）；
- `full` 模式永久保留，作为需要严格审查语义场景（正式对照）的回退。

## 7. 执行序列（R1/R2 落地后 → 两格真实验证）

前置事实（已逐行核对）：R1/R2 已实现，97 项新增回归绿；原组合日志确有 **5 行 FAILED**，其中 1 项严格续审用例未显式选择 `full`，随后两个参数都已通过，因此当时报告写的是 **4 项剩余修前失败**。这四项分别是空 `repairProbes`、迁移仍为 `replacement_failed`、已返回正确 probe 但 pending 未清、返回普通 gap 而未完成 legacy 分类；不能概括为五项同一根因。这些文件未进入 10-22 的 1579 全绿门，四项在修前宿主也已复现。完整证据与后续扩大域分诊见 `artifacts/native-review-acceptance-2026-10-23/`。

### 7.1 裁决域分诊（阻塞项，最先做，时间盒半天）

**判定问题**：`repairProbes`（executor 反馈通道）与 10-22 已修复的"候选 receipt 混入动态 Reviewer probe"是否同一通道？对比两者的产出代码路径后三选一：

| 结局 | 动作 | 验收 |
|---|---|---|
| A 修代码 | 若为独立反馈通道且契约正确：恢复"确认 gap → probe 回传"，这批测试即规格 | 5 测试转绿，无验收语义放宽 |
| B 重述契约 | 若 probe 移除是同一通道的有意修复：改测试锁定现行为，docstring 记录与 10-22 修复的因果依据 | 改后测试全绿且锁定 |
| C 正式降级 | 若通道确属废弃设计：移除测试并在决策记录说明 | 记录在案 |

禁止：删除/跳过测试不留决策记录；悬置不决。分诊完成前不开两格验证——若验证格走到 gap 裁决，空反馈会污染解读。

**分诊终态：A，修代码。** `repairProbes` 是 `ImplementationCompletionReview` 向原实现会话交付完整、独立归因反例的反馈；10-22 排除动态 `review_regression_*` 的路径是 `ExecutionCandidate.receiptRefs` 的正式候选声明，二者不同，且该修复明确保留 probe ledger、回放与失败证据。分诊时（初始 HEAD `89288e87`）10-22 改动仍是工作区补丁，不能伪称当时已有独立源码提交；本轮随后才纳入验证宿主冻结。此次修正显式 host migration 与旧负裁决捷径、独立归因、分类落盘的顺序；有 replacement supersession 时仍须独立 audit 接受才可退休原测试。重复调用、恢复和预算中断均选取当前精确绑定的反例，旧认证负裁决记录字节不重写。原测试保留，并补真实父恢复与提交后耗尽反例。详见 `adjudication-channel-review.md`。

### 7.2 TLS 修复（独立 infra，可并行）

定位 `[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate` 根因（端点证书链 / 系统信任库 / 代理注入）。修复必须保持证书校验开启（诊断确认本轮未关校验，修复不得开此口子）。验收：干净进程内对该端点请求校验通过。

**当前验收事实**：同原评测 Python 3.12、精确 API 路径、干净进程的三次无认证 HEAD 均在 `CERT_REQUIRED` 与主机名校验开启时返回 HTTP 401。当前系统代理返回有效公有链；历史失败没有留存 peer 链，根因未证实，不能宣称已永久修复。新评测显式使用既有系统信任库 `SSL_CERT_FILE=/etc/ssl/cert.pem`，冻结其 SHA-256；不装根、不修改生产 TLS、不关闭校验。运行前再做相同预检；真实格若再次 TLS 失败，原样保留且不补跑。证据见 `tls-diagnosis.md`。

### 7.3 R1/R2 验收门闭合

- 完成审查域回归全绿（含 7.1 分诊后的终态）；97 项新增回归维持绿；修前失败清零或全部有分诊记录。
- 冻结当前提交为验证宿主，记录 SHA。

**闭合记录（2026-10-23）**：精确验证宿主的 81 个测试文件共 **713 passed in 300.37s**，包含 R1/R2 原 97 项新增和裁决反馈修复新增 3 项；验证 harness 另有新 27 项加原 80 项共 107 项通过，不混入 713 的生产回归计数。历史失败、严格续审 `full` 选择和两个预算配置 fixture 的显式虚拟模型配置均逐项记录于 `regression-closure.md`，无删除或跳过测试。验证源码提交为 **`c9002748014a507b1bb86d0202cfffa2e77d43e0`**（`codex/native-review-validation-host-20261023`）：保留原八格宿主基线，仅覆盖 R1/R2 与裁决修复的 11 个生产文件及两个评测脚本，未混入工作区其他 Memory 改动。正式冻结使用原 Python 3.12.8，28 个依赖及模型路由指纹与原八格完全一致；公共测试预检通过。首次外层沙盒阻止 verifier 子沙盒的离线预检失败已留档，未发生模型调用。

### 7.4 两格 bounded 真实验证（预注册后执行）

预注册：题目（stock 形态 ×1、config 形态 ×1）、模型不变（GLM-5.3-Flash）、预算不变（40 请求 / 500k token / 600s）、模式 `bounded`、审计标准沿用。
门：stock 格审查链在预算内到达终态决定（`approved`/`rejected`）；`budget_exhausted` = 未过门。config 格只记录不设门。
完成后必做：实测各阶段耗时写回 §4 并冻结上限起点值；对照 R3 两个激活条件出具一行结论。

**已执行并终审**：预注册 `0b6dc79612fdb276359a121440750a4834fdb684` 后 stock→config 各一次；原模型、预算、题面、oracle 和审计边界未变。最终独立审计 **2/2 有效、0 错误、0 警告**，但 stock=`budget_exhausted`，行为门失败。两格共 15 次物理请求，已知 token 下界 163,146，2 次截止中断用量 unknown；无 TLS 错误。候选均通过产物验收，accepted final 均为 false。未重跑，详情见 `evaluation-report.md`。

### 7.5 分叉决策

- **两格过门** → 预注册 gen-2 六格 bounded 重跑（沿用旧门：总 ≥4/6、每组 ≥2/3、每题 ≥1/2）。
- **不过门** → 回 `full` 模式分析轨迹：若 inspection 是瓶颈且数据支持 → 按 R3 冻结设计激活缩窄实现；否则止损，维持显式协作形态（E→V 工程门已过，作为产品能力保留）。

原则：一次一个变量；全部失败原样保留；不补跑、不换模型、不挑轮次。

**本轮最终分叉：停止 gen-2 六格 bounded 扩跑，R3 不激活，保留此前已过工程门的显式 E→V 能力。** 已回看存活的 full 轨迹，历史 stock 的首次 inspection 较慢，但其完成后已经直接进入核对；现有代码也已有有效 inspection 的续审复用。新两格未到 inspection，不支持以复用解决当前失败。这个停止决定不构成“模型是唯一瓶颈”的因果结论，也不证明协作优于单 Agent。
