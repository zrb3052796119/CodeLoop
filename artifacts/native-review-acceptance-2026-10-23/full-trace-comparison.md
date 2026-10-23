# 原八格 stock/config 的 full 轨迹对照（非冻结、只读）

历史 stock 完成过一次有效的独立 inspection，已进入 reconciliation；config 从未进入 inspection。两格均没有“完成 inspection 后又重做同一 inspection”的存活证据。stock 的 inspection 较慢，但这单独不能证明 R3 的结果复用会解决该次失败：那是第一次 inspection，之后旧宿主已经直接进入核对。

本文只读取原 `artifacts/multi-agent-trigger-2026-10-23/run-v1/` 及其引用的现存证据文件；没有修改旧材料、联网或模型调用，也没有读取或预判新两格 bounded 的结果。旧执行行为相当于本轮保留的 full：三段 max_steps=None、共享父预算与全局截止，尚无本轮新增的阶段/链限额。不要将旧配置中不存在的 full 参数冒充当时显式设置。

## 时间口径与实际阶段

旧 harness 未记录新的 native-review-observation 阶段入口/出口，因此不能恢复 Python 函数级的精确纯阶段耗时。下表使用**该阶段首个实际物理请求 startedEpoch → 下阶段首请求 startedEpoch**，最后一段到 guardian 的 deadlineReachedEpoch；这是可复算的实际墙钟窗口，包含阶段内工具、探针、请求间工作及少量交接开销，未含首请求之前的初始化。journal 的原生 model.started 与边界相符（毫秒级记录）。最后一段是被硬截止截断的已观察时长，不是完成该阶段所需时长。

“有终态请求秒数”仅累加 ledger 已记录的 seconds，不补估 started 请求。用量未知仍记 unknown。两种秒数不能互相替代。

| 格 / 阶段 | 请求号 | 从格开始的窗口（秒） | 观察到的墙钟跨度（秒） | 有终态请求秒数 | unknown calls |
| --- | --- | --- | ---: | ---: | ---: |
| stock 父工作 | 1–4 | 0.227324 → 92.152852 | 91.925528 | 90.339251 | 0 |
| stock review | 5–10 | 92.152852 → 276.442181 | 184.289329 | 183.839495 | 0 |
| stock inspection | 11–17 | 276.442181 → 538.713181 | 262.271000 | 261.105759 | 0 |
| stock reconciliation | 18–19 | 538.713181 → 600.003651，截断 | 61.290470 | 58.122665 | 1 |
| config 父工作 | 1–7 | 0.202713 → 464.332232 | 464.129519 | 461.991876 | 1 |
| config review | 8–10 | 464.332232 → 600.003226，截断 | 135.670994 | 14.181723 | 1 |
| config inspection / reconciliation | 无 | 未启动 | 不适用 | 不适用 | 不适用 |

stock 请求 19 于第 596.881766 秒开始，至 guardian 约有 3.121885 秒在途；config 请求 10 于第 478.545927 秒开始，至 guardian 约有 121.457299 秒在途。它们没有终态 usage，不能填成零。config 父请求 5 另有 IncompleteRead/unknown；其余父侧主要耗时已在既有 native-review-diagnostic.md 描述。

journal 只出现各一次 implementation.completion_review_started（stock sequence 35，config 47）；均无 implementation.completion_decided。stock 使用一个 reviewer ID 与一个 coverage auditor ID；inspection 和 reconciliation 使用同一个 coverage_audit_e5afa7e76a931b990bb20765。两格均为 HardTimeout，产物独立裁判通过与原生 final 被接受是不同事实。上述原生调用不是 task 协议税；这里也没有模型单因归因依据。

## 已观察到的三次原生工具拒绝

通过每个实际 parent request 的工具 schema、前一物理响应的 toolCalls 及后一 request 里实际送达的 tool result 配对，并按 tool_call_id 去重。stock 有以下三项；config 已完成的原生请求未留下 schema/提交错误。

| 发出请求 → 错误已送达的正文 | 错误 | 后续行为与边界 |
| --- | --- | --- |
| stock 8 → parent-requests/9.json | review_test 带 sourceEvidence，却缺 correctsProbeId/correctionReason；completion_review_invalid | 当时 schema 未暴露三字段全有或全无的关联。请求 9 去掉该额外字段，真实 acceptance probe 通过；不能把请求 8 的全部 58.565 秒当作纯 schema 浪费，它还包含探针编写。 |
| stock 15 → parent-requests/16.json | ready_for_evidence 携带非空 findings；coverage_inspection_invalid / ready_requires_no_findings | 实际 schema/说明未表达 ready 要求 findings=[]；请求 16 改为空。 |
| stock 16 → parent-requests/17.json | sourceEvidence 引用 allocation_report.py 第 1–9 行越过实际文件；source_validation | 这是具体来源锚点错误，不是相同字段约束。请求 17 修正后被宿主接受。 |

实际 acceptance probe：review 请求 9 一条通过；inspection 请求 13 一次响应提出四条 challenge，后续真实工具结果均通过。inspection 请求 11、12 先独立读文件；请求 14 grep 后，15、16、17 连续修正提交。没有这些审查要求父代理修改产品的轨迹，也没有第二轮“父修改→重新 inspection”。

## stock 的有效 inspection 与绑定证据

稳定存活的 `private/trigger-stock-allocation-r1-auto/parent-requests/18.json` 中，实际工具结果明确返回：

```json
{"grantsCoverage":false,"status":"ready_for_evidence","inspectionRef":"/var/folders/f4/9n10ft0d23v18qrjy2_l_wy40000gn/T/minicode-completion-review-st28__zy/00147-coverage-inspection.json"}
```

其后同一请求的工具表已切为 completion_coverage_audit_submit，且新增披露了普通 proposal/evidenceIndex；请求 19 仍在 reconciliation。接受发生在请求 17 响应完成与请求 18 发出之间，约第 538.662–538.713 秒。**ready_for_evidence 是有效独立观察记录，不是 coverage approved。**

上述临时目录本次仍存在，只读核对得到：

- `00147-coverage-inspection.json`：origin=fresh、phase=inspection、result.status=ready_for_evidence、findings=[]，8 条 observations，6 个已送达源码读取、4 个 challenges。原字节 SHA256：`391cae27900615ca1eb7d7d31d163eca896fb8d70cb92dda8df19f15995b4b58`。
- `00149-coverage-inspection.json`：同一个结果、reads、challenges 与绑定，phase 改为 reconciliation；是一次 disclose 后的状态快照，不是第二次 inspection。原字节 SHA256：`ab614667cff6fe49ca66d9bdad9a7400763e710d17c1fe698b7446387ec1d484`。
- 该目录有 13 个 coverage-inspection checkpoint，其中仅上述两个具有非空 result。checkpoint 数量不能计为 inspection 次数。临时目录不属于冻结包，未来可能消失；实际请求正文中的接受回执和后续阶段切换是持久历史证据。

两份记录与 reconciliation 请求正文共有的绑定：

| 字段 | 值 |
| --- | --- |
| auditId | coverage_audit_e5afa7e76a931b990bb20765 |
| candidateDigest | sha256:b9aab6faea273a6b424bdbf47eed8701c8ceb0036ee9af2cf2c61aa99d3dedba |
| objectiveDigest | sha256:b84a6062927f6e5f0e38d2163f829fe82ef3055721ac7323b5d11cdc256cdeab |
| proposalDigest | sha256:ae60bc5bc7644fdd55b0af3861cee1abdabb5fbc069abe0cb424c898e81bbc71 |
| supersessionDigest | sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945 |

config 没有任何 inspection schema 请求、inspection submit 或有效独立观察回执，不能把父测试通过或未完成 review 视为可复用 inspection。

## 对 R3 缩窄设计的约束

R3 需要已完成且有效的独立 inspection，以及同候选、同任务和未变的证据绑定、可验证的原上下文/来源。普通 review approved、review 腿 probe 通过、partial inspection 或工具无效提交均不满足。历史 stock 的记录能证明其自身当时具备这种独立观察，不能因此借给新 bounded 格：新格 owner、候选、proposal 与证据集合仍须真实重新绑定，不能仅凭同 case ID 复用旧结论。

旧冻结宿主本身已有相应的基本 continuation 行为：

- `private/execution-host/minicode/implementation_completion_review.py:232` / `:993`：pending audit 按候选 digest 匹配；候选改变清除其继承权，未变则校验 pending state。
- `private/execution-host/minicode/coverage_audit_state.py:131`：校验 audit identity、原 objective/requirements、candidate/proposal/supersession digest、immutable records、实际 messages/delivery 及 inspection record。
- `private/execution-host/minicode/completion_coverage_inspection.py:81`：校验完整记录与绑定；`:318` 的 disclose 保留已接受独立结果并进入 reconciliation。
- `private/execution-host/minicode/completion_coverage_audit.py:993`：只有 phase=inspection 且 result=None 才执行 inspection；已有结果只 disclose，phase=reconciliation 则直接核对。

因此，后续若要激活 R3，必须先定位既有 continuation 在实际失败点为何没有复用、是否真的重复了已完成有效 inspection，或哪些受支持的续审边界丢失了可验证状态。本次两条 full 轨迹没有这一重复成本证据。stock 第一次 inspection 慢、config 父侧耗时长，本身都不能推出“缓存复用即可解决”。如果未来 bounded 在第一次 inspection 完成之前耗尽，也不能用一个不存在的完成记录跳过独立检查。

本文未恢复执行任何旧 continuation，不能证明跨进程恢复已经成功。新 bounded 数据应另行检查实际终态、阶段墙钟、unknown usage、候选/证据绑定与重复 inspection，再依据既定门分叉；不得把本历史观察当成未跑 bounded 的结论。

## 局部可复核路径

下列路径均相对 `artifacts/multi-agent-trigger-2026-10-23/run-v1/`：

- `private/trigger-stock-allocation-r1-auto/transport-summary.json` 与 `parent-requests/{8,9,11,15,16,17,18,19}.json`。
- `private/trigger-config-overlay-r1-auto/transport-summary.json` 与 `parent-requests/{8,9,10}.json`。
- 两目录各自 `execution-start.json`、`process-cleanup.json`；`results.json` 提供裁判和 HardTimeout 结果。
- stock journal：`private/trigger-stock-allocation-r1-auto/journal/dashboard/workspaces/ws_b6b2e4a13cd50fd3/runs/run_42a22ecd875b42f1aaaa3318b121289f/events.ndjson`，sequence 35–78。
- config journal：`private/trigger-config-overlay-r1-auto/journal/dashboard/workspaces/ws_5d4f460a88d579ff/runs/run_3ad8e7698bc041a79848418b62d7b621/events.ndjson`，sequence 47–54。

计算：按 toolNames 中三种原生 submit 名分组；span = 下一组首请求 startedEpoch − 本组首请求 startedEpoch，末组使用 process-cleanup.deadlineReachedEpoch；known request seconds = Σ已有 seconds；unknown = usage.source 不为 provider 的请求数。


## 后续追加：已结束的 bounded stock（不读取在运行 config）

本节在 stock bounded 终态后追加，更新前文“新格尚未读取”的时间边界。仅查看 `artifacts/native-review-acceptance-2026-10-23/run-v1/private/trigger-stock-allocation-r1-auto/`；未读取仍在运行的 config 结果。依据是物理请求工具调用、实际工具 result、transport ledger、native observer、宿主交付/清理回执；不读取或引用隐藏模型 reasoning。

**该格触达的是 review 的 90 秒墙钟上限；没有进入 inspection，也没有形成可交付的 native probe 或原生批准决定。预算机制安全退出，不能据此称为 bounded 行为门通过。**

| stock 对照 | 历史 full | 本次 bounded |
| --- | --- | --- |
| 父工作请求 | 1–4，共 4 次 | 1–2，共 2 次 |
| review 请求 | 5–10，共 6 次；普通提案提交成功 | 3–6，共 4 次；3 个 completed + 1 个 deadline failed |
| review 墙钟 | 首物理请求→下一阶段首请求 184.289 秒 | observer phase_selected→budget_exhausted 90.024680 秒 |
| inspection | 7 次请求，262.271 秒，形成有效独立观察 | 未启动；无有效独立 inspection 记录 |
| reconciliation | 2 次请求，61.290 秒观察窗口，被 600 秒截止截断 | 未启动 |
| 原生终态 | 无 completion_decided，HardTimeout | completionReview=budget_exhausted，status=inconclusive，requirementsAccepted=false |
| 总物理请求 / unknown | 19 / 1 | 6 / 1 |

full 的阶段窗口和 bounded 的 observer 函数选择时刻不是同一种计时精度，表内已经分别标注，不能把差额当作因果节省。两次父工作与候选本身也不同；这仍是固定工程验证，不是可归因的随机对照。

### 时间与退出链

- `native-review-observation.jsonl` sequence 6 于格开始第 **78.772465 秒**选择 review：stageCalls=8，阶段绝对截止 `873844.313903708`（monotonic）。实际使用 4 个 review 请求；没有触达 8 次准入上限。
- 请求 3、4、5 完成目录/源码/测试读取以及 grep；请求 6 于格开始第 **129.031057 秒**进入流，39.742977 秒后失败。
- sequence 17 在截止后约 **0.024693 秒**报告 `implementation.completion_review_budget_exhausted`，phase=review、reason=wall_clock。phase_selected 到该事件为 **90.024680 秒**。当时原父任务全局截止尚余约 **431.203 秒**，所以是本地审查阶段限额，不是 600 秒 guardian 硬杀。
- sequence 18 于格开始第 **168.798806 秒**形成 `completionReview=budget_exhausted`、`reason=completion_review_budget_exhausted`、`requirementsAccepted=false`、`canContinue=false`；随后有 task.outcome 与 observer_finished。宿主 response 是明确 partial，lastPublicChecks=passed、continuesAfterResponse=false。`process-cleanup.json` 为 cleanupComplete=true、killedPids=[]，没有 deadlineReachedEpoch。
- parent 已知 input+output 为 **26,912 tokens**；review 已知 **22,314 tokens**，另 1 个 unknown。全格已知总量 **49,226 tokens**，unknown 保留，未补零或估计。

### 错误性质与证据资格

请求 6 的 ledger 是 `errorClass=ModelCallDeadlineExceeded`、`errorPhase=stream`、`httpStatus=200`、`streamDone=false`、usage.source=unavailable、toolCalls=null。结合 observer 的阶段截止与 wall_clock 事件，可证这是审查期限耗尽导致的流中断。它与旧后续格的 `URLError / CERTIFICATE_VERIFY_FAILED` 是不同失败；本格该请求已收到 HTTP 200，账本没有 TLS 信任校验失败记录。不能将 deadline unknown 改写成 TLS 错误，也不能把未知 usage 填零。

对 review 请求 3–6 正文内实际工具结果按 tool_call_id 去重：观察到 11 个已送达只读工具结果（list_files/file_tree/read_file/grep_files），**没有 completion_review_invalid 或 coverage_inspection_invalid**。但本格没有真正完成 review_test 或 native submit，因而“没有 schema 错误”不等于旧三类错误分支都经本次实测验证。

最后请求没有完整工具调用可供执行；observer 的终态明确 `probeReceipts=[]`、`unresolvedProbes=[]`、`repairProbes=[]`、`failureAdjudications=[]`。因此不存在已执行、已验证、可授予覆盖或修复权限的 native probe。本节不从隐藏推理或未完成流猜测探针内容，也不把未完成内容当作交付证据。父工作已经写入的 `tests/test_eval_regression.py` 与其公开检查属于另一类产物，不能替代未完成的原生审查。

### 本格对 R3 的含义

这条 bounded 轨迹在 **第一次 review** 就耗尽，未启动 inspection，故不支持“inspection 是本格瓶颈”的判断，也没有跨 turn 重复已完成 inspection 的事实。R3 的缩窄设计要求一个已完成、有效、绑定未变的独立 inspection；本格缺少这个前提，不能以缓存/复用名义跳过它。

历史 full 的 stock 候选 digest 是 `sha256:b9aab6faea273a6b424bdbf47eed8701c8ceb0036ee9af2cf2c61aa99d3dedba`，本次 bounded 是 `sha256:e12cdfdc97c7701cbed87e88f98978cc839ef92ccc013e98b7bf67d5ccdadebd`，也不是同 owner 的同一审查上下文。旧 full 完成的 inspection 不能移植为新格的独立批准依据。就已结束的 stock 而言，只能确认预算边界和 incomplete 语义按原设计生效；不能据此激活 R3、延长预算、重跑或放宽 final 接受。config 与两格总门结论留待其真实终态和独立审计。

本追加的精确证据：新 stock 的 `transport-summary.json` 请求 1–6、`parent-requests/{3,4,5,6}.json`、`native-review-observation.jsonl` sequence 6–20、`execution-start.json`、`response.txt`、`process-cleanup.json`。
