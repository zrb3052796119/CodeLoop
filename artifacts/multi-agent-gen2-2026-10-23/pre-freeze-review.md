# Gen-2 冻结前独立审阅

审阅日期：2026-10-23。审阅者范围：准备脚本、read-context 候选三文件、registry-lifetime 元数据、预注册文档、新 runner 的实际 deadline / commit 检查 / 答案隔离路径，以及独立 auditor 和工程门分析脚本。没有运行评测模型；未修改生产源码。

最终结论：本次审阅范围内未发现仍未修复的冻结阻断问题。候选来源、read-context 行为反例、答案隔离路径、实际执行期限、提交先于模型调用的检查，以及预注册工程门实现一致。已发现的准备脚本假红和子进程解释器隔离缺口均已修。四个 gen-2 离线测试文件联合 **54 passed**，结果见 `final-local-tests.txt`。此结论允许进入正式冻结与提交；并不替代随后对真实冻结包、预注册 commit 和实际运行收据的审计。

## 历史来源与旧题排重

- read-context 的两个目标均使用真实父提交已有 API：`grep_files` 按 Unicode `splitlines()` 误把字符串数据当源码换行；`read_file` 字符 offset 越过 EOF 后把请求 offset 报成文件总长度。没有借用父版尚不存在的 `start_line/end_line` API制造失败。
- 已逐字节核对 `9aaa6a434ae0a86766d28f8b1b91529451a31eb8` 的 `minicode/tools/read_file.py` / `grep_files.py` 与准备包 shared_files 相同；reference hash 对应 `ad20e4a893248a747e3b3ac741c280ded413ff54` 的真实完整文件。reference 是父生产树加这两个文件的原始修复 blob，未人工反向构造 bug。
- 旧八题依据保留的 `three-arm-real-2026-10-13/audit-report.md` 和 `multi-agent-fixes-2026-10-13/implementation-summary.md`：digest 权威、条件裁决、remediation 补报、异步送达确认、正式结果所有权、并行 task 参数错位、视觉预算、profile/report-type 恢复。新题的触发输入、被破坏的坐标/EOF契约、修复边界不属于这些任务。
- 后续工程轮固定仍为 remediation/concurrent/vision；`implementation-progress-2026-10-14/implementation-report.md` 确实记载搜索/读取行号统一，但将其列为 **执行宿主改进**，随后真实模型任务仍是原三题。未将“宿主曾包含修复”误判为受测任务重叠。保留的 memory-corroboration、north-star、skill 题登记亦未命中此次具体目标。
- 原八题完整包已删除，因此这里是对可审计登记、报告、脚本、原回归的独立核对；不声称逐字节比过无法恢复的全部原 oracle。预注册文档已经如实声明此边界。

## 独立行为证据

在临时目录独立执行新版准备脚本前的同一 read-context 内容：父版公开检查 **2 passed**，隐藏 oracle 在真实搜索输出 `needle` 被错标为第 3 行时失败；reference 公开检查 **2 passed**、oracle通过。另单独调用父版真实 `read_file`，规范化长度为5的 `a\r\n甲😀\r\n` 在 offset=100 时错误输出 `TOTAL_CHARS: 100`，确认 EOF 子缺陷并非被首个 grep 失败掩盖的假设。

独立探针记录 `minicode.tooling`、`minicode.tools.read_file`、`minicode.tools.grep_files` 的 `__file__` 全在临时父版本目录；没有宿主修复代码串入。临时目录已清理。

root 将 oracle 改为执行全部检查并输出 JSON 后，已复核 `candidate-pack-v2/offline-proof.json`：read-context **60 项检查，父版20个目标错误、reference 0错误**；两者公开检查退出0。registry-lifetime 父版4个目标错误、reference 0；summary-budget 父版3个目标错误、reference 0。三题均为明确行为失败→成功，不是缺依赖或导入失败。

## 发现与处理

1. **已修复：准备脚本原先把任意非零 oracle 退出当作真实反例。** 初版 `prepare_multi_agent_gen2_tasks.py` 仅检查 baseline 非零/reference为0，可误接受 ImportError 或初始化错误。现 `target_failure_proven()` 要求 baseline exit=1、JSON `passed/ok=false` 且目标 errors/failures 非空，reference exit=0 且通过、无目标错误；导入异常或 exit=2 无合法收据时拒绝。read-context 已同步输出目标检查 JSON。
2. **已规范：registry-lifetime 元数据只覆盖实际修复文件。** `sourcePaths` 现在只有 `minicode/tooling.py`；background_tasks 仍来自共享父版本，未作为修复覆盖。字段统一为 baselineCommit/referenceCommit/referenceCommittedAt；新增测试授权统一为 `tests/test_eval_regression.py`。
3. **已修复：父/子 Python 隔离标志一致。** 原 child 启动继承 common runner 的无 `-I` argv，可能与 freeze probe 的依赖环境不同；最终 `launch_child` 已显式加入 `-I -B`。seed/role/材料路径/receipt/judge-policy 接口已在 runner、auditor 与自测中一致。

## 答案隔离

准备包的 `tasks.json` 保留 hidden oracle/provenance，属于宿主材料。实际执行端 `run_three_arm_real.execute_trial()` 只投影 `shared_files + case.files`；公开提示只读取 case prompt、精确授权路径和公开验证 argv。`ImplementationSpec` 同样没有 oracle、reference源码、修复说明或离线失败日志。

受测工作区在独立临时目录；原 host/gen-2材料不复制到其内部。工具读路径经过 `VerificationScope.require_tool_read()`，run_command 限于公开测试与新增测试命令。隐藏 oracle 只在 Agent 清理后写到宿主 private 目录，并由共同裁判在交付副本执行。共同裁判先验证 `minicode` import root，结束后逐模块 `_origins()` 检查；不能借执行宿主已经修好的源码通过。

本结论沿用已声明的非对抗生成测试边界，不把任意恶意 Python 的文件系统访问当作已完成 OS 沙箱隔离。

## Runner 与独立审计核对

- gen-2 私有 common runner 实际 `module.LIMITS` 设为600秒；common execute 据其写 transport deadline，`create_agent_turn_runtime(deadline_monotonic=transport.deadline)` 使用同一期限，环境 task timeout 也来自相同 LIMITS。不是仅改文案。guardian读取 execution-start 的 deadline；共同裁判单独120秒。
- `dispatch()` 在 `runner()`、runtime/adapter构造前调用 `require_committed_registration()`；缺完整SHA立即拒绝，真实 Git object 必须逐字节包含 manifest 和 registrationFiles，且 commit 是当前HEAD祖先。直接 child 入口走同一检查。
- archive解包后实际 `execve` 到该宿主；再次比对inventory、依赖环境和模块导入来源。child写execution-host收据在execute_trial之前。已有执行收据、transport账本或execution-start拒绝覆盖。
- 六格按固定 seed 的完整笛卡尔积顺序运行；旧方法被加载为私有模块，没有调用旧prepare/freeze或复用旧任务身份。真实600秒 TransportConfig 参数、提交拒绝反例、从归档宿主执行和工作区不含隐藏 oracle 均有离线测试覆盖。
- auditor 独立核对归档及宿主完整清单、预注册 Git blob、entrypoint/loadedModules 的来源与 SHA、公开基线和 oracle hash、明确 JSON 目标失败→通过证据，并沿用共同裁判对新增回归测试、清理、范围和用量的标准。gen-2 schemaVersion=1 不会绕过旧裁判的回归测试凭证要求。

## 工程门与成本解释

`analyze_multi_agent_gen2.assess()` 要求完整且不重复的 3题×2组、有效 final audit、至少4次真实修复、每组至少2次、每题至少1次，以及三个 explicit 格的真实 Executor / workflow 送达证据。源文件实际改变、隐藏 oracle、新增回归测试、范围、清理和完整用量均纳入条件或独立审计。同分3/3对3/3可通过；不加入严格优于 single 或40%协议税门。这与本代预注册一致，不能解释为统计优越性或模型单因果归因。

成本统计字段已与真实 transport 账本核对：`role`、provider usage、`seconds`、`toolCalls[].name` 均为现有收据字段。角色绝对请求、已知 token 和请求延迟分别报告；未知用量保留计数，编辑工具请求只表示尝试，非 Executor 请求占比只是描述指标。报告明确请求延迟合计不等于阶段墙钟时间、代际数字不可比。

## 离线验证记录

- 独立执行 prepare + runner 两文件：**31 passed**。
- 独立执行 auditor 文件：在允许本地进程树查询的环境 **13 passed**。受限沙箱曾因 `ps` 查询权限使既有 cleanup 拒绝；未放宽或跳过清理断言。
- root 联合执行 prepare / runner / auditor / analysis 四文件：**54 passed in 1.84s**，其中工程门10项反例覆盖未知用量、损坏账本、范围、虚假送达、缺 Executor、成功集中于两题、重复/缺格与无效审计。
- 全部为离线测试；未调用评测模型，未消费六格试次。
