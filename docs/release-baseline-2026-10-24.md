# CodeLoop 治理基线 — 2026-10-24

**当前状态：P0 `9f936942` 的本机完整随机验收已通过并推送 main；Phase 1 go 仍未成立。** 首次 P1/P3 `eb17c62a` 为 14,932 passed / 1 failed，第二次 `3cadcb75` 为 14,932 passed / 14 failed；两次均为 8 skipped / 0 errors / 3 warnings。后续生产修复及并发观察 fixture 定向验证均已记录，最新版完整 CI 验收仍待定。Phase 2 旧冻结已撤销运行用途，r2 已发布并启动 32 个正式 cell；实验正在运行，最终结果待 32 个 cell 全部完成后汇总。

main 与开发分支已于 2026-10-25 原子推送至 `ff2f18ea`；该 P0 文档/证据序列的生产及测试字节与通过的 `9f936942` 相同。其后 Phase 2 历史冻结记录 `ab17ca7e` / `ea31eed2` 已推送开发分支。P1 已提交修复包括 `54367b5d`、`1f39822b`、`693f504a`；`8fba3787` 已包含 PTY 修复；随后并发观察 fixture 修正不改该生产字节，仍等待与最新候选对应的完整 CI 证据；旧尝试失败和局部通过均保留。

## 可复现环境

- 原始开发 HEAD：`88ec62d0ca7d8b6b090c31fda1835642d7e0e603`；原始 main：`638b7dd1`。
- 本机：macOS / Python 3.12.8 / pytest 9.1.1 / pytest-randomly 4.1.0。
- 全量诊断随机种子：`20261024`；禁止 `-x`。CI 使用 `github.run_number`，每个 OS/Python 矩阵任务打印并保存相同种子和 JUnit XML。
- 测试在不可变源码副本中运行；使用独立 HOME，剥离继承的 `MINI_CODE_*` 配置。HTTP 测试允许本机 loopback；不借用真实模型凭证。
- 原始日志、XML、关键反例和初始/诊断源码哈希已整理为仓库内的[Phase 0 诊断证据包](../artifacts/governance-trial-2026-10-24/phase0-diagnostics/README.md)，逐文件与压缩包 SHA-256 见该目录的 `manifest.json`。源码副本和初始 WIP 原件仍保留在本机；证据包不重复收录源码树。
- P0 完整验收的原始日志、XML、运行元数据、汇总及运行包装脚本另存于 [P0 完整验收证据包](../artifacts/governance-trial-2026-10-24/phase0-acceptance-9f936942/README.md)。旧诊断包保持原字节，未用通过结果替换失败证据。

## P0 完整随机验收

| 项目 | 记录 |
| --- | --- |
| 源码 | `9f9369421486f018c837f84d21bd727aed6ab326`，干净源码的独立本地 clone，detached HEAD |
| 源码归档 SHA-256 | `7e497a3ac5022f9fa1f9b8b6f15981d04f8c3f4ace644ab9eefd9600aae764d5` |
| 时间（Asia/Shanghai） | 2026-10-24 23:16:53.215103 → 2026-10-25 00:10:47.626483 |
| 执行 | `python -m pytest tests -vv -ra --tb=short --randomly-seed=20261024 --junitxml=…`；无自定义逐用例诊断插件，完整 argv 保存在 manifest |
| 结果 | 14,923 passed / 8 skipped / 0 failed / 0 errors / 3 warnings，退出码 0 |
| 耗时 | pytest 汇总 3231.54 秒（53 分 51.54 秒）；外层进程 3234.411 秒；JUnit suite 3230.704 秒，计时边界不同 |

收集输出为 **14,927 items / 4 skipped**：14,927 个实际用例 = 14,923 通过 + 4 个运行时跳过。JUnit 另为 4 个收集阶段跳过的模块生成条目，因此 XML 是 **14,931 条 testcase / 8 skipped**。这不是多跑或漏跑 4 个用例，也不能推断未收集模块包含多少测试。

8 项跳过逐项保留如下；未将跳过项计为通过。

| 阶段 | 节点（均在 `tests/` 下） | 原因 |
| --- | --- | --- |
| 收集 | `test_implementation_progress_analysis.py:11` | 无 `numpy`，`could not import 'numpy': No module named 'numpy'` |
| 收集 | `test_implementation_regression_analysis.py:11` | 同上 |
| 收集 | `test_single_vs_multi_analysis.py:15` | `Optional experiment-analysis dependency`；该处 `numpy` importorskip |
| 收集 | `test_three_arm_analysis.py:15` | `Optional analysis dependency`；该处 `numpy` importorskip |
| 运行 | `test_evaluation_header_timeout.py::test_verified_https_slow_headers_keep_hostname_and_certificate_checks[tls]` | `ephemeral local TLS certificate fixture` |
| 运行 | `test_evaluation_header_timeout.py::test_untrusted_https_certificate_is_still_rejected[tls]` | `ephemeral local TLS certificate fixture` |
| 运行 | `test_integration.py::TestLiveAPI::test_simple_question` | `ANTHROPIC_API_KEY not set — skipping live API test` |
| 运行 | `test_integration.py::TestLiveAPI::test_tool_use_via_api` | 同上 |

3 条警告均为 Python 3.12 的 `DeprecationWarning`：多线程进程调用 `fork()` 可能使子进程死锁。触发它们的测试均通过，但警告仍保留：

- `tests/test_memory_curator_schedule.py::test_forked_child_does_not_clean_up_an_inherited_transaction_fd`，位置为该文件第 985 行。
- `tests/test_memory_curator_schedule.py::test_forked_child_cannot_inherit_nested_scheduler_lock_authority`，位置为标准库 `multiprocessing/popen_fork.py:66`。
- `tests/test_memory_lock_sharding_red.py::test_forked_manager_cannot_publish_inherited_dirty_project_snapshot`，位置为该文件第 869 行。

该结果只证明上述源码、环境和种子下的 P0 完整套件通过（包含声明的跳过），不能证明生产任务能力或其他平台同样通过。旧 CI 安装/导入 smoke 不能单独证明 wheel 来源；后续已完成独立 wheel 来源验证，见下一节。

## 首次 P1 全量与后续修复

首次组合候选 `eb17c62a5510b6746c5fc677618303c9b83b96e6` 已完整执行，seed `20261024`：**14,932 passed / 1 failed / 8 skipped / 0 errors / 3 warnings**，pytest 2889.76 秒、外层 2892.346 秒，退出码 1。唯一失败为 `tests/test_memory_hot_store.py::test_nested_budget_caps_cumulative_wait_across_multiple_commands` 的 `200.36325 ms < 200 ms` 断言；不是新增 context-retention 行为断言失败。完整 XML SHA-256 为 `32ee65f8c431da2096afedbaf0970e7cc3b5a9c4cde4a7a51b6e3c313ceb8b87`。

第二次本机完整尝试 `3cadcb7584526b41af7cc928d6c58d5bb19d8ea5` 已结束，seed `20261024`：**14,932 passed / 14 failed / 8 skipped / 0 errors / 3 warnings**，pytest **2631.92 秒**、外层 **2634.330 秒**，退出码 1。14 项失败全部属于 v40/v41 staged source 声明；`827c1751` 已提交相应修正，不能倒填为旧尝试通过。

[两次本机完整尝试证据包](../artifacts/governance-trial-2026-10-24/phase1-acceptance/README.md)同时保留 `eb17c62a` 与 `3cadcb75` 的完整日志、XML、元数据和失败节点，共 **21 个成员**，gzip **1,212,390 字节**，SHA-256 `048f611bce563fd55c8c56bf361046379d6354b36a43b2c93aed1d64d913a5c6`。包内 `repairedAcceptancePassed=false`；它记录两次本机失败尝试，不是最新 PR 的完整验收。JUnit suite 时间分别为 2889.078 / 2631.148 秒，与 pytest 汇总和外层计时边界不同。

| 后续提交 | 已验证范围 | 可恢复证据与独立审查 |
| --- | --- | --- |
| `71347fd5` hot-store 测试/文档 | 生产 AST 不变；累计等待余额、真实 worker 和迟到命令拒绝保留；347 项邻接通过，3 种人工故障各被拒绝 | [证据包](../artifacts/governance-trial-2026-10-24/hot-store-budget-proofs/README.md)，含 `independent-review/`；包内全量 trace 待输出属于当时状态 |
| `d0f728e0` 现有 wheel 检查器 | 同字节测试旧源 3 failed / 2 passed → 修复后 5 passed；首次导入即优先 installed target 并逐模块检查来源 | [证据包](../artifacts/governance-trial-2026-10-24/wheel-import-proofs/README.md)，含 `checker/` 与 `independent-review/summary.md` |
| `37761457` Windows portable 发布 | 关闭临时句柄后保留所有路径检查；同字节 2 failed → 2 passed，portable 10、安全/发布 96、packaging 9 passed | [证据包](../artifacts/governance-trial-2026-10-24/windows-portable-publication-proofs/README.md)，含 `independent-review.*` 与模块隔离证明 |

独立 wheel-only 检查已分别从精确 `9f936942` 和 `eb17c62a` 构建/安装实际 wheel，用 `python -I -S` 验证各自 **9 个指定模块**的 resolved `__file__` 均在 installed target，sys.path 无源码、editable 或 site-packages。记录位于上述 wheel 包的 `wheel-only/`；不外推到全部递归依赖。

原 CI run `40111546762` 的 Windows 3.11/3.12 job 确认是既存 `_publish_portable` 的 WinError 32，故障文件在初始 `88ec62d0`、`9f936942`、P0 main 字节相同。同一 run 的 Ubuntu Python 3.11.17 与 macOS Python 3.11.9 内部错误均来自既有 `test_run_command_lifecycle.py:246` 全局猴补 `os.name="nt"`：`run_command.py:288` 的 `Path(command).name` 选择不支持的 WindowsPath，随后 pytest 报告/退出也崩溃。Ubuntu 原 JUnit 4934 tests / 0 failures / 1 internal error / 7 skipped；旧 fixture 已在真实 Python 3.11.15 复现。`7b8157e7` 修复只使用测试模块局部 `os`/`sys`/`subprocess` 代理，生产字节不变；Python 3.11/3.12 各 124 项相关通过，最终同字节目标测试在 exact P0 1 passed，2 个合成坏 flags/session 反例仍正常断言失败。[fixture 修复和原始 CI 证据](../artifacts/governance-trial-2026-10-24/run-command-fixture-proofs/README.md)已归档，不将其归为生产 bug。上述定向结果不代替新版组合候选的完整 CI 验收，Phase 1 go 尚未成立。

`3c844a39` 的原生 [CI run `40115957909`](https://github.com/zrb3052796119/CodeLoop/actions/runs/40115957909) 已观察到 Windows 3.11/3.12 的 packaging 与 clean-archive 步骤通过；这不是 Windows job 或完整套件通过。**包含 PTY 修复与本次并发观察 fixture 修正的最新版候选尚待对应完整 CI 验收，Phase 1 go 继续 pending。**

旧 CI `40111546762` 的 Ubuntu 3.12 完整结果为 14,911 passed / 2 failed / 18 skipped；两项失败分别源于 fixture 依赖目录枚举顺序、在真实 outer Future 完成前过早发出信号。`c6d1e485` 已提交修正，仅调整测试前提，相关 60 项（seed `20261024`）与 124 项（seed `42`）通过，但范围有重叠、不相加。`827c1751` 已将审查通过的 Windows publisher 生产改动补入未冻结 v41 的精确 staged source 声明：相同最终测试旧声明 15 failed / 19 passed，新声明 34 passed，相关 179 项通过；40 份历史 pin 字节不变，不激活 v41。原始失败、独立审查及边界见[跟进证据包](../artifacts/governance-trial-2026-10-24/ci-followup-proofs/README.md)。

最新修复已分组提交：`54367b5d` 使用显式宿主 Python fixture，并为已注册 toolchain 的祖先目录保留 metadata-only 查询；文件内容读取与沙箱拒绝断言保留。`1f39822b` 用局部时钟、真实 reader 和 Future 完成同步修正两项调度前提，没有延长 sleep 或放宽墙钟阈值。`693f504a` 修复 evaluation transport 将 durable ledger 消费滞后误判为 idle：先检查已收到的队列项，仍按 reader 时间拒绝真实 receive gap，并保留绝对 deadline、取消、EOF/error 与字节上限。沙箱邻接 **108 passed**、timing 两文件 **77 passed**、transport 相关 **87 passed**；其中 transport 最终同字节 10 个边界用例在旧源 **3 failed / 7 passed**、新源 **10 passed**。这些范围有重叠，不能相加。原始失败、反例、故障注入和独立审查见[macOS 跟进证据包](../artifacts/governance-trial-2026-10-24/macos-ci-followup-proofs/README.md)。

旧 CI `40114448888` 的 macOS Python 3.11 集成节点 `test_run_command_via_agent` 收到成功但空白的输出。真实 PTY 控制实验分别证实：Darwin 最后一个 slave 关闭可丢弃未读数据；旧空 select 后的退出分支也会跳过已排队输出。原 CI 的具体交错未知。本次仅修复 `_run_with_pty`，保留 slave 至排空结束，先清理后代进程，再以零超时 readiness 查询排空已有数据；取消、绝对 deadline、输出上限和异常关闭均保留。最终相同测试字节在旧源 **4 failed / 2.86 秒**、修复后 **4 passed / 1.48 秒**；相关 **19 passed / 3.40 秒**已包含这 4 项，不能相加。独立真实大输出 smoke 与只读审查通过；最终空 readiness 的取消/截止反例及人工删 checkpoint 故障证明另列。见[PTY 跟进证据包](../artifacts/governance-trial-2026-10-24/pty-output-followup-proofs/README.md)：44 个成员，gzip 28,986 字节，逐项实际恢复校验通过。**本次修复尚无对应完整 CI 验收；未改 ROOT 的 Phase 2 冻结源码，也不重跑其 32 个 cell。**

旧 `3c844a39` 的 CI `40115957909`（macOS Python 3.11）另暴露 `test_repeated_candidate_repair.py` 将并行 verifier 回调完成顺序误作请求顺序。真实 Barrier/Event 交错让第二轮 regression 先于 compat 完成，旧 fixture 精确复现 `[0, 2, 1, 0]` 断言失败，同时工作流已 committed、模型调用数仍为 7。本次**仅改测试，生产与脚本仍与已推送 `8fba3787` 相同**：按 assignment/patchDigest 分轮次、checkId/argv 绑定结果，拒绝重复/缺项；仍精确要求 compat=0、regression=2→1→0，Analyst 按 regression 身份取失败，未排序裸退出码。同一生产和受控顺序下，旧 fixture **1 failed / 8.28 秒**、新 fixture **1 passed / 8.90 秒**；无插件单节点和全轮逆序分别 **1 passed / 8.31 秒**、**1 passed / 8.78 秒**，均为同一测试的执行，不能累加。最终断言拒绝六类人工关联错误，独立只读审查 GO；见[并发结果关联证据](../artifacts/governance-trial-2026-10-24/repeated-repair-order-proofs/README.md)，29 个成员、gzip 23,858 字节，实际恢复逐项校验通过。未重跑 quality 或完整套件；运行中的 `8fba3787` CI 保留原身份，不能倒填本次 fixture，最新候选完整 CI 仍待定。ROOT 与 Phase 2 源码未改。

CI `40118490617`（macOS 3.12）与 `40120281015`（Ubuntu 3.12）的 caller 失败暴露同一测试前提缺口：60ms caller 期限在 transport 初始化前开始，15ms heartbeat wait 也不保证实际间隔低于 50ms idle。本次仅用局部时钟和真实 reader/queue/SQLite 握手建立这些前提，保留 caller/idle/cancel、精确计账与关闭断言，并补已过期 caller 不计未开始请求的反例；生产与脚本仍同 `8fba3787` / `715dd122`。最终字节相关 **113 passed / 4.91 秒**，含恢复后本文件 **15 passed / 0.91 秒**，不相加；四个合成控制/计账故障均被拒绝，不称历史生产 bug。两次 CI 原迹、受控前后证明、无效尝试及最终独立只读审查见[请求时序证据包](../artifacts/governance-trial-2026-10-24/continuous-request-timing-proofs/README.md)。最新版完整 CI 仍待完成，不据此宣称 Phase 1 go；ROOT 与 Phase 2 输入未改。

历史离线 quality（PTY 修复前）为 current **88/88**、a **57/57**，两次 remote calls 均为 0；执行前后核验的 `minicode/**/*.py` 内容集合 SHA-256 均为 `fff5e4db7d261650dd431587dd0bbb6c016474bc6ecada3555679b7b3d1fd3f8`，对应本次 PTY 修复前的源码；该摘要不覆盖新增的 PTY 改动。元数据的 HEAD 为当时的 `3c844a39`，实际源码摘要包含随后提交的修复；这是离线开发集重放，不能凭相同生产字节推断完整测试或新的 transport 路径已通过验收。

当前离线 quality 已在 P1 HEAD `715dd1226ef6994990283994ab8db9293185b176` 加请求时序 fixture（SHA-256 `f73325d0f18305d10cee66b636f7aa118c39020fc19589c2a1610065864852ce`）上各执行一次：current **88/88 / 14.28 秒**、a **57/57 / 15.06 秒**，两次 remoteCallCount 均为 **0**。运行前后 439 个源码/脚本/fixture 文件 SHA 映射相同；其中 319 个 `minicode/**/*.py` 的生产内容集合 SHA-256 为 `4ce824100db5350ba47aae0bdd96bc74ed3a09948cdfb08195a39abfee4a35a7`，已覆盖 PTY 修复。原始 JSON stdout、空 stderr、元数据、来源映射、实际 wrapper 及摘要计算口径见[本次 quality 证据包](../artifacts/governance-trial-2026-10-24/phase1-post-policy-quality/README.md)。这是离线开发集重放，不是模型任务成绩，两个 profile 范围不累加；最新版完整 CI 仍待完成，不以这些结果替代 Phase 1 完整验收。


较早修复候选的 `compileall minicode tests` 与 scoped Ruff 结果、10 文件 SHA 已随两次本机尝试归档（`phase1-repaired-static-checks.json`）；它们绑定当时源码，不覆盖随后新增的测试、声明或 transport 修改，也不替代最新完整 CI。

## 已验证项目

| 项目 | 结果 | 限制 |
| --- | --- | --- |
| `compileall` + `ruff check minicode --select=E,F --ignore=E501` | 通过 | 是项目现有 scoped lint，不宣称全仓全部规则零问题 |
| `evaluate_agent_quality.py --profile current` | 88/88 checks，通过 | 离线开发集，不是实际任务能力证明 |
| `evaluate_agent_quality.py --profile a` | 57/57 checks，通过 | 同上；冻结输入及阈值未改 |
| MCP SSE 错误路径 | 新测试 4/4 通过；初始源码 3 failed/1 passed | 同字节测试确认 NameError 修复，非简单文字断言 |
| focused include search | 19/19 通过；相关 139/139 通过 | 16 个旧测试未 opt-in bounded；是 fixture 契约迁移，不是生产权限修复 |
| v41 staged closure | 24/24 通过；相关 193/193 通过 | 40 份历史 pin 未改；active 仍 v40；不代表 v41 获准激活 |
| functional audit | 207 能力、59 注册工具、0 findings；7 项审计回归通过 | 129 pass / 55 partial / 1 unavailable / 6 blocked / 16 not_reachable；不把未测能力算通过。实际历史源码反例 5 failed / 2 passed |
| 原有 core WIP 依赖组 | 随机 76 文件：1060 passed / 1 failed，641.94 秒；提示契约修正后该文件 18 passed | 失败为旧提示词断言；原始日志保留，不表述为 1061 项同次全绿 |
| skill-chain 干净副本依赖 | 本地及无 `.repair-work` 副本各 29/29 通过 | 最小 evaluator/scorer 源码及冻结输入已纳入版本控制 |
| 报告压缩归档 | 恢复/公开链接 13/13；相关消费者 132/132 通过 | 仅归档 4 份生成报告；8 份大测试/回放输入保留原路径与字节 |
| 请求预算及失败诊断审计 | 相同最终测试：当前 4 passed、冻结 R2 4 failed；相关随机 100 passed | 预算计入实际选中工具 schema；不增加原测试总预算，不绕过原子准入；审计仍拒绝缺失或未来 Analyst 证据 |
| checkpoint 未执行证明 | 相同最终测试：当前 2 passed、冻结 R2 2 failed；相关随机 86 passed | 允许合法非负准备耗时；仍要求 sandbox unavailable、无退出码及空输出，保留篡改反例 |
| 历史提交授权及长 trace 投影 | 相同最终测试：旧生产 2 failed；当前历史授权 7 passed、重复修复投影 2 passed、相关 15 passed | 仅明确 policy 2/3 可用完整当前 Verifier 授权；当前 policy 6、旧 patch、缺失 checks 仍拒绝。紧凑输出保留最终链，完整失败轮次仍留在持久审计 |
| 失败写入续跑 fixture | 当前 2 passed；同一最终 fixture 对冻结 R2 也 2 passed | 明确本地单次调用耗尽及独立审查；保留同 Executor、真实再验、审计与提交断言，属于 fixture 迁移 |

## 全量诊断记录

| 尝试 | 不可变诊断提交 | 结果 |
| --- | --- | --- |
| R1 | `bd86d0198b00f32a6afd2b4d6aea1bdb1999547b` | 收集阶段 2 errors / 4 skipped，36.99 秒；两个跟踪测试依赖忽略目录，已修复 |
| R2 | `cb327c3796b0d34fb3235a15fec6f1988f072adf` | 收集 14,849 items / 4 skipped；完成 5355 passed / 58 failed 后，诊断日志插件被禁止文件访问的测试拦截并中断（1809 秒）；不是全量结果 |
| R2 未完成节点诊断续跑 | 同一 R2 源码哈希 | 9271 passed / 139 failed / 8 skipped / 22 errors / 5413 deselected；pytest 2382.44 秒，外层进程 2385.16 秒；不是完整套件验收 |

R2 的局部事件日志、完整原始错误及续跑 XML 均保留在诊断证据包内。续跑按第一次已完成的 call 节点排除，改变了同一进程中的前导测试集合；两次数字不能拼接成一次完整套件结果。上述 P0 验收已从头运行，且未加载该逐用例文件日志插件。

这些诊断提交位于本机临时副本。R2 启动后新增的修复不能倒填到 R2 的源码身份；修复后的 P0 完整验收使用独立记录的 `9f936942`。

原报告的 `14,522 passed / 359 failed / 22 errors` 没有取得对应源码和原始日志。本轮已用冻结源码和最小前导顺序证明三个独立污染源：

| 来源 | 原始反例 → 修复后 | 修复边界 |
| --- | --- | --- |
| 跨进程 session 测试在父进程修改全局存储路径 | 单跑隔离文件 16 passed；前导后 15 passed / 2 failed → 同序列 17 passed，相关 32 passed | 测试改用 monkeypatch；不改隔离断言或通用 conftest |
| 旧双臂评测 runner 给共享 `task` 定义留下当前 trial 的范围包装 | 原始前导 + 五个独立绿节点：1 passed / 5 failed → 仅换 runner 后 6 passed；相关 55 passed | runner 复制工具定义再包装；最终新断言对旧 runner 仍失败，workflow 受害测试未改弱 |
| lazy-import 测试删除 `sys.modules` 后未恢复工具模块身份 | core 导入测试 → full registry 重导入 → HTTP 节点：2 passed / 1 failed；去掉中间重导入则通过 → 修复后相关 86 passed | 将原导入检查放进独立解释器；HTTP 受害测试字节与 R2 相同，当前与 R2 生产均通过 |

这些证据解释已复现的失败族，不能外推原报告全部 workflow 失败。其余诊断分为真实生产缺陷、fixture 契约漂移、测试环境限制和诊断工具失效；原始红日志、无效基线尝试及每组范围均保留。真实缺陷包括 MCP SSE 错误路径的未定义变量、阶段输出预算漏算工具 schema、v6 失败诊断后的 Analyst→Reviewer 审计绑定、checkpoint 把准备耗时误作执行证据，以及历史 verifier-only 提交授权和多轮修复后的长 trace 投影错误。fixture 迁移则显式选择 bounded/full 模式或 Analyst/Reviewer 路由，并提供当前要求的真实独立检查及分页证据，不据此宣称生产修复。todo 测试中同文件共享默认列表的污染也已用局部 monkeypatch 隔离，不与上述跨文件最小前导证明混为一谈。

Phase 2 的 [16 个历史任务准备包](../artifacts/governance-trial-2026-10-24/phase2-preparation/README.md)和 runner 适配已提交；92 项历史 oracle 检查与 114 项 runner 离线回归已完成。Phase 2 的旧 `9f936942` 冻结（manifest SHA-256 `987f7cf31627ca85f18cdf2a270dae3918c5bf6cacac68cf02053dd767da91be`）已在 0 模型试次 / 0 结果时撤销运行用途，[原件](../artifacts/governance-trial-2026-10-24/phase2-freeze/README.md)保留供审计。transport 修复已单独 cherry-pick 到实验分支 `8b15640a`，该分支的 `minicode` 仍为 P0，未混入 Phase 1 抽取。[r2 冻结更正](../artifacts/governance-trial-2026-10-24/phase2-freeze-r2/README.md)已随 `2706e00e` 发布，新 manifest SHA-256 为 `32dacc257169eff53026a3b8e1663ed68b62ceae1adac118589073921d47d332`；16 个任务内容、32 个 cell 顺序与验收条件不变，角色轮数仍显式配置为 12/8/15，经济预算为 `null`。**32 个正式 cell 已启动；实验正在运行，最终结果待 32 个 cell 全部完成后汇总。** 实验分支的生产、脚本和测试字节保持冻结至报告，不将离线检查计入模型成绩。首次默认值误认导致的 freeze 失败也未调用 API；详见[正式任务预注册](real-task-comparison-2026-10-24.md)。本 P0 验收结果不混入 Phase 2 证据。

## 反馈回路建议

本轮先记录成本，不新增 gate。开发时用变更相关单测（秒级）和有明确边界的集成回归（分钟级）；PR/合并候选保留随机全量。本轮 P0 实测 pytest 53 分 51.54 秒，外层 53 分 54.411 秒；历史约 34 分钟不能代替此次成本。后续拆档应由实际测试耗时和隔离依赖决定，不能凭文件名把慢测试整体豁免。


旧 `3cadcb75` 的 Windows 完整 CI 另有系统性平台失败：Python 3.11 为 13,222 passed / 1,390 failed / 246 errors / 112 skipped，3.12 为 13,217 passed / 1,395 failed / 246 errors / 112 skipped。主簇涉及可信 Git 查找、POSIX 文件权限/信号和路径契约。用户已明确批准本期正式支持 Linux/macOS、原生 Windows 标为实验性，并保留完整失败诊断与移植清单；[平台范围](platform-support.md)和独立 Windows diagnostic workflow 已据此更新。未批量跳过或将失败改成成功，Windows 打包步骤通过仍不能代替完整平台验收。
