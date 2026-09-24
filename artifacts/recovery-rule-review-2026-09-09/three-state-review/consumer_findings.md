# 三态合同消费者复审

上一轮 `objective-contract-review/consumer_probe.json` 的 7 个 Engine 反例现在均没有 recovery、没有可持久化 claim；episode 也仅保留原 cwd 的真正重试。复验结果见 `previous_consumer_results.json`。

以下两类新问题均使用临时目录中真实 pytest 输出，经真实 run_command validator、runtime trace emitter 和 ReflectionEngine 复现。无模型调用、无正式记忆写入、无生产代码改动。

## [P1] 路径例外没有将“缺失对象”“重定位对象”和完整输入覆盖绑定起来

位置：`minicode/reflection_evidence.py:1246–1255`，尤其 `1248–1250`；底层 `1426–1429` 使用任意路径对后缀匹配。消费者位于 `2515–2524`、`2604–2612`。

- 真实首跑：`python -m pytest tests/test_auth.py tests/test_missing.py -q --tb=short`，退出 4，报告缺少 `tests/test_missing.py`。
- 后续：`python -m pytest backend/tests/test_auth.py -q --tb=short`，退出 0，1 passed。缺失的 `tests/test_missing.py` 仍然不存在。
- 合同为 `conflict`，但“任一输入被证明不存在”加“另一个输入存在加前缀路径”使例外通过，生成 `confirmed` 且 `value_accepted=true`。

同类 default-scope 边界：缺失 `tests/test_auth.py` 后运行 `pytest billing`，真实目录 billing 中测试通过。`billing` 为 bare operand，`1255` 把无 `input_paths` 错当默认范围，同样确认错误恢复。

修复边界：缺失路径解析应返回具体对象；只有该对象的被证明重定位能够放宽对应路径身份，其他输入仍须覆盖。`not success.input_paths` 不等于已证明默认范围；显式 bare operand 或未知工具语义不能使用默认范围例外。

## [P1] 嵌套程序的参数错误可以豁免顶层有效的验证范围参数

位置：`minicode/reflection_evidence.py:1274–1280` 全文扫描错误消息；`1290–1295` 从顶层合同移除参数。operational 消费者在 `2593–2603` 于关系判断前应用豁免，generic 消费者同样受影响。

真实 pytest 场景：顶层 conftest 注册 `--run-integration`，该参数决定是否运行集成测试。集成测试内部调用 argparse 的 `nested-helper`，其 stderr 为 `unrecognized arguments: --run-integration`。

- 首跑顶层 pytest 带 `--run-integration`：参数被正确接受，测试执行，退出 1，1 failed / 1 passed。
- 后续移除该顶层参数：退出 0，1 passed / 1 skipped。失败的测试被跳过。
- 原始目标关系为 `conflict`，但全文中的嵌套错误使顶层参数被豁免，生成 `confirmed` 且 `value_accepted=true`。
- 成功后再次带原参数运行，仍退出 1。仅去掉 nested-helper 错误中的匹配语句的诊断对照，则 `value_accepted=false`，定位到新增豁免逻辑。

修复边界：参数豁免需要证明被拒绝的是本次顶层调用的参数解析阶段，而非测试内部、子进程或日志中的同名参数；已经有测试实际执行并失败的证据时，不能靠全文参数错误短语放宽范围冲突。无法确认阶段或错误来源时保留冲突。

复跑：

```sh
.venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/three-state-review/consumer_probe.py
```

完整实际命令、返回码、输出、runtime trace、合同、recovery 和可持久化 claim 均保存在 `consumer_probe.json`。
