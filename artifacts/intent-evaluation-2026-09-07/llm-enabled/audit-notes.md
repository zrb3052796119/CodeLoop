# 开启语义兜底的独立运行审计说明

日期：2026-09-07。独立只读核查生产解析器、语义适配器、模型工厂、预算与持久观测接线；本审计未调用模型提供方，未改生产源文件、运行配置、题库或原始结果。最初冻结清单中的 8 个文件 SHA-256 均仍匹配。本文件说明如何解释主评测产出的 live 结果，本身不是 live 评测结果。

## 忠实运行路径

生产接线见 `minicode/agent_runtime.py:469–505`：

1. 从现有 `load_runtime_config(ROOT)` 得到运行设置，仅将本轮的 `intentSemanticClassifier.enabled` 设为 `True`；首轮保留其他实际设置。不要输出完整 runtime 或 provider 配置。
2. 使用 `create_model_adapter(model=runtime['model'], tools=ToolRegistry([]), runtime=runtime)` 创建实际配置模型；确认没有 `force_mock` 或 `MINI_CODE_MODEL_MODE=mock`。空工具注册表适合本次纯解析评测，因为生产分类器始终强制禁用工具，不需要初始化 MCP、真实工具或 Agent 执行循环。
3. 使用 `build_semantic_intent_adapter(model, runtime=runtime)` 构造生产适配器，并断言结果非空。工厂可能因校准错误返回 `None`，单独修改 enabled 不能证明模型实际参与。
4. 每题从同一冻结 context 构造 `IntentContext.from_messages(...)`。只把当前题、已有上下文传给 `IntentParser(semantic_adapter=adapter).parse(...)`；沿用原 `score` 函数，在解析返回后才读取 golden。
5. 每题单独构造 `AgentTurnBudget.from_runtime(runtime)`，模拟一条独立用户 Turn。为常规 Agent 保留至少 1 次调用，预算至少应容许 2 次；50 题误用一个很小预算会造成后段伪失败。
6. 通过 `observe_run` 获取真正的 `RunObservation`，使用 `RunJournal(ROOT, data_dir=本轮目录/'journal')` 注入隔离的持久存储。可用 `source='unknown'` 和只含 case ID 的 title；`unknown` 为合法来源，可避免 headless/tui 额外的 curator outbox 记录。不要复制测试中的内存 `_Sink` 再声称持久观测。
7. `call_context` 必须传入 `agent_budget`、`event_sink`、`cancellation_token`、`deadline_monotonic`。取消对象可用 `TurnCancellationToken('turn_' + uuid4().hex)`；父 deadline 应明显长于分类器 deadline（例如当前 monotonic + 60 秒）。在解析前断言 `observation_active is True`，退出上下文前保存 run ID，退出后重新读取 journal 事件核验。

`_controls_available` 实际强制核验预算方法和活跃 event sink；取消和父 deadline 在函数签名中允许 `None`，但评测提供完整控制与生产路径更一致。标准模型适配器还必须支持 `disable_tools`、输出 token/字节上限、温度覆盖、零重试、隔离推理状态、取消和截止时间。

## 超时、置信度及调用范围

默认分类器 timeout 为 **1.5 秒**，在一次调用准备好后创建绝对截止时间，并与父截止时间取更早者。标准适配器将网络打开、响应读取与调用后的检查纳入 deadline；并非只限制模型生成时间。默认零重试。分类器自己的超时返回无语义提案，并记录 `timeout`；用户取消或父 deadline 则作为控制流向上抛出。

因此应先完整运行“只开启 + 1.5 秒”的版本。如果出现大量 timeout，可额外运行、单独报告 **10 秒**诊断轮（当前合法最大值为 10 秒）；这个结果属于“开启并放宽超时”，不能覆盖或冒充只开启的结果。不要只重试失败题后混入第一轮分数；整轮或子集应分别标明分母。延迟、提供方负载、请求缓存与请求顺序会变化，两轮不是受控的延迟因果实验。

内置校准为 `bootstrap-conservative-v1`，`productionFit=false`，未声称用真实生产样本拟合。其分箱采用左闭右开（末箱包括 1）：

| 模型原始 confidence | 校准后的 confidence |
| --- | ---: |
| `[0, 0.60)` | 0.45 |
| `[0.60, 0.75)` | 0.58 |
| `[0.75, 0.90)` | 0.72 |
| `[0.90, 1.00]` | 0.82 |

默认 minConfidence=0.72 意味着所有提案的原始 confidence 均须至少为 0.75；多操作响应取最小校准置信度，任一低于阈值则整组拒绝。不应把这个保守分箱值或模型自报 confidence 当成已验证的正确概率。阈值保持不变，不用这 50 题拟合校准后再在原题上宣称提升。

代码已支持复合请求中 **唯一未覆盖、非否定分句**的语义补全（`IntentParser._semantic_fallback_target`），所以模型调用数可能多于 baseline 的 unknown 数量。现有文档的“recognized operation skips semantic inference entirely”描述不完整，应以实际 parser diagnostics 为准。多于一个未覆盖分句时保守跳过。未正确识别的否定也可能抑制调用。

模型只增加允许的肯定意图/动作；不撤销已有规则操作，不产生文件路径/实体、否定操作、权限或高风险动作。已有错误肯定修复/删除等不会因打开兜底自动消失，实体补全仍依赖本地上下文逻辑。补充动作可能通过动作优先级改变 primary，因此即使是追加也需要检查回退题目。

## 统计与证据口径

- 保留 50 题及全部旧 golden，主结果并列报告 `primary_ok` 与 `all_labeled_checks_ok`。后者仅代表全部事先标注约束通过，不代表每个结构化字段完整且精确匹配。
- 对每题报告 old→new、改善/退步/不变、`diagnostics.semantic_status`、规则/语义操作及耗时。不要用 classifier 复用实例残留的 `last_status` 代替该题 diagnostics；跳过题不会调用 `infer` 来重置状态。
- 统计 `skipped`、`accepted`、`timeout`、`low_confidence`、`abstained`、`invalid_response`、`provider_error`、`network`、`control_unavailable`、`budget_exhausted` 等。`accepted` 仅代表通过分类器门槛，不代表 golden 正确；本地 parser 还会二次过滤提案。
- 独立核对每个 `model.started` operationId 有且仅有一个 `model.completed` 或 `model.failed`；成功传输还应有 `model.costed`。按 `purpose='intent_classification'` 计数，重读持久 journal，而不仅相信内存打印。
- 延迟分别给全 50 题与实际调用模型的子集，避免大量毫秒级 skipped 题让总体中位数掩盖模型开销。比较原始 baseline 与本次模型调用次数、成功/失败耗时，说明原 baseline 是纯解析延迟。
- 成功调用区分 provider usage 与估算 usage。发送后失败可能只能保守记录输入 token 估算；无 `model.costed` 的失败不能推断真实免费，未知或未定价成本不能当成零。
- 完成后再次核对冻结文件 SHA-256，记录本轮非敏感配置（启用状态、模型名称、阈值、timeout、输入输出上限、校准身份）和输出文件。不要存储凭证、完整 runtime、鉴权头、provider 原始异常或无必要的原始模型输出。
- 50 道人工合成题属于配对探索性评测，结论限于这组问题；不能直接外推生产自然流量的准确率。

## 实际 harness 与默认轮复核

已只读审查 `llm-enabled/run_live.py`，并在默认轮完成后重读 `default-1p5s/results.json`、`summary.json`、`freeze.json` 与全部 50 题的持久 `events.ndjson`。没有追加调用 provider。

- 实际模型工厂创建 `OpenAIModelAdapter`，未使用 mock；分类器工厂返回非空。该轮配置为既有模型 `glm-5.3-flash`，只开启 enabled，timeout=1.5、minConfidence=0.72、输入输出与校准设置保持默认。
- 50 题的 case、顺序、全部 golden 与冻结的两份题库逐条一致，ID 唯一。runner 直接导入旧 `score`，只在 `parse_intent` 返回后评分。源文件、题库、baseline freeze/results 与 runner 的记录哈希匹配。
- 每题预算、取消 token 与 deadline 均独立；每题活跃观测器确实转发到真实隔离 journal。内存捕获的所有 model events 与逐题落盘 events 内容完全一致。
- 32 题 skipped；18 题发送模型请求，全部 timeout。18 个 `model.started` operation ID 均一对一对应 `model.failed`，没有漏配、重复终止或未结算 token reservation。
- 主意图仍为 26/50，全部已标注检查仍为 23/50；所有题的评分布尔值、issues、只读性均与 baseline 相同。结构化 actual 除 diagnostics 与 timestamp 外逐字段相同，因此该轮无提升并非仅分数巧合。
- `summary.cost_status` 读取 `model.costed.payload.status` 是正确字段，直接对应 `pricing.CostQuote.to_event_payload` 的 `status`。该轮 `cost_status={}` 是因为没有成功传输后的 `model.costed`，不是键名错误。12,561 个输入 token、0 个输出 token 为失败请求的预算保守估算；并非 provider 实报使用量，更不能用 budget 的 0 成本推断实际账单为零。
- 延迟中位数：全 50 题约 4.254 ms，实际模型调用 18 题约 1,507.568 ms；P95 约 1,508.950 ms。旧 baseline 复用 `IntentParser`，新 runner 按生产 `parse_intent` 的有语义适配器路径每题新建 parser，所以全体毫秒级中位数差不能全部归因于 LLM。模型调用子集和超时分布更能表达新增开销。

没有发现影响该轮正确性结论的 harness 错误。若后续轮出现解析异常，注意 runner 在 `observe_run` 内捕获异常，因此 journal 的 `run.completed` 只表示评测病例执行完毕，不能单独视为解析成功；应同时核对 result.actual、issues 和 semantic diagnostics。目前默认轮没有此类解析异常。

## 10 秒诊断轮独立复核

已只读复核 `timeout-10s` 的完整 50 条结果、summary、freeze 与 50 个持久 journal。用独立的字典级评分逻辑重算主标签、肯定/否定要求、动作、实体、只读性及相对顺序；50 条评分全部吻合。题目及 golden 与两份冻结题库一致；生产源、baseline 结果与 runner 哈希继续吻合。该轮改变 enabled 与 timeoutSeconds=10，其余分类器设置保持。

| 口径 | 禁用 baseline | 仅开启，1.5 秒 | 开启并放宽至 10 秒 |
| --- | ---: | ---: | ---: |
| 主意图正确 | 26/50 | 26/50 | 33/50 |
| 全部已标注检查通过 | 23/50 | 23/50 | 31/50 |
| 相对 baseline 全部检查改善 | — | 0 | 8 |
| 相对 baseline 原通过题变失败 | — | 0 | 0 |

8 个严格改善案例为 C02、C06、C09、C14、C27、C28、C29、C45；其中 C45 原主标签已经正确，新增语义配置操作才让全部约束通过。10 条模型接受提案中，另外 C26、C33 的冻结评分仍失败。

32 题 skipped，18 次实际调用产生 10 accepted、7 invalid_response、1 timeout。持久事件与结果中内存 events 完全一致；18 started 均有唯一 completed/failed 配对，17 completed 与 17 costed 的 operation ID 集合一致，无预算 reservation 残留。全部 17 次完成调用的 usage.source 均为 provider。费用事件全部为 `status='unavailable', reason='model_unpriced'`：这表示当前本地价格表没有该模型报价，不能报告为免费。

17 次完成调用的提供方用量合计为 9,919 输入 token、3,306 输出 token；另有 1 次超时保守估算输入 698 token。summary 的 13,923 总 token 混合了这两种来源，应标明该差异。实际模型调用子集耗时中位数约 5.587 秒，整组 P95 约 6.116 秒，最大约 10.008 秒。

### 两个评分之外的变化

**C26 是合理的类别近义，不宜当成未理解。** “这个项目适合用 SQLite 还是 PostgreSQL？说说取舍。”从 `unknown` 变为只读 `review/compare`，没有引入数据库修改。生产分类器 prompt 将 review 定义为评估质量、正确性、风险或备选方案，因此评估两种数据库取舍符合其分类定义。冻结标签仅允许 question/explain，所以正式仍计失败；可另列“事后接受 C26 近义则全部检查 32/50”，不要改写正式 31/50 或修改 golden。

**C33 存在未体现在总分中的主意图偏移。** 原结果保留重构、测试的禁止，并用 `explain/read` 表达缓存命中率下降的原因分析。10 秒轮对未覆盖分句“只阅读现有代码”补入 `search/read`；两个肯定操作同为 read 时，primary 选择更早操作，因此主意图从 explain 变为 search。原 explain 操作仍保留，禁止重构/测试及只读性仍正确，要求的 debug 依旧缺失。这是阅读手段抢占主要分析目标的问题，不能用“0 通过题退步”概括为完全没有副作用。两轮严格本就都不通过，所以总分不会反映这个偏移；原审计把 C33 的 explain 视为可能宽松接受的灰区，也不应自动将该宽松解释沿用到现在的 search 主意图。

### 无效响应与输出上限

7 个 invalid_response 为 C11、C23、C25、C30、C37、C40、C49，全部 `usage.outputTokens=256`，恰好达到本轮输出上限。其中 6 个 `contentPresent=false`（除 C37 外的上述案例）；C37 有内容但未通过格式/协议验证。

这些字段支持“输出预算不足或与模型输出形式不匹配需要后续排查”的判断，但本次没有保存原始 provider 响应、终止原因或推理 token 明细。不能断言 7 个都是同一种 JSON 截断，也不能把推理耗尽预算当作已证实根因。此次诊断保持 256 token 上限，没有改模型、prompt、思考设置或阈值来追求原题分数。

结论：仅打开默认配置在此轮没有提升，原因是 18 次调用全部超时；允许至 10 秒后，严格全部检查由 46% 提升到 62%（增加 8 题、16 个百分点），显示语义补全有用，但时延、输出预算适配和已有规则误判仍明显限制结果。
