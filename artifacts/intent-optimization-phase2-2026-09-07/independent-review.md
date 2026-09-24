# 第二轮独立代码审查

状态：审查完成。三组确认新增问题均已修复，并通过固定探针复核；当前没有尚未解决的已确认新增问题。

本审查只读生产源码、第一轮结束快照与第二轮新增测试，不读取封存 fresh40 题目、作者说明或任何 fresh40 结果，不调用真实模型。使用 25 条独立构造的边界输入，分别从两个独立 Python 进程加载 baseline 与 candidate；伪适配器只用于暴露 fallback 入参，不执行样例动作。证据见 `independent-review-probes.json`。

## 已确认新增问题

### P1：背景执行过滤吞掉直接日志记录和无连接词的条件处理命令

- 输入：`运行 app.py 时记录所有失败请求。`
- 输入：`运行 app.py 时如果失败立即退出。`
- 第一轮 baseline：均保留 `test/execute`，不触发 LLM。
- 本轮候选：均为 `unknown/unknown`，无操作，也不触发 LLM。
- 定位：`_is_reported_execution_mention` 对 circumstance result 任意位置搜索“失败/出现”等词，而 `_EXECUTION_HANDLING_DIRECTIVE_RE` 只认带“就/要/请”等有限前缀的处理动作。因此“记录所有失败请求”这一祈使句也被当作已经发生的失败；`_semantic_clause_is_non_instruction` 随后阻止兜底恢复。
- 建议：把事实陈述与直接处理/记录动作区分开，避免仅凭“失败”断言背景；保留直接“记录…”及“如果…立即退出”等自然句式的运行权限，同时保持 `运行 app.py 时出现了错误，请分析原因。` 的只读边界。

### P2：中文泛化因果模式把非软件分析变成调试

- 输入：`分析招聘成功率下降的原因。`
- 第一轮 baseline：`explain/read`。
- 本轮候选：`debug/analyze`。
- 定位：`_PRECISE_DEBUG_PATTERNS` 新增中文因果模式包含“成功率/失败/异常/延迟”等领域通用词，但 `_GENERIC_CAUSE_DIAGNOSIS_RE` 及后续软件主体检查只覆盖英文。
- 建议：对中文同类泛化原因模式也要求明确的软件主体或可信当前软件上下文，或把无需主体的关键词收窄到软件专有词。不要因单独命中“成功率”就授予调试领域。

## 本阶段未发现新增问题的范围

- “解释…然后运行/修复”的后续明确动作在独立 probes 中保留。
- 引用建议后跟真实删除的原测试已保留。
- 英文非软件 `diagnose/investigate cause` 的已修边界未发现新的已确认问题。
- 不把 baseline 已存在的错误、尚未证明的假设或测试偏好列作本轮发现。

审查阶段 parser SHA-256：`bb07e701073a7a04c52e53d4b1b091b1d28ed2bb3287c2729fa3f4ef9dfe8f60`。后续整合会更新最终状态与复核证据；此 hash 不是最终发布冻结值。

## 实体部分已确认新增问题

### P1：背景报告的文件成为追加测试的写入目标

- 历史：`给当前模块添加测试。旧测试结果保存在 reports/old.log。`
- 当前：`再加一个 None 输入的用例。`
- baseline：当前 `test/create` 的 `files=[]`。
- candidate：当前 `test/create` 的 `files=["reports/old.log"]`，并声明已使用上下文。
- 同义历史 `给当前模块添加测试；reports/old.log 只是历史输出。` 可复现相同差异。
- 定位：`_bind_current_file_references` 从整段 frame 抽所有文件，再以先前主操作属于安全测试域为门槛；这不证明抽出的文件就是那个测试操作的对象。唯一可见背景路径被确定性写进当前操作目标。
- 建议：追加测试的候选来源必须绑定到先前实际测试请求所在语段；不能从整段背景资料收集目标。仅改用 prior operation.entities 仍不足以修复第一个复现：当前上游分句可能保留句号后的背景资料并使先前操作实体已被污染；分号版本先前测试操作的 clause 与 files 则正确隔离。
- 证据：`independent-review-entity-background.json`；独立一般指代探针在 `independent-review-entity-probes.json`。

## 非回归覆盖说明

根任务提出的反引号目标缺失已经核对：历史 `审查 \`alpha.py\` 和 \`beta.py\`。` 后接 `继续审查后一个文件。`，两个版本均没有绑定文件，因此不是本轮新增回归。新绑定方法当前只从遮蔽后的 raw frame 提取候选，会漏掉上述常见格式；这是本轮指代功能尚未覆盖的输入。此前 REVIEW 操作本身已正确提取被反引号包裹的两个文件，可作为安全候选来源的一部分。历史引用的整条命令则仍应遮蔽，不能为补此覆盖缺口直接解析所有被保护内容。

## 请求框架修复复核

对完全相同的 25 条独立探针重新运行（没有增加题目）。直接日志记录与无连接词条件处理恢复 baseline 的执行识别，招聘成功率恢复 `explain/read`；原有只读背景、后续明确命令探针保持不变。`When running app.py, if it fails, abort.` 恢复 baseline 的允许兜底状态，伪适配器故意输出 update 仅用于检查调用门槛，不能据此声称真实语义已正确。证据：`independent-review-probes-recheck.json`。这两组发现可关闭。

## 实体目标修复的第二次复核

固定 17 条实体探针重放确认背景日志路径已经不会成为当前测试目标，证据 `independent-review-entity-recheck.json`。阅读新 `explicit_test_subject_files` 时，发现它从 raw frame 搜索目标语法，没有使用此前已正确解析的否定/撤回；对此仅增加三个紧邻变式核对同一边界：

- 历史 `给 legacy.py 不要添加测试；给当前模块添加测试。`。
- 历史 `给 legacy.py 添加测试的要求已经取消；给当前模块添加测试。`。
- 当前均为 `再加一个 None 输入的用例。`。

两个版本的 prior operations 都已经正确表示 legacy 请求是 negated、当前模块请求是 affirmed；baseline 的当前测试没有文件，候选重新绑定了 `legacy.py`。这是新 helper 从文本重新恢复已否定对象造成的同类目标来源污染，应只允许先前 affirmed TEST 操作的有效语段进入目标 helper，并继续限制背景文件语法。证据 `independent-review-entity-negation.json`。已要求作者在这组固定范围内处理，不继续扩张测试集。

## 最终复核结论

实体固定探针共 20 条最终重放：背景日志、明确否定与撤回的历史目标均不会成为当前追加测试文件；明确当前测试主体 `utils.py` 仍能继承，否定 `legacy.py` 与明确 `utils.py` 并存时仅选择后者。最后实现将写入候选限定于此前 affirmed TEST CREATE/UPDATE 操作的有效 clause，再通过明确测试目标语法选择文件。证据 `independent-review-entity-final-recheck.json`。

本审查合计使用 25 条请求框架探针、20 条实体探针。三组发现关闭；没有运行真实模型，没有读取封存 fresh40 题目/作者说明/结果，没有以这些审查探针充当独立评估集。根任务仍应完成正常回归与封存评估。反引号历史指代遗漏保持为 baseline 已存在的覆盖限制，不据此阻塞当前修复。

最终复核源文件 SHA-256：
- `minicode/intent_parser.py`: `1fe71bdef84ffaafcbd6e249e7beeb718eb047efd83862062e75089f8e45237d`
- `minicode/intent_negation.py`: `0601aa2b21ac45531113e2422f87c5824a5f5427153139ca56567f11566fb393`
- `minicode/intent_entity_targets.py`: `adea365a7b474211ae5325d0cbec0297e79f7e52e3654ac14062a140fc0b277d`
