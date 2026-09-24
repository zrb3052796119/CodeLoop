# Skill 检索与加载结果独立审阅

本次审阅保留已冻结的 20 题、预期集合和生产源码。作者在冻结后才获准读取结果及生产实现；新增执行仅为无网络的显式标识符解析检查，以及对保存结果和请求证据的离线重算，没有重跑任务或修改标签。

cases.json SHA-256：`c5930a29a4b8289600df4f48eab3caeb8c4a0f15e0c76d9c0b6ed4e153fcb2cc`。

## offline 别名诊断结果

已独立按 cases.json 重算 offline/results.json，结果与逐题 retrieval_strict 一致。offline 为关闭 embedding 的别名诊断，intent semantic classifier 和 candidate reranker 均关闭；它不能代表完整 live 路径。**8/20 仅衡量冻结的 outcome/选择集合条件，不是全部用户约束遵循率。S19 虽计为严格检索成功，真实 loader 的全禁用权限探针已失败。**

| 指标 | offline | 解释 |
| --- | ---: | --- |
| 全部题严格检索 | 8/20，40.0% | 必需组齐全、无禁止项且 outcome 正确 |
| 正例严格检索 | 6/17，35.3% | S01、S02、S09、S11、S13、S15 |
| 正例候选覆盖 | 7/17，41.2% | 加上 S04，但 S04 含不合理候选 |
| 正例最终选择覆盖 | 7/17，41.2% | 此轮候选与选择集合一致 |
| 负例严格检索 | 2/3，66.7% | S19、S20；S18 误触发 |
| 系统提示投影一致性 | 20/20 | 只证明既定投影规则成立，不证明候选正确 |
| 本地 loader 可读及正文 hash | 14/14 次 | 脚本调用，不能算模型自发加载 |

自动 summary 的 `selected_coverage=10` 和 `candidate_coverage=10` 包含三道负例的空必需组真值。S18 即便误触发也有 coverage=true，因此不得把 10/20 称作正例召回率。`coverage()` 的空组行为来自 evaluator.py:62；严格评分另检查 allowed_skills，正确排除了 S18。

审阅的 offline/results.json SHA-256：`296da9922f418f1f057644f433921c7380451192e29cce9971adc75f06e48ef1`。

## 主要检索问题

| 题目 | 观测 | 判断 |
| --- | --- | --- |
| S03、S06、S08、S12、S16 | intent 为 unknown，路由说明 no-affirmed-operation | 自然教学、安全审计、上下文并发诊断、结构整理及测量表达未形成可路由操作；不能简单归因于分数不足 |
| S04 | README 加上 TDD | 入门使用例子被引向测试流程；覆盖了必需项但严格失败 |
| S05 | 选择依赖管理 | F401/E722 清理被误导到依赖/配置，不是静态检查技能 |
| S07 | 选择 TDD | 已有 pytest 断言失败被当成测试先行实现，缺少必要 pytest 调试技能 |
| S10 | 主意图 code.delete，无匹配 | 缺陷背景中的删除动作压过了“先复现测试再修复”的任务目的 |
| S14 | clarify，explicit-skill-unresolved | 完整名称识别成功，同时生成伪未知名 code-，见下文 |
| S17 | design-review 与 README | 文档步骤存在，但通用设计审查不能替代安全审计 |
| S18 | safe-refactor 与 TDD | 文案中引用的技能名污染任务解释，见下文 |

S08 已由 evaluator 构造 IntentContext 并传给 route_skill_turn，不能把其失败归因于评测遗漏了对话。结果中的 authority.context_used 仅表示 Skill 指令权威的上下文使用，不等于完整任务语义上下文是否被读取。

## S14：合法显式名称被切出伪未知名称

这不是用户的约束冲突，也不是目录缺少技能。目录中存在 `code-skills/minicode-study`，且 required_operations 为空；题目本身没有禁止该技能所需操作。

实际调用生产纯解析函数 `_positive_explicit_reference_tokens`，原始题目得到：

```json
["code-", "code-skills/minicode-study"]
```

`minicode/skill_router.py:4173` 的中文“标识符 + skill/技能”模式允许标识符与尾缀间零空格，尾部 `skill` 又没有词边界。正则在 `使用 code-skills/minicode-study` 中回溯出 `使用 code-skill`，捕获 `code-`；另一条无标签限定名模式正确捕获完整名称。这两个匹配是同一位置的重叠解释。

`skill_router.py:4223` 逐条收集全部模式结果，仅按 token 字符串去重，没有消除被合法完整名称覆盖的短匹配；`skill_router.py:4311` 找不到 code- 的目录所有者，于是把它加入 unresolved。结果中同时存在正确 requested 和错误 unresolved。

`skill_routing_turn.py:232` 因 unresolved 选择 clarify；`skill_router.py:595` 对整个本轮关闭加载权限。因此即使 live 模型自行找到正确名字，也可能被真实 loader 拒绝，不能把加载拒绝反过来解释为用户请求含糊。

本次不修改生产规则，不替换题目为更易匹配的拼法。冻结输入复核文件 offline/freeze-verification.json 的 unchanged=true。

## S18：显式权威与普通语义路由是不同的误触发入口

S18 的三个英文名仅作为海报标签改写素材，且本轮明确解释“不是在调用”。它不包含真实源码、测试、提交或 README 任务。

offline 的 authority.requested 为空，说明这次并非把名称当成显式 Skill 指令。问题发生在普通任务语义路由：intent 被识别为 refactor.update，safe-refactor 的理由包含 `keyword:safe-refactor`；TDD 则通过 update 相关操作匹配进入选择。

所以只修复显式名称语法不足以解决 S18。解释层需要保留“这些名称是处理对象，而非操作指令”的角色边界。冻结规则下，两项均在 forbidden_skills，故严格失败。

live 若模型忽略这些错误推荐、直接完成中文短语改写，可以记录模型在本观察窗口正确避免了加载，但检索层仍失败。live 若因模型请求错误或超时而没有任何 load，则是不可观察，不能把“没有加载”算成正确负例。

## S19：全禁用指令未进入权限，真实 loader 仍返回正文

原句 `不要加载或使用任何 Skill` 未命中 `_GLOBAL_SKILL_DENIAL_RE`，实际 authority.deny_all=false，prompt_mode=inventory_names。`skill_router.py:4196` 的中文全局否定模式只允许一个可选动作，不支持“加载或使用”这种并列。

该题 offline 仍无选择，是因为没有形成可路由操作。root 随后使用本轮真实 load policy 做了本地强制调用，negative-policy-probe.json 记录：

```json
{
  "mode": "local-forced-denial-contract-probe-not-model-choice",
  "deny_all": false,
  "prompt_mode": "inventory_names",
  "requested_name": "code-understanding/codebase-explanation",
  "loader_ok": true,
  "body_returned": true
}
```

这证明问题已经延伸到实际权限执行层：用户要求禁用所有 Skill，本轮却仍允许目录回退中的技能加载，并返回正文。它比提示模式差异更强，不能写成“全禁用机制通过”。

这仍是**本地脚本强制加载探针，不是模型自发调用**，不能据此声称模型已违反本轮指令。冻结评分只检查选择集合和 outcome，未检查全禁用 load policy，所以维持原 8/20，并把这个权限失败单列，不能临时改标签抹掉问题。live 若模型遵守自然语言而不加载，只能证明这次模型行为符合要求，不能修复已经确认的权限缺口。

## live 复核口径

用户明确授权既定出站后，root 完成了主 live 的 20 题单次运行。此前自动审核拒绝已解决；本独立审阅仍仅离线读取保存证据，没有发起网络调用、重跑模型或修改冻结文件。以下口径用于审阅，实际结果见下一节。

1. 按 17 道正例计算候选和选择覆盖率，并同时报告全部 20 题的严格检索率；空必需组负例不进入召回分母。
2. 从各题 result.json 的 load_calls 验证模型发起、真实 loader.ok、qualified_name 绑定及正文 hash，不能使用 local_loader_contract_checks 替代。
3. 正文送达必须在下一次请求中同时出现原始内部 tool_result 与生产转换后的 provider tool 消息，并有该次 provider 调用完成的记录。model_probe.py:281 和 :367 的确认条件符合此口径。它证明正文到达模型调用边界，不能证明模型按正文完整执行任务。
4. evaluator.py:169 要求存在模型调用且所有已发起调用均完成。负例首调用失败时，model_load_strict 和 body_handoff_strict 应为 false；不得以空集合自动算成功。若后续失败发生在先前已确认送达之后，逐题可保留先前送达事实，整体严格观测仍按冻结逻辑评分。
5. 负例 `body_handoff_strict=true` 的语义是“正确不使用 Skill”，并不意味着发生了正文 handoff。报告正文事件比例时只统计正例，并将负例正确非使用另列。
6. 探针在第一个非 load 工具请求、assistant-only 回复或四次调用预算处停止，不执行真实任务。S16/S17 可能先加载第一阶段技能，等读取/测量/审计后才需要第二阶段。此时只能写“观察窗口未覆盖完整多步骤”，不能写“模型永远不会加载后续技能”或“完整任务失败”。
7. S16 第二步骤还有“逐条转换确实最贵”的条件。冻结评分要求两组得到覆盖；没有执行测量时，不应把未进入该条件分支提升为完整任务行为错误。保留分数，并披露条件和窗口限制。
8. 同理，其他正例先请求 read_file/list_files 而被停止，只能证明本次动手前窗口未观察到 Skill load。探针没有运行真实 AgentLoop 的显式加载 final gate，不应声称验证了该 gate。

## 主 live 最终独立核验

独立脚本 review-validation.py 从原始题目重新计算覆盖/严格分数，并逐条追溯模型响应中的真实 load_skill 请求、实际 loader 输出、正文 SHA-256、后续内部 tool_result 和 provider tool 消息，以及后续调用是否完成。没有仅信任汇总布尔值。全部逐题字段与主 evaluator 相符，结果保存在 review-validation.json。

- 完整题目 20 道，正例 17 道，负例 3 道。
- 主 live/results.json SHA-256：`508e712dc86620d6d2e01385cab36991e5229d4e107a7636a03afeb8d7e9a562`。
- 独立核验 290 个冻结输入 hash，均未变化；live/freeze-verification.json 也为 unchanged=true。
- 主 live 使用既定 GLM 模型与 embedding；意图语义分类器和候选 reranker 保持关闭，模型探针保持原有零重试配置。路由日志保存 14 组 skill-routing 的 started/completed 事件；20 题的最终选择集合均与 offline 相同。

| 指标 | 主 live | 与 offline 的差异/边界 |
| --- | ---: | --- |
| 全部题严格检索 | 8/20，40.0% | 相同，不能升级解释为全约束遵循 |
| 正例严格检索 | 6/17，35.3% | 相同 |
| 正例候选覆盖 | 7/17，41.2% | 相同；未混入负例空组 |
| 正例最终选择覆盖 | 7/17，41.2% | 相同 |
| 可观察模型响应 | 18/20 | 全部 17 正例可观察；S18、S20 超时 |
| 正例模型自发加载严格 | 10/17，58.8% | 主观察窗口内的成功；offline 未运行模型，不能作零分对比 |
| 全部题模型行为严格 | 11/20，55.0% | 10 正例正确加载，加 S19 正确不加载 |
| 正例正文 handoff 严格 | 1/17，5.9% | 仅 S15，存在真实后续调用证据 |
| 全部题 body_handoff_strict 字段 | 2/20 | S15 正文送达，加 S19 正确不使用；绝不是两次正文注入 |
| 全部题 end_to_end_strict 字段 | 2/20 | 同为 S15、S19，表示冻结的联合判断，不是完成真实任务 |

主 live 一共观察到 **13 次模型发起的 load_skill 尝试，其中 12 次真实 loader 成功，12 份正文 hash 全部正确**。S14 的一次尝试被真实权限拒绝。12 次成功加载中，只有 S15 有已完成的下一次模型请求携带正文；其余 11 次均在同批次遇到非 load 工具请求后被观察窗口截断。

### 模型能补回部分检索漏召回，但不能替代检索分数

S06、S08、S10、S12 的路由均没有选择技能，使用名字目录回退；模型分别自发加载了安全审查、并发诊断、缺陷回归与安全重构，覆盖了冻结必需组。这解释了正例自发加载严格 10/17 高于正例严格检索 6/17。四题的检索结果仍失败，不能因模型补救而改写检索分数。

S07 则加载了误推荐的 TDD，缺少必需的 pytest 调试技能，属于实际观察到的不合预期加载，而不是缺少后续 handoff 才导致失败。S03、S04、S05 先请求查看仓库，没有在窗口内加载；只能记录未观察到先行加载，不能断言后续永远不加载。

### S14、S18、S19 的 live 证据

- **S14：确定是系统挡住了正确模型选择。** 第一次模型调用只请求 `load_skill(code-skills/minicode-study)`；真实 policy 的 allowed_bindings 为空，reason_code=explicit-skill-unresolved，loader 返回失败。第二次模型调用收到拒绝后请求 ask_user。正确名称始终存在，不能归因于模型没找到技能、用户名含糊或用户操作约束冲突。
- **S18：确定检索误触发，模型行为不可判断。** 选择仍为 safe-refactor 与 TDD。主模型首调用在约 30 秒触发 ModelCallDeadlineExceeded，无已完成响应、无 load 记录。model_load_strict=false 与 body_handoff_strict=false 是保守且正确的计分；不能用空集合给负例成功，也不能声称模型真的误加载了这些技能。
- **S19：本次模型正确不用技能，权限漏洞仍存在。** 模型直接返回所需 Python 表达式，没有工具请求。它是负例中唯一已观察到的正确不使用。真实 policy 仍为允许 17 项的 inventory-fallback，与先前强制 loader 探针的缺口一致。模型遵从本轮自然语言不能替代权限层的强制禁用。
- **S20：普通闲聊的模型行为不可观察。** 检索正确无技能，但主模型首调用同样在约 30 秒超时，不能计作成功不加载。主结果保留单次零重试记录，不通过重跑挑选更好的负例分数。

### 多步骤与混合工具批次不能被当成完整任务失败

S16 首次响应请求 list_files 与 grep_files，探针立即停止；没有执行耗时测量，更无法确定条件中的逐条转换是否确实最贵。冻结的两组全覆盖在窗口内未满足，应保留分数并标注“完整多步骤未观察”，不能推断最终会漏掉性能分析或后续条件重构。

S17 首次响应加载 design-review 并同时请求 list_files，未继续到正文下一次送达或 README 写作阶段。其安全审计必需技能在检索中缺失，且当前 selected-only policy 不允许加载该技能，这是已确认的上游问题；README 后续步骤是否会加载则未被本窗口观察到。两类问题应分别陈述。

S01、S02、S06、S07、S08、S09、S10、S11、S12、S13、S17 均成功加载后在同批次遇到其他工具而停止。它们不能被报告为“loader 无法加载”或“正文被截断”：正文完整返回给探针，但主窗口没有下一次模型请求证据。后续若用诚实的 blocked 非 load 结果做 controlled-handoff 补充实验，应独立列出受控干预与送达证据，不能回填这次自发窗口分数。

S15 则先以单独 load_skill 调用取得正文，第二次真实模型调用的内部与 provider 请求都携带完整正文，随后请求 git 工具而停止。这是本主窗口唯一确认的正例正文送达；未执行 git 状态、提交或其他任务动作，因此不宣称提交任务已完成。
