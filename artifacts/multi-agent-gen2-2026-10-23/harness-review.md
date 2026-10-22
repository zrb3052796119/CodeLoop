# Gen-2 六格 harness 只读审查

审查日期：2026-10-23。只读取代码并写本文；没有运行 prepare/freeze/run/child，没有调用模型，没有修改生产或评测脚本。

## 结论

保留现有生产初始化、权限范围、共享物理请求账本、进程回收、独立裁判和独立审计；增加独立的 gen2 协议入口及审计适配。不能只把新三题塞进旧 `prepare`：旧入口和审计器都硬编码旧题 ID，当前预算也已是 1800 秒。

建议新增 `scripts/run_multi_agent_gen2.py`、`scripts/audit_multi_agent_gen2.py`，使用现有 successor 的私有模块加载模式复用运行实现；旧入口、旧协议和旧结果保持不动。Gen-2 只暴露 prepare/freeze/run/child/gate，不自动接旧 48 次 formal。此处是接入方案，未实施。

## 现有任务与冻结格式

任务实际执行投影只有以下字段；谱系等元数据可以额外保存，但不得拼入模型提示：

```json
{
  "shared_files": {"minicode/__init__.py": "...", "pyproject.toml": "..."},
  "cases": [{
    "id": "gen2-<fixed-id>",
    "prompt": "公开问题与验收要求，不含答案或隐藏测试",
    "files": {"minicode/target.py": "修复前源码", "tests/test_public.py": "公开兼容测试"},
    "authorized_paths": ["minicode/target.py", "tests/test_eval_regression.py"],
    "verification_argv": ["python3", "-m", "pytest", "-q", "tests/test_public.py"],
    "oracle_source": "仅独立裁判执行的隐藏验收 Python 源码"
  }]
}
```

真实结构见 `tests/test_implementation_progress_live.py:265`；执行端在 `scripts/run_three_arm_real.py:448` 读取 case，`:466` 只把 `shared_files + files` 投影进临时工作区，`:519` 构造公开提示。`runner.execute_trial` 将公开目标、授权路径及公开检查构成共享 `ImplementationSpec`，没有传 `oracle_source`（`scripts/run_implementation_progress_live.py:486`）。新增回归测试路径和独立运行方式在 `scripts/implementation_judge.py:30` 固定。

当前 `prepare` 要求原始 8 题、原始 manifest hash 和固定旧三题（`run_implementation_progress_live.py:527`）；当前 `freeze` 再次比较原始内容，不只相信 prepared hash（`:668`）。Gen-2 应替换此来源验证为独立任务包的精确 ID 集合、公开基线和隐藏 oracle hash，以及真实修复 commit / parent commit / 原始路径 / baseline-reference 离线结果。不能伪造旧 `sourceManifestSha256` 或冒称 calibration 复用。

现有 manifest 为 schema 5，主要绑定：协议、任务/计划/源码/harness/ZIP hash、模型和实际路由 hash、limits、角色上限策略、共同裁判、公开 fixture/preflight、schedule、arms、repeat、seed、concurrency（`:746–792`）。Gen-2 应有新协议 ID 和明确代际字段；新增固定三题谱系、公开基线逐文件 hash、完整执行宿主 inventory hash、依赖环境指纹、离线 oracle 反例证据 hash、六格门的字面阈值、与历史不可比声明。旧 `reusedCalibrationCases` 不适用于新题。

## 最小 prepare/freeze 流程

1. `prepare` 仅接受新空目录和经复核的三个新题；顺序和 ID 一次确定。不依赖已删除的旧 `artifacts/three-arm-real-2026-10-13`。当前 checkout 也没有旧 `artifacts/implementation-progress-2026-10-14`，不得自动重造历史目录。
2. 每题保存公开 baseline、真实 reference 及隐藏 oracle 的来源 hash；证明 untouched baseline 的公开命令通过、隐藏 oracle 对 baseline 失败且对真实 reference 通过。reference/修复 diff/oracle 不进入受测工作区或模型提示。新增 `tests/test_eval_regression.py` 由受测 Agent 交付，基线不预填。
3. 公开 preflight 复用 `run_public_preflight(..., require_pytest_report=True)`，在真实 scope/sanitizer/Verifier 下运行；要求 pytest 机器报告，不能只认退出码或字符串。该能力已在 `scripts/public_verification_fixtures.py:203` 存在。
4. 固定 exactly 3 × (`single`, `explicit`) × repeat 1，serial concurrency 1；新固定 seed 写进预注册，生成后将完整六条 schedule 冻结。不沿用旧分析计划的“某题 explicit 成功后才运行 single”顺序条件；单格失败保留，继续预定其他格，只有清理失败等基础设施安全条件中止。
5. freeze 在离线证明完成后产生任务、计划、完整 host ZIP、逐文件 inventory、preflight receipt、manifest；已有文件或任何 prior-call evidence 不覆盖。准备/冻结阶段安装拒绝 provider dispatch 的测试哨兵，证明零模型请求。

## 600 秒和模型配置必须同时改到实际执行与描述

`scripts/run_three_arm_real.py:61` 当前是 500000 token、40 请求、1800 秒，request/idle/connect 为 300/120/30，输出上限 8192、成本安全上限 5 美元。实际 transport 和 owner deadline 使用该模块的 `LIMITS`（`:475`），运行中环境也覆盖 `MINICODE_TASK_TIMEOUT_SECONDS`（`:560`）。Gen-2 私有模块必须设置新的、字面冻结的 `LIMITS["wallSeconds"]=600`；不能只写 manifest 或命令行说明。

模型固定 `glm-5.3-flash`、temperature 0、内部 HTTP retries 0、父子共享 40/500000/600，所有角色 actual route fingerprint 必须相同。request/idle/connect、输出上限和成本上限如沿用，明确写入新预注册；不要继承操作者私有配置造成额外变量。当前 `role_turn_configuration` 会读取 process/private env，并保留历史有限上限兼容（`run_implementation_progress_live.py:71`）。Gen-2 应固定显式角色 override 策略，例如将未请求的额外角色上限固定为 0，仍由共享预算与生产 Assignment 分配约束；不要复用旧 invalid-value fallback 再宣称纯生产配置。

同时更新 gen2 的共同要求/分析计划/guardian 文案中的 1800；共同裁判仍独立 120 秒，`JUDGE_POLICY["agentBudgetSeconds"]` 目前也是 1800 的描述字段（`implementation_judge.py:31`）。新协议可以绑定拷贝的 judge policy 为 600；审计器须明确认该新 policy，不能改旧冻结口径或只在输出文案替换。

## 隐藏 oracle 的现有隔离及保留方式

`tasks.json` 含隐藏 `oracle_source`，但整个任务包位于宿主 output，受测工作区只获得公开文件映射。原 finalizer 在 Agent 终态且进程清理完成之后，才把 oracle 写到 `private/<trial>/oracle.py`（`run_three_arm_real.py:769`）。successor 共同裁判在两份独立交付副本上分别运行 oracle 和新增测试，120 秒共同期限包含复制、执行、receipt 和清理（`implementation_judge.py:248–313`）。实际 `minicode` 导入来源必须属于交付副本（`:122–160`）；oracle 不能导入 execution-host 的已修复代码冒充候选。

Gen-2 保持 oracle/reference/修复提交 diff 在父宿主私有材料中，不放到 `shared_files`、`files`、prompt、ImplementationSpec、公开 preflight 或模型可读 freeze 副本。提交到 Git 不代表对受测 Agent 可见：执行宿主和任务包路径必须留在其权限根之外，不能把含隐藏 oracle 的整个 gen2 目录复制到 trial。公开 pytest 不得以 `-k` 筛选绕过新增测试验收；新增文件独立执行、非空收集、每项 setup/call/teardown 成功，skip/xfail/xpass 均不通过。此仍是现有非对抗测试边界，并非任意恶意 Python 的 OS 隔离承诺。

## 完整执行宿主冻结与混合工作区

现有 `production_sha()` 哈希全部 `minicode/**/*.py`（`run_three_arm_real.py:101`），旧 freeze 也打包全部这些 Python 文件，所以未跟踪的 `workflow_admission.py` / `workflow_brief.py` 会被包含；并非仅从 Git HEAD 取源码。但现有 archive 只是校验材料：`run_suite` 从当前 `ROOT` 启动当前脚本（`:843–881`），不会解包 ZIP 后执行。因此 freeze 后继续改工作区会阻断运行，且不能证明实际运行来自独立冻结宿主。

最小可靠办法：

- 从当时完整可执行工作区复制 host 到独立 staging，收齐全部 `minicode/**/*.py`、明确的 harness 依赖闭包、`scripts/__init__.py`、必要包资源和依赖声明；排除 `.git`、密钥、运行状态、其他 artifacts、测试答案。旧 `SUCCESSOR_HARNESS_FILES` 未包含 `scripts/__init__.py`，新冻结应补上。记录 Python 实际路径/版本及依赖版本，不把凭证写入材料。
- 对每个文件绑定 relative path、size、SHA-256；目录删除/不存在同样作为 inventory 的集合约束。完成 ZIP 后验证其全部条目精确等于 inventory，不允许 traversal、重复项或越界 symlink。整个树 hash 是 executionHost hash，旧 production hash 只作兼容维度。
- 将 **host archive、inventory、manifest、任务/计划/离线证据和 gen2 新脚本** 作为显式 allowlist 提交；不 `git add -A`，不提交当前混合修改的 `minicode/agent_loop.py`、memory 系列、`run_journal.py`、README 等源路径。新协议实际运行时先在干净临时目录解包并校验 host，然后从该 host 的 gen2 脚本启动子进程；再次检查生产模块 `__file__` 和子进程入口均落在该冻结根。
- 完整当前 host 可能包含其他未提交功能的共同背景。它们在 archive 中作为冻结运行条件被记录，不应声称 archive 只包含本轮 Phase 1 的改动，也不能把本轮对照解释为 Phase 1 的因果 A/B。两组用同一 host 才满足本次 single/explicit 方案。如果“不得记录任何其他工作字节”是更严格限制，则必须另做 HEAD+仅 Phase1 补丁的独立重建与完整验证，不能把混合源码文件整体覆盖到干净树后声称已分离。

可用 provenance：`artifacts/multi-agent-practicality-2026-10-22/initial-status.txt` 和 `preexisting.diff` 记录修复前混合状态；`verified-source-hashes.json` 明确只覆盖本地回归的有限文件集合，不是完整评测冻结 manifest。`agent_loop.py` 和 `run_journal.py` 在最初就有其他改动，按文件整体提交会夹带它们。复制/冻结不需要改这些源路径或清理用户修改。

## 提交前禁止模型运行：当前缺口和建议接口

现有 `main` 的 run/child 只校验 manifest/hash（`run_implementation_progress_live.py:834`），不检查 Git commit，也没有模型调用开关；底层 execute 还主动设 `MINI_CODE_MODEL_MODE=real`。因此设置一个外部 mock 环境变量并不能防止误调用。

建议 gen2 `run` 和 `child` 均要求 `--allow-model-calls --preregistered-commit <sha>`，两者进入同一个 `require_committed_registration()`；缺任一项在创建运行目录、transport、runtime 或 provider 之前拒绝。只允许固定 schedule 中的下一格，已有账本则拒绝重跑。

commit guard 必须读真实 Git object：指定 commit 中的 manifest、plan、tasks、host ZIP/inventory、harness/审计器逐项与已冻结字节相符，并要求该 commit 可从指定仓库当前 HEAD 到达；不能只相信可编辑的 `committed=true` 或 commit 字符串。验证发生在父 run 和直接 child 两入口；child 再校验归属宿主和相同 commit 证明。`prepare/freeze/gate` 不接受这个付费开关且不走 runtime.execute。

避免自引用：manifest 不内嵌将要包含自身的最终 commit SHA。冻结后先提交全部预注册字节，再由 run 参数传入该 commit；运行收据记录 commit、manifest blob/hash 和执行时间。该收据是执行证据，不能反向替代已提交预注册。若修改任何冻结字节，须新目录/新 freeze/新提交；不修改既有试次或覆盖失败。

## Gate 与独立审计适配点

`gate` 当前硬编码旧三题（`run_implementation_progress_live.py:589`）；`audit_implementation_progress.py:23` 和 `:370` 再独立固定同一集合。Gen-2 runner 和 auditor 都需要从已提交新协议的固定三题集合独立校验，不要只改 runner。保留独立审计不导入执行 runner、不执行证据的做法。

所有六格均须有终态、final audit 有效、scope/Agent cleanup/judge cleanup/账本完整、全部物理请求输入输出和缓存用量完整。成功格须 hidden oracle、新增回归测试通过，且有实际授权非测试生产源码差异；explicit 还须真实 Executor 与所属 workflow 的 host owner-ack 送达，不能以 workflow 容器或模型自报计数。门为总 ≥4/6、single ≥2/3、explicit ≥2/3、每题 ≥1/2；三个 explicit 均须有协议送达证据。失败、未知 usage、被拒绝/超时均保留，不调模型、不替题、不补抽、不选择最好轮次。

现有 changedSourceFiles 只认授权的 `minicode/*.py`（`run_implementation_progress_live.py:466`）。新题若仍是本项目生产 Python 修复可直接沿用；若候选生产路径不在该目录，须预注册精确 source-path 集合后在 runner/auditor 同步修改，不能运行后扩大成功定义。新三题与旧代结果明确不可比，只报告本代工程门；不自动计算历史增益或启动旧 formal。

## 实施前必要的离线反例

- 三新题 baseline 公开通过、hidden 失败、真实 reference hidden 通过；oracle 不进入模型 prompt/spec/public files。
- prepare/freeze/gate 零 provider call；未提交、错误 commit、任一字节篡改、直接 child 缺开关均在零 model call 时拒绝。
- archive 包含 Phase1 两个新模块且执行确实从 archive；活工作区后续变化不被导入；不悄悄退回 HEAD 旧实现。
- run/child 的真实 transport deadline 恰为 600 秒，40 calls/500000 tokens 同源；judge 仍为另计 120 秒。
- 六格笛卡尔积、固定次序、清理停止/恢复、已有账本不重跑，以及 4/6、2/3、1/2 临界门的真假反例。
- 非空生产变化、完整新增测试、usage 缺失、错误 owner-ack 各自能使工程门失败；不能仅检查最终 success bool。
