# CodeLoop 治理试行：执行记录与批准方案

开始日期：2026-10-24（Asia/Shanghai）；试行期两周。
初始开发提交：`88ec62d0`；初始 main：`638b7dd1`。

## 执行约束

- 保留初始未提交修改；原始 diff、index、源码 SHA-256 和副本已保存到本机临时诊断目录。
- 不 stash/reset 当前工作树，不重写历史。按验证结果分组提交。
- 无全绿证据或明确批准的豁免，不合入 main。
- 冻结新 controller / cybernetic / memory_* 模块及新 gate。
- Phase 1 先测量，按 go/no-go 决定是否抽取；Phase 2 先预注册真实任务验收条件。
- 用户已指定 Phase 2 主模型 GLM 5.3 Flash，不设 API 费用上限；仍记录每次运行成本和停止条件。
- 个人 Markdown 已退库并迁入本地私人笔记目录；临时恢复备份保留，不上传笔记正文或私人目录。

## 进展

- [x] 保存初始工作树基线。
- [x] 修复 3 项 scoped Ruff 错误；MCP 同字节测试反例证明通过。
- [x] 搜索测试显式选择 bounded 模式，并补 continuous/越界对照。
- [x] Memory v41 staged source closure 修复及历史 pin 验证。
- [x] 审计工具集合/汇总计数验证。
- [x] R2 诊断失败分类与定点修复：已证明 session 路径、评测 task 包装、模块缓存三处顺序污染；保留红绿反例。
- [x] P0 修复分组提交；冻结提交 `9f9369421486f018c837f84d21bd727aed6ab326` 完成随机全量验收：14,923 passed / 8 skipped / 0 failed / 0 errors / 3 warnings，seed `20261024`。
- [x] P0 已合入并原子推送：main 与开发分支一并推至 `ff2f18ea`，含通过的 P0 源码和验收文档；其后 Phase 2 冻结记录 `ab17ca7e` / `ea31eed2` 已另行推送开发分支。P1 后续已通过 PR #2 合入 `ed21acb3`，见下方当前进展。
- [x] P0 源码内的 Phase 3 整理：指定杂物、个人文件、4 份可复现大报告归档；当时保留 8 份测试/回放大输入；license 来源核验与 mock 文档修正。
- [x] Phase 1 D1/D2、生产抽取及独立审阅完成；8 份 pinned 输入已独立归档。首次合并候选 `eb17c62a5510` 全量结束：14,932 passed / 1 failed / 8 skipped / 3 warnings，2889.76 秒；唯一失败是 hot-store 累计等待测试的 `200.36325 ms < 200 ms` 墙钟断言。
- [x] Phase 1 GO：`e6492f9b` 已满足 497 行净减、4 个目标闭包迁出、最多 11 个参数及完整支持矩阵要求；CI `40130139264` / seed `50` 通过，已通过 [PR #2](https://github.com/zrb3052796119/CodeLoop/pull/2) 合入 main `ed21acb3`。来源与跨 attempt 边界见[最终支持矩阵](../artifacts/governance-trial-2026-10-24/phase1-supported-matrix/README.md)及 [release baseline](release-baseline-2026-10-24.md#phase-1-最终支持矩阵)。
- [x] main seed `52` 的 deadline fixture 失败已修复；精确候选 `fbfed052` 的 seed `53` 六项正式 CI 全部通过、无重跑，四个正式平台目标两参数均实际通过。2026-10-25 10:44:17 经 [PR #3](https://github.com/zrb3052796119/CodeLoop/pull/3) 合入 main `8c0a9582`。[PR #3 正式 CI 证据](../artifacts/governance-trial-2026-10-24/pr3-formal-ci/README.md)与原 seed `52` 红例分别保存，生产未改；自然 main seed `54` 不混作 seed `53` 证据。
- [x] Phase 2 准备包与 runner 适配已提交：16 个真实历史任务、32 个配对 cell、92 项历史 oracle 检查、114 项 runner 离线回归。
- [x] Phase 2 旧冻结已撤销实付运行用途并保留审计；transport 单独修复进入实验分支 `8b15640a`，`minicode` 仍为 P0，任务内容、顺序和验收条件不变。
- [x] Phase 2 r3 完成 32/32，single 8/16、auto 7/16；未记录 task 委派或叶角色请求，详细角色链覆盖仍为 0/32，完整成本和人工次数未知。[r3 完整结果与维护决策](real-task-comparison-r3-results-2026-10-25.md)保留全部任务、未知项及分析边界。决定维持现有配置并继续新机制冻结，不据此宣称协作/记忆增益。r2 的 5 完成 / 1 中断 / 26 未运行仍为独立 pilot，不合并结果。

旧 `3cadcb75` 的 Windows 完整 CI 另有系统性平台失败：Python 3.11 为 13,222 passed / 1,390 failed / 246 errors / 112 skipped，3.12 为 13,217 passed / 1,395 failed / 246 errors / 112 skipped。主簇涉及可信 Git 查找、POSIX 文件权限/信号和路径契约。用户已明确批准本期正式支持 Linux/macOS、原生 Windows 标为实验性，并保留完整失败诊断与移植清单；[平台范围](platform-support.md)和独立 Windows diagnostic workflow 已据此更新。未批量跳过或将失败改成成功，Windows 打包步骤通过仍不能代替完整平台验收。

## 新发现与已执行决策

以下修复记录按发生时点保留，其中“完整 CI 待验收”等文字描述当时状态；当前 Phase 1 GO 结论以本页进展及最终支持矩阵为准，旧失败和归档不改写。

- 首次干净副本随机运行在收集阶段失败：两个跟踪测试导入 `.repair-work/` 下未跟踪的 evaluator/scorer。已将最小源码与冻结输入迁到 `scripts/skill_chain/`；原字节和 SHA 保留，两个测试在不含旧目录的副本中 29/29 通过。
- 搜索 16 项失败由未显式 opt-in bounded phase 引起；生产连续搜索语义保持不变。新增越界哨兵和连续模式对照。
- Memory 的 12 项失败来自未冻结 v41 候选闭包漏列两个已实际修改的来源；当时显式补齐25项，不改40份历史 manifest、不授权 v41 激活。
- 已在冻结 R2 证明三个独立污染源并修复来源：父进程 session 全局路径未恢复；旧双臂 runner 修改共享 `task` 定义；lazy-import 测试破坏工具模块缓存身份。分别保留最小前导红例、仅变污染源的绿例及原受害测试；不据此外推原报告所有 workflow 失败。
- 个人笔记已退库并移出工作区，保存于本地私人笔记目录；临时恢复备份保留，原忽略规则不变。三个指定杂物与旧 `.repair-work` 已移到本机临时恢复归档；必要测试依赖先迁出后再整理。
- Phase2 已确认配置模型名称为 `glm-5.3-flash`（custom provider）；不在文档输出凭证。

- 原有 memory 依赖闭包已提交；551 项相关测试通过。原有新增模块属试行前 WIP，不作为冻结期间增加模块的先例。
- 随机全量 R2 在 5355 passed / 58 failed 后因诊断插件文件 I/O 被测试 monkeypatch 禁止而中断。相同冻结源码的未完成节点续跑为 9271 passed / 139 failed / 8 skipped / 22 errors（5413 deselected，pytest 2382.44 秒）；续跑不是完整套件证据，两次结果不能拼成全绿。
- 已确认并修复阶段预算漏算实际工具 schema、v6 失败诊断后的 Analyst→Reviewer 审计绑定、checkpoint 把合法准备耗时误作已执行证据；分别留同字节测试的旧生产反例。MCP SSE 未定义变量修复同样有实际反例。其他测试的模式、角色及证据协议迁移不冒充生产修复。
- 历史 policy 2/3 的 verifier-only 授权和多轮修复长 trace 投影也有相同最终测试的旧生产红例；保持当前 policy 6 审查、冻结候选身份和输出上限。紧凑输出省略的诊断轮次仍保存在完整持久审计中。

- 原有 core WIP 依赖组已提交：76 文件随机测试1060通过、1处旧提示词断言失败；修正后该文件18通过。未把两次结果拼成同次全绿。
- 功能审计已动态观察真实契约，0 findings；故障注入仍能报告元数据/required参数/会话事实保存失败。
- current 88/88、a 57/57、compileall 与现有 scoped Ruff 已通过。P0 随机全量于 2026-10-24 23:16:53.215103 至 2026-10-25 00:10:47.626483（Asia/Shanghai）完成：pytest 3231.54 秒，外层进程 3234.411 秒。源码为 `9f9369421486f018c837f84d21bd727aed6ab326`，干净独立 clone、独立 HOME、无自定义逐用例诊断插件，退出码 0。P0 基线及验收文档已随 `ff2f18ea` 原子推送 main / 开发分支，没有批准豁免；该本机结果不代表后续跨平台 CI 全绿。
- 收集 14,927 个用例 = 14,923 通过 + 4 个运行时跳过；XML 另计 4 个收集跳过模块，因此是 14,931 条记录、8 项跳过。跳过分别来自 4 个可选 numpy 分析模块、2 个临时本地 TLS 证书 fixture 和 2 个缺少 API 凭证的 live 测试；3 条警告均为多线程进程 `fork()` 的 Python 3.12 弃用警告。逐项节点、原因与警告位置见 [release baseline](release-baseline-2026-10-24.md#p0-完整随机验收)，跳过项未计为通过。
- 既有 CI 安装/导入 smoke 存在 editable 来源误判，已由 `d0f728e0` 修复：同字节测试旧检查器 3 failed / 2 passed，修复后 5 passed。另从精确 `9f936942` / `eb17c62a` 构建并安装真实 wheel，以 `python -I -S` 独立验证各自 9 个指定模块全部来自 installed target；这不是所有递归依赖来源审计。[来源证明及独立审查](../artifacts/governance-trial-2026-10-24/wheel-import-proofs/README.md)。
- `71347fd5` 将 hot-store 断言改为实际等待请求、累计余额和迟到命令拒绝，撤回 200 ms 总墙钟保证；生产 AST 不变。347 项邻接通过，3 种人工故障仍被拒绝，独立审查和清理探针无阻塞。[证据包](../artifacts/governance-trial-2026-10-24/hot-store-budget-proofs/README.md)中的“全量 trace 待输出”是打包时状态；现已由首次候选完整 XML 确认上述唯一失败。
- CI run `40111546762` 的 Windows 3.11/3.12 失败来自既存 portable 发布路径的 WinError 32，相关生产字节在 `88ec62d0`、`9f936942`、P0 main 相同。`37761457` 在 fsync/fstat 后关闭临时句柄，再保留前后路径校验；同字节反例 2 failed → 2 passed，portable 10 passed、安全/发布 96 passed、packaging 9 passed，独立审查通过。[原 CI、修复和审查证据](../artifacts/governance-trial-2026-10-24/windows-portable-publication-proofs/README.md)。`3c844a39` 原生 Windows 3.11/3.12 的 packaging/clean-archive 步骤已通过，但不是 job 或全套通过。原 CI run `40111546762` 的 Ubuntu 3.11 与 macOS 3.11 内部错误同因：既有 `test_run_command_lifecycle.py` 全局修改 `os.name`，使 Python 3.11 构造不支持的 WindowsPath，继而破坏 pytest 报告/退出；Ubuntu 原 JUnit 为 4934 tests / 0 failures / 1 internal error / 7 skipped。本机真实 Python 3.11.15 已复现。`7b8157e7` 仅将测试改为模块局部 `os`/`sys`/`subprocess` 代理，生产不变；Python 3.11/3.12 各 124 项相关通过，最终同字节目标测试在 exact P0 1 passed，2 个合成坏 flags/session 反例正常断言失败。[fixture 证据包](../artifacts/governance-trial-2026-10-24/run-command-fixture-proofs/README.md)。新版完整 CI 仍待，不能将 fixture 污染称为生产缺陷。
- 第二次本机完整尝试 `3cadcb7584526b41af7cc928d6c58d5bb19d8ea5` 已结束，seed `20261024`：**14,932 passed / 14 failed / 8 skipped / 0 errors / 3 warnings**，pytest **2631.92 秒**、外层 **2634.330 秒**，退出码 1。14 项失败全部属于 v40/v41 staged source 声明；`827c1751` 已提交相应修正，不能倒填为旧尝试通过。
- [两次本机完整尝试证据包](../artifacts/governance-trial-2026-10-24/phase1-acceptance/README.md)同时保留 `eb17c62a` 与 `3cadcb75` 的完整日志、XML、元数据和失败节点，共 **21 个成员**，gzip **1,212,390 字节**，SHA-256 `048f611bce563fd55c8c56bf361046379d6354b36a43b2c93aed1d64d913a5c6`。包内 `repairedAcceptancePassed=false`；它记录两次本机失败尝试，不是最新 PR 的完整验收。JUnit suite 时间分别为 2889.078 / 2631.148 秒，与 pytest 汇总和外层计时边界不同。
- 旧 CI `40111546762` 的 Ubuntu 3.12 完整结果为 14,911 passed / 2 failed / 18 skipped；两项失败分别源于 fixture 依赖目录枚举顺序、在真实 outer Future 完成前过早发出信号。`c6d1e485` 已提交修正，仅调整测试前提，相关 60 项（seed `20261024`）与 124 项（seed `42`）通过，但范围有重叠、不相加。`827c1751` 已将审查通过的 Windows publisher 生产改动补入未冻结 v41 的精确 staged source 声明：相同最终测试旧声明 15 failed / 19 passed，新声明 34 passed，相关 179 项通过；40 份历史 pin 字节不变，不激活 v41。原始失败、独立审查及边界见[跟进证据包](../artifacts/governance-trial-2026-10-24/ci-followup-proofs/README.md)。
- 本轮原始日志、XML、初始/诊断源码哈希和关键证明已整理到[仓库证据包](../artifacts/governance-trial-2026-10-24/phase0-diagnostics/README.md)，含逐文件 SHA-256、压缩包 SHA-256 和恢复步骤。失败、诊断中断和无效基线尝试均保留；不复制临时源码树。
- 后续 P0 完整通过的 5 份原始文件另存于 [P0 验收证据包](../artifacts/governance-trial-2026-10-24/phase0-acceptance-9f936942/README.md)，压缩后 596,106 字节，逐文件恢复比对通过。它只覆盖 `9f936942`，不覆盖后续 P1/P3 候选，不覆盖后续 main 状态变化，也不代表 Phase 2 已执行。
- 最新修复已分组提交：`54367b5d` 使用显式宿主 Python fixture，并为已注册 toolchain 的祖先目录保留 metadata-only 查询；文件内容读取与沙箱拒绝断言保留。`1f39822b` 用局部时钟、真实 reader 和 Future 完成同步修正两项调度前提，没有延长 sleep 或放宽墙钟阈值。`693f504a` 修复 evaluation transport 将 durable ledger 消费滞后误判为 idle：先检查已收到的队列项，仍按 reader 时间拒绝真实 receive gap，并保留绝对 deadline、取消、EOF/error 与字节上限。沙箱邻接 **108 passed**、timing 两文件 **77 passed**、transport 相关 **87 passed**；其中 transport 最终同字节 10 个边界用例在旧源 **3 failed / 7 passed**、新源 **10 passed**。这些范围有重叠，不能相加。原始失败、反例、故障注入和独立审查见[macOS 跟进证据包](../artifacts/governance-trial-2026-10-24/macos-ci-followup-proofs/README.md)。
- 旧 CI `40114448888` 的 macOS Python 3.11 集成节点 `test_run_command_via_agent` 收到成功但空白的输出。真实 PTY 控制实验分别证实：Darwin 最后一个 slave 关闭可丢弃未读数据；旧空 select 后的退出分支也会跳过已排队输出。原 CI 的具体交错未知。本次仅修复 `_run_with_pty`，保留 slave 至排空结束，先清理后代进程，再以零超时 readiness 查询排空已有数据；取消、绝对 deadline、输出上限和异常关闭均保留。最终相同测试字节在旧源 **4 failed / 2.86 秒**、修复后 **4 passed / 1.48 秒**；相关 **19 passed / 3.40 秒**已包含这 4 项，不能相加。独立真实大输出 smoke 与只读审查通过；最终空 readiness 的取消/截止反例及人工删 checkpoint 故障证明另列。见[PTY 跟进证据包](../artifacts/governance-trial-2026-10-24/pty-output-followup-proofs/README.md)：44 个成员，gzip 28,986 字节，逐项实际恢复校验通过。**本次修复尚无对应完整 CI 验收；未改 ROOT 的 Phase 2 冻结源码，也不重跑其 32 个 cell。**
- 旧 `3c844a39` 的 CI `40115957909`（macOS Python 3.11）另暴露 `test_repeated_candidate_repair.py` 将并行 verifier 回调完成顺序误作请求顺序。真实 Barrier/Event 交错让第二轮 regression 先于 compat 完成，旧 fixture 精确复现 `[0, 2, 1, 0]` 断言失败，同时工作流已 committed、模型调用数仍为 7。本次**仅改测试，生产与脚本仍与已推送 `8fba3787` 相同**：按 assignment/patchDigest 分轮次、checkId/argv 绑定结果，拒绝重复/缺项；仍精确要求 compat=0、regression=2→1→0，Analyst 按 regression 身份取失败，未排序裸退出码。同一生产和受控顺序下，旧 fixture **1 failed / 8.28 秒**、新 fixture **1 passed / 8.90 秒**；无插件单节点和全轮逆序分别 **1 passed / 8.31 秒**、**1 passed / 8.78 秒**，均为同一测试的执行，不能累加。最终断言拒绝六类人工关联错误，独立只读审查 GO；见[并发结果关联证据](../artifacts/governance-trial-2026-10-24/repeated-repair-order-proofs/README.md)，29 个成员、gzip 23,858 字节，实际恢复逐项校验通过。未重跑 quality 或完整套件；运行中的 `8fba3787` CI 保留原身份，不能倒填本次 fixture，最新候选完整 CI 仍待定。ROOT 与 Phase 2 源码未改。
- CI `40118490617`（macOS 3.12）与 `40120281015`（Ubuntu 3.12）的 caller 失败暴露同一测试前提缺口：60ms caller 期限在 transport 初始化前开始，15ms heartbeat wait 也不保证实际间隔低于 50ms idle。本次仅用局部时钟和真实 reader/queue/SQLite 握手建立这些前提，保留 caller/idle/cancel、精确计账与关闭断言，并补已过期 caller 不计未开始请求的反例；生产与脚本仍同 `8fba3787` / `715dd122`。最终字节相关 **113 passed / 4.91 秒**，含恢复后本文件 **15 passed / 0.91 秒**，不相加；四个合成控制/计账故障均被拒绝，不称历史生产 bug。两次 CI 原迹、受控前后证明、无效尝试及最终独立只读审查见[请求时序证据包](../artifacts/governance-trial-2026-10-24/continuous-request-timing-proofs/README.md)。最新版完整 CI 仍待完成，不据此宣称 Phase 1 go；ROOT 与 Phase 2 输入未改。
- 同一旧 CI `40121258480`（macOS 3.12，PR head `715dd122`、实际 merge `d793e1e6`）另有 **14,960 passed / 2 failed / 8 skipped**。两项均修测试前提：取消后的 parent 返回不代表已运行回调全部结束，最终 fixture 先验证取消和单次模型调用，再独立清理并等待两工具 body；观测端口的 20ms 预算不等于整个 workflow 的 500ms 墙钟承诺，最终 fixture 用局部 clock 与真实 worker 握手检查 typed timeout、容量占用、late result 和 audit replay。取消全文件 **37 passed / 1.18 秒**；观测邻接 **18 passed / 1.07 秒**，单节点重复不叠加。每项最终字节各拒绝三个合成故障，不能称六个历史产品 bug。原 CI raw/XML 共存一份，中间自动释放 fixture、被覆盖的首版 receipt 与缓存/lint 失败原迹均保留；两项独立只读审查无阻塞，见[取消与最终观测证据包](../artifacts/governance-trial-2026-10-24/ci-cancellation-observation-proofs/README.md)。
- 本轮仅改上述两项测试，生产内容摘要仍为 `4ce824100db5350ba47aae0bdd96bc74ed3a09948cdfb08195a39abfee4a35a7`。root 只读核对既有 quality 的 **439 个输入文件全部逐字节相同**，本轮两测试不在该集合内，因此继续引用已归档 current **88/88**、a **57/57**、各 **0 API** 的原执行记录；没有重跑 quality，也不由源码相同推断新 fixture 或完整 CI 已验收。ROOT 与 Phase 2 r3 冻结输入未改。
- 旧 `1decc813` CI `40124691703` 的 macOS 3.11完整结果为 **14,962 passed / 1 failed / 8 skipped**（seed48）：`test_failed_write_candidate_continuation.py` 把并发 formal check 完成顺序误当请求顺序，实际 `[1,0]` 触发 fake Executor 内断言。本次仅改该 fixture，按 assignmentId/patchDigest 分轮并绑定 checkId/实际请求 argv，仍拒绝重复、缺失和错配；未排序裸退出码，原 nonformal 串行检查、同会话、预算、独立 review 及持久 audit 断言保留。相同生产和受控反序下，旧 fixture **1 failed / 2.05 秒**；最终两个参数在受控/自然顺序分别 **2 passed / 10.54 秒**、**2 passed / 10.52 秒**（局部seed `20261024`），是同2用例重复执行、不可累加。7类人工观测记录错配仅证明最终 helper 与模型外断言的敏感性，不称历史产品bug；root只读复核无阻塞，详见[失败写入续跑顺序证据](../artifacts/governance-trial-2026-10-24/failed-write-order-proofs/README.md)。生产与脚本未改；root再次核对quality的439输入全同，当前测试不在该集合内，继续复用原quality记录而未重跑。原CI交错未知，不将受控反例称为精确重放；包含本修订的完整CI仍待。
- 历史离线 quality（PTY 修复前）为 current **88/88**、a **57/57**，两次 remote calls 均为 0；执行前后核验的 `minicode/**/*.py` 内容集合 SHA-256 均为 `fff5e4db7d261650dd431587dd0bbb6c016474bc6ecada3555679b7b3d1fd3f8`，对应本次 PTY 修复前的源码；该摘要不覆盖新增的 PTY 改动。元数据的 HEAD 为当时的 `3c844a39`，实际源码摘要包含随后提交的修复；这是离线开发集重放，不能凭相同生产字节推断完整测试或新的 transport 路径已通过验收。
- 当前离线 quality 已在 P1 HEAD `715dd1226ef6994990283994ab8db9293185b176` 加请求时序 fixture（SHA-256 `f73325d0f18305d10cee66b636f7aa118c39020fc19589c2a1610065864852ce`）上各执行一次：current **88/88 / 14.28 秒**、a **57/57 / 15.06 秒**，两次 remoteCallCount 均为 **0**。运行前后 439 个源码/脚本/fixture 文件 SHA 映射相同；其中 319 个 `minicode/**/*.py` 的生产内容集合 SHA-256 为 `4ce824100db5350ba47aae0bdd96bc74ed3a09948cdfb08195a39abfee4a35a7`，已覆盖 PTY 修复。原始 JSON stdout、空 stderr、元数据、来源映射、实际 wrapper 及摘要计算口径见[本次 quality 证据包](../artifacts/governance-trial-2026-10-24/phase1-post-policy-quality/README.md)。这是离线开发集重放，不是模型任务成绩，两个 profile 范围不累加；最新版完整 CI 仍待完成，不以这些结果替代 Phase 1 完整验收。
- **Phase 1 GO：候选 `e6492f9be7357a707bb8f05c722aa2af4eefcc3d` 已满足原定验收条件，已通过 [PR #2](https://github.com/zrb3052796119/CodeLoop/pull/2) 合入 main `ed21acb3`。** `run_agent_turn` 净减 497 行、4 个目标闭包迁出、新增函数最多 11 个参数；[CI run `40130139264`](https://github.com/zrb3052796119/CodeLoop/actions/runs/40130139264) 的完整支持矩阵已通过，seed `50`，实际 checkout merge 为 `b0091e50b8f434cf811f3cdec5159d44052d3e09`，与 PR head 分别记录。Linux Python 3.11/3.12 **各 14,952 passed / 19 skipped**，macOS Python 3.11/3.12 **各 14,963 passed / 8 skipped**，四份 XML 均为 **0 failed / 0 errors**；六个选定 job 的安装、编译、lint、离线质量与包检查均成功。结果由 **attempt 1 的五个成功 job + attempt 2 重跑的 macOS 3.11 整个 job** 组成；只执行过一次 `rerun --failed`。原 macOS 3.11 pytest 通过后上传遇到 `ENOTFOUND`，job 失败且缺少 XML，这些原迹保留；另外五个 attempt 2 API job ID 是继承记录，不能当作五次新执行。原始日志、四份 XML、来源映射及逐成员 SHA 见[最终支持矩阵证据包](../artifacts/governance-trial-2026-10-24/phase1-supported-matrix/README.md)，46 个成员已实际恢复比对。Windows 两项仅为实验性包/静态检查通过，完整 runtime 仍有已知失败，不属于本期正式支持验收；历史失败不改判。
- Phase 2 的首次冻结 `987f7cf3…` 在零模型试次时撤销，其后 [r2 冻结](../artifacts/governance-trial-2026-10-24/phase2-freeze-r2/README.md) `32dacc25…` 因公开契约缺口受控停止：**5 次完成、1 次中断未判分、26 次未运行**，固定分母与原始记录保留为 [r2 不完整试跑](real-task-comparison-r2-pilot-2026-10-25.md)，不倒填成功或合并成绩。用户确认补齐公开契约后重新执行完整一轮；[r3 注册](../artifacts/governance-trial-2026-10-24/phase2-preparation-r3/README.md)、[冻结](../artifacts/governance-trial-2026-10-24/phase2-freeze-r3/README.md)和旧试跑处置已在首个 API 调用前随 `f41a9006`、`48a8216487b2951ca15e442a76629ee9e824047d` 发布。**r3 已按该冻结 manifest `b32718a77f1680cf2d7d465d655a95bdcdcf5c6c745c3d30e1b9e430193d3b01` 完成 32/32；single 8/16、auto 7/16，见[r3 完整结果与维护决策](real-task-comparison-r3-results-2026-10-25.md)。** 两组仍为 GLM 5.3 Flash、同一顺序和限制；产品维持 P0，评测器含 r2 transport 修复，不含 P1 抽取或后来的 PTY 修复。报告已在匹配冻结的执行树上完成，既有 92/114 离线检查不计模型成绩；[r3 说明](real-task-comparison-r3-2026-10-25.md)保留事后修订及读范围暴露等解释限制。

## 用户批准的 v2 方案

以下保留批准时的计划文本；其中原报告统计与工期属于当时的依据和目标，实际证据以以上执行记录及 [release baseline](release-baseline-2026-10-24.md) 为准。

CodeLoop 治理试行方案（v2 完整版）

▎ 依据：2026-10-24 重审 + 你的修正结论 + 全量测试分类结果。
▎ 试行期 2 周。Phase 0 / Phase 3 直接执行，Phase 1 / Phase 2 各设 go/no-go 决策点。
▎ 已验证事实全部标注 file:line，可直接执行。

---

Phase 0 — 基线修复与合入 main（1.5–2 天）

目标：让“当前树是绿的”重新成为真命题，且“绿”可被机械验证。

第 1 步：静态检查清零（半小时）

#: 1
问题: F821 活 bug：@staticmethod 引用 self，SSE 无匹配响应时抛 NameError
掩盖协议错误
位置: minicode/mcp.py:1251（调用点 :1224）
动作: _response_from_sse 加 server_name: str 参数，调用点传
self.server_name；保持 staticmethod
────────────────────────────────────────
#: 2
问题: 上述回归测试
位置: tests/ 新增
动作: 无匹配 id 的 SSE 文本 → 断言抛 RuntimeError（含 server 名）
────────────────────────────────────────
#: 3
问题: E731 lambda
位置: minicode/tooling.py:398
动作: 改 def
────────────────────────────────────────
#: 4
问题: F401 未用导入
位置: minicode/workflow_runner.py:27
动作: 删 AgentBudgetExceeded
────────────────────────────────────────
#: 5
问题: audit 计数失配（实际 59、冻结 58）
位置: tests/functional_audit/test_runner.py:69
动作: 改为“集合比对 + 计数”双断言，未来加工具在正确位置报错

第 2 步：104 个未提交文件分组提交（半天）

- 按模块分组（router / eval / workflow / docs 分开），先跑相关文件的单测再提交
- 每组提交信息注明对应 gate/实验（沿用你现有 conventional commits 风格）

第 3 步：确定性测试失败修复（28 个）

- test_focused_include_search.py 单独跑 16 failed（symlink 逃逸、递归目录解析）
- test_memory_retrieval_production_baseline_v41.py 单独跑 12 failed（release-join / manifest-pin）
- 两个测试文件是已提交状态，而 minicode/memory_retrieval.py、minicode/tooling.py 在未提交修改里——先判定方向：预期行为应该是代码迁就测试还是测试跟随新行为，逐个拍板
- 需要坐实因果时可“暂存 WIP 重跑对照”——动你的未提交工作树，等你明确授权才做

第 4 步：污染源二分（半天）★ 新增

全量实测：14,522 passed / 359 failed / 22 errors / 34 分 21 秒，其中约 190 个 workflow 失败在单独跑时全绿（test_workflow_v2_structured_routing.py 整文件单独 79/79 过）——是套件级状态泄漏，不是代码回归。

- 二分法：失败文件单独跑（绿）→ 按字母序成批加入前导文件 → 收缩到最小污染集
- 定位后：给污染测试补 GlobalStateGuard/IsolatedTestHome 防护，或修泄漏源
- timebox 半天；找不到就退而求其次：记录已知互斥文件对 + 靠第 5 步兜底

第 5 步：顺序随机化防回归（1 小时）★ 新增

- dev 依赖加 pytest-randomly，CI 全量跑启用随机顺序、固定随机 seed 上报
- 目的：污染在 PR 阶段暴露，而不是攒到全量夜跑；“顺序固定的绿”不再算绿

第 6 步：全量验证 + 基线文档

- compileall + ruff（0 错）+ evaluate_agent_quality --profile current / --profile a + 全套 pytest（不带 -x，随机顺序）
- 结果写入 docs/release-baseline-2026-10-24.md：数字、日期、commit hash、套件运行时长（当前实测基线 34 分钟）、随机 seed
- 34 分钟对反馈回路偏长，顺带记录分档建议（单测层 / 集成层拆分），不强求本期做

第 7 步：合入 main + 退库个人文件

- main 停在 2026-08-25、落后 56 个提交——验证全绿后 merge，CI 徽章恢复意义
- interview-prep.md、study_qa.md：git rm --cached，移私人笔记库，.gitignore 补条目

Phase 0 验收：ruff 0 错；确定性失败清零；随机顺序全量跑绿（或显式豁免清单）；工作树干净；main = 开发分支；基线文档入库。

---

Phase 1 — run_agent_turn / ToolContext 收敛：第一个垂直切片（第 1 周）

原则：先定状态归属和阶段边界，不机械拆文件（5,308 行、54 参数、26 个闭包，机械搬文件只会把闭包依赖翻译成更多参数）。

┌──────┬───────────────────────────────────────────────────────────────────┐
│  天  │                               动作                                │
├──────┼───────────────────────────────────────────────────────────────────┤
│      │ 只测量不写代码：AST 脚本产出 26 个闭包 ×                          │
│ D1   │ 自由变量矩阵，按四类归组——turn 生命周期态 / 工具执行态 /          │
│      │ 预算与截止 / 遥测                                                 │
├──────┼───────────────────────────────────────────────────────────────────┤
│      │ 选边界最清晰的一组闭包试点（建议 enforce_skill_load_before_final  │
│ D2–3 │ / reconcile_task_ledger 一带），提升为显式类（如                  │
│      │ SkillLoadGate），依赖构造注入                                     │
├──────┼───────────────────────────────────────────────────────────────────┤
│      │ ToolContext 试点：37 字段（35                                     │
│ D4   │ 下划线）按能力域分组，只迁一个域（建议子代理域 _subagent_mailbox  │
│      │ 等），改只读 property + 显式方法；保留 slots dataclass            │
├──────┼───────────────────────────────────────────────────────────────────┤
│ D5   │ 度量 + go/no-go                                                   │
└──────┴───────────────────────────────────────────────────────────────────┘

go 判据：随机顺序全量绿；run_agent_turn 净减 ≥300 行；切片闭包组完全离开其作用域；无新增 >30 参数函数；单 PR 可 review。
no-go 判据：D1 矩阵显示闭包共享 >60% 自由变量——阶段边界实际不存在，停止迁移，转产设计文档。

Phase 2 — 真实任务对照实验（第 1–2 周，与 Phase 1 并行）

目的：用四个指标回答“bounded 多 agent / memory 相对单 agent 有无增量收益”，为默认档位和新机制冻结提供依据。

1. 任务集：从 .mini-code 会话历史与真实仓库挑 15–20 个真实任务（非合成），按读/写/重构分档，预注册验收清单后再跑
2. 对照：单 agent 直做 vs bounded 多 agent（现默认）；可选加 memory warm/cold
3. 指标：成功率（预注册清单）、总成本（token + 调用数）、墙钟延迟、人工干预次数（run_journal 计数审批/纠正事件）
4. 复用设施：run_journal、scripts/analyze_continuation_adoption.py 的 content-free 标记、evaluate_agent_quality.py 密封清单模式
5. 产出：一页对照报告入 docs/，沿用现有 gate 文档格式；结论正负都记录

冻结条款（试行期生效）：不新增 controller / cybernetic / memory_* 模块，不开新 gate；新想法进 backlog 不进代码。

Phase 3 — 仓库治理（半天，穿插执行）

1. 大文件：artifacts/ 中 >1MB 的 JSON（最大 17.2MB，已跟踪合计约 161.5MB）改 LFS 或"manifest + SHA-256 摘要”；已入历史的不重写（单独决策）
2. license：先核实 Python 上游（QUSETIONS/MiniCode-Python）与 TS 上游实际授权；上游无 license 时不要单方面加 MIT——选项：联系作者 / 维持 view-only 声明 / 私有化
3. 本地杂物：删 minicode/agent_loop.py.orig、.repair-work/ 快照、probe.txt、你好.py（均 untracked，无 git 影响）；.gitignore 加 *.orig
4. README 修正：headless mock 一节补实测确认的真实缺口——仅设 mock 模式仍要求模型配置才可运行

---

时间线与决策点

第 1–2 天        第 1 周                第 2 周
Phase 0 ──────┬→ Phase 1（切片 → go/no-go）→ 决定：继续收敛 or 先设计
基线修复+合main└→ Phase 2（真实任务对照）──→ 决定：multi-agent/memory 默认档位
Phase 3（穿插）────────────────────────→ 直接执行，无决策点

试行期总验收

- main 恢复为真基线（含随机顺序全量绿的机械证据）
- run_agent_turn 有一个已验证的抽取先例 + 状态归属文档（或 no-go 转出的设计文档）
- 一份真实任务对照报告（含每任务原始记录）
- 跟踪内容无个人文件；ruff / audit 检查回到 0 红并由随机顺序 CI 保持
