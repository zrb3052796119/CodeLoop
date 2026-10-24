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
- 未给出私人笔记库目的地，个人 Markdown 退库后保留为本地忽略文件，不发布笔记正文。

## 进展

- [x] 保存初始工作树基线。
- [x] 修复 3 项 scoped Ruff 错误；MCP 同字节测试反例证明通过。
- [x] 搜索测试显式选择 bounded 模式，并补 continuous/越界对照。
- [x] Memory v41 staged source closure 修复及历史 pin 验证。
- [x] 审计工具集合/汇总计数验证。
- [x] R2 诊断失败分类与定点修复：已证明 session 路径、评测 task 包装、模块缓存三处顺序污染；保留红绿反例。
- [x] P0 修复分组提交；冻结提交 `9f9369421486f018c837f84d21bd727aed6ab326` 完成随机全量验收：14,923 passed / 8 skipped / 0 failed / 0 errors / 3 warnings，seed `20261024`。
- [x] P0 已合入并原子推送：main 与开发分支一并推至 `ff2f18ea`，含通过的 P0 源码和验收文档；其后 Phase 2 冻结记录 `ab17ca7e` / `ea31eed2` 已另行推送开发分支。后续 P1 修复候选尚未合入 main。
- [x] P0 源码内的 Phase 3 整理：指定杂物、个人文件、4 份可复现大报告归档；当时保留 8 份测试/回放大输入；license 来源核验与 mock 文档修正。
- [x] Phase 1 D1/D2、生产抽取及独立审阅完成；8 份 pinned 输入已独立归档。首次合并候选 `eb17c62a5510` 全量结束：14,932 passed / 1 failed / 8 skipped / 3 warnings，2889.76 秒；唯一失败是 hot-store 累计等待测试的 `200.36325 ms < 200 ms` 墙钟断言。
- [ ] Phase 1 go：`3cadcb75` 完整随机运行及 PR #2 原生 CI 均进行中，本机已知 v40 closure 失败；后续三项定点修正尚未提交发布，不能把局部通过当作最终验收。
- [x] Phase 2 准备包与 runner 适配已提交：16 个真实历史任务、32 个配对 cell、92 项历史 oracle 检查、114 项 runner 离线回归。
- [x] Phase 2 在 `9f936942` 冻结 32 个 cell，manifest SHA-256 `987f7cf31627ca85f18cdf2a270dae3918c5bf6cacac68cf02053dd767da91be`；角色轮数显式配置为 12/8/15，非当前默认值，经济预算仍为 `null`。
- [ ] Phase 2 实付运行：模型 API 调用为零、结果为零，正式运行尚未开始；已推送的[冻结记录](../artifacts/governance-trial-2026-10-24/phase2-freeze/README.md)仍绑定 `9f936942`。首次默认值误认导致的 freeze 失败也未调用 API，详情见[任务预注册](real-task-comparison-2026-10-24.md)。

## 新发现与已执行决策

- 首次干净副本随机运行在收集阶段失败：两个跟踪测试导入 `.repair-work/` 下未跟踪的 evaluator/scorer。已将最小源码与冻结输入迁到 `scripts/skill_chain/`；原字节和 SHA 保留，两个测试在不含旧目录的副本中 29/29 通过。
- 搜索 16 项失败由未显式 opt-in bounded phase 引起；生产连续搜索语义保持不变。新增越界哨兵和连续模式对照。
- Memory 的 12 项失败来自未冻结 v41 候选闭包漏列两个已实际修改的来源；当时显式补齐25项，不改40份历史 manifest、不授权 v41 激活。
- 已在冻结 R2 证明三个独立污染源并修复来源：父进程 session 全局路径未恢复；旧双臂 runner 修改共享 `task` 定义；lazy-import 测试破坏工具模块缓存身份。分别保留最小前导红例、仅变污染源的绿例及原受害测试；不据此外推原报告所有 workflow 失败。
- 个人笔记已退库、仍在原路径本地忽略。三个指定杂物与旧 `.repair-work` 已移到本机临时恢复归档；必要测试依赖先迁出后再整理。
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
- CI run `40111546762` 的 Windows 3.11/3.12 失败来自既存 portable 发布路径的 WinError 32，相关生产字节在 `88ec62d0`、`9f936942`、P0 main 相同。`37761457` 在 fsync/fstat 后关闭临时句柄，再保留前后路径校验；同字节反例 2 failed → 2 passed，portable 10 passed、安全/发布 96 passed、packaging 9 passed，独立审查通过。[原 CI、修复和审查证据](../artifacts/governance-trial-2026-10-24/windows-portable-publication-proofs/README.md)。修复后原生 Windows CI 正在运行。同一 run 的 Ubuntu 3.11 与 macOS 3.11 内部错误同因：既有 `test_run_command_lifecycle.py` 全局修改 `os.name`，使 Python 3.11 构造不支持的 WindowsPath，继而破坏 pytest 报告/退出；Ubuntu 原 JUnit 为 4934 tests / 0 failures / 1 internal error / 7 skipped。本机真实 Python 3.11.15 已复现。`7b8157e7` 仅将测试改为模块局部 `os`/`sys`/`subprocess` 代理，生产不变；Python 3.11/3.12 各 124 项相关通过，最终同字节目标测试在 exact P0 1 passed，2 个合成坏 flags/session 反例正常断言失败。[fixture 证据包](../artifacts/governance-trial-2026-10-24/run-command-fixture-proofs/README.md)。原生远程重跑和最终完整套件仍待，不能将 fixture 污染称为生产缺陷。
- 封存候选 `3cadcb7584526b41af7cc928d6c58d5bb19d8ea5` 的本机完整随机套件仍在运行，已知 v40 closure 失败；[草稿 PR #2](https://github.com/zrb3052796119/CodeLoop/pull/2) 的原生 [CI run `40114448888`](https://github.com/zrb3052796119/CodeLoop/actions/runs/40114448888) 同样运行中。以下跟进修正尚未提交发布，不能用于覆盖该封存候选的失败或宣称 Phase 1 go。
- 旧 CI `40111546762` 的 Ubuntu 3.12 完整结果为 14,911 passed / 2 failed / 18 skipped；两项失败分别源于 fixture 依赖目录枚举顺序、在真实 outer Future 完成前过早发出信号。修正仅调整测试前提，相关 60 项（seed `20261024`）与 124 项（seed `42`）通过，但范围有重叠、不相加。另将已审查的 Windows publisher 生产改动补入未冻结 v41 的精确 staged source 声明：相同最终测试旧声明 15 failed / 19 passed，新声明 34 passed，相关 179 项通过；40 份历史 pin 字节不变，不激活 v41。原始失败、独立审查及边界见[跟进证据包](../artifacts/governance-trial-2026-10-24/ci-followup-proofs/README.md)。
- 本轮原始日志、XML、初始/诊断源码哈希和关键证明已整理到[仓库证据包](../artifacts/governance-trial-2026-10-24/phase0-diagnostics/README.md)，含逐文件 SHA-256、压缩包 SHA-256 和恢复步骤。失败、诊断中断和无效基线尝试均保留；不复制临时源码树。
- 后续 P0 完整通过的 5 份原始文件另存于 [P0 验收证据包](../artifacts/governance-trial-2026-10-24/phase0-acceptance-9f936942/README.md)，压缩后 596,106 字节，逐文件恢复比对通过。它只覆盖 `9f936942`，不覆盖后续 P1/P3 候选，不覆盖后续 main 状态变化，也不代表 Phase 2 已执行。

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
