# 固定八格结束后的独立诊断（非冻结材料）

只读依据：`run-v1/results.json`、各格 `private/<id>/transport-summary.json`、`calls.jsonl`、`parent-requests/`、`process.log`、`process-cleanup.json`、存活的 `transcript.json` 与 journal。代码解释使用 `run-v1/private/execution-host/` 的冻结副本（提交 `e85a95803e248e157523e10dd0f1fe5bbed3f9bb`）。本文件不更改任务、门槛、用量或执行结果，不属于冻结注册材料，也不发起重跑。

## 三个必须分开的结果

1. **产物裁判**：6/8 个交付副本通过公开检查、新增回归、oracle 与写入范围；最后两格未实现任务。这是现有固定裁判的结果。
2. **原生 final 接受**：未观察到任何一格有已接受的最终交付。前五格为 HardTimeout，顶层正常结束存档未完成；第六格有明确 partial / `requirementsAccepted=false`；最后两格也为 partial。不能把 6/8 产物通过改写为 6/8 原生完成门通过。
3. **task 协作**：存活的已完成响应中未观察到 task 启动或 child 送达；含失败/未决父响应的格不能由此完整证明零启动。本文分类的是实际宿主原生审查开销，不能把它计作 task 协议税，也不能从八格直接归因为模型单一原因。

## 全八格物理调用和已知用量

按每个实际请求的工具表分类。所有原生请求仍在 transport 中归于 `main`，因此不能直接用 actor=`main` 当作“父工作”。原生类别包含 `completion_review_submit`、`completion_coverage_inspection_submit`、`completion_coverage_audit_submit`，以及 **`completion_probe_adjudication_submit`**。最后一类在 limit-clamp 的请求 10–17 共 8 次，不应漏算进父工作。

下表 tokens 是 provider 已报 input+output 的和；“0 已知”仅表示没有收到已知用量，不证明实际为零。unknown 是物理请求数，不补填或估算。

| 固定顺序 / case | 父工作 calls | 原生 calls | 父已知 tokens | 原生已知 tokens | 父 unknown | 原生 unknown |
|---|---:|---:|---:|---:|---:|---:|
| 1 stock-allocation | 4 | 15 | 53,776 | 265,443 | 0 | 1 |
| 2 config-overlay | 7 | 3 | 97,463 | 12,934 | 1 | 1 |
| 3 record-pagination | 5 | 15 | 84,030 | 216,106 | 0 | 1 |
| 4 limit-clamp | 3 | 20 | 29,839 | 323,990 | 0 | 1 |
| 5 extension-case | 6 | 14 | 97,127 | 106,291 | 0 | 4 |
| 6 import-retry | 4 | 11 | 53,703 | 101,470 | 0 | 2 |
| 7 cart-discounts | 1 | 0 | 0 已知 | 0 | 1 | 0 |
| 8 report-timezones | 1 | 0 | 0 已知 | 0 | 1 | 0 |
| **合计** | **31** | **78** | **415,938** | **1,026,234** | **3** | **10** |

完整分类合计如下，保留 input/output 分项便于复算：

| 实际请求类别 | calls | 已知 input | 已知 output | unknown |
|---|---:|---:|---:|---:|
| 父工作 | 31 | 371,338 | 44,600 | 3 |
| 完成测试审查 | 42 | 494,580 | 51,520 | 4 |
| 独立覆盖检查 | 26 | 250,450 | 23,350 | 5 |
| 覆盖证据核对 | 2 | 40,135 | 2,604 | 1 |
| probe 失败归因裁决 | 8 | 154,642 | 8,953 | 0 |
| **合计** | **109** | **1,311,145** | **131,027** | **13** |

总已知 tokens 为 **1,442,172**，另有 **13 次 unknown usage**。这些是绝对数；不能将已知用量比例视为完整成本比例。HTTP adapter 的 retries=0 与宿主在同一会话中重新发起、重新计账的请求不同；失败后的宿主重入仍消耗同一 40 calls / 500k tokens / 600s 预算。

## 各格停止与最终接受状态

| case | durationSeconds | 外层 errorClass | 可证 final 状态 |
|---|---:|---|---|
| stock-allocation | 600.003651 | HardTimeout | 停在覆盖证据核对；没有原生 completion_decided 和正常顶层 transcript |
| config-overlay | 600.003226 | HardTimeout | 停在完成测试审查；没有原生 completion_decided 和正常顶层 transcript |
| record-pagination | 600.004359 | HardTimeout | 停在独立覆盖检查；没有原生 completion_decided 和正常顶层 transcript |
| limit-clamp | 600.002241 | HardTimeout | 经过 probe 裁决，最终停在独立覆盖检查；没有原生 completion_decided 和正常顶层 transcript |
| extension-case | 600.010037 | HardTimeout | 停在独立覆盖检查；没有原生 completion_decided 和正常顶层 transcript |
| import-retry | 355.917279 | null | 正常返回 **partial**，明确 requirementsAccepted=false、lastPublicChecks=passed |
| cart-discounts | 0.263428 | null | 首请求 TLS 失败后正常返回 **partial**，lastPublicChecks=unavailable |
| report-timezones | 1.079758 | null | 首请求 TLS 失败后正常返回 **partial**，lastPublicChecks=unavailable |

前五格的 `process-cleanup.json` 记录硬截止和进程终止；没有正常落盘的顶层 `execution.json` / `transcript.json`。后三格有这些文件，`killedPids=[]` 且 cleanupComplete=true。`errorClass=null` 和 journal 的 `run.completed` 表示宿主函数/生命周期正常结束，不能解释成模型成功或 final 被批准。

## 第六格 import-retry：产物通过，覆盖检查因网络故障暂停

精确目录：`run-v1/private/trigger-import-retry-r1-auto/`。

- 请求 1–4 完成父工作；请求 5–10 为普通完成测试 reviewer；请求 11–15 为独立覆盖检查。没有进入证据核对提交阶段。
- 请求 **14**：`httpStatus=200`，已经收到流字节和 provider 响应标识；48.944721 秒后 `errorClass=IncompleteRead`、`errorPhase=stream`，`streamDone=false`，无完整 provider usage。它证明一个已建立 HTTP 响应的流未完整结束，不能进一步证明中断源。
- 请求 **15**：4.210066 秒后 `URLError`、`errorPhase=headers`，无 HTTP 状态、providerRequestId 或首字节。`process.log` 给出具体文本：

  `Model API error (URLError): <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate (_ssl.c:1000)>`

- 原生事件在该格 journal 的 sequence **67** 为 `implementation.completion_decided`，`status=inconclusive`、`canContinue=false`、`continuesSameExecutor=false`。存活 transcript 的完成反馈还明确给出 `reason=coverage_audit_interrupted`、`inspectionPhase=inspection`；父候选完成说明被保存为 `_rejected_completion=true` 的进度内容。
- `response.txt` 是宿主 partial 回执：`executionEnded=true`、`continuesAfterResponse=false`、`lastPublicChecks=passed`、`requirementsAccepted=false`。因此它在 355.9 秒正常结束，不是 600 秒被杀，也不是原生覆盖已接受。
- 两条失败请求均维持 unknown；没有从外层 budget 的数字反推实际 usage。独立裁判随后检查了已有源码/回归副本而通过，两者并不矛盾。

## 第七、八格：可证为 TLS 信任校验失败，不能推断额度

精确目录分别为 `run-v1/private/trigger-cart-discounts-r1-auto/`、`run-v1/private/trigger-report-timezones-r1-auto/`。

- 各只有第 **1** 个物理请求；请求本身耗时分别 **0.054357s**、**0.860214s**，不是整格 durationSeconds。
- 两者均 `URLError`、`errorPhase=headers`、无 HTTP 状态、无 providerRequestId/providerResponseId、无首字节、无 provider usage。原始账本不保存 errorMessage；具体错误来自各格 **process.log 和 transcript 中原样保存的错误响应**，均为上面相同的 `CERTIFICATE_VERIFY_FAILED ... self-signed certificate`。
- 两格 journal sequence **6** 为 `model.failed`，`failureKind=provider_error`，随后 sequence **8** 是宿主 `run.completed`。没有模型工具响应、没有改源码、没有进入原生 review。存活 final 均为 `delivery=partial`、`requirementsAccepted=false`、`lastPublicChecks=unavailable`。
- 冻结 `minicode/agent_loop.py:1228` 明确将 authentication/certificate failure 视为 terminal；`:4651` 构造 `Model API error (...)` 返回文本。因此这种快速正常返回与证书错误分支一致，不是宿主墙钟截止。

**证据只支持 TLS 证书信任验证失败。** 没有 HTTP 429、402、额度余额或 provider 配额错误证据。无法仅凭这些日志判断自签名证书来自远端链、中间网络设备、代理还是本机信任环境，不能归因账户额度耗尽、被限流或模型拒绝任务。本次没有网络探测、改证书设置或重试调用。

## 结论边界

6/8 的裁判通过率、原生最终交付未获接受、109 次实际物理请求及 13 次未知用量应并列报告。原生完成/覆盖/归因审查共 78 次请求，是当前宿主执行路径的重要开销；它们不是 task 自主协作触发，也不自动证明“模型是唯一瓶颈”。最后两格的网络失败更不能用于推断模型不会解决这两个任务。保留预注册判定与独立审计，不删格、不补轮、不把 unknown 写成零。
