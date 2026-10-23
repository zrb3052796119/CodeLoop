# 原生完成审查：本地回归分诊收尾

2026-10-23。本记录整理 R1/R2 及裁决通道 A 修复的本地证据；不把历史分批通过相加为一次全绿，也不把验证 harness 用例计入生产回归。没有运行远端模型、删除/跳过失败测试或放宽生产接受权限。

**最终收尾状态**：精确宿主的最后一次 **81 文件、713 用例全部通过，713 passed / 300.37s，exitCode=0**。`exact-host-regression-closed.txt` 保存完整 pytest 输出；`exact-host-regression-exit.json` 绑定该日志并记录 elapsedSeconds=300.5710258483887。这关闭的是选定完成审查与相关预算回归域，不是整个仓库所有测试，也不是模型效果验收。

## 证据时间线与计数

| 范围 | 原始结果 | 分诊与后续证据 |
| --- | --- | --- |
| 最初 R1/R2：26 文件、389 用例 | 384 passed / 5 failed，153.42s | 四项在真实修前 HEAD 同失败；第五项是严格续审 fixture 在新 bounded 四次核对限额下无法再提交。原始日志在 `../native-review-economics-2026-10-23/final-regression.txt`。不能把这一步写成全绿。 |
| 最初第五项严格续审 | 修前 HEAD passed；当前显式 full passed | 只为该严格续审测试明确 full，原断言保留；两个参数最终 2 passed / 8.11s，见原目录 `strict-resume-final.txt` 及 `r2/strict-resume-*.json/.txt`。 |
| 裁决通道 A 修复 | 修前 4 failed / 28 passed；定向修后 36 passed，随后扩展 43 passed | `adjudication-before.txt`、`adjudication-first-fix.txt`、`adjudication-closure.txt`。这仍是局部结果，不替代扩域回归。 |
| 扩域首轮：80 文件、710 用例 | 676 passed / 32 failed / 2 errors，454.98s | `full-domain-first.txt` 与完整 node 清单 `expanded-failures.json`。34 个问题均保留并逐项分诊，不能继续沿用旧 389 用例的局部结论。 |
| 精确宿主完整复测 | 711 passed / 2 failed，340.15s | `exact-host-regression-final.txt`。两项均为 `test_unlimited_agent_budget.py` 配置 fixture 的 `No model configured`，不在审查执行中失败。 |
| 两项配置 fixture 的定向复测 | 34 passed，0.14s，exit 0 | `unlimited-budget-isolated.txt` 是原工具 stdout 与退出码的保存，明确标注 tool-captured，不伪称完整 subprocess 日志。 |
| 最后完整复测：81 文件、713 用例 | **713 passed，300.37s，exit 0** | `exact-host-regression-closed.txt` / `exact-host-regression-exit.json`。这是最后一次完整运行的真实结果，未与分批通过数相加。 |

最初新增 **97 项 R1/R2 生产回归**已经包含于上述生产测试集合。裁决分诊又新增 **3 项**：同候选重复调用/恢复/预算收尾一项，真实父会话迁移恢复与独立 product_gap 提交后预算中断两项；因此扩域的 710 → 713 是新增这三项，不能再将 97 或 3 加到 713 之外。

新两格验证 harness 的 **27 项**另计，与已有 trigger harness **80 项**联合为 **107 passed / 8.06s**，见 `validation-materials/final-offline-regression.txt`。它们验证冻结、计数和门定义，不证明真实模型通过，也不计入 713 项生产审查回归。

## 最初四项旧失败：选择 A 修复通道

历史与接口复核见 [adjudication-channel-review.md](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/native-review-acceptance-2026-10-23/adjudication-channel-review.md>)。`repairProbes` 是独立归因后的完整反例向同一实现者的反馈；10-22 排除动态 probe 的是 `ExecutionCandidate.receiptRefs` 正式声明通道，二者不同。不能用后者为前者的缺失辩解，也不能把四个不同断言失败概括为“probe 都为空”。

生产修复限定在显式同候选 pending migration：避免旧 accepted negative 提前吞掉新迁移；独立归因之后完成分类；存在 supersession 时保留独立 audit 批准后才退休原 probe。普通有效 native negative 仍优先，不要求为返回真实 product_gap 先完成无关 retirement。重复 assess、恢复和预算收尾使用当前精确绑定的反馈，认证的旧 native decision 字节不被改写。

新增三项反例验证：完整反例在重复/恢复/预算路径仍可取得；真实 `run_agent_turn` 恢复不能被旧反馈去重提前放回 writer；真实独立 product_gap 已提交但 classification 前中断时保留完整 gap、pending 状态和原始 authority，不虚构 retirement。原始 A 修复与追加测试结果分别保存在 `adjudication-final-targeted.txt`、`migration-feedback-resume.txt`。

## 扩域 34 项的契约归类

修前基线来自原八格冻结宿主，另在真实 HEAD 复核最初四项。34 个问题中 **26 个在修前代码复现（24 failed + 2 errors）**，**8 个在修前通过、当前默认 bounded 下达到新限额**。计算依据是：8 文件组修前 15 failed，加 `root-extra-baseline-local-telemetry.txt` 的 9 failed / 8 passed / 2 errors。后者只把旧 supervisor telemetry 输出改指临时目录，未改生产分支；原权限噪声日志 `root-extra-baseline.txt` 也保留，不能拿它证明语义失败。

| 类别 | 调整与保留的不变量 | 主要证据 |
| --- | --- | --- |
| 既有证据阅读协议与 provider fixture：8 文件、15 个旧失败 | 实际完整读取 source / independent authority / display document 的所有分页，核 cursor、digest、candidate；补入既有只读 `completion_evidence_read` 与真实独立 inspection 腿。未读完仍拒绝，display reader 不授予审批权，普通 reviewer 不能自行退休 assertion。 | [test-contract-reconciliation.md](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/artifacts/native-review-acceptance-2026-10-23/test-contract-reconciliation.md>)；`contract-reconciliation-before.txt` 15 failed；最终当前代码 38 passed、修前 production + 同一最终测试 38 passed，分别见 `contract-reconciliation-after.txt`、`contract-reconciliation-old-production.txt/.json`。 |
| 负裁决、异常继承与 pending audit：4 文件、6 个旧失败 | 真 product_gap 先交付并保留未退休旧 probe；产品修复后才由独立 audit 接受退休。fixture error 用真实独立 probe_error 归因；submit transport 成功不等于批准。未完成 audit 明确保持 `coverage_audit_interrupted`、pending、resumable，恢复 authority 不变；伪造、未读证据、缺 supersession edge 仍拒绝。 | `route-history-fixtures.txt` 22 passed；对应原失败在 `root-extra-baseline-local-telemetry.txt`。 |
| 其余旧 fixture：3 个失败、2 个 setup errors | pending migration 使用真实 authenticated delivery envelope；repair priority/provider handoff 补实际 inspection；需要 probe 归因的 fixture 先提交 unknown/inconclusive，避免 source-backed missing 本来就应立即交付而绕开归因。event 展示字段与认证反馈字节分开比较。真实同 owner、probe source/receipt、不可提前展示成功及 adapter tool isolation 继续断言。 | `root-extra-baseline-local-telemetry.txt`、中间失败 `root-fixture-repairs.txt`、最终 `provider-fixture-final.txt`；最终覆盖以完整宿主复测为准。 |
| 严格续审超过新 bounded 限额：8 个 node | 对有意完整走长证据分页、再次提交及恢复的旧场景显式选择 full，保留原行为断言。涉及 interruption 1、parent resume 4、receipt snapshot 3；并未把全套改成 full。bounded 耗尽、保留负裁决、父预留和不可假批准由新增真实循环测试继续覆盖。 | 修前 `root-extra-baseline-local-telemetry.txt` 的 8 passed；`strict-full-adapted.txt` 中这部分通过，另外两项旧 setup errors 此后按上一行修复。 |

所有校正保留原行为目标；有一项过时测试名调整为完整 source/independent-authority 语义，未删除测试。没有以 skip/xfail、删断言或放宽生产权限取得通过。

## 精确宿主与最后两项配置前置

最终测试隔离目录是 `/tmp/native-review-validation-host-cl3u8c9q`，使用 `.venv-acceptance/bin/python`（Python 3.12）。生产来自原冻结 archive SHA256 `a118a493563821da50b8d2e15385f9cdc686ee433c36e44b8f0a3990b88d7d3d` 加明确的 11 文件生产覆盖；逐文件 before/after SHA 见 `final-production-receipt.json`。完整输入、生产与测试 hash、81 文件选择和仅测试使用的 telemetry 输出路径见 `exact-host-regression-input.json`、`regression-files.txt`。

最初隔离环境缺少测试辅助脚本及根路径标志而 collection 失败，随后一次配置诊断运行中断；`exact-host-regression.txt`、`exact-host-regression-complete.txt` 原样保留，均不计为完成的回归结果。补齐的是测试依赖，不把未声明生产文件混入验证宿主。

最后两项失败发生在加载配置时：`test_migrated_config_keeps_explicit_budget_settings`、`test_config_preserves_zero_override_and_reports_unlimited` 未设置模型。现在分别 `monkeypatch.setenv("MINI_CODE_MODEL", "test-model")`，只提供 dummy 配置，不创建/调用模型；迁移预算、零值覆盖及 unlimited 的原断言不变。34 项定向通过后，同一 81 文件集合已完整重跑并得到 713 passed；没有以 711+34 的算术相加宣称全绿。

本地回归现已关闭；真实两格仍须按冻结后的预注册运行和独立审计。局部测试、本审查域完整通过及离线预算公式均不证明 stock 能在预算内形成原生终态，也不证明多 Agent 优越性。
