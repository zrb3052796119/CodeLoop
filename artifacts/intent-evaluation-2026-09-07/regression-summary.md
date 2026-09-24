# 意图解析现有针对性回归测试

- 命令：`python3 -m pytest -q tests/test_intent_parser_v2.py tests/test_intent_runtime_integration.py tests/test_intent_constraints.py tests/test_intent_semantic_classifier.py`
- 结果：**333 passed，0 failed，0 skipped，exit code 0**。
- pytest 耗时：1.57 秒；完整子进程耗时：2.122 秒。
- 范围：意图解析、运行时 canonical intent 复用、约束解析、语义分类器合约。语义分类器使用 fake model / monkeypatched transport，未调用真实模型或网络 provider。
- 状态隔离：根目录 `conftest.py` 在 minicode 导入前使用 `IsolatedTestHome`，每条测试重置隔离状态，并通过 `GlobalStateGuard` 检查真实 `~/.mini-code` 的保护文件和根目录。
- 污染检查：无 `REAL HOME POLLUTION DETECTED` 报告，pytest 正常退出；本次状态保护检查通过。
- 环境问题：无 localhost sandbox 拦截或其他环境错误。
- 限制：现有回归通过只说明既有测试覆盖范围符合预期，不能代替新增 50 条自然语言问题的独立准确率评估，也不证明真实模型语义回退准确率。

完整输出见 `regression.log`。
