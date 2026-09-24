# 新 40 题计分契约的作者澄清与冻结

版本：`new40-author-action-alternatives-v1`。

原 `holdout.json` 和 `holdout-authorship.md` 保持全部字节不变。原题库已在实现审查之前独立编写并冻结。这次是在候选第一次运行新 40 题之前，经原 scorer 的静态审查发现字段含义冲突：原 scorer 把 `expected_actions` 的每个 intent 键视为必须存在的肯定意图；作者为解释／问答、代码／重构等合理备选分别列出了动作，原意并非要求全部标签同时出现。

本澄清不修改问题、标签、动作取值、实体、只读、否定或顺序。旧 50 题继续使用原 scorer；新 40 题使用本文件解释的专用契约，因此不能宣称两套题使用完全相同的 strict 计分契约。同一新 40 题的基线与候选必须使用完全相同、事先冻结的 `holdout_scoring.py`。已完成的新 40 基线仅根据保存的 actual 重新计分，不再调用解析器或模型，原始评分及记录应另行原样保留。

制定本契约时，作者未读取新 40 题的任何 baseline／candidate 运行结果，也没有读取任何真实逐题解析输出。候选新 40 尚未首次执行。作者收到过旧 50 的总体完成状态，此信息不参与下表的逐题解释；分组仅依据冻结题目、原 rationale、原标签及作者最初的语义意图确定。

## 唯一规则差异

1. 仅下表明确标为备选的一组意图，省略其中未实际出现的标签的动作检查；实际出现的标签仍必须满足它自己的原始动作列表。
2. 每个备选组必须至少出现一个符合原动作列表的肯定 intent/action 对，不能因全部缺失而通过。
3. 不同任务组之间仍为 AND。单独列出的、非备选的动作要求仍沿用原 scorer，全部必需。
4. `required_affirmed`、`forbidden_affirmed`、`required_negated`、主意图、禁止动作、实体、只读与顺序检查都原样委托给原 scorer。`required_affirmed` 不因所属标签也是备选主标签而变成可选。
5. `score` 拒绝与冻结题库不完全相同的题目字典。模块导入时核验题库、最初作者声明及原 scorer 摘要，并恢复原 scorer 导入产生的 sys.path 修改；不导入 minicode，避免影响基线隔离。

## 逐题作者说明

符号 `或` 只表示同一个语义任务的可替换标签；`且` 表示两项都必须出现。表中全部动作仍取原题原列表。

| 编号 | 动作意图要求 | 作者语义及未放宽部分 |
| --- | --- | --- |
| N01 | explain 或 question | 单一操作影响解释；system 禁止与否定仍必需。 |
| N02 | explain 或 question 或 review | 单一假设方案分析；refactor 仍被否定。 |
| N03 | explain 或 question | 撤回代码要求后只解释后果；code 否定、文件和只读要求不变。 |
| N04 | 无动作备选；完全原规则 | 只有禁止，没有肯定任务；unknown 及两个 negated 要求不变。 |
| N05 | explain 或 question | 比较参数含义是单一问答；test 禁止不变。 |
| N06 | explain 或 question 或 review | 单一容量推演；configure 禁止不变。 |
| N07 | search 必需；完全原规则 | 实际任务为检索，引用文本不产生代码修改授权。 |
| N08 | search 必需；完全原规则 | 日志检索与已取消的系统操作保持区分。 |
| N09 | review 必需；完全原规则 | 结构评审不是文档编辑；document 禁止不变。 |
| N10 | explain 或 question | 解释日志建议；重命名和移动禁止不变。 |
| N11 | code 必需；完全原规则 | 实际新增源文件，create 要求不变。 |
| N12 | code 或 debug | 同一个精度修复可用实现修改或缺陷修复描述。 |
| N13 | code 或 refactor | 同一个无用代码清理任务的两种合理标签。 |
| N14 | configure 必需；完全原规则 | 修改配置字段，update 要求不变。 |
| N15 | document 必需；完全原规则 | 新建说明文档，create 要求不变。 |
| N16 | test 必需；完全原规则 | 创建或补充测试；不要求现场再执行测试。 |
| N17 | refactor 或 code | 同一个拆分函数的结构修改任务。 |
| N18 | system 必需；完全原规则 | 真正的重启执行请求；execute 不变。 |
| N19 | memory 必需；完全原规则 | 真正要求保存长期偏好。 |
| N20 | system 或 code | 同一个项目文件移动任务；move／rename 原取值不变。 |
| N21 | debug 必需；完全原规则 | 定位故障原因，原只读诊断要求不变。 |
| N22 | explain 或 question | 单一概念比较问答。 |
| N23 | search 必需；完全原规则 | 定位定义与调用位置。 |
| N24 | review 必需；完全原规则 | 代码风险审查，原只读要求不变。 |
| N25 | test 必需；完全原规则 | 明确运行测试，execute 不变。 |
| N26 | document 必需；完全原规则 | 补充已有文档，update 不变。 |
| N27 | code 必需；完全原规则 | 编写函数；区间合并是函数业务含义，不更换代理动作。 |
| N28 | explain 或 question | 单一浅拷贝与深拷贝概念解释。 |
| N29 | search 仍必需；question 仅出现时检查其动作 | 原 required_affirmed 明确要求 search；question 只是可接受主标签，不要求额外创建独立问答操作。 |
| N30 | chat 必需；完全原规则 | 寒暄，原动作列表与只读要求均不变。 |
| N31 | code 且 test；完全原规则 | 两个独立交付物都被肯定，主意图可选不等于子任务可省。 |
| N32 | explain 或 question | 撤回删除后单一解释任务；系统否定与实体不变。 |
| N33 | code 且 document；完全原规则 | 功能新增和文档补充都是独立必需任务。 |
| N34 | search 且（explain 或 question） | 检索和解释是两项任务，不能只检索或只解释就通过。 |
| N35 | review 且 test；完全原规则 | 评审与测试两项都必需，原顺序检查不变。 |
| N36 |（refactor 或 code）且（explain 或 question）| 实施结构迁移与解释职责是两项任务，两个组各自必需。 |
| N37 | code 必需；完全原规则 | 只肯定代码任务，document 否定和两个指代实体不变。 |
| N38 | memory 且 document；完全原规则 | 保存偏好与编辑文档两项都必需。 |
| N39 | configure 且 test；完全原规则 | 配置修改与测试执行两项都必需，原顺序不变。 |
| N40 | 无动作备选；完全原规则 | 无法唯一指代当前事项，unknown 原预期不变。 |

没有发现需要改题、改标签或借助真实模型输出才能解释的剩余冲突。以上 16 题包含明确备选组，其余 24 题对原 scorer 的输入完全不作修改。

## 接口与验证

- `score(case, intent)` 接受与原 scorer 相同的 ParsedIntent 公共接口并返回 `(primary_ok, issues)`。
- `score_recorded(case, actual, *, read_only=None)` 接受保存的 `to_detailed_dict` 输出；优先使用原记录中的 read_only。仅提供 actual 时，依照保存的 operations 与 scalar action 重建原有只读投影。基线与候选 `ParsedIntent.is_read_only` 的 AST 已核实相同。原解析异常的 actual=null 仍计失败。
- 通过 330 项人工构造的计分契约检查，包括 40 题的规则见证、备选组合、缺失组、所有原约束、已出现备选的错误动作、两个接口一致性及标签篡改拒绝。这些是计分器的人工对照，不是解析器输出、模型生成结果或评估通过率。
- 验证没有导入解析器、没有调用模型、没有读取真实结果；模块导入不改变调用方 sys.path。

## 冻结摘要

冻结日期：2026-09-07（会话本地日期）。从以下摘要生成起，作者不再修改 `holdout_scoring.py` 或本契约文件；任何后续问题只报告，不按候选输出调整规则。

- `holdout.json` SHA-256：`a99d48fa2c459fb04653855d9c7d3721100ae84c212b7537cf9c7c6cc7af3d3e`
- `holdout-authorship.md` SHA-256：`cb67239489d4c65eb2dd49bc84defdb0386e6d0628e40ca58367b94bd580d665`
- `holdout_scoring.py` SHA-256：`0afc7833b9c4ade6307bae3affc36366556924e30d04b9f3c2f295419de69737`
- 原 `run_evaluation.py` SHA-256：`d7239de029e47782e4fc2d220964c6640e65b88d09c9777f3469ac4221a680f0`
