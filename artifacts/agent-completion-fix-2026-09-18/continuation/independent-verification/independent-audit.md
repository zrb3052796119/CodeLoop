# CodeLoop 续办独立验收

针对 `run_b3bc7136802f466f99680ea79000d9ff` 结束后的最终文件进行检查。结论：**原始“目录超过十个文件时漏跑、误报通过”缺陷已修复，Agent 新增的三个测试能拒绝真实旧版。完整任务交付仍未完成，运行因费用预算耗尽返回了部分结果。**

这些命令由宿主在模型停止后独立执行，不计入 CodeLoop 自主完成的工具调用或测试证据。没有调用模型、修改目标源码/测试、提交代码或访问网络。

## 结果

| 验收项目 | 真实结果 | 证据 |
| --- | --- | --- |
| 12 文件末位失败 | 实际执行 12 个文件，11 passed / 1 failed；工具正确返回失败 | `oracle-after.json` |
| 12 文件全部通过 | 实际执行 12 个文件，12 passed | `oracle-after.json` |
| 指定一个通过文件 | 只执行该文件，1 passed，未扩展到邻近失败文件 | `oracle-after.json` |
| Agent 原样最终新增测试 | 修复后 3 passed，旧生产版本 3 failed，双方均 33 deselected | `regression-proof/proof.json` |
| 整个相关测试模块 | `tests/test_tools.py`：36 passed | `focused-module.stdout.txt` |
| 改动文件 lint | 项目 CI 的 E/F 规则通过，沿用既有 E501 排除配置 | `lint.stdout.txt`、`target-checks.json` |
| diff 格式检查 | `git diff --check` 返回 0 | `target-checks.json` |
| 目标文件未被验收改写 | 两个冻结文件 SHA256 与验收前一致，Git 状态一致 | `post-verification-hashes.json` |

## 对照依据

目标目录为 `/private/tmp/codeloop-completion-recheck-20260918`。冻结时 HEAD 为 `638b7dd1f07c56b7fa864832f70cdcafc0cb3b0b`，仅两份跟踪文件有修改：`minicode/tools/test_runner.py`、`tests/test_tools.py`；还有运行前已存在的未跟踪运行锁。

`agent-generated.patch` 和 `frozen-final/` 保存了模型结束时的最终改动，`frozen-manifest.json` 保存路径、大小、SHA256 和新增测试节点。

旧版由该确切 Git 提交创建隔离工作树。只复制 Agent 最终的 `tests/test_tools.py`，不复制生产修复，并在相同解释器、环境策略、测试选择下执行同一组测试：

```text
python3 -m pytest tests/test_tools.py -k 'runs_every_discovered_file_beyond_ten or reports_failure_in_file_beyond_ten or never_reports_pass_when_output_reports_failures' -q --tb=short
```

测试文件 SHA256：`5cda3a97ea5d9031c446204b242e350773109bf56942fedca46bffea081c4c74`。

旧版的三个失败均命中对应断言，而非依赖、导入、权限或夹具错误：第一个发现命令漏掉最后两个文件；第二个使用真实 pytest 子进程证明末位失败被误报为成功；第三个针对额外新增的输出判定行为，以模拟的退出码 0 / 输出包含失败场景断言失败。第三个测试不应被描述成原始十文件限制缺陷的额外真实子进程复现。

外部 oracle 使用真正的 pytest 子进程和测试落盘标记独立计数，且校验导入的是目标目录的生产模块。原 oracle 脚本未更改。

## 结论边界

模型实际最终回复仍是费用预算耗尽的部分结果；独立测试通过不能把该任务改判成完整交付。该实测没有证明多 Agent 调度或 PR 交付。

此次 Agent 还扩展了超过 200 文件的目录收集分支及输出文本判定逻辑。只读审查发现目录分支提前返回可能漏传 `pattern`、`verbose` 和 `coverage`；这些新增分支的定向探针已由另一个审计任务执行。本报告只确认上表所列原始契约，不将它们外推为全部新增行为正确。相关新缺陷结果应一并纳入最终报告。
