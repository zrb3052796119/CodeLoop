# 五类恢复合同问题：实施与验收

本轮已修复 three-state-review 的 T1–T5，并补齐最终独立复查发现的 `-V`、不可解析/截断调用及 `pytest -q` 范围分类边界。生产改动仅涉及 `minicode/reflection_evidence.py` 和 `minicode/reflection_synthesis.py`，原有工作区修改保持；新增三个回归测试模块，共 43 项。

## 最终行为

- **参数边界**：抽取器优先使用结构化 argv；渲染调用只解码一层，只有明确的 shell `-c/-lc` 参数允许继续解析。`-k`/`-m` 的带空格表达式保持完整；按具体程序声明参数个数，pytest 的 `-q/-v/-s` 不再吞掉位置目标。未知参数保留值、顺序和重复项；畸形引号及截断标记不能形成确认身份。
- **实际执行**：帮助、版本（含大小写不同的 `-V`）、仅收集测试等模式不提供 passed verification。无法解析或截断的调用保留 unknown。`pytest -q` 有目标或选择条件时是 targeted，只有明确的默认全范围执行才是 full。
- **路径例外**：缺失对象返回具体目标集合；只允许重定位被证明缺失的那个对象，其余目标必须仍被覆盖。默认范围例外仅接受工具合同明确的 `list_files` 空路径。
- **参数错误豁免**：只接受独立的顶层参数错误诊断，或由匹配本次程序的 usage/error 包络证明的执行前拒绝。测试已执行、captured stderr、嵌套 helper 等证据阻止把外层有效的范围参数当成无效参数删除。
- **证明链隔离**：逐个 recovery 获取其独有错误集合与后续检查，时间、目标和错误关系必须在同一条链上成立。多条 recovery 同时引用不能混用这些条件。

合法的 read_file 路径修正、list_files 默认目录重试、顶层坏参数修正、同选择范围的 wrapper/展示变化及完整验证链均有正对照保留。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 38 个 reflection、recovery、记忆写入相关模块 | **841 passed in 6.35s** |
| 新增回归，当前实现 | **43 passed**（包含在上述测试面） |
| 同一套新增测试，加载本轮实际修复前源码快照 | **27 failed, 16 passed**，失败为预期断言反例 |
| 历轮探针 | **13 组复跑完成**；本轮与先前已修复反例全部拒绝，合法对照保持 |
| Ruff 未定义/未使用等 F 类检查 | **通过** |
| 独立最终复查 | `-V`、不可解析帮助输出、截断调用及 scope 边界通过 |

首轮解析测试修前 13 项失败；路径例外先复现 3 项失败，参数错误豁免再复现 3 项失败；多 recovery 的两个排列先失败。最终新增的完整 43 项又在本轮原始源码快照上验证了 27 个断言失败，避免仅靠当前实现的绿色结果判断回归有效性。

证据文件：

- [完整测试日志](surface-tests-final.txt)、[测试模块清单](test-files.json)
- [修复前快照上的同一套测试](baseline-final-tests.txt)
- [历轮探针聚合结果](all-probe-results.json)、[可重跑脚本](replay_probes.py)
- [独立最终复查](t12-final-review-independent-after.json)
- [异常例外实施记录](exception-contract-implementation.md)、[证明链实施记录](t5-implementation.md)
- [本轮 evidence 差异](reflection_evidence.py.patch)、[本轮 synthesis 差异](reflection_synthesis.py.patch)

## 范围与边界

本轮完成的是已审查的结构性误确认修复。没有调用真实模型提供方或写入正式记忆；真实 pytest 探针在临时目录执行，记忆相关测试使用仓库隔离环境。RecoveryLessonAgent 的生产接线、shadow 对照和逐结论语义支持评测仍未开展。原 synthesis 探针中“真实摘录后添加无依据推广”的已披露语义边界保持，不能把本轮结构测试通过解释为任意自然语言结论都已得到支持。
