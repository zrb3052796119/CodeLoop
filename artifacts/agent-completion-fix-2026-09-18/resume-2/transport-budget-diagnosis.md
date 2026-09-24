# 两次 transport timeout 与续办预算诊断

日期：2026-09-18。只读分析生产代码、已保存的安全配置摘要和 journal 元数据；未读取凭据文件，未发起网络或模型请求，未改生产代码或目标仓库。本报告审阅的是 `run_live_resume_2.py` 的准备版本，实际开跑后应以保存的有效配置、事件和预算快照为准。

## 已证实的停止原因

| 运行 | 完成 / 失败调用 | 失败位置及耗时 | 已返回 input / output tokens | 内部费用估算 |
| --- | --- | --- | --- | --- |
| review-followup `run_da39459e06164042bbc2a2aaf7524c0f` | 6 / 1 | step 7，120,005 ms | 61,311 / 825 | 0.2102856 |
| review-retry `run_974428e87dfa49ef867b105c2b038f4c` | 3 / 1 | step 4，120,003 ms | 46,161 / 610 | 0.1543722 |

两次错误均是 `model transport open timed out`。已返回 usage 均未达到各轮配置的 token / call / cost 上限，故这两次停止不是预算耗尽。失败调用没有 provider usage 收据，其实际 token 消耗和费用未知，不能算作零。

`minicode/openai_adapter.py` 的 `_open_verified_url_controlled` 为 transport open 设置本地绝对 watchdog，等待后台 `open_verified_url` 返回。该错误还可能发生于等待受限后台 I/O worker 槽位时。`minicode/tls.py` 最终使用 `urllib.request.urlopen`，其 open 阶段包括等待响应头，错误名不能证明仅仅是 DNS、TCP 或 TLS 建连失败。

真实 headless harness 调用 `runtime.execute(initial, observation)`，未提供 presentation。`agent_runtime.py` 因此不设置流式回调；adapter 仅在有回调时添加 `stream: true`，故这两轮走非流式请求。服务端排队、推理或生成直到发响应头之前的等待，都可能耗尽 open 的 120 秒；当前日志不足以区分这些原因，也不足以证明模型正在长时间 thinking。

两次失败之前的成功调用分别耗时 4.2–9.8 秒和 5.6–14.5 秒。不存在“所有请求都超过 120 秒”的证据。更早 continuation 中最长成功调用约 52.8 秒，说明延迟存在变化，但不能据此推断本次超时必然稍等就好。

adapter 内层只对可重试 HTTP 状态和 `URLError` 自动重试，不捕获这个 watchdog `TimeoutError`。默认 `modelMaxRetries=4` 不能解释成这两次超时已经各重试 4 次。宿主的新主循环恢复改动由另一项回归验证，本报告不把尚未执行的恢复行为算作实测成功。

## 哪些配置确实影响请求

| 配置 | 真实代码路径 | 本次可作出的结论 |
| --- | --- | --- |
| `maxOutputTokens` | adapter 写入 JSON `max_tokens` | 8192 是单次输出控制；不是总 token 预算。provider 是否接受、如何计算 reasoning tokens，需真实响应证实。 |
| `agentTurnBudget.maxTokens` | `AgentTurnBudget.from_runtime` | 是本地整轮累计入场控制，不会自动变成请求 JSON 的 `max_tokens`。 |
| `modelTimeoutSeconds` | adapter → `bounded_request_timeout` → open / read watchdog | 显式 180 覆盖默认环境回退 120；另受全局剩余 deadline 限制。 |
| `modelMaxRetries` | adapter HTTP/URL 错误重试循环 | 未设时默认 4，最多初始请求加 4 次内层重试；不含主循环恢复。 |
| `enableThinking` | 布尔值才写入 `enable_thinking` | 未设置意味着不发送此字段，不代表 thinking 已关闭。 |
| `reasoning_effort_override` | 单次 adapter 参数写入 `reasoning_effort` | 普通主循环没有传此 override，不能仅设置一个同名 runtime 字段就假定生效。 |
| presentation / stream callback | callback 存在才写 `stream: true` | 新 harness 仍为非流式，与既有比较一致。 |

`create_model_adapter` 保留 runtime 控制，因此 harness 的内存覆盖能到达真实 adapter。配置 loader 支持输出上限环境字段；本次 timeout / retries / thinking 的显式控制应直接在内存 runtime 设置，不应假定随意加配置文件字段就会加载。

历史 `safe_runtime_summary` 没有记录这些 wire 控制，无法追溯是否曾设置了大的输出值或不兼容参数。`resume-2/control-fields-before.json` 当前快照中这些字段的 `.get` 值均为 null，表示当前未获得有效显式值，不能倒推前两轮配置。前两轮没有留下 400 响应、参数不支持或 token 上限错误证据，所以“maxTokens 不兼容”目前不是已证实根因。

180 秒是 open 的局部限制，也是后续 read 单独使用的限制，不一定是一次完整响应的总时长。全局 1200 秒 deadline 才会共同约束 open、read 和退避。增大到 180 秒只能给慢响应更多时间，不构成原因修复。

不建议本轮顺手开启 streaming：当前流式分支没有发送 `stream_options.include_usage`，只在某类空 choices usage chunk 中收 usage；缺失时会回退输入/文本估算。改变流式模式可能改善响应头等待，但会改变此次比较条件及 usage 质量，需另有明确验证。

## 未定价模型与预算的真实含义

当前模型 `glm-5.3-flash` / custom 在审计价格目录中无匹配，`model.costed` 标记 `unavailable / model_unpriced`。真实账单不可从这些事件得出。

`record_budgeted_model_call` 保留本地费用 guard：没有权威价格收据时调用 `cost_tracker.calculate_cost`，未知模型使用代码已有默认值，按每百万 token：input 3、output 15、cache read 0.30、cache write 3.75。这里是既有内部估算单位，不是宣称 GLM 的实际美元价格，也没有证明它一定是实际账单的上界。公式对 prompt input 和 cached input 分别累加，在 provider prompt 总数已含缓存时会重复计入一部分估算。

更早 continuation 的已返回 usage 对应内部估算 5.0523144，因此 $5 guard 在下一次入场时停止是可解释的。guard 根据已结算金额判断，未为未知输出预留精确费用；最后一次调用结算可超过阈值。token guard同样以输入估算入场，最终输出结算可能越过阈值。有限额度有控制作用，但不可描述为绝不超过的外部账单硬上限。

超时新补丁若使用 `fail_model_call(..., charge_estimate=True)`，会保留尝试计数、计入此前预留的输入估算并标记 usage 不确定；未知输出和真实费用仍未知。该估算必须和 provider 返回的 token 数区分。未定价成功响应的价格同样是不确定结算。共享子进程预算会拒绝把这种不确定结果当成精确收据。

本次 40 adapter calls / 1,000,000 tokens / 内部 $5 guard / 1200 秒 deadline / max output 8192 均是有限控制，可以在不杜撰价格、不关闭 guard 的前提下运行。它们不保证一定完成 40 次调用：先触及任一边界就应收尾。40 是 adapter 调用计数，内部 HTTP 重试可能造成更多网络尝试；建议显式记录或限制 `modelMaxRetries`，不能把它等同于最多 40 个 HTTP 请求。

## 新 harness 的只读审阅结论

符合本次实验边界的部分：复用实际保存的历史 messages；目标仓库固定；不复制凭据；只写安全白名单配置摘要；不改持久用户配置或价格；MCP 和用户交互关闭；工作状态隔离；工具 wrapper 调用原工具且只记工具名/成功标志；外层 1260 秒 alarm 与 runtime 1200 秒 deadline 都有限。提示词要求真实修改、测试和 Git/PR 交付，并允许认证阻塞时诚实报告。

开跑前建议核对下列具体点，均已通知主线程：

1. **有效预算优先级。** `AgentTurnBudget.from_runtime` 的环境变量和 flat runtime 字段优先于 nested `agentTurnBudget`。仅修改 nested 字段不保证实际额度。离线断言有效值为 40 / 1,000,000 / 5，并保存 actual runtime 的安全摘要；不要在不检查的情况下宣称这些上限已经生效。
2. **保存两种 usage。** 在成功 provider usage 汇总外，增加 `runtime.agent_budget.snapshot()` 的安全字段与 model failure/不确定性说明。当前 summary 的 input/output 来自成功事件，不能体现超时输入估算；snapshot 也不是 provider 账单。`AgentBudgetSnapshot` 本身没有公开 uncertain 字段，不应虚构字段或把所有 fallback 估算标为权威。
3. **重试与模式记清楚。** 白名单 `.get` 输出 null 不能代替有效默认。记录 `non-streaming`、180 秒、1200 秒，以及有效 retry 值；如需使 wire 尝试更可控，可在本轮内存里显式设置 0 或 1 次内层重试。
4. **真实 GitHub 交付。** push 仅允许 origin；此前目标记录的 origin 是本地镜像 `/private/tmp/codeloop-live-pr-20260918`。开跑前应只读确认当前 remote，不能把推到本地镜像当成推到 GitHub。当前 callback 不允许修正 remote，若仍指向本地需要主线程明确处理或如实标记交付阻塞。`gh pr create` 最好强制明确 `--repo zrb3052796119/CodeLoop`，避免未带参数时依赖本地推断。
5. **提交范围。** edit 与显式 git add 都允许整个隔离目标仓库；commit 禁止 amend/no-verify，但仍允许 `-a` 或额外路径。这里不是严格“仅相关两文件”的机械约束。提交前核对实际 staged diff；如需 callback 本身约束，应限定实际修改清单并拒绝 `commit -a`。
6. **可执行验证入口。** 复用 permission helper 允许 `ruff check`，不允许 `python -m ruff check`。这可能让正常项目用法遭拒；可明确允许同样只读的模块形式，或保证 agent 可找到已安装 ruff 可执行文件。
7. **成功口径。** harness exit 0 仅代表 Python 外层未抛异常。最终完成与否仍应读 `task.outcome`、实际测试输出、commit 与远程 PR；不能把 host fallback 文本或命令未抛异常当成交付完成。

这些建议不要求新模型调用，也不构成对现有目标修复的人工代写。本轮独立验收仍应在模型停止后冻结 diff/测试并执行原样反证。

## 证据入口

- `transport-budget-evidence.json`：上述三轮调用计数、耗时、usage 与本地 fallback 公式结果。
- `control-fields-before.json`：当前 request 控制白名单快照，非历史追溯。
- `../continuation/review-followup/live-run/summary.json`、`../continuation/review-retry/live-run/summary.json` 与其 journal：两次原始失败记录。
- 代码：`minicode/openai_adapter.py`、`minicode/agent_runtime.py`、`minicode/model_registry.py`、`minicode/config.py`、`minicode/model_call_control.py`、`minicode/agent_budget.py`、`minicode/cost_tracker.py`、`minicode/pricing.py`、`scripts/run_north_star_live.py`。

## 开跑前更新

主线程已落实并由本次只读脚本检查确认：`modelMaxRetries=1`；配置安全摘要和真实 runtime 初始 budget snapshot 双断言 40 / 1,000,000 / 5；finally 写出 `budgetSnapshot`，单列 `failedModelCallsWithoutUsageReceipt` 并声明估算不是账单。`effective-budget-preflight.json` 与更新后的 `run-scope.json` 已保存。保持非流式、不设置 thinking、全局 deadline 1200 秒。

主线程另外报告已通过原生 `git remote get-url` 确认当前 origin 为 `https://github.com/zrb3052796119/CodeLoop.git`；此前本地镜像疑点已解除。本子任务没有再次访问目标仓库。上文第 1–4 点据此已解决或明确了口径，不能再把旧 remote 记录称作当前交付阻塞。提交范围、ruff 命令形式与成功判定仍应按实际运行证据核对，不构成未经验证的失败结论。

本报告未对 provider 价格、参数兼容性或服务器状态做在线查询；没有凭有限本地日志给出确定的远端根因。
