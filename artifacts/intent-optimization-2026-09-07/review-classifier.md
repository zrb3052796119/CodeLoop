# Classifier / configuration independent review

结论：在本次对比范围内，未发现可确认、需要修复的新增问题。此结论限于静态审查，不代表真实 provider 的响应质量或延迟已经得到验证。

审查对象是 `.repair-work/intent-phase1/baseline/minicode` 对应基线与当前 `minicode/intent_semantic_classifier.py`、`minicode/intent_semantic_calibration.py`、`minicode/config.py`，以及新增 `tests/test_intent_semantic_delivery.py`。为核对接口衔接，读取了 `openai_adapter.py` 的能力声明、参数校验、请求覆盖和诊断返回位置，以及既有预算、调用控制和类型定义。未改动实现，未调用模型，未运行保留集，未读取题目运行结果。

核对结果：

- 新增 JSON / reasoning 控制仅在模型明确声明能力时传入；新增参数也纳入调用签名支持检查。读取到的 OpenAI adapter 接口与调用方名称及取值一致，且 adapter 会再次核对官方 GLM 路由。没有发现 classifier 向未声明能力的 provider 注入这些参数的路径。
- 输出 token 默认上限从 256 调为 512，仍经过 32–1024 范围校验并由每次调用覆盖传入。预算预留、模型调用次数保留、取消、父级截止时间、本地截止时间及零重试设置的既有控制路径没有被绕过。输出内容与 provider 信封仍分别受原有字节限制约束。
- 直接构造配置与 `from_runtime` 对新字段进行类型和枚举校验；`config.py` 对环境及持久化设置沿用既有的规范化与无效值回退方式。没有发现新配置值能够扩大 token 上限或工具权限。
- `length` / `max_tokens` 即使伴随完整 JSON，也会在语义解码前被拒绝；该分支发生在用量记账之后，因此拒绝结果仍被计费，预留得到结算。新增测试覆盖了这种完整 JSON 但被 token 上限截断的情况。
- 新增诊断只包含字符计数、布尔值、已验证的 reasoning 枚举和停止原因白名单。未知停止原因映射为 `other`，没有把原始诊断、输入或输出正文追加到事件中。
- 校准契约升至 v3 会使 v2 外部校准不再匹配。由于生成控制、默认输出上限与接受条件已改变，这种失效合理；原有 builder 会安全停用语义适配器，并输出不含路径的固定警告。内置校准跟随当前契约，外部校准仍需要精确模型匹配及原有实验授权。

本审查没有修改已冻结的 `holdout.json` 和 `holdout-authorship.md`。实现读取发生在保留集冻结之后。
