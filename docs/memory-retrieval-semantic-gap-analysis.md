# Memory Retrieval Semantic Gap Analysis

> Every entry below is synthetic. Confirmed gaps satisfy the strict Phase 3A definition before being listed.

## Strict Attribution

- Confirmed: `37`.
- Analysis / sealed: `19` / `18`.
- Confirmed categories: `11`.
- Non-semantic candidate misses: `1`.
- Downstream or successful non-gap cases: `34`.

## Confirmed Gaps

### sg-pos-alias-03

- Category / primary: `alias_acronym_equivalence` / `sg-mem-alias-03`.
- Split / scope: `analysis` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 这次按用户验收测试的方式描述行为，不要只列内部单元测试。
- Semantic value: UAT 是用户验收测试的稳定缩写，记忆还限定了该用户采用的外部行为口径。
- Lexical failure mechanism: 中文查询没有英文 UAT token。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-03`.
- Future retrieval value: 报告可能只覆盖内部实现检查。

### sg-pos-alias-04

- Category / primary: `alias_acronym_equivalence` / `sg-mem-alias-04`.
- Split / scope: `analysis` / `project`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 把内容分发边缘节点的缓存失效流程补上。
- Semantic value: CDN 是内容分发网络的稳定缩写，purge 对应边缘缓存失效。
- Lexical failure mechanism: 中文展开表达与英文 CDN purge 没有共享词元。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-domain-02`.
- Future retrieval value: 缓存失效请求可能缺少去重与定位信息。

### sg-pos-alias-05

- Category / primary: `alias_acronym_equivalence` / `sg-mem-alias-05`.
- Split / scope: `sealed` / `local`.
- Language / overlap: `en->zh` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: Refresh the software bill of materials before packaging.
- Semantic value: SBOM 是 software bill of materials 的标准缩写，记忆提供打包前刷新时序。
- Lexical failure mechanism: 英文展开名称与中文上下文中的 SBOM 缩写几乎无重叠。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-domain-03`.
- Future retrieval value: 发布物会携带陈旧依赖清单。

### sg-pos-alias-06

- Category / primary: `alias_acronym_equivalence` / `sg-mem-alias-06`.
- Split / scope: `sealed` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 对这次变更做一次发布前的快速检查。
- Semantic value: preflight 是该用户对发布前快速检查的稳定称呼，语义和时点都一致。
- Lexical failure mechanism: 中文请求没有英文 preflight。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-01`.
- Future retrieval value: 代理可能遗漏用户约定的发布前检查流程。

### sg-pos-config-03

- Category / primary: `behavior_to_configuration` / `sg-mem-config-03`.
- Split / scope: `analysis` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 在我的会话里，工具输出总被截得太短。
- Semantic value: USER 配置中的软限制直接决定诊断工具输出裁剪长度。
- Lexical failure mechanism: 中文现象没有英文 tool_output_soft_limit 字段。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-01`.
- Future retrieval value: 关键错误上下文会在展示前被截断。

### sg-pos-config-05

- Category / primary: `behavior_to_configuration` / `sg-mem-config-05`.
- Split / scope: `sealed` / `local`.
- Language / overlap: `en->zh` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: New local sessions keep restoring tabs from yesterday.
- Semantic value: restore_workspace_state 控制新会话是否恢复先前标签页，正好对应昨日状态复现。
- Lexical failure mechanism: 英文现象与中文配置说明缺少共享术语。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-library-02`.
- Future retrieval value: 用户会持续进入陈旧工作上下文。

### sg-pos-config-06

- Category / primary: `behavior_to_configuration` / `sg-mem-config-06`.
- Split / scope: `sealed` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 我希望命令执行失败后保留完整的本地现场。
- Semantic value: 该 USER 配置使失败沙箱跳过清理，满足保留现场的要求。
- Lexical failure mechanism: 中文要求没有英文 preserve_failed_workspace 配置键。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-02`.
- Future retrieval value: 失败证据会在诊断前被删除。

### sg-pos-constraint-03

- Category / primary: `goal_to_project_constraint` / `sg-mem-constraint-03`.
- Split / scope: `analysis` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 这次重构尽量少打断我，直接完成能够确定的部分。
- Semantic value: 该 USER 约束定义了少打断的安全边界：可逆细节自主处理，不可逆且缺证据才询问。
- Lexical failure mechanism: 中文目标没有英文 irreversible、evidence 或 reversible details。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-02`.
- Future retrieval value: 代理可能频繁确认或擅自做不可逆决定。

### sg-pos-constraint-05

- Category / primary: `goal_to_project_constraint` / `sg-mem-constraint-05`.
- Split / scope: `sealed` / `local`.
- Language / overlap: `en->zh` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: Allow two local test runs to execute concurrently.
- Semantic value: 并发测试要求实例隔离，按任务标识派生目录可防止共享沙箱冲突。
- Lexical failure mechanism: 英文并发目标未复述中文任务标识或临时目录规则。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-path-03`.
- Future retrieval value: 运行之间会覆盖文件并产生不稳定结果。

### sg-pos-constraint-06

- Category / primary: `goal_to_project_constraint` / `sg-mem-constraint-06`.
- Split / scope: `sealed` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 请把最终答复压缩到我能快速审阅的程度。
- Semantic value: 简洁答复仍必须保留验证结果和影响路径，这是稳定 USER 审阅约束。
- Lexical failure mechanism: 中文目标没有英文 verification result 或 affected paths。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-03`.
- Future retrieval value: 过度压缩会删除用户判断变更是否可靠所需信息。

### sg-pos-context-05

- Category / primary: `multi_clause_contextual_relevance` / `sg-mem-context-05`.
- Split / scope: `sealed` / `local`.
- Language / overlap: `en->zh` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `26`.
- Synthetic query: For the local preview token, rotate credentials without disconnecting requests already in flight.
- Semantic value: local preview token、轮换与在途请求三项条件定位到双代宽限窗口。
- Lexical failure mechanism: 英文任务与中文记忆跨语言，且单个 token/rotate 不能表达在途连续性。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-library-03`.
- Future retrieval value: 轮换瞬间会中断正在处理的请求。

### sg-pos-correction-03

- Category / primary: `correction_supersession_rephrasing` / `sg-mem-correction-03`.
- Split / scope: `analysis` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 按我后来确认的方式展示测试结果。
- Semantic value: “后来确认”指向 active correction，测试结果应同时显示成功和失败计数。
- Lexical failure mechanism: 中文请求没有英文 counts、failures-only 或 replacing。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-negation-02`.
- Future retrieval value: 报告会缺少总体通过情况。

### sg-pos-correction-06

- Category / primary: `correction_supersession_rephrasing` / `sg-mem-correction-06`.
- Split / scope: `sealed` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 这次按新的交互约定处理可逆选择。
- Semantic value: 新的交互约定是可逆选择无需确认，仅不可逆决定询问。
- Lexical failure mechanism: 中文概括没有英文 no longer、confirmation 或 irreversible decisions 的直接词元。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-negation-02`.
- Future retrieval value: 代理会继续对每个可逆细节打断用户。

### sg-pos-enzh-01

- Category / primary: `english_to_chinese` / `sg-mem-enzh-01`.
- Split / scope: `analysis` / `project`.
- Language / overlap: `en->zh` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: A resumed upload starts again from the first chunk.
- Semantic value: 已确认偏移量是上传恢复时避免从首块重传的依据。
- Lexical failure mechanism: 英文症状与中文断点规则没有共享词元。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-symptom-01`.
- Future retrieval value: 大文件恢复会重复传输。

### sg-pos-enzh-02

- Category / primary: `english_to_chinese` / `sg-mem-enzh-02`.
- Split / scope: `analysis` / `local`.
- Language / overlap: `en->zh` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: Two browser tabs overwrite each other's draft changes.
- Semantic value: 版本检查能够检测两个页面基于旧状态的覆盖写入。
- Lexical failure mechanism: 英文标签页症状没有出现中文并发控制术语。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-object-01`.
- Future retrieval value: 用户较新的草稿内容可能丢失。

### sg-pos-enzh-03

- Category / primary: `english_to_chinese` / `sg-mem-enzh-03`.
- Split / scope: `analysis` / `user`.
- Language / overlap: `en->zh` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: Keep the terminal answer brief and operational.
- Semantic value: 直接给出结论和动作就是简短且可操作的终端回答。
- Lexical failure mechanism: 英文请求与中文偏好没有共享词元。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-01`.
- Future retrieval value: 回答会变得冗长且缺少行动焦点。

### sg-pos-enzh-04

- Category / primary: `english_to_chinese` / `sg-mem-enzh-04`.
- Split / scope: `analysis` / `project`.
- Language / overlap: `en->zh` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: The list briefly shows records from the previous account after sign-in.
- Semantic value: 身份切换前清空查询缓存能够避免短暂展示旧主体记录。
- Lexical failure mechanism: 英文视觉症状没有使用中文缓存清理术语。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-object-02`.
- Future retrieval value: 可能短暂泄露前一账户信息。

### sg-pos-enzh-05

- Category / primary: `english_to_chinese` / `sg-mem-enzh-05`.
- Split / scope: `sealed` / `local`.
- Language / overlap: `en->zh` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: A successful scheduled run appears again after the coordinator recovers.
- Semantic value: 唯一完成凭据可以阻止协调器恢复后重复执行已完成计划。
- Lexical failure mechanism: 英文故障描述没有出现中文世代凭据术语。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-symptom-03`.
- Future retrieval value: 定时任务可能重复产生外部副作用。

### sg-pos-enzh-06

- Category / primary: `english_to_chinese` / `sg-mem-enzh-06`.
- Split / scope: `sealed` / `user`.
- Language / overlap: `en->zh` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: Do not restate a decision that I already made explicit.
- Semantic value: 说明影响和后续动作避免重复用户已经确认的决定。
- Lexical failure mechanism: 英文请求与中文偏好没有共享词元。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-03`.
- Future retrieval value: 回答会重复已经结束的决策讨论。

### sg-pos-preference-02

- Category / primary: `user_preference_rephrasing` / `sg-mem-preference-02`.
- Split / scope: `analysis` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 不要停在方案里，把能验证的改动直接落地。
- Semantic value: 请求直接落地与 USER 对可逆编码任务实施加测试的偏好一致。
- Lexical failure mechanism: 中英文表达不同，且 proposal-only 与停在方案没有共享词元。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-02`.
- Future retrieval value: 代理可能只给设计而不完成代码和测试。

### sg-pos-preference-03

- Category / primary: `user_preference_rephrasing` / `sg-mem-preference-03`.
- Split / scope: `analysis` / `user`.
- Language / overlap: `en->zh` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: When showing failures, give me the decisive lines rather than the entire terminal log.
- Semantic value: 保留决定性错误行而非完整日志正是该 USER 故障汇报偏好。
- Lexical failure mechanism: 英文请求与中文记忆没有共享词元。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-03`.
- Future retrieval value: 输出会被大量无关日志淹没。

### sg-pos-preference-04

- Category / primary: `user_preference_rephrasing` / `sg-mem-preference-04`.
- Split / scope: `analysis` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 涉及不可逆的数据操作时先让我确认。
- Semantic value: 不可逆数据操作属于稳定的显式审批边界。
- Lexical failure mechanism: 中文请求与英文 destructive/approval 表达缺少直接词元。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-direction-01`.
- Future retrieval value: 代理可能未经授权修改或删除数据。

### sg-pos-preference-05

- Category / primary: `user_preference_rephrasing` / `sg-mem-preference-05`.
- Split / scope: `sealed` / `user`.
- Language / overlap: `en->zh` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `26`.
- Synthetic query: Keep the response focused on behavior, risks, and test evidence.
- Semantic value: 当前要求的行为、风险和测试证据与既有代码审阅偏好一致。
- Lexical failure mechanism: 英文 focused/evidence 与中文行为回归/缺失测试不直接匹配。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-02`.
- Future retrieval value: 审阅可能偏向无关风格问题。

### sg-pos-preference-06

- Category / primary: `user_preference_rephrasing` / `sg-mem-preference-06`.
- Split / scope: `sealed` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 这次不要用远程服务，所有验证都在本机完成。
- Semantic value: 本机完成且不用远程服务等价于 USER 对诊断评测的离线零调用偏好。
- Lexical failure mechanism: 中文本机/远程与英文 offline/external model 没有共享词元。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-domain-03`.
- Future retrieval value: 测试可能产生网络依赖、费用或数据外发。

### sg-pos-recovery-03

- Category / primary: `symptom_to_recovery` / `sg-mem-recovery-03`.
- Split / scope: `analysis` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 我只改了一个接口，生成的补丁却总带上无关文件。
- Semantic value: 按显式路径和编译依赖重建变更集能够排除任务外修改，直接解决补丁夹带问题。
- Lexical failure mechanism: 中文症状没有出现英文 change set、path 或 compilation 表达。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-01`.
- Future retrieval value: 用户可能误提交无关修改。

### sg-pos-recovery-05

- Category / primary: `symptom_to_recovery` / `sg-mem-recovery-05`.
- Split / scope: `sealed` / `local`.
- Language / overlap: `en->zh` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: After a worker restart, completed notifications are sent one more time.
- Semantic value: 持久化投递账本可识别重启前已完成通知，是重复发送的直接恢复措施。
- Lexical failure mechanism: 英文症状与中文投递账本表达没有明显词元交集。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-direction-03`.
- Future retrieval value: 外部接收方会处理重复通知。

### sg-pos-root-03

- Category / primary: `symptom_to_root_cause` / `sg-mem-root-03`.
- Split / scope: `analysis` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 在我的终端里，长命令的最后几个字符会被提示符盖住。
- Semantic value: ANSI 字节被算作可见列会高估提示符宽度，直接导致输入尾部覆盖。
- Lexical failure mechanism: 中文现象与英文 ANSI、visible columns 没有共享高信息词元。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-object-03`.
- Future retrieval value: 调整终端尺寸无法消除宽度计算错误。

### sg-pos-root-05

- Category / primary: `symptom_to_root_cause` / `sg-mem-root-05`.
- Split / scope: `sealed` / `local`.
- Language / overlap: `en->zh` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: A file watcher reports the same edit twice only inside containers.
- Semantic value: 挂载层对同一次保存转发两种事件，可解释容器内的双重通知。
- Lexical failure mechanism: 英文症状没有中文挂载层及宿主事件术语。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-symptom-01`.
- Future retrieval value: 仅在应用层去重会掩盖平台事件来源。

### sg-pos-root-06

- Category / primary: `symptom_to_root_cause` / `sg-mem-root-06`.
- Split / scope: `sealed` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 我要求只输出 JSON，但工具结果前面偶尔多出解释文字。
- Semantic value: 格式化器先写前言再检查结构化模式，会稳定产生 JSON 前的解释文字。
- Lexical failure mechanism: 中文输出症状没有英文 formatter、preamble 或 structured-only。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-direction-02`.
- Future retrieval value: 继续收紧解析器不能阻止上游多余文本。

### sg-pos-zero-03

- Category / primary: `zero_overlap_paraphrase` / `sg-mem-zero-03`.
- Split / scope: `analysis` / `user`.
- Language / overlap: `en->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: Show commands without a decorative introduction.
- Semantic value: Starting with the actionable line implements the requested absence of decorative preamble.
- Lexical failure mechanism: The request says decorative introduction while the memory says terse and action first.
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-01`.
- Future retrieval value: Responses violate a stable user interaction preference.

### sg-pos-zero-06

- Category / primary: `zero_overlap_paraphrase` / `sg-mem-zero-06`.
- Split / scope: `sealed` / `user`.
- Language / overlap: `zh->zh` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 回答里只保留决定和下一步。
- Semantic value: 省略寒暄并给出结论动作与当前请求完全一致。
- Lexical failure mechanism: 查询使用决定和下一步，记忆使用结论、动作和寒暄。
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-03`.
- Future retrieval value: 回复格式违背稳定用户偏好。

### sg-pos-zhen-01

- Category / primary: `chinese_to_english` / `sg-mem-zhen-01`.
- Split / scope: `analysis` / `project`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 支付回调偶尔会被处理两次。
- Semantic value: An idempotent provider receipt prevents repeated callback delivery from applying twice.
- Lexical failure mechanism: Chinese symptom wording has no shared tokens with the English delivery contract.
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-symptom-01`.
- Future retrieval value: Repeated provider events can duplicate state changes.

### sg-pos-zhen-02

- Category / primary: `chinese_to_english` / `sg-mem-zhen-02`.
- Split / scope: `analysis` / `local`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 后台任务重启后又从头开始跑。
- Semantic value: Durable batch checkpoints are the established continuation mechanism after worker restart.
- Lexical failure mechanism: The Chinese restart symptom does not name the English checkpoint mechanism.
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-symptom-02`.
- Future retrieval value: Large jobs repeat completed work.

### sg-pos-zhen-03

- Category / primary: `chinese_to_english` / `sg-mem-zhen-03`.
- Split / scope: `analysis` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 命令行回复不要写大段背景。
- Semantic value: Compact decision-focused CLI output satisfies the request to omit long background.
- Lexical failure mechanism: The Chinese wording cannot match the English preference lexically.
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-01`.
- Future retrieval value: Terminal answers become unnecessarily verbose.

### sg-pos-zhen-04

- Category / primary: `chinese_to_english` / `sg-mem-zhen-04`.
- Split / scope: `analysis` / `project`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 用户切换组织后仍然看到上一个组织的数据。
- Semantic value: Tenant and principal partitioning prevents records from a previous organization being reused.
- Lexical failure mechanism: The Chinese visibility symptom omits the English cache partition terminology.
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-object-02`.
- Future retrieval value: Cross-organization data can be displayed.

### sg-pos-zhen-05

- Category / primary: `chinese_to_english` / `sg-mem-zhen-05`.
- Split / scope: `sealed` / `local`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 偶尔出现已经成功的消息又被发送一次。
- Semantic value: Transactional completion prevents an already applied outbox delivery from being replayed.
- Lexical failure mechanism: The Chinese duplicate symptom does not mention outbox transaction semantics.
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-symptom-03`.
- Future retrieval value: Customers can receive duplicate notifications.

### sg-pos-zhen-06

- Category / primary: `chinese_to_english` / `sg-mem-zhen-06`.
- Split / scope: `sealed` / `user`.
- Language / overlap: `zh->en` / `zero` (`0.0`).
- First failure / rank: `candidate_generation_top20` / `33`.
- Synthetic query: 不要在回答里重复解释我已经确认过的选择。
- Semantic value: Reporting consequences and next actions avoids re-explaining an explicit decision.
- Lexical failure mechanism: The Chinese request and English preference share no lexical vocabulary.
- Not metadata/scope/lifecycle/budget: Primary is approved/active/safe, globally visible in its declared scope, has complete structured labels, and fails before Gate, Consolidator, Controller, and budget.
- Hard-negative controls: `sg-neg-preference-03`.
- Future retrieval value: The response repeats settled discussion.

## Non-Semantic Candidate Misses

- `sg-pos-rename-05`: `structured_rename_relation_is_primary_evidence` (stage `candidate_generation_top20`, rank `21`).

## Downstream Or Successful Non-Gaps

- `sg-pos-alias-01`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `7`).
- `sg-pos-alias-02`: `primary_entered_diagnostic_top20, first_loss_is_rendered` (stage `rendered`, rank `8`).
- `sg-pos-config-01`: `primary_entered_diagnostic_top20, first_loss_is_rendered` (stage `rendered`, rank `3`).
- `sg-pos-config-02`: `primary_entered_diagnostic_top20, first_loss_is_rendered` (stage `rendered`, rank `8`).
- `sg-pos-config-04`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `1`).
- `sg-pos-constraint-01`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `2`).
- `sg-pos-constraint-02`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `13`).
- `sg-pos-constraint-04`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `1`).
- `sg-pos-context-01`: `primary_entered_diagnostic_top20, first_loss_is_rendered` (stage `rendered`, rank `1`).
- `sg-pos-context-02`: `primary_entered_diagnostic_top20, first_loss_is_rendered` (stage `rendered`, rank `2`).
- `sg-pos-context-03`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `1`).
- `sg-pos-context-04`: `primary_entered_diagnostic_top20, first_loss_is_rendered` (stage `rendered`, rank `1`).
- `sg-pos-context-06`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `1`).
- `sg-pos-correction-01`: `primary_entered_diagnostic_top20, first_loss_is_rendered` (stage `rendered`, rank `2`).
- `sg-pos-correction-02`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `7`).
- `sg-pos-correction-04`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `1`).
- `sg-pos-correction-05`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `15`).
- `sg-pos-preference-01`: `primary_entered_diagnostic_top20, first_loss_is_rendered` (stage `rendered`, rank `5`).
- `sg-pos-recovery-01`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `12`).
- `sg-pos-recovery-02`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `18`).
- `sg-pos-recovery-04`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `1`).
- `sg-pos-recovery-06`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `1`).
- `sg-pos-rename-01`: `primary_entered_diagnostic_top20, structured_rename_relation_is_primary_evidence, first_loss_is_rendered` (stage `rendered`, rank `1`).
- `sg-pos-rename-02`: `primary_entered_diagnostic_top20, structured_rename_relation_is_primary_evidence, first_loss_is_rendered` (stage `rendered`, rank `1`).
- `sg-pos-rename-03`: `primary_entered_diagnostic_top20, structured_rename_relation_is_primary_evidence, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `1`).
- `sg-pos-rename-04`: `primary_entered_diagnostic_top20, structured_rename_relation_is_primary_evidence, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `1`).
- `sg-pos-rename-06`: `primary_entered_diagnostic_top20, structured_rename_relation_is_primary_evidence, first_loss_is_rendered` (stage `rendered`, rank `1`).
- `sg-pos-root-01`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `6`).
- `sg-pos-root-02`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `15`).
- `sg-pos-root-04`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `1`).
- `sg-pos-zero-01`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `14`).
- `sg-pos-zero-02`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `11`).
- `sg-pos-zero-04`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `1`).
- `sg-pos-zero-05`: `primary_entered_diagnostic_top20, first_loss_is_relevance_gate` (stage `relevance_gate`, rank `12`).

## Interpretation Boundary

This suite proves that the architecture can miss useful semantic relations under fixed lexical pressure. It does not prove how often those relations occur in production. File rename cases whose relevance depends primarily on explicit migration metadata remain structured-retrieval failures, not confirmed semantic-only gaps.
