# 评估方法独立审查

结论：最初发现的三个有效性问题已在候选首次执行新 40 题之前处理：备选动作标签被原 scorer 意外解释为全部必选；旧 live 事件字段与新汇总不兼容；汇总未强制验证 freeze。按本次读取到的修订脚本，没有剩余已确认的阻断问题。真实调用的解释仍需区分尝试、成功返回和失败，不能把 `model.started` 数单独称为成功 provider 响应数。

## 评分契约与前后可比性

原 scorer 的 `expected_actions` 检查是逐键 AND。这个行为对旧 50 题保持不变。独立作者最初对新 40 题的一部分字段使用了备选标签语义，静态读到原 scorer 后确认这会扭曲新题 strict 分数，已立即报告并暂停候选运行。

解决方式是保留题库全部字节和全部原标签，新增并冻结 `holdout_scoring.py` 与 `holdout-scoring-contract.md`：16 题明确划定备选组，24 题直接原样使用原 scorer，所有非动作要求维持原检查。独立任务仍为 AND，特别是 N34 的搜索与解释、N36 的重构与解释都分别必需。旧 50 与新 40 的 strict 计分契约因此并不完全相同，正式结果必须明确说明；同一新 40 的基线与候选则使用同一冻结模块。

当前 `evaluate.py` 对 new40 调用专用 score，并额外保存 original_conjunctive_scoring；old50 仍调用原 score。`rescore_baselines.py` 只读取已保存 actual 和 read_only，调用同一模块的 score_recorded，在新的 `-rescored` 目录保存，不触及原 results 文件，也不导入解析器或构造模型。原记录摘要和新 scorer 摘要写入重计分 freeze。这个链路能在不二次模型调用、不按结果改题的前提下恢复可比性。

计分器经 330 项人工构造契约检查验证。检查不使用任何真实解析输出；具体规则、逐题解释、披露及摘要见冻结契约文档。

## 基线导入隔离

`evaluate.py` 在导入 minicode 之前把 source-root 放在 sys.path 首位。虽然原 run_evaluation 模块在导入时调整了 sys.path，但它没有在顶层导入 minicode，随后 source-root 插入发生在生产模块第一次导入之前；解析器模块路径还会断言属于目标 source。baseline 的 minicode 是普通包，没有扩展跨目录 package path 的初始化代码。

新 holdout scorer 不导入 minicode，并显式恢复原 scorer 导入前后的 sys.path。人工导入检查确认它既没有加载任何 minicode 模块，也没有改变调用者路径。这保证在正常的独立 Python 进程执行方式下，评分模块不会将 baseline 的解析器或 provider 导入切换成候选实现。

额外仅核对了旧 50 原 freeze 的源码摘要：其中列出的 intent_parser、intent_constraints、intent_negation、intent_semantic_classifier、skill_routing_turn 五个生产文件均与 baseline 快照一致。没有读取新 40 baseline 或 candidate 结果，也没有把原 freeze 未记录的依赖声称为已验证。

## 真实 provider 与 telemetry

live 模式经所选源码树的 load_runtime_config、create_model_adapter、build_semantic_intent_adapter 构建实际适配器，并拒绝 MockModelAdapter。每题使用生产预算、取消 token、父截止时间及真正持久化 RunObservation；事件从 RunJournal 读取，并与预算快照及 run_id 一起保存。未发现评估脚本通过 stub、monkeypatch、手造模型响应或预期标签向解析器泄漏答案的路径。

`model.started` 发生在语义调用尝试开始时，包含随后发生的配置失败、provider 错误或超时；因此当前 calls 和 call_median_ms 是调用尝试的指标。成功返回的证据应同时查看 model.completed 的 usage.source，区分 provider 与 estimated，以及 model.failed、model.costed 和预算里的 token 数。已向主代理说明此区分并建议将 usage_sources 和 token 合计纳入最终汇总。原始事件中保留了这些信息，无需重新调用模型。

旧 live runner 保存 event_type，新 runner 保存 type。修订后的 summarize.event_type 同时支持二者，消除了原先会在汇总旧 live 结果时触发的 KeyError。

## 输出与 freeze

评估输出目录采用 exist_ok=False，保留每题记录和 durable journal；完整 summary 只在遍历结束后生成。runner 记录源码文件、题库、原 scorer、专用 scorer 和自身摘要，并记录有效语义配置及覆盖项；结束时检查 source、runner、scorer 和 cases 是否未变。

修订后的 summarize.verify_run 要求完整 summary 和 freeze、所有 unchanged 标志为 True、50／40 条完整样本数、summary 与行级 strict 总数一致，并重新核验生产源码、题库和 scorer 摘要。它也检查已知 runner 摘要、重计分所引用的原 results 摘要以及旧 live 对旧基线的摘要链。最后比较 baseline/candidate 的完整 case 字典序列一致，才计算逐题改善和回归。此前“记录失效但仍照常汇总”的问题已处理。

汇总还将 read_only 返回 False 的检查失败与实际 affirmed 的写／删／执行等动作误判分别统计，避免把 UNKNOWN 的只读投影失败说成获准写操作。

本报告只审查方法与保存链路，没有运行 evaluate、summarize 或 rescore_baselines，也没有读取任何新 40 运行结果。作者仅在得到后续明确授权后重新读取自己的冻结题库以制定契约；这不涉及查看 baseline 输出。候选是否完成、实际分数和 provider 成功率由主代理另行验证。

## 本次静态审查的脚本摘要

- `evaluate.py` SHA-256：`892083db7ae6eaad73487698c38b9283c23b8172106d8b3e36021cb68470f8b4`
- `summarize.py` SHA-256：`0e013488aaba425a20aa316837eeef260a26ce4a2a2efa4e23cd1ac3301bbc6e`
- `rescore_baselines.py` SHA-256：`b825e9dae915bd5520348f41368e28df0f022e82431b2bca9486bf2d032e0627`
- `holdout_scoring.py` SHA-256：`0afc7833b9c4ade6307bae3affc36366556924e30d04b9f3c2f295419de69737`
- `holdout-scoring-contract.md` SHA-256：`3aebf63e7205d22b0f0c0463299ff67400fad93ebcf15e13e46467bbdfcd1da4`
