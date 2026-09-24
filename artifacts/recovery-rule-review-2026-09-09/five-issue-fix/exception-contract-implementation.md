# T3/T4 修复与验证

## T3：路径例外必须保留完整目标关系

`_missing_requested_targets` 返回错误明确命名的缺失输入对象。`_proven_path_exception` 逐个检查失败调用输入：保持原锚定身份的目标可直接覆盖；只有被证明缺失的那个相对路径允许加前缀重定位；任意其余目标被丢弃都会拒绝。原有布尔 missing-object helper 保留兼容包装。

默认范围例外只支持真实 `list_files` 工具的空路径语义，要求可执行和输入形态一致。`pytest billing` 或其他无已识别路径的命令不再成为默认范围例外。该 helper 新增 keyword-only `tool_name`，generic/operational 两个调用点均已传入。

## T4：无效参数豁免绑定外层解析阶段

不再从错误全文任意位置收集无效参数。接受两种边界清楚的来源：独立的单条 `UsageError`/`ArgumentError` 诊断，或从消息首行开始的 usage banner，随后由本次调用程序发出的 error 诊断。执行证据（测试结果、执行栈、captured stdout/stderr 等）会否决豁免；其他程序名的嵌套 parser 也被拒绝。只从匹配诊断行提取无效参数。

`_invalid_argument_flags_named_by` 新增 `objective` 参数，由 `_without_error_named_arguments` 传入；其外层接口不变。

## 验证

新增 `tests/test_reflection_recovery_exception_contract.py`，通过真实工具 validator 和 runtime trace emitter 调用 `ReflectionEngine`，覆盖例外的准入结果。

- T3 先观察到 3 failed / 3 passed，修复后 6 passed。
- 再追加 T4，观察到 3 failed / 8 passed，修复后全部 11 passed。
- 与 `test_reflection_operational_recovery.py`、`test_reflection_generic_tool_recovery.py` 合跑：48 passed。
- 合法 `read_file` 重定位、`list_files {"path":""}`、顶层 `UsageError: --bad-option`、真实 pytest usage 格式，以及只重定位缺失目标并保留其他输入的正例均保留。
- 重跑上一轮三个真实 pytest 场景（错对象重定位、裸 billing 目录、嵌套 UsageError）：全部没有 recovery、`value_accepted=false`。完整实际命令与输出保存为 `consumer_probe_after.json`，未覆盖历史探针结果。

复跑测试：

```sh
.venv-acceptance/bin/python -m pytest -q tests/test_reflection_recovery_exception_contract.py tests/test_reflection_operational_recovery.py tests/test_reflection_generic_tool_recovery.py
```

未调用真实模型或正式记忆。生产修改仅限上述 helper、其本地配套 helper 和指定调用行；parser 区块由父代理负责。
