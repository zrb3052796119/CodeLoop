# Memory v40/v41 冻结认证失败诊断

日期：2026-09-08。范围：全仓首轮报告中的 `test_memory_retrieval_production_baseline_v40.py` / `v41.py` 共 15 项失败。只读诊断；未修改生产源码、冻结 manifest、pin 或基线期望。

## 结论

这 15 项失败在修复前的真实生产源码快照上已经存在。同一份认证脚本、同字节测试文件分别运行旧源码和当前源码，均为 **15 failed、18 passed**，失败 node ID 集合完全相同，并与全仓首轮的这 15 项一致。

但不能据此说本轮没有改变受保护源码。本次多 Agent 修复改变了其中 4 个受保护文件的全部或部分内容；并行 Skill 工作还改变了其他受保护内容。认证先被既有未声明差异阻断，无法证明当前源码已获得新版本认证，更不能用这组失败推断 Memory 检索行为有无回归。

## 对照方法与可复核范围

- 旧源码来自 `/private/tmp/minicode-multi-agent-buggy-ahhcgrxd`，创建时机由 `baseline.json` 记录为修复前用户工作树。已按记录逐一验证 258 个生产 Python 文件的 SHA-256，全部吻合。
- 原始快照保持只读，测试在新的临时副本运行。两个测试文件与原始快照逐字节相同。
- v1–v40 的 40 份历史 manifest：旧快照、当前树和脚本内 pin 三者一致，全部通过校验。
- 99 个认证保护路径中，97 个源码路径来自真实旧快照。快照未保存 scripts 目录，因此使用同一份当前认证脚本、CLI 包装脚本及两个被声明为 v41 新增依赖的 evaluator 脚本作为共同 harness；这些文件的 SHA-256 已单独记录。**这不是完整历史仓库复原，而是原样共同 harness 对真实旧生产源码的对照。**两份 evaluator 仅作为文件哈希输入，不是本次阻断差异的来源。
- 使用现成 `/Users/zhourunbo/miniconda3/bin/python`，两边均执行 `python -m pytest tests/test_memory_retrieval_production_baseline_v40.py tests/test_memory_retrieval_production_baseline_v41.py -q --tb=short`。这些认证测试不依赖 loopback 服务；没有因沙箱 socket 限制失败。

| 源码 | 结果 | 首个源码准入错误 |
| --- | --- | --- |
| 修复前真实源码 + 共同 harness | 15 failed / 18 passed，1.51 秒 | v40: `unexpected v39 source differences`；v41: `unexpected v40 source differences` |
| 当前源码 + 相同 harness | 15 failed / 18 passed，1.59 秒 | 同上 |

## 实际阻断原因

`scripts/memory_retrieval_production_baseline.py:2956` 的 `build_v40_candidate` 先验证历史 manifest，然后把显式声明的 v41 变化投影回 v40 历史哈希，最后要求相对 v39 的 changed 路径集合精确等于允许集合。旧源码和当前源码经过该投影后均有 **44** 个 changed 路径，而允许集合为 **42** 个，多出的两个路径是：

- `minicode/file_review.py`
- `minicode/tools/http_utils.py`

`scripts/memory_retrieval_production_baseline.py:3040` 的 `build_v41_candidate` 要求相对 v40 的 changed 路径集合精确等于声明集合。旧源码和当前源码均有 **21** 个，而声明集合为 **16** 个，多出的五个路径是：

- `minicode/context_compactor.py`
- `minicode/file_review.py`
- `minicode/memory.py`
- `minicode/tools/http_utils.py`
- `minicode/tools/network_safety.py`

以上五个文件在旧快照与当前树中各自字节完全一致，证明这些准入阻断差异在本次修复前已经存在。没有缺少预期 changed 路径的情况。

两份 builder 都在检测缺失文件、生成候选、写入候选和 release join 验证之前检查 changed 路径集合。因此：

- v40 三项失败：两项候选/历史保留测试在首次构建候选时失败；缺依赖负例先遇到既有差异，实际异常消息与预期的 `required v40 source files missing` 不符。
- v41 十二项失败：两项候选/只读测试、一项缺依赖负例、一项 CLI 打印测试、四项候选 writer 测试及四项 release join 测试，都无法穿过同一源码准入门。缺依赖和 fsync 故障测试没有运行到原本针对的故障点。
- CLI 将认证错误投影为 `baseline_verification_failed`，进程退出码为 1；不是 Memory 检索调用或运行时异常。

15 个完整 node ID 与逐项错误保存在 JSON 和两份 pytest 日志中。这里没有把一处前置门失败解释成 15 个独立功能故障。

## 快照后新增哈希变化及归属

99 个受保护路径中，快照后实际字节发生变化的文件为以下 7 个。完整 old/current SHA-256 已记录在 `memory-freeze-diagnosis.json` 的 `sourceRows`。

| 文件 | 本次多 Agent 修复内容 | 并行任务内容 / 归属限制 |
| --- | --- | --- |
| `minicode/agent_loop.py` | 父续跑、receipt 处理 | 同时包含并行 Skill load barrier，不能将整文件归本轮 |
| `minicode/agent_runtime.py` | `final_response_text` | `skill_selection_mode` 属于并行 Skill 任务，不能将整文件归本轮 |
| `minicode/tooling.py` | `_continue_parent` | 本次多 Agent 修复 |
| `minicode/tools/task.py` | 角色权限、共享熔断、description | 同时包含并行 Skill 上下文变化，不能将整文件归本轮 |
| `minicode/config.py` | 无 | 并行 Skill 任务 |
| `minicode/main.py` | 无 | 并行 Skill 任务 |
| `minicode/tui/input_handler.py` | 无 | 并行 Skill 任务 |

归属信息由父任务及角色审查代理核对。哈希变化本身只证明字节变了，不能区分同一文件内不同任务的贡献。

其中除 `config.py` 外的 6 个文件早已处于 v41 显式声明的 changed 集合，当前额外编辑没有增加 changed 路径名；`config.py` 是声明的 v41 新增文件。因此旧/当前错误文本中的路径集合仍相同，**不代表这些文件的新内容已获认证**。v40 builder 对声明的 v41 changed 路径使用历史哈希投影，同样不能用 v40 投影确认这些新增编辑。

## 处置边界

本次保留冻结历史和拒绝认证行为。若未来需要当前树通过生产源认证，应走独立的源码差异审查和新版本认证流程，并纳入并行工作；不应改写 v40 历史、放宽允许集合或重算 pin 来消除本次红灯。当前结论仅是“这 15 项准入失败已在修复前出现，且当前存在另行记录的新源码变化”。

证据文件（均位于本目录）：

- `diagnose-memory-freeze.py`：可复运行的只读探针。
- `memory-freeze-diagnosis.json`：旧快照完整性、共同 harness 哈希、99 个保护路径的三方哈希、准入差异及失败 node ID。
- `memory-freeze-old-pytest.txt`、`memory-freeze-current-pytest.txt`：两边原始 pytest 输出。
- `memory-freeze-probe.txt`：探针输出。
- `full-suite-python313.txt:13671` 起：全仓首轮相关失败详情。
