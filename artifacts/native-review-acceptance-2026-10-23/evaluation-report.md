# R1/R2 两格 bounded 验证结果

**未过门，停止扩大运行；R3 暂缓，保留此前验证过的显式 Executor→Verifier 工程能力。** 两格原样各运行一次，最终独立审计 2/2 有效、0 错误、0 警告；stock 的决定为 budget_exhausted，触发预注册的停止分支。没有六格重跑、换模型、改题、延长限额或选优。

## 冻结与前置

- 分诊终态 A：repairProbes 是独立反馈通道，恢复真实反例及分类/恢复顺序；与 10-22 候选正式 receipt 过滤不同。原四项失败测试保留并已修复，新增真实父恢复与预算中断反例。五行失败与四项剩余差别已在方案 §7 和 adjudication-channel-review.md 对齐。
- 完成审查域 81 文件 **713 passed in 300.37s**；包括原 97 项 R1/R2 新增和 3 项裁决回归。另有验证 harness 新 27 项与原 80 项共 107 项通过。失败分诊见 regression-closure.md；没有删除、skip 或 xfail。
- 验证源码：`c9002748014a507b1bb86d0202cfffa2e77d43e0`，独立 ref `codex/native-review-validation-host-20261023`。宿主保留原八格基线，加精确 11 个生产文件和 2 个脚本。当前工作区其他 Memory 改动未混入。
- 预注册：`0b6dc79612fdb276359a121440750a4834fdb684`；manifest SHA256 `d19ff66a177b7ac5169e9731d6a33c42bef838f7572c989b514a5756161a42f2`。固定原 stock→config、GLM-5.3-Flash、每格 40 物理请求 / 500000 token / 600 秒，output 8192、temperature 0、retry 0、seed 20261023。原 Python 3.12.8、28 项包清单及路由未变。
- TLS 使用原系统 CA，证书及主机名校验一直开启。运行前无认证 HEAD 返回 401，两格没有观察到 TLS 错误。历史失败 peer 链未留存，根因仍未证实，不能宣称永久修复。第一次离线公共预检的外层沙盒阻断已保留，正常宿主环境下同一 verifier 预检通过；当时没有模型调用。

## 真实结果

| 指标 | stock（唯一行为门） | config（仅描述） |
| --- | ---: | ---: |
| 全格执行时间 | 168.808s | 486.501s |
| 物理请求：全部 / 原生 review | 6 / 4 | 9 / 3 |
| review 活跃墙钟（到终态） | 90.026s | 90.032s |
| inspection / reconciliation | 均未进入 | 均未进入 |
| 已知 tokens / 未知用量请求 | 49,226 / 1 | 113,920 / 1 |
| 隐藏 oracle / 新增回归 | 通过 / 通过 | 通过 / 通过 |
| completionReview | budget_exhausted | budget_exhausted |
| requirementsAccepted / accepted final | false / false | false / false |

review 时间包含工具与本地工作，按独立 auditor 的 phase_selected→terminal 口径；约 26/32ms 的截止后收尾不是失败原因，失败是限额内未形成 approved/rejected。未启动阶段没有实测耗时，不填 0 秒。两格均正常交付 partial 候选并完成清理，非 600 秒 HardTimeout；不把结果行的 candidate success=true 解读为最终需求已接受。

两个中断请求均已返回 HTTP 200，在 stream 阶段触发 ModelCallDeadlineExceeded；每格 1 次用量 unknown 原样保留。共 15 次请求、已知 token 下界 163,146；不能报告完整成本。已完成的原生请求只完成读取，没有完成 review_test 或 native submit，因此没有观察到 schema 拒绝也不能证明 R1 的原错误分支已经实测覆盖。

## 参数冻结与分叉

保持并冻结 review **8 请求/90s**、inspection **8/150s**、reconciliation **4/60s**；链级 ≤入口剩余时间 50%，先保留父收尾 2 请求及 max(60s,剩余15%)。两格实际时间预留分别 78.207s、60s，阶段没有重置。此次只获得到上限仍未完成的 review 观察，另外两阶段无新样本；没有依据估计它们的完成时长，因此不事后调大参数来改写门或追加试次。R2 的安全边界生效，预算内形成独立决定的目标未达到。

**R3 两条件均未满足：两格都在第一次 review 耗尽，未进入 inspection；既有 full 轨迹没有同绑定已完成 inspection 被重复执行的证据。暂不激活。** 历史 full stock 首次 inspection 较慢且完成后直接进入核对，旧代码已有有效结果续审复用，不能用不存在的本格 inspection 记录或不同候选的旧记录跳过独立检查。详见 full-trace-comparison.md。

停止 gen-2 六格 bounded 扩跑，保留此前显式 E→V 的工程能力；此前 six-cell 结果仍不证明协作优于单 Agent。这两格是固定工程验证，不能把门失败单因归为模型能力，也不能宣称 R1/R2 提高协作有效率。完整证据保留，不补跑。

## 证据

- final-independent-audit.json、final-independent-review.md：独立终审与解释；validation-summary.json：机器可读结论。
- run-v1/manifest.json、analysis-plan.md、materials/host-source.json：冻结输入与逐文件来源。
- run-v1/results.json、trials/*.json：两格终态；private/ 下原始 provider 请求、物理 SQLite 账本、native-review-observation、产物及清理证据保留本地，不纳入 Git 公开摘要。
- exact-host-regression-closed.txt、regression-closure.md、tls-preflight-before-run.json、full-trace-comparison.md：前置与历史比较。
