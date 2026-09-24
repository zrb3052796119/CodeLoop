# 恢复规则审查:实施记录

日期:2026-09-09。实施 [review.md](review.md) 的建议顺序第 1-3 步;第 4 步(shadow 对照评测)需要真实模型,不在本次范围。本文件记录每项改动、验证结果与剩余边界。

> **第二轮(同日)**:依据 [implementation-review/review.md](implementation-review/review.md) 修复 F1-F7 与低优先级项,详见文末"复审修复(第二轮)"一节。

## 第 1 步:三个误判的修复

### 1a. 不同测试目标归并为一次 confirmed 恢复

`_call_target()`(reflection_evidence.py)此前只从 `command` 提取目标,忽略 `args` 与 `cwd`;`pytest tests/test_auth.py` 与 `pytest tests/test_billing.py` 的目标同为 `pytest`。现在结构化输入中的 `args` 非 flag 操作数(测试选择范围)与 `cwd` 参与目标标识(`minicode/reflection_evidence.py:_call_target`),两条调用不再配对。旧行为保留为 legacy 回退:tool_call 无结构化 input 时仍按原递归 command 查找。

### 1b. 资源指纹 basename 交集当作同目标

新增完整路径身份:`_invocation_path_resources()` 提取整条规范化多组件路径,`_workspace_paths_equivalent()` 只承认相等或绝对/相对后缀关系。`_same_operational_objective` 与 `_same_generic_tool_objective` 在两侧都带目录上下文时改用完整路径判定;basename/last2 交集仅在单侧缺目录上下文时作为 fallback。`backend/tests/test_config.py` 与 `frontend/tests/test_config.py` 不再配对;`/repo/tests/test_auth.py` 与 `tests/test_auth.py` 仍然配对。

### 2. 工具成功不等于验证通过

`_verification_result()` 增加 `N error(s)`、`failures=/errors=N` 计数模式(reflection_evidence.py)。`1 passed, 1 error in 0.15s` 判为 failed;`no errors`、`0 errors` 不受影响。pipeline 掩蔽场景不再产生 passed verification,claim 层不再确认。

### 3. 共享 validator 的关系缺口

规则生成器内部的三个关系检查提取为模块级共享函数(`_passed_verifications_after_recovery`、`_verification_revidence_matches_recovery`、`_verification_reproduces_earlier_failure`,reflection_synthesis.py),规则生成器改为薄委托。`ReflectionClaimValidator` 对 confirmed 的 `recovery`/`root_cause` claim 新增准入检查:

- 引用的 passed verification 必须与引用的 recovery 建立关系(直接 call 关联 / full scope / 红绿复跑 / 文本重叠)且顺序在恢复之后;
- 无 recovery 引用的 confirmed recovery 拒绝(`confirmed_recovery_missing_recovery_reference`);
- 关系不成立拒绝(`confirmed_recovery_verification_unrelated`)。

规则与模型候选现在走同一关系检查:同一证据不再出现"规则 inferred、直构 LLM 候选 confirmed"的分叉。

### 回归用例

新增 [tests/test_recovery_misassociation_regression.py](../../tests/test_recovery_misassociation_regression.py):四个探针反例 + 路径等价边界 + validator 两侧一致性(规则与直构候选对无关 verification 得到同样拒绝)。

## 第 2 步:保留调用参数与 diff 的有界证据输入

- `_call_invocation()` 渲染 `timeout`(与其他 budget 参数同类的标量输入);`(cwd: X; timeout: N)` 形式,无 cwd 时保持旧格式。timeout=1 → 120 的重试不再折叠为"未变化重试"。
- `ToolEvidence` 新增有界 `invocation` 字段(`_extract_tools` 填充);LLM envelope 的 tools 投影输出该字段(reflection_llm.py)。
- envelope 的 recoveries 投影新增 `change_summary`(diff 摘录)。
- `extract_tool_file_roles` 对 command 工具同样解析结构化 `args` 中的路径——此前 `command="pytest", args=["tests/test_b.py"]` 形式会使目标文件从证据中完全消失(实施中发现的既有缺口)。
- explicit `recovery`/`fix` 事件支持 `verification_call_ids` 声明;提取器仅保留 trace 中真实产生 tool_result 的 call(有界、过滤)。

## 第 3 步:按恢复片段的生成与选择

### 摘录与正文分开(3a)

- `ReflectionClaim` 新增 `evidence_quotes`(可选字段,向后兼容存量 capture 回放)。
- LLM 输出 schema(calibrated)增加该字段;parse 层逐字核验每条摘录,不在证据文本中的摘录拒绝整个候选(`evidence_quote_not_grounded`)。
- `ReflectionClaimValidator._validate_statement_alignment` 对带摘录的 claim 独立复核摘录真实性,通过后跳过"statement 必须逐字包含原句"的限制——经验正文允许抽象,可逐字核验的部分由摘录承担。无摘录的 claim(规则生成器)维持原逐字路径。

### RecoveryLessonAgent(3b)

新增 [minicode/recovery_lesson.py](../../minicode/recovery_lesson.py):

- `extract_recovery_episodes()`:两类片段——已建立关系的 recovery(含 linked checks,`causal_status=verified`)与规则路径拒绝确认的变化输入重试(含 transient/timeout 案例,`causal_status=candidate`)。上限 10 个片段、每片段 4 个调用。
- `episode_to_task_evidence()`:片段投影为子 TaskEvidence。
- `RecoveryLessonAgent`:复用 `LLMReflectionSynthesizer`(独立上下文、结构化输出、预算/超时/取消),逐片段调用,候选过统一 validator + value gate;输出 `RecoveryLessonResult`(片段、逐片段 outcome、通过准入的 lessons)。candidate 片段在子证据中无 recovery 记录,confirmed 候选被统一准入拒绝,只能以 inferred/观察形式存在——与报告"不应自动上升为因果结论,但应保留给学习器分析"一致。

测试:[tests/test_recovery_lesson.py](../../tests/test_recovery_lesson.py)(timeout 片段提取、verified 片段、真实摘录的抽象正文接受、伪造摘录拒绝、candidate 片段无法升为 confirmed、按片段 abstain 记录)。

## 配套的评估 fixture 更新

[tests/fixtures/reflection_claim_precision_holdout](../../tests/fixtures/reflection_claim_precision_holdout) 中 `gap-recovery-105/106` 原依赖"unlinked targeted check 由 LLM 确认"的旧行为(即第 3 项修复关闭的漏洞)。现 trace 中的 recovery 事件声明 `verification_call_ids`,由规则直接确认(`rule_sufficient`);manifest 的 real_ab 集合以 root-cause-107 与 multiple-errors-111 替换两 case。`test_scripted_gap_fill_counts_at_least_two_correct_rule_gaps` 改为断言 gap fill 无 false positive / 无 regression,且两个 case 由规则确认。

## 验证

- 报告列出的 7 个测试模块 + 新增回归测试:**138 passed**(128 原有全保持)。
- 更广 reflection 面(evidence/llm/capture/evaluator/replay/precision/shadow/value 等 26 个模块):**576 passed**。
- memory 持久化面(e2e/audit/atomic write/write contract/persistence boundary):**172 passed**。
- 全量收集 10035 项无错误。
- 两个探针(evidence_probe / synthesis_probe)终态:
  - args 不同目标 + 无关编辑:0 recovery、无 persistable claim;
  - 同 basename 不同目录:0 recovery;
  - 混合 `1 passed, 1 error`:verification 判 failed、无 persistable claim;
  - timeout 变化:规则无 recovery(符合),片段层保留 candidate episode(Phase 3 测试锁定);
  - 语义等价改写(无摘录)仍拒绝;带真实摘录的抽象正文接受(新通道);
  - 无关 verification 的直构候选:拒绝(`confirmed_recovery_verification_unrelated`)。

## 剩余边界(未在本次覆盖)

1. **第 4 步 shadow 评测**:错误经验接受率、有效经验召回率、适用范围需要在固定评测集 + 真实模型对照后测量;`RecoveryLessonAgent` 尚未接入 agent_reflection 的生产选择路径,切换以评测达标为前提。
2. **无依据推广**:逐字摘录 + 引用正确仍不能证明 statement 新增结论(如"还能解决所有分布式数据库缺陷")受到支持。结构层无法判定语义支持;需要报告所述的逐结论语义审查与跨任务验证。
3. 混合输出中非计数的 error 形态(如 cargo `error: could not compile` 在进程成功时的 pipeline 掩蔽)仍依赖文本推断,识别不了时维持现状语义(unknown/passed by status),未进一步收紧。

## 复审修复(第二轮)

依据 [implementation-review/review.md](implementation-review/review.md),按其建议顺序先修 F1/F2/F3,再补片段证据与结果合同(F4-F7)及低优先级项。

### F1:摘录通道的状态继承

`ReflectionClaimValidator._source_statuses()` 对带 `evidence_quotes` 的 claim,decision 状态按 `evidence_ids` 引用继承,不再以"正文包含原句"为条件。改写 + 真实摘录 + confirmed 现在与逐字对照一样被 `epistemic_status_overclaim` 拒绝;root_cause 变体与生产 `mode=llm` gap_fill 路径同步闭合(quote probe:`selection_source=rule`)。

### F2:目标身份

- cwd 从资源指纹剥离(`_invocation_command_source` 剥掉渲染尾部的 `(cwd: ...; timeout: N)`),不再是可交集的"共同资源"。
- 绝对路径保留前导 `/`;相对路径以锚解析(`_anchored_paths`):锚 = 结构化 cwd ∪ 本调用文本中出现的绝对路径及其父目录(覆盖 `bash -lc "cd /ws && cmd rel"` 形态的包装重试)。同拼写相对路径在不同 cwd 下不再相等;无锚相对路径进入独立 `rel:` 命名空间,永不与绝对路径相等;后缀猜测(endswith)完全移除。
- 唯一结构化例外:失败调用可证实目标不存在(`FileNotFoundError`/`does not exist`/中文等价)且成功侧是同一相对路径的**加前缀重定位**时,视为修正(`_is_missing_target_failure` + `_relocated_path_matches`)。断言失败的换文件重跑(报告反例)不适用此例外。`src/auth.py → backend/src/auth.py` 的 read_file 修正依赖此例外保留。

### F2b:选择范围

`_invocation_selection()` 提取选择 flag(`-k/--keyword/--marker/--deselect/--ignore/--spec`,含 `=` 与分离两种值形态);`-m` 因 `python -m` 的解释器语义歧义被有意排除(漏报优先于误报)。`_CallAttempt.selection` 参与 `_call_target` 与 operational/generic 配对:`-k=expired` 失败、`-k=valid` 通过不再配对;同值 `-k` 加 `-q` 之类的展示 flag 变化仍然配对(回归测试双向锁定)。

### F3:红绿复跑的目标核对

`_verification_reproduces_earlier_failure` 重写:failed 与 passed 检查通过各自 call 的 invocation 建立目标身份(`_verification_target_matches`:selection 相等 + 锚定路径等价,无路径时比较可执行头)。任一侧 invocation 缺失时红绿不成立(无法确定即保持候选)。auth 失败 + billing 通过不再构成红绿;rule 生成器同场景降级 inferred。

### F4:candidate followup 按操作对象筛选

`extract_recovery_episodes` 的 candidate feed:后续调用须与失败调用共享业务 token(可执行名/操作数,`_invocation_operands` 交集非空),`git status` 之类无关成功调用不再抢占;保留有界的多个相关重试(`_EPISODE_MAX_FOLLOWUPS=2`)供模型判断。

### F5:子证据投影的最小完整证据链

`episode_to_task_evidence` 保留:linked passed 检查、recovery 之前同 kind 的 failed 检查(含其 invocation 所在的 tool_calls,`_episode_failed_check_context`)、episode 调用关联的 `referenced_files`。验收以"投影前后同一候选准入一致"为准(回归测试 + 探针 `parent_valid == sub_valid`)。

### F6:claim ID 命名空间与逐片段结果

通过的 lesson 重写为 `"{episode_id}:{claim_id}"`;`EpisodeLessonOutcome.persistable_claims` 填充该片段被接受的 lesson,`RecoveryLessonResult.lessons` 与之一一对应。跨片段聚合后 ID 唯一(探针:2 episode 各 1 lesson,dict 去重后 2 条)。

### F7:invocation 可作为摘录

- parse 层 `_claim_evidence_texts` 与 validator quotes 核验来源均加入 `ToolEvidence.invocation`。
- validator 新增 `tool_invocations_by_event` 索引,并在 `event_types` 中把 tool call/result 事件标记为可引用类型(修复引用 tool 事件被 `invalid_evidence_reference` 误拒)。

### 低优先级项

- `LLMSynthesisAttempt.model_called`:真实到达 provider 的调用才置位(预算拒绝、envelope 拒绝为 False;timeout/provider error 为 True)。`RecoveryLessonResult.llm_called` 按此计数。
- `RecoveryLessonResult.observations`:通过 validator 但未持久化的 claim 作为非持久化观测保留(candidate episode 的 inferred warning 等),带 episode 命名空间。

### 验证(第二轮)

- reflection 全测试面(26 模块):**589 passed**;memory 持久化面(7 模块):**172 passed**。
- 三个复审探针终态:identity 4 反例全部不确认;quote 探针 F1/F3 全部拒绝且生产路径回落 rule;episode 探针 F4-F7 全部符合。
- 首轮探针(evidence/synthesis)复验:行为不变。

### 剩余边界(第二轮后)

1. shadow 对照评测(第 4 步)仍未进行,`RecoveryLessonAgent` 仍未接入生产选择路径。
2. 逐字摘录 + 引用正确仍不能证明 statement 新增结论的语义支持;`-m` 选择变化目前不参与配对(保守漏报)。
3. 无 invocation 的历史 trace 中红绿关系不可判定,统一按候选处理(确认降级),语义上偏保守。

## 复审修复(第三轮:统一目标合同)

依据 [second-fix-review/review.md](second-fix-review/review.md),按其建议把**操作目标、验证范围和错误关联**收敛为一个合同,供抽取器、validator 和片段筛选共同使用;关系筛选先于限量。

### CallObjective 合同

`minicode/reflection_evidence.py` 新增 `CallObjective`(`call_objective()` 解析、`objectives_same_target()` 匹配):

- **可执行**:wrapper(`bash -lc`)/`VAR=x` 前缀/`cd X &&` 消费后取头;`python -m <module>` 组合规范化(runner 模块名),`test_runner` 等别名对齐,vendored bin 取 basename。
- **角色分离的路径**:位置参数与 `::node` 的 path 部分为输入目标;`--junitxml`/`--html`/`--report`/重定向目标为输出工件(不参与目标匹配);inline `cd` 目录与结构化 cwd 为锚。
- **选择范围**:`-k/--keyword/--marker/--deselect/--ignore/--spec` 与 `::node`;`-m` 按**程序位置**分类——紧随 python 解释器的是模块 flag,runner 上下文中的是 marker 选择,无法判定的置 `ambiguous_selection`,任何配对降级为候选(不确认)。
- **匹配语义**:可执行相同 + 选择范围相同(无歧义)+ 输入目标锚定后**覆盖**(通过侧至少覆盖失败侧命名的每个目标)。共词、共享输出、共享 cd 一律不构成关系。

消费方:`_CallAttempt.objective`(抽取器)、`_same_operational_objective`/`_same_generic_tool_objective`(规则路径)、`_verification_target_matches`(红绿)、episode followup 筛选。generic 的 token-overlap fallback 收窄到"两侧均无文件身份"的结构化工具。

### missing-target 例外绑定缺失对象

`_missing_object_is_requested_target()`:错误消息中被报告缺失的对象必须就是请求的输入目标(缺失路径 == 请求路径,或请求路径逐字出现在缺失语境旁)。缺 fixture/config 的 FileNotFoundError 不再许可重定位。例外同时接受"缺失后的默认范围重试"(`list_files {"path": ""}`,维持 North-Star live shape)。

### 红绿链闭合

`_verification_reproduces_earlier_failure` 接收 `related_errors`:red side 的 call 必须属于 recovery 关联的错误——auth 的恢复不能借用 billing 的"失败→通过"。两侧 invocation 经 `_objective_from_rendered`(还原渲染 cwd 为锚)走同一 `objectives_same_target`;dropped-file、node-selector、单侧无路径等范围漂移全部拒绝。

### 片段筛选先关系后限量

- candidate followup:`_same_operation_objective`(共享合同)判定相关性后才应用 `_EPISODE_MAX_FOLLOWUPS`;同解释器的无关脚本(`python scripts/cleanup.py`)不再占用配额。
- `_episode_failed_check_context`:先保留绑定到 recovery 关联错误的 red side(不受噪声挤占),再以同 kind 的其余失败补足限额。

### 验证(第三轮)

- reflection + memory 全测试面(33 模块):**770 passed**(含第三轮新增 8 个回归)。
- 第三轮探针:identity 4 反例(marker、inline cd、junitxml、missing fixture)全部不确认;grounding 7 组红绿对照全部符合期望(含正确对照通过),unrelated red check 拒绝;episode F4 真实重试保留、F5 parent/episode 准入一致。
- 第一、二轮全部探针复验:行为不变。

### 剩余边界(第三轮后)

1. shadow 对照评测(第 4 步)未开展;`RecoveryLessonAgent` 未接入生产选择路径。
2. `--lf` 等依赖运行历史的 flag 未纳入选择集合(无法静态判定,保守漏报);摘录正确的语义推广审查仍待评测驱动。
3. invocation 无从解析的极端 shell 形态(多层嵌套、命令替换)会留下空 objective,一律按候选处理。

## 复审修复(第四轮:确认入口统一走完整合同)

依据 [objective-contract-review/review.md](objective-contract-review/review.md),按"所有确认入口经过完整合同;明确冲突不能被 fallback 覆盖;解析未知不能升级确定性"收敛。

### 三态关系:match / conflict / insufficient

`objective_relation()` 取代布尔匹配。`objective_scope_conflict()` 定义**不可覆盖的冲突**(选择范围不同、可执行不同、无法分类的选择、未识别参数差异)——任何例外与启发式都不得推翻;`insufficient`(双侧无文件身份)是 fallback 唯一可介入的状态。`_proven_path_exception()`(重定位/默认范围)只放宽路径关系,且仅当缺失对象被证明为请求目标;`echo done` 之类可执行不同的"默认范围"不再是修正。

### 主抽取分支接入合同

`_derive_recoveries` 的同目标判定从截断的 `target` 字符串改为 `objective_relation(...) == "match"`(target 仅用于展示文本)。第六个文件被丢弃、`-m` 在 token 截断之后变化,都不再隐藏。

### 未识别参数逐字保留

展示白名单(`-q`/`-v`/`--tb` 等)外的 flag 以 `-flag=value` 形式进入 `unknown_flags`:两侧一致视为无害,任何差异(新增/删除/改值)→ 冲突 → 候选。真实 `--ignore-glob` 排除失败测试的案例不再确认。唯一豁免:UsageError 类错误点名的无效参数(移除它是证明过的修正,如 `--bad-option`)。`--lf` 这类历史依赖的 flag 按未知处理,测试期望同步更新(不再称"展示参数")。

### 缺失对象绑定收紧

删除"请求路径出现在错误全文任意位置"的通道(真实 traceback 天然包含测试路径);仅接受缺失语句旁(前向与后向两种形态)提取的路径等于请求目标。真实 pytest traceback + fixture 缺失的重定位不再确认。

### 红绿归属来自 evidence 自身

红绿的 red-side 范围从 `recovery.related_error_ids → evidence.errors` 权威派生,claim 的 `related_error_ids`(替换/增删/置空)不再影响;无法绑定失败调用(call_id 缺失)时红绿不成立,不再回退全任务搜索。validator 的关系检查同样改用 recovery 派生的错误集合。

### 片段合同补齐

- `call_objective_from_rendered()` 成为渲染 invocation → objective 的唯一适配入口(还原渲染 cwd 为锚);validator 与 episode followup 共用,其他目录的同名调用不再挤掉真实重试。
- episode 投影为**每一条保留的 verification** 补齐其 invocation 所在的 tool_call;截断只作用于保留多少条检查,不作用于已保留检查的证明链。

### 验证(第四轮)

- reflection + memory 全测试面(33 模块):**780 passed**(含第四轮新增 9 个回归)。
- 第四轮探针:consumer 7 反例(含两个真实 pytest 运行)全部不确认;parser 的 `--ignore-glob` 真实案例合同拒绝(inline-cd 负对照保持);claim 四变体(auth-only/加 billing/替换/置空)与 unbound error 全部拒绝;episode cwd 真实重试保留;第五条检查父/子准入一致。
- 第一、二、三轮全部探针复验保持。一个良性行为变化:`1 passed, 1 error` 场景的 generic 证据记录因 `tail -20` 的未识别参数差异不再生成(该记录本就不可持久化)。

### 剩余边界(第四轮后)

1. shadow 对照评测未开展;`RecoveryLessonAgent` 未接入生产选择路径。
2. 语义推广的逐结论审查仍待评测驱动;`--lf` 等历史依赖参数按未知处理属保守候选(非漏报免责)。
3. 极端 shell 形态(多层嵌套、命令替换)留下空 objective 时按候选处理。

## 直接实施修复（第五轮：T1–T5）

依据 [three-state-review/review.md](three-state-review/review.md)，本轮直接完成五类问题，并经独立复查补齐相关边界。详细记录与输出见 [five-issue-fix/completion.md](five-issue-fix/completion.md)。

- **T1 参数边界**：抽取器优先使用结构化 argv；只在明确 shell wrapper 的脚本参数边界继续解析，保留完整带空格的选择表达式。展示参数按具体程序声明零参数/单参数语义；未知参数保留值、顺序和重复项，不再吞掉位置目标。
- **T2 实际执行**：help/version/collect-only 不提供 passed verification，区分 `-V` 与 `-v`；无法解析、畸形引号、包含截断标记的调用保持 unknown。`pytest -q` 带目标或选择条件时标为 targeted。
- **T3 路径例外**：绑定具体缺失对象，只允许重定位该对象，其他目标必须保留覆盖；默认范围仅允许明确的 list_files 空路径语义。
- **T4 参数豁免**：要求独立顶层诊断或匹配外层程序的执行前 usage/error 包络；嵌套 helper、captured stderr、已执行测试的结果不能豁免有效范围参数。
- **T5 证明链**：逐 recovery 获取自身错误和后续验证，阻止多个 recovery 混用时间条件与错误内容。

验证：38 个相关模块 **841 passed**；新增三个模块共 **43 项回归**，同一套测试在本轮真实修复前源码快照上为 **27 failed、16 passed**，当前全部通过。**13 组历轮探针**复跑，本轮和先前修复的反例均拒绝，合法对照保持；Ruff F 类检查通过。独立最终复查确认 `-V`、不可解析/截断调用与范围分类边界关闭。

未调用真实模型或写入正式记忆。RecoveryLessonAgent 生产接线、shadow 和逐结论语义支持评测仍属后续工作，未把这些已披露边界计为本轮完成内容。

## 2026-09-09：RecoveryLessonAgent shadow 接线与真实有界评测

完成默认关闭的独立生产 shadow 分支、专用 episode 上下文/指令、每任务调用上限、nullable 计量、隐私摘要和可复跑的 same-episode Rule 对照评测器。相关面 43 模块 **973 passed**，本阶段新增 69 项测试；包含真实 MemoryPipeline 先提交再调用、完整记忆内容不变、用户 `.env` 读取、预算/超时/取消与部分报告。

离线固定合成数据：11 cases、10 episodes，10 scripted / 0 provider，Agent 与相同子证据的 Rule 均准入 6 条。真实网络外合计 10 次请求尝试：9 次 15 秒 deadline；剩余 1 次放宽到 60 秒后约 23.9 秒返回，但正文为空（provider usage 为 1933 input、1200 output，恰达输出配置上限）。没有可解析 claim，无法判断语义质量或收益；失败请求用量未知，总费用保持未知。没有改用户模型配置、开启新 shadow 开关或把 Agent 接入正式记忆选择。

详细实施、验收与报告见 [shadow-evaluation/completion.md](shadow-evaluation/completion.md)。下一步应先修正当前反思模型的响应预算/正文输出配置，再在同一数据上完成有效输出与逐结论语义对照，不能以本轮结构测试通过代替模型质量验收。

## 2026-09-09：反思模型超时与空正文修复

上段记录的模型阻塞已经修复。专用反思 client 原来未使用已声明支持的 low reasoning 和 JSON-object 控制；当前 GLM-5.3-Flash 默认 max 思考与 15 秒 / 1,200 token 的反思预算不匹配。现按适配器能力传递请求控制，保持零重试及无工具；普通 Agent 请求和用户配置不变。补齐停止原因与思考存在性诊断（不保留思考正文），区分截断与真正空输出；提供方局部超时不再被误当成父任务截止。生成 schema 同时声明原有任务标题和 outcome 的宿主固定值，严格 parser 不放宽。

相同预算真实复跑：**10 / 10 片段返回并解析成功，超时、空正文、截断均为 0**，中位 8.77 秒、最长约 12.03 秒；6 条结构准入（4 recovery/confirmed、2 warning/inferred），4 候选只保留观察。逐条复核后又修复候选片段漏掉后续失败复跑的问题，按同目标与错误链时间窗口补齐失败检查和调用证据；定向 2 / 2 真实复跑通过，模型正确陈述“仍失败”和未证实因果，两条仍不准入经验。

最终相关 51 模块 **1,115 passed**，新增 38 项回归。请求控制同一测试在真实修前快照为 3 failed / 1 passed；失败证据投影同一组为 6 failed / 3 passed，修后全通过。Ruff F、diff check 与独立复查通过。原始完整批次、定向复验及逐条语义裁定独立保留；未把提示词约束当作普遍语义保证，shadow 默认关闭、正式记忆选择保持原路径。

完整原因、变更、测试与剩余表述精度边界见 [model-response-fix/completion.md](model-response-fix/completion.md)。
