# 首两格原生审查诊断（非冻结材料）

本文件是运行后只读诊断，不属于 `run-v1/manifest.json` 的注册材料，不改预注册、裁判、预算或用量。仅覆盖 `trigger-stock-allocation-r1-auto` 与 `trigger-config-overlay-r1-auto`。执行宿主来自冻结提交 `e85a95803e248e157523e10dd0f1fe5bbed3f9bb`；下述源码行号以 `run-v1/private/execution-host/` 内文件为准。

两格最终交付副本均通过公开检查、新增回归和独立 oracle，写入范围通过；两格运行进程均被 600 秒硬截止停止。**产物通过不等于原生完成审查已经批准交付。** 已完成响应记录中的 `task` 启动请求为零，未观察到 child 执行或送达；config 的失败及未决父请求使 `launchClassificationComplete=false`，不支持完整证明零启动。以下已识别开销是宿主自动调用的原生完成审查，不能记作 task 协议税。仅凭这两格也不能归因为模型单一原因。

## 证据与统计口径

- 实际物理响应及用量：`run-v1/private/<trial>/transport-summary.json` 的 `requests[]`、`calls.jsonl`；实际发送的完整正文与工具 schema：同目录 `parent-requests/<call>.json`。
- 主循环与嵌套审查事件：stock 的 `journal/dashboard/workspaces/ws_b6b2e4a13cd50fd3/runs/run_42a22ecd875b42f1aaaa3318b121289f/events.ndjson`；config 的 `journal/dashboard/workspaces/ws_5d4f460a88d579ff/runs/run_3ad8e7698bc041a79848418b62d7b621/events.ndjson`。
- 截止与收尾：各格 `execution-start.json`、`process-cleanup.json`；裁判：`run-v1/results.json` 与各格 `judgement/`。
- 分类依据是实际请求工具表：`completion_review_submit` = 完成测试审查；`completion_coverage_inspection_submit` = 独立源码覆盖检查；`completion_coverage_audit_submit` = 证据核对。它们在物理账本中仍归于 `main`，不是 task 子代理。
- 下表秒数仅累加已有 `seconds`，不补估未完成请求。token 仅累加 provider 已报 input+output，不把未知填零。两格没有正常落盘的顶层 `transcript.json` / `execution.json`；`response.txt` 为空。请求正文保留了后续请求所见对话，账本保留已完成响应，因此仍能重建这里列出的事实。

| 格 | 阶段 / 请求号 | 已知请求秒数 | 已知 input+output tokens | 未知用量请求 |
|---|---|---:|---:|---:|
| stock | 父工作 1–4 | 90.339 | 53,776 | 0 |
| stock | 完成测试审查 5–10 | 183.839 | 75,184 | 0 |
| stock | 独立覆盖检查 11–17 | 261.106 | 147,520 | 0 |
| stock | 证据核对 18–19 | 58.123 | 42,739 | 1 |
| config | 父工作 1–7 | 461.992 | 97,463 | 1 |
| config | 完成测试审查 8–10 | 14.182 | 12,934 | 1 |

stock 的原生审查占 15/19 个物理请求，已知 token 为 265,443/319,219；config 为 3/10 个请求，已知 token 为 12,934/110,397。这是描述性拆分，不能当作完整成本比例；尤其 config 的最后审查请求有约 121 秒在途窗口但无终态用量。

## stock：完成候选之后，经历三个原生审查阶段

1. 请求 1 阅读公开源码；请求 2 修改三个产品文件；请求 3 新增回归测试。请求 4 在约第 92 秒前返回完成说明；其输入已有宿主当前补丁的公开检查 2 passed、回归检查 10 passed。
2. 第 92.153 秒进入完成测试审查，请求 5–10。审查者重新读源码；请求 8 首次 `review_test` 因参数组合被拒；请求 9 去掉 `sourceEvidence` 后独立 acceptance probe 通过（2 cases / 21 assertions）。请求 10 提交 `status=approved` 的普通审查提案。
3. 该提案不是最终覆盖批准。第 276.442 秒启动新的独立覆盖检查上下文，请求 11–17。请求 13 声明四个 probe（两个 acceptance、两个 diagnostic）；后续实际结果均 passed。请求 15 的 `ready_for_evidence` 携带非空 findings 被拒；请求 16 改空 findings 后，`allocation_report.py` 的 1–9 行引用越界被拒；请求 17 修正引用后提交成功。
4. 第 538.713 秒，同一个 coverage auditor 上下文进入证据核对，请求 18–19；这里没有再启动一个新的 task 角色。请求 18 读取普通提案及完整证据，19 于第 596.882 秒发出，600 秒到来时仍无物理终态。

账本中的写工具仅出现在父请求 2、3；原生审查期间没有再次修补产品代码。审查运行了隔离 probe，并纠正自身提交格式。事件日志只有一次 `implementation.completion_review_started`，没有 `implementation.completion_decided`；因此这是一次尚未完成的内部审查链，不是反复“审查失败→回父代理改代码→再审”的已证循环。也不能把普通提案的 approved 写成最终批准。

## config：大部分时间先消耗在父工作，审查未形成决定

- 请求 1 耗时 166.978 秒，provider 输出 8,192 tokens，`stopReason=length`，没有可见响应正文或工具调用。随后请求 2、3 才开始实际读文件。此处仅记录完成原因和输出长度，不引用内部推理正文。
- 请求 4 耗时 171.242 秒，写入两个产品文件。请求 5 在 51.541 秒后以 `IncompleteRead` 失败，用量未知；日志明确有 `model.retry_scheduled`，请求 6 在同一父会话新增回归测试。请求 7 于约第 464 秒前返回完成说明。
- 第 464.332 秒才进入完成测试审查。请求 8、9 查看目录并阅读产品与测试；请求 10 于第 478.546 秒开始，直到 600 秒被停止。没有已完成的 review_test、completion_review_submit、coverage inspection 或 coverage reconciliation 请求。
- 没有原生审查要求后修补产品的证据，也没有完成审查的明确负面决定。不能用“审查返工”解释 config 的主要耗时。

## 冻结代码如何形成这条路径

- `scripts/run_implementation_progress_live.py:504` 给直接实现绑定 `ImplementationSpec`。`minicode/agent_loop.py:3680` 的完成门在父模型打算结束时先执行当前候选检查；检查通过后在 `:3745` 发出审查开始事件，在 `:3776` 同步调用 `assess`。只有它返回并通过 `:3794` 后才形成完成决定。因此父模型宣称完成并不能跳过这段审查。
- `minicode/implementation_completion_review.py:1732` 运行完成测试 reviewer。`:1746` 对 approved 普通提案无条件进入独立 coverage audit；`:1750` 附近明确说明测试作者不能自行批准其测试的语义充分性。stock 的阶段切换与此一致。
- `minicode/completion_coverage_audit.py:993` 先运行独立 inspection，`:1011` 在 observation 提交后给**同一个**上下文披露普通提案，`:1036` 再运行 evidence reconciliation。inspection 的系统提示在 `minicode/completion_coverage_inspection.py:138` 明确“不要求固定数量测试”；stock 四个 challenge 是实际模型选择，不能说宿主强制四个。
- 这三个原生调用均使用 `max_steps=None`，继承 `host._agent_budget` 和同一个 `host._task_deadline_monotonic`（review `:1734`，inspection/audit `:1002`、`:1039`）。未见原生审查专用时间上限或比例预留。全局上限仍有效，本轮没有无限时运行。
- config 的重试发生在宿主循环 `minicode/agent_loop.py:4623`；冻结 `modelMaxRetries=0`（`scripts/run_three_arm_real.py:132`）关闭的是 adapter 内重试，不会关闭此宿主重入。实际重试作为新物理请求被计费、计预算，没有漏记成免费请求。这是需要明示的“retries=0”口径区别，不能据此补写缺失用量。

## 可定位的局部缺陷候选（本轮不修、不改变裁判）

1. **review_test 的字段关联没有暴露给模型。** stock 请求 8 的实际 schema 只列四个必填项，另外 `sourceEvidence`、`correctsProbeId`、`correctionReason` 分别可选，没有关联约束；runtime 却在 `implementation_completion_review.py:1078` 要求后三项全有或全无。模型仅增加 sourceEvidence 因此触发“缺 correctsProbeId/correctionReason”。实际工具说明也没解释这个组合。候选修复范围是描述/schema 对齐，不是放宽结果验收。该失配促成一轮可见格式重试，不能把整次 58.565 秒都归为纯浪费，因为其中也包含测试编写。
2. **inspection 的 ready/findings 约束没有出现在实际 schema 或说明。** stock 请求 15 的 schema 允许任意字符串数组 findings，description 也未说明 `ready_for_evidence` 要求空数组；而 `completion_coverage_inspection.py:68`、`:273` 强制为空。请求 16 因此重提交。请求 17 的行号修正是另一项实际返工，错误提示给出了具体越界位置，不能将其也归为隐藏字段约束。
3. **原生批准路径缺乏与任务规模/剩余时间相配的经济边界。** stock 已有产品检查与独立 acceptance probe 通过，仍按既有设计支付新的独立读取、challenge、证据核对；在同一剩余全局窗口内没有阶段消费上限。两格支持调查该控制流的固定成本，但没有对照实验，不能证明移除任一步仍有相同正确率，也不能单因归责模型或直接关闭验收门。
4. **硬截止与终态存档之间存在可观测性缺口。** `scripts/run_three_arm_real.py:684` 仅在 `runtime.execute` 正常返回后写 transcript；600 秒 guardian SIGKILL 使最后 provider 请求与顶层 final 接受状态没有终态。现有前置 body、ledger、journal 足以保留已发生事实，但不能恢复未知响应或补填 token。后续可单独评估逐步 transcript/checkpoint 落盘，不能将本轮 unknown 改为零。

## 可证停止原因及结论边界

`results.json` 两格均 `errorClass=HardTimeout`、duration≈600.003 秒；`process-cleanup.json` 均含 deadlineReachedEpoch 和 SIGSTOP→SIGKILL，`cleanupComplete=true`、无 survivor。账本 admission rejection 为零，调用数 19/10，尚未触达 40 次上限；因此已证停止原因是全局墙钟截止，不是请求准入拒绝、任务协议失败或已完成的负面覆盖决定。

stock 保留一条 started 请求用量未知；config 保留一个 IncompleteRead 失败请求及一个 started 请求用量未知。provider 延迟、父模型长输出、宿主原生多段审查和字段契约失配都在观察中出现；没有模型切换或消融对照，不能估计各因素的因果贡献。这份诊断只解释已有两格，不修改固定八格的协议或任务继续执行方式。
