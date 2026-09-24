# Fresh40 计分方法独立审查

结论：**方法审查通过，可启动 fresh40 baseline。** 此结论针对下面冻结版本的计分、来源隔离和证据记录机制，不包含任何真实 parser 准确率或模型调用结果。

- 审查者：独立出题代理 `/root/phase2_fresh_holdout`。
- 状态：**FROZEN**。
- 冻结时间（UTC）：`2026-09-07T07:18:09.763952+00:00`。
- 审查范围：`score_holdout.py`、`score_loader.py`、`evaluate.py`；原评测文件仅提取其 `score` 函数；自编且已冻结的 40 题及作者声明。
- 未读取生产实现、已有其他题目、任何实际评测结果或候选修复；未运行真实 parser、模型或评测 runner 的 `main()`。所有运行检查仅使用人工 `ParsedIntent`-like 对象及人工模块对象。

## 计分语义

`required_operation_groups` 与全部原始标注一致：每组至少命中一个备选，各组必须分别满足；备选中的 intent 必须与该备选所列 action 同时匹配，不能跨备选拼接。只有 `affirmed_operations` 能满足这些组。这个扩展不会覆盖主标签判定。

扩展首先调用原 `score`，保留其主意图、必需肯定意图、禁止肯定意图、必需否定意图、原 `expected_actions`、禁止动作、实体、只读和操作顺序校验，然后仅追加缺失操作组的问题。原 `expected_actions` 的每键 AND 语义也未改变；fresh40 本身不使用该字段。

对 40 题逐项核对后，6 个多组题的 intent/action 备选集合两两不相交，因此单个操作不能同时满足这些题的两个必要步骤。只读限制由动作及 `is_read_only()` 单独约束，不通过某个意图标签直接推断写权限。题目及标注没有变更。

## 人工验证

最终三个脚本共通过 **21 项人工检查**：

1. 组内第二标签备选可命中；action 列表中的另一动作可命中。
2. 缺首组、缺次组、全缺均正确失败，分别报告缺失组。
3. 不允许跨备选拼接 intent/action；否定操作不能满足肯定操作组。
4. 操作组通过时，主意图错误仍保留；无扩展时返回值与原计分一致。
5. 同时构造全部九类旧评分失败，扩展结果完整保留原错误，顺序不变。
6. 新旧约束可以共同通过；只读、顺序和实体校验保持有效；空组会被拒绝。
7. 在干净进程中导入三个审查模块，不会加载任何真实 `minicode` 模块。
8. 来源校验接受人工构造的同源模块，拒绝混入另一目录的人工依赖模块。
9. 数据与作者声明常量、实际文件哈希均匹配作者冻结记录；全部 40 题结构及多组语义通过。

以上为 21 个具体断言的合并说明，不是对真实 parser 的测试成绩。未创建或修改生产测试。

## 来源隔离与 telemetry

旧 runner 不再以模块形式被执行。`score_loader.py` 只将唯一的 `score` AST 节点编译到独立命名空间，避免旧 runner 的导入副作用。`evaluate.py` 先选定来源目录，再导入 parser，并在运行前后校验全部已加载的 `minicode` 模块路径。

`--live` 使用配置中的模型适配器、语义适配器及正式的 `RunJournal`/`observe_run`/预算上下文，排除已知的 `MockModelAdapter`。每题的调用证据来自对应 `run_id` 的实际 `model.*` 事件与预算快照；调用题目统计由 `model.started` 事件计算，没有把 parser 调用次数直接当作模型调用次数。

因此，记录路径可以提供真实运行证据；本审查没有执行模型，不能预先确认某轮存在成功的远端调用。baseline 的实际调用、跳过、失败和降级状态应由后续运行的事件、诊断与预算记录判定。

## 冻结与运行接受条件

fresh40 在加载题目时强制核对数据和作者声明 SHA。运行冻结记录包含来源 Python 文件哈希、题目哈希、原 scorer、所选扩展 scorer、loader、runner、有效模型/分类配置及已配置校准文件的路径摘要与内容 SHA；结束时检查来源、脚本、题目、模块来源和校准文件是否保持一致。

本次审查发现的旧 runner 整模块导入、作者冻结值未绑定以及校准文件未记录三个问题，已在下列版本修正。首次审查和修订复核均发生于本代理读取任何真实结果之前。

正式 fresh40 baseline 应使用正常的非优化 Python 进程、完整 40 题及 `--live`。runner 将完整性检查写入 summary，因此接受一轮结果时须要求 `source_unchanged`、`runner_unchanged`、`scorer_unchanged`、`module_sources_valid`、`calibration_unchanged`、`cases_unchanged` 全部为真，并核对 runner/scorer/loader 与本记录一致。后续候选比较须沿用同一冻结题目和评分依赖；来源代码哈希可以不同，评分方法不能随结果调整。

这是方法审查的接受条件，不是额外的用户确认流程。本代理不启动 baseline，也不读取其结果。

## 冻结 SHA-256

| 文件 | SHA-256 |
| --- | --- |
| `score_holdout.py` | `59059a80cb591a892828dba038e3d20cdbbe2d058b389b8cc6c36e05c2c9d366` |
| `score_loader.py` | `5631b077a924462d1c86ec2fd73b9320fc120ef7aa7d95657270d106a08019aa` |
| `evaluate.py` | `00c5987aa18aa8a25af7ad3ffce3fac89c2eab474b7247e92bbb51c02df67aba` |
| `holdout.json` | `c2fdf309e37976007f0b5c6803a1c2c318daa7f96834e06d348597fb50daed53` |
| `holdout-authorship.md` | `f456fdf06c07e1b7ea2e054a8fa4d178d8ed3be11d21641bde1f926d7f140478` |

本记录写入后不再修改。若方法脚本后续需要修订，应形成单独版本的审查记录，并在再次评测前冻结；不能把本次审查结论自动应用于哈希不同的脚本。
