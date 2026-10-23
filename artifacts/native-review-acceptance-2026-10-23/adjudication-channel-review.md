# 裁决反馈通道分诊：选择 A，保留并修复独立反馈契约

审阅日期：2026-10-23。只读核对真实 Git 历史、当前混合工作区与既有本地证据；本次未运行模型、pytest、修改生产源码或测试、提交 Git。

## 裁决

**选择 A。`repairProbes` 是仍在使用的独立反馈通道，不能依据 10-22 的 candidate receipt 修复删除或降级。** 10-22 修复处理 `ExecutionCandidate.v1.implementationReceiptRefs` 的检查 ID 归属；`repairProbes` 处理原实现会话收到的、经独立归因的可执行反例。二者不是同一序列化入口，也没有共同的“移除 probe”决策。B 的历史因果前提不成立；C 与现有生产消费、续审和回归契约相反。

这里的 A 是保留“当前候选的有效 product_gap 裁决 → 完整 acceptance probe 回传”，修复实际阻断该契约的生产状态转换。它不意味着把尚未裁决、错误 fixture 或已有 source-backed negative 中不存在的 probe 伪造为 confirmed product gap；也不意味着将五个不同失败都解释为同一代码错误。

## 可核对的历史

- 当前 HEAD：`89288e8741cb4d87f8d3b094597a4c995d9ea5ca`。
- `git log --all -- minicode/workflow_runner.py minicode/implementation_completion_review.py` 最近一次修改这两个生产路径的实际提交为 `ad20e4a8`（2026-10-19 18:18:43 +0800），父提交 `9aaa6a434ae0a86766d28f8b1b91529451a31eb8`。没有这些路径的 10-22 源码提交。
- ad20 与 HEAD 的 `implementation_completion_review.py` Git blob 同为 `f430740ba432f5907ec5ae2aafbcceb5d7e09656`；`workflow_runner.py` 同为 `1cbf0f0bd7f2054101c0f107d6a9d44aacd7cf34`。10-22 receipt 过滤目前在 `git diff HEAD -- minicode/workflow_runner.py` 中。因此应称“10-22 工作区修复及其验证记录”，不能虚构对应源码提交。后续冻结评测材料的提交不改变上述生产路径历史。
- ad20 的 `implementation_completion_review.py:1407–1434` 已明确写明：外部证据路径用于宿主归档，原实现者可能只能读自己的 workspace，因此完整的 confirmed product counterexample 必须直接进入 feedback；未知/刚失败测试不能自行授予产品修改权限。现有 R2 将这些谓词抽为方法，没有删掉这一契约。
- ad20 的 `workflow_runner.py:4194–4211` 则把 checked candidate 的所有执行 receipts 都加入 formal refs，同时仅向 parser 传入原始 request verification checks。这才是 10-22 修复的错配。

## 两条生产—传输—消费链

| 边界 | 结构化 workflow 候选声明 | 原实现会话的裁决反馈 |
| --- | --- | --- |
| 生产 | Executor checked candidate 包含原检查与动态 `review_regression_*` 的实际执行 receipts | `ImplementationCompletionReview` 保存 probe source、candidate-bound receipt、独立 failure adjudication |
| 身份/契约 | `ExecutionCandidate.v1` 闭合字段及 `ImplementationCheckReceipt.v1`，一一对应原始 request checks | completion decision 的 `repairProbes`，每项含原始完整 `ReviewerProbe.to_dict()`，其中包括 `code` |
| 传输 | `workflow_runner.py:4253–4287` 只将原始 check IDs 变成 formal refs；仅 ledger 已知动态 probe ID 可分流，未知 ID 拒绝 | `agent_loop.py:3846–3850,3878–3904` 将完整 decision 放入同 owner completion receipt，JSON 注入原会话 user feedback |
| 消费 | `multi_agent_contracts.py:1795–1894` 精确验证每一个原始 check、owner、candidate digest、执行证据，refs 数量必须匹配 checks | `CompletionFeedbackViews.project_message` 保留原文及哈希；`EvidenceViews._walk` 仅特别处理 executionReport、probeReceipts、diagnosticObservations，**没有删除或截断 repairProbes/code**；原 Executor 读反馈继续实现 |
| 后续保障 | 动态 probe 仍在 Reviewer ledger，进入 repair evidence、重跑、final coverage，未被丢弃 | `HostImplementationContinuation.capture/:137–143` 保存原反馈及 messages/review state，构造校验要求反馈确实在原 messages 中（:67–73）；重入保持同 owner/candidate 身份 |

当前 workflow 侧还有直接证据：

- `workflow_runner.py:3144–3153` 将当前 Reviewer probe 的 sourceRef/receiptRef 加入 implementation repair request；`:3310–3323` 发布 `ReviewProbeExecution.v1`；`:3390–3490` 保留 correction、回放及 source-bound 检查。
- `artifacts/multi-agent-practicality-2026-10-22/checked-candidate-baseline.md:13–17` 明确说明该修复仅分离 formal declaration，probe source、失败、回放和最终审查仍在 ledger/audit。
- `tests/test_checked_candidate_workflow.py:320–333` 要求同一 Executor 修复，并保留动态 probe 的 failed/failed/passed/passed 执行序列。这和“移除 repair probe”相反。

当前 direct completion 侧的精确授权边界：

- `implementation_completion_review.py:256–264` 的 `_failure_authority` 同时绑定 probe ID、candidate digest、实际 receiptRef、source digest、objective digest。
- `probe_failure_adjudication.py:97` 的 `validate_adjudication_record` 校验独立归因材料；`:172–203` 的 `assess` 检查当前候选身份。失败测试本身不等于 product_gap。
- `implementation_completion_review.py:808–838` 仅向 repairProbes 放入未退休 acceptance probe，且已有当前 product_gap authority 或历史通过记录；`_confirmed_product_gap_result` 进一步要求当前 failed receipt 和 product_gap authority。
- `:1480–1486` 保留完整源码必须直接回传的注释；`:1540–1614` 的归因及 pending-result 调用这些谓词。diagnostic 与未归因失败留作证据，不自动成为修复指令。
- `completion_feedback_view.py:21–80` 留存完整原消息，`completion_evidence_view.py:129–148` 对 repairProbes 递归原样保留。`tests/test_completion_feedback_size.py:214` 已通过真实 provider request body 检查完整 `repairProbes[0].code == PROBE`，不能据 compact feedback 推断此通道废弃。
- `completion_repair_decision.py:20–69` 的 delivered_repair_matches 比较真实已交付的完整 native negative（仅允许外层 envelope/storage 地址差异）。不能为了新展示字段或补空数组改写旧认证负裁决。

## 五项日志不能合并为“确认 gap 后 probe 都为空”

`artifacts/native-review-economics-2026-10-23/final-regression.txt` 的实际五行 FAILED 对应：

| 测试 | 日志中的实际失败 | 历史判断 |
| --- | --- | --- |
| `test_true_product_gap_is_the_only_assertion_feedback_sent_to_executor` | `repairProbes == []`，期望 WRONG | real HEAD 同失败；需首先确认是否真的执行了新的归因，而不是恢复已有 source-backed negative |
| `test_classified_product_gap_cannot_restore_without_each_exact_independent_authority` | migration `replacement_failed`，期望 `classified` | real HEAD 同失败；状态提交/恢复问题，不是日志已证实的空反馈 |
| `test_corrected_expectation_exposes_real_gap_and_returns_it_in_same_assessment` | 已通过正确 `repairProbes == [right]` 断言，随后 `pendingLegacyResolution` 未清空 | real HEAD 同失败；明确反证“此通道没有回传” |
| `test_confirmed_product_gap_preserves_acceptance_and_diagnostic_history` | reason 为 `confirmed_product_gap`，旧断言要求 `legacy_acceptance_product_gap` | real HEAD 同失败；该日志停止在 reason 断言，未证明后续 probe 断言失败 |
| resume `[fix-probe-preserve-product]` | bounded reconciliation 4 次调用用完，4 次读取之后无法再执行第 5 次提交 | real HEAD 1 passed/current full 1 passed；它是新默认 bounded 与严格续审 fixture 的边界，不是 ad20 遗留空反馈 |

前三项 real HEAD 复现见 `r2/budget-result-preexisting-failures.txt`；reason 差异见 `r1/independent-review.md`。第五项见 `r2/strict-resume-head-local-telemetry.json/.txt` 和 `r2/strict-resume-current-full.json/.txt`。HEAD 的第二次复核仅把 telemetry 报告写入临时目录，生产及测试 Git bytes 未改；第一次的 sandbox 写权限失败不能算语义结论。

特别是第一项：`tests/test_completion_failure_adjudication.py:39–48,99–106` 的 pending fixture 从 `_seed_legacy` 恢复，后者在 `test_completion_probe_purposes.py:37–50` 已对同一 `release → return n` 候选接受 source-backed missing decision。修前 `ImplementationCompletionReview.assess:889–897` 有意先恢复该有效负裁决，避免无关归因阻断已经成立的产品修复。**不能在未执行新 product_gap 归因时为满足旧数组断言硬塞 WRONG。** 这里另有显式 host pending migration，最小生产修复可限定为让该请求完成其归因，而保留一般 source-backed negative 优先恢复；不能仅把失败数组填满。测试必须真实走归因、观察其提交权限。

## 最小落实建议

1. 在 §7 明确选择 A，并纠正五项失败的事实分组。维持两格真实验证前先完成裁决域本地门的顺序。
2. 逐项修复 legacy migration 的真实状态提交/恢复缺陷，保留 exact authority、candidate/source/receipt 绑定、退休记录及同 Executor 反馈。不要靠将未知失败归类为 product_gap 或跳过独立审核来恢复数组。
3. 对 strict-full 等实际契约边界，按明确且仍正确的生产契约修正场景或断言，并记录具体因果；reason 差异应先检查 migration 是否按时完成，而不是先改断言。对于被旧 negative 捷径跳过的显式 host migration，优先修正该限定条件下的生产顺序。这不是 B，更不是允许删除反馈通道。
4. 成功门应覆盖真实 target failure → 独立 product_gap → 完整代码进入父 provider body → 同 owner 可继续/恢复；probe_error/inconclusive/diagnostic 仍不得成为产品修复命令；旧 authority 在新候选不可复用。保留既有 formal receipt 分流回归，防止另一条通道倒退。

结论范围：已完成历史与通道契约裁决，**A 可据证据执行，B/C 无证据依据**。本报告不把未运行的新修复称为通过，不替代 root 对四项遗留状态/fixture 的逐项根因和修复验证。

## 实施草稿的独立边界复核

随后对比 `before/implementation_completion_review.py` 与 root 的最小 ordering 改动，未见批准权限放宽：限定同候选的显式 pending migration 跳过旧 negative/cache 捷径；先完成归因再提交 classified；pending migration 与 pending supersession 同时存在时仍进入独立 audit。负裁决返回前调用的 `accept_supersessions` 仍逐边执行 `validate_acceptance`，要求实际独立 audit 的 accepted edge、原目标/源码/receipt/见证精确绑定；失败 successor 额外需要 product_gap authority。overall changes_required 本身不批准任何替换。

提交给 root 的两个闭环风险：

1. `agent_loop.py:3742` 的恢复反馈去重捷径在 assess 之前就返回，仍需避免吞掉同候选显式 pending migration；只改 assess 不覆盖所有真实 owner 续审入口。
2. migration classified 完成后，同候选再次 assess（尤其 from_state 清空 cache 后）仍可能恢复旧 acceptedRepairDecision 的空 probes。应选择当前 ledger 的有效 gap decision，保留旧认证记录原文；不要给旧 native decision 添字段/换嵌套证据。建议选择条件限定为同候选已 classified 的 migration 加当前精确 product_gap authority，并复用至 assess、预算关闭与父侧去重。migration_audit 条件也应显式绑定当前 candidateDigest。

必要的最小反例为：同候选第二次 assess、from_state 后 assess、预算关闭仍带完整已确认 probe；旧候选 authority 不复用；没有显式 migration 的 missing+unknown/repair-priority 仍快速返回原负裁决。本节是只读设计审查，不宣称这些新增边界已经验证。

### 后续 `_current_repair_feedback` 复核

当前新选择方法已把 assess、预算关闭和 `agent_loop` 的真实反馈去重入口统一：同候选 pending migration 不走普通恢复捷径；同候选 classified history 加精确当前 product_gap 返回新的有效反馈；普通路径仍恢复旧 native negative。`migration_audit` 也已显式绑定当前 candidateDigest。旧 `acceptedRepairDecision` 认证字节不变，未增加批准权限。

新增 `test_classified_gap_feedback_survives_repeat_restore_and_budget_closure` 有真实 probe/独立归因构造，再验证第二次 assess、from_state、budget result，并要求实际 commands 不增加及原 acceptedRepairDecision 不变。这是有效的状态恢复反例，但不是 parent `run_agent_turn` 恢复集成测试。建议再覆盖“已交付旧 source-negative + 显式 pending migration”的真实 HostImplementationContinuation，确认旧反馈去重不会把该迁移吞掉，完整 probe 到达同 owner 的实际模型请求。

另已向 root 指出尚未被新增测试覆盖的窗口：pending migration 内已保存 product_gap、classified 尚未提交时预算耗尽，`preserve_pending_negative=True` 目前仍优先返回旧空 probe negative，外层 `or _confirmed_product_gap_result` 不会执行。这不会误批准，但可能遮住已成立的反例。应在不退休未独立批准的 supersession、不改旧认证记录的前提下检查该窗口。

验证披露：root 报告修复域 43 项通过；扩大的 80 文件套件仍在运行且已出现其他失败。此时不得据旧 389 项门或 43 项局部结果宣称“完成审查全域全绿”。本审阅未并发运行长套件，后续分诊以最终日志为准。
