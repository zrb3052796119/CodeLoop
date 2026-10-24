# CodeLoop 真实历史任务比较预注册（2026-10-24）

状态：**Phase 0 基线已通过；32 个试次的任务、适配器和运行时已冻结，尚未调用模型**。冻结源码为 `9f9369421486f018c837f84d21bd727aed6ab326`；正式执行等待本机全量测试退出，避免资源竞争污染延迟指标。本文不设新的扩张 gate，不修改既有历史证据。模型按用户授权使用当前配置的 `custom / glm-5.3-flash`，沿用现有凭证与端点；API 支出不设上限。

## 问题与样本

比较相同模型下，单 agent 和显式有界多 agent 在真实工程任务上的成功率、token/物理调用开销、墙钟时间与人工介入。这里的“真实”指任务来自本仓库实际 Git 变更，提示由研究者根据变更重建，**不是原始用户会话的逐字记录**。没有访问或发布个人会话内容。

纳入16题：只读诊断5、修改7、重构4。修改/重构从对应提交的真实父版本开始，不注入人工缺陷；只读题查看变更后版本，并解释其实际行为。验收取该提交已有测试的相关新增/修改检查，固定测试字节和选择器。每题记录完整基线/参考 SHA、源码逐文件 SHA、提交时间、提示、允许修改范围、私有 oracle SHA、离线验证日志。

| 任务 ID | 层次 | 参考提交 | 历史父版本 | 验收主题 |
|---|---|---|---|---|
| read-router | 只读 | 8a1b3d9e | e85a9580 | 空模型目录、不可用候选、强制模型及切换失败行为 |
| read-deadline | 只读 | 594e7dba | 0aa4e462 | 亚毫秒剩余期限、过期异常、timeout 上界 |
| read-atomic-replace | 只读 | 5658aad9 | 3717baa0 | 临时描述符关闭与原子替换顺序 |
| read-closing-reminder | 只读 | a9bdb56c | f724d382 | 缺 typed note 的一次补救及不补救条件 |
| read-image-boundaries | 只读 | f353c105 | b7265e97 | 图像输入、凭证、路由边界和成功请求 |
| write-cache-tip | 修改 | 3bb0fd35 | 5cdbdd55 | 最后消息块的缓存标记与回退 |
| write-cache-fallback | 修改 | 1f972f55 | e04dfc1d | 提示缓存、关闭开关、HTTP400 回退和 usage |
| write-todo-isolation | 修改 | c8345cba | 688ed5a8 | agent 间 todo 状态隔离 |
| write-user-profiles | 修改 | 688ed5a8 | ac9fb3cc | 用户/项目 profile 合并和覆盖 |
| write-profile-parser | 修改 | a1051e54 | 45e9a0cb | Markdown profile 解析、验证和缓存 |
| write-profile-dispatch | 修改 | c0c3f4fb | 510d3650 | 自定义只读 profile 的分派 |
| write-profile-contracts | 修改 | 510d3650 | a1051e54 | 自定义类型的结果、观察和模型继承契约 |
| refactor-finalization | 重构 | 56d8cd85 | f8ae6fd5 | 脱离可变对象的 finalization 快照 |
| refactor-workflow-runner | 重构 | f8ae6fd5 | f444a079 | 工作流执行模块边界及注入 seam |
| refactor-frozen-change | 重构 | f444a079 | 3782553c | 冻结变更的权属、完整性和提升 |
| refactor-workspace | 重构 | 3782553c | 4d3b7c1d | 工作区事务与 deadline 模块边界 |

最初18题中的 continuation-store / continuation-resume 两题排除：其早期历史快照的 tooling 引用了尚不存在的 capability_contracts；不允许混入后续实现来补齐基线。保留排除材料与失败日志。deadline 验证初次缺少历史 scripts 支撑而误加载宿主安装包；已补上同版本支撑后重新验证，初次失败日志保留，未修改产品源码。

## 验收与隔离

16题的历史参考检查全部通过，同一检查在真实父版本全部拒绝；共92个参考检查实例。新增功能/模块的父版本可能因缺少目标 API 拒绝，这只证明功能当时不存在，不能当作既有行为缺陷。该离线校验只证明裁判和参照解可运行，不是待评模型的成功结果。

只读题不能仅靠固定答案关键词通过：响应须含场景事实、两处可定位且属于实际执行函数的源码引文，以及可执行 pytest 诊断。裁判在独立副本运行诊断，要求非零检查、真实实现调用、全部通过且无 skip/xfail；router/deadline/atomic-replace/closing-reminder 的同一诊断还须在真实历史父版本发生测试失败，收集/导入错误不算。image 是新增功能审计，另核验实际函数返回的各类拒绝及成功 visual 路由事件，不把“旧版没有模块”计为诊断证据。它仍是非对抗性生成测试裁判，不宣称抵御任意恶意 Python。

修改/重构须同时通过隐藏历史 oracle、独立的新增回归测试裁判、允许路径范围检查，并有真实的非测试源码修改。新增回归放在 `tests/test_eval_regression.py`，不得靠修改测试替代实现。裁判在交付副本运行，不能把裁判引入的变更计为模型交付。

模型工作区只含真实基线的 `minicode/**/*.py`。历史验收测试、修复差分、参考目录、正确答案、oracle、其他题和 Git 元数据不进入工作区；最终提示不含隐藏验收的源代码或绝对路径。只读任务的变更后源码就是被要求研究的对象，不属于隐藏解泄漏。命令使用 argv 和现有路径/权限检查；CodeLoop 不是 OS 沙箱，获准运行的 Python 仍可能读取宿主可访问路径。这里仅提供非对抗性的材料分离，不能承诺外部 oracle 对恶意代码不可读。已观察到的隔离违规会单列，不能作为成功样本；没有观察到也不证明恶意访问不可能。

## 冻结运行条件

16题 × 2组 × 1次，共32个 trial。使用 seed20261024 打乱题序，题内交替 single/auto 与 auto/single，串行运行。`single` 禁用 task 工具；`auto` 允许显式有界自动委派。主角色与子角色同为 GLM5.3 Flash，禁用异构模型路由；两组 profile/skills/记忆均为空，禁用 MCP 和网络工具。

运行时固定 temperature0、modelMaxRetries0、maxOutputTokens8192、共享1800秒任务期限、连接30秒/空闲120秒/请求300秒；子角色显式设置 explore12/plan8/general15轮，最大深度2。token、模型调用数和货币预算均为 `null`（无限额），不得悄悄继承历史 runner 的40调用/50万token/$5限制。180秒独立裁判时间不计入 agent 墙钟。

冻结前首次校验拒绝了未显式配置的角色上限：`resolve_max_turns` 对缺失配置返回 `None`，而非 `AGENT_TYPES` 中遗留的12/8/15。此次使用预注册的12/8/15显式环境配置后冻结成功；未改题目、验收、顺序或预注册上限，首次 provider 调用仍未发生。因此本实验比较的是上述受控 bounded 条件，不能称为未配置产品默认档位的实测。

成本主指标是 provider 报告的输入/输出/cache token 与实际物理请求数。自定义端点收费未知，货币成本保持 `null`，不用旧模型价推算。未知 usage 不计零；失败、超时、无 usage 与委派失败全部保留。记录实际子角色执行及宿主接收交付，不能把一次 task 请求当作有效协作。

人工批准、人工纠正与宿主自动策略批准分别记录。需要独立的人类事件来源；缺失则人工次数未知，不能把无人值守运行的零手工操作解释为日常使用无需纠正。成功率按层次和全部题报告；开销与耗时同时报告全体试次和成功子集，失败不能从分母消失。

首次 provider receipt 前冻结提示、验收、样本、顺序、执行宿主源码、适配器、模型路由 hash 和运行参数；之后不换题、不调 oracle、不重跑失败 cell。已在现有 `run_three_arm_real.py` 增加注册任务装载：保持历史模式，研究模式读取冻结 single/auto 顺序、无限经济预算和未知货币价格，复用工作区、journal、transport、清理及独立裁判。只读任务移除写入和命令工具；修改/重构复用 `implementation_judge.py` 的新增测试要求。注册文件、任务内容与私有诊断依赖在运行前重新核对，防止冻结前后混入不同基线或 oracle。

16个 prepared oracle 已通过现有宿主裁判的调用接口和进程清理；只读 oracle 的5组有效/错误事实/仅答案/硬编码断言/xfail反例也已检查。两组生命周期测试使用真实运行时、适配器、分派与transport，把HTTP替换为离线SSE，验证同模型子角色、无限预算、token/调用记账及只读工作区不变。宿主须允许进程枚举；普通沙箱的 `ps` 限制会使既有guardian判清理未完成，不能绕过该检查。上述均为离线软件验证，不是32个正式模型 trial 的结果。

首次调用前的实际 manifest 与冻结记录已保存到[冻结记录](../artifacts/governance-trial-2026-10-24/phase2-freeze/README.md)。

## 可复现材料与限制

可提交材料位于 `artifacts/governance-trial-2026-10-24/phase2-preparation/`：`manifest.json` 记录16题、32个 cell、提交/源码树及验收 SHA；`cases/*` 保存提示、私有 oracle、参考差分和父版本/参考版本证明；`SHA256SUMS.json` 固定材料字节。源码只存 Git 引用、Python 文件清单摘要和文件数，不复制16份完整源码。`rebuild.py` 仅验证与解包材料，调用现有 runner 的 `prepare`，没有模型调用或冻结功能。重建需要保留相应 Git 历史；浅克隆/无 Git 归档须先取得这些已知对象。

以下准备命令不会调用模型，并要求输出目录尚不存在；临时目录可自行替换：

```sh
.venv-acceptance/bin/python artifacts/governance-trial-2026-10-24/phase2-preparation/rebuild.py --check
.venv-acceptance/bin/python artifacts/governance-trial-2026-10-24/phase2-preparation/rebuild.py --output /private/tmp/codeloop-governance-20261024/phase2-portable
```

以下为重建实验时的冻结和执行命令；当前目录已完成冻结，不得覆盖已有 manifest。第一步显式指定预注册角色上限，第二步调用已授权模型：

```sh
env MINI_CODE_SUBAGENT_EXPLORE_MAX_TURNS=12 \
  MINI_CODE_SUBAGENT_PLAN_MAX_TURNS=8 MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS=15 \
  .venv-acceptance/bin/python scripts/run_three_arm_real.py freeze --output /private/tmp/codeloop-governance-20261024/phase2-portable/execution
.venv-acceptance/bin/python scripts/run_three_arm_real.py run --output /private/tmp/codeloop-governance-20261024/phase2-portable/execution
```

这是单仓库、已知历史修复、同作者任务聚簇的回顾性内部比较，且每 cell 仅一次。profile/工作流题相关，任务不独立；模型可能见过公开历史，无法保证盲测。结果只支持这些工作负载和冻结配置下的描述，不能外推到日常生产力、与成熟产品的优势或“多 agent 一般更好”。本期没有 warm/cold 记忆消融，不用已有记忆结果替代本次证据。
