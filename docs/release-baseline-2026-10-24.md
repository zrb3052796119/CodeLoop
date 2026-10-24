# CodeLoop 治理基线 — 2026-10-24

**当前状态：P0 冻结提交 `9f936942` 的完整随机验收通过，main 尚未合入。** 本轮为 14,923 passed / 8 skipped / 0 failed / 0 errors，另有 3 条警告；跳过范围见下文。后续 P1/P3 合并候选仍在全量运行，已有一项未关闭失败，本记录不将其视为通过。Phase 2 已冻结，模型 API 调用为零，等待本机全量进程退出后运行。

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

该结果只证明上述源码、环境和种子下的 P0 完整套件通过（包含声明的跳过），不能证明生产任务能力或其他平台同样通过。P1/P3 合并候选 `eb17c62a5510` 的全量运行与未关闭失败另行处理。既有 CI 安装/导入 smoke 不能证明导入来源是新构建的 wheel；模块来源检查正在复核，本记录不将其作为 wheel 来源验收证据。

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

Phase 2 的 [16 个历史任务准备包](../artifacts/governance-trial-2026-10-24/phase2-preparation/README.md)和 runner 适配已提交；92 项历史 oracle 检查与 114 项 runner 离线回归已完成。P0 已通过，Phase 2 已在同一 `9f936942` 源码冻结 32 个 cell，manifest SHA-256 为 `987f7cf31627ca85f18cdf2a270dae3918c5bf6cacac68cf02053dd767da91be`。角色轮数显式配置为 12/8/15，不能称为当前默认值；首次因默认值误认而失败的 freeze 未生成 manifest、未调用 API。当前模型 API 调用为零、结果为零，等待本机全量进程退出后运行；详见[正式任务预注册与冻结记录](real-task-comparison-2026-10-24.md)。本 P0 证据包不混入 Phase 2 证据。

## 反馈回路建议

本轮先记录成本，不新增 gate。开发时用变更相关单测（秒级）和有明确边界的集成回归（分钟级）；PR/合并候选保留随机全量。本轮 P0 实测 pytest 53 分 51.54 秒，外层 53 分 54.411 秒；历史约 34 分钟不能代替此次成本。后续拆档应由实际测试耗时和隔离依赖决定，不能凭文件名把慢测试整体豁免。
