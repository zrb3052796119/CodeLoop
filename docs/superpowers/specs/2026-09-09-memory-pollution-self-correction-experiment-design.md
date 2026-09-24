# 记忆污染与自净实验（Memory Pollution & Self-Correction）设计

日期：2026-09-09
suite：`minicode-memory-pollution-self-correction-2026-09-09-v1`
类型：robustness-check round 1（机制验证 + 方向估计，非高统计功效声明）

## 1. 背景：前两轮实验留下的开放问题

- 2026-08-21 大样本研究证明了**干净库**（一条相关、已批准的路径恢复教训）的收益：工具调用 -79.2%、输入 token -57.6%。
- 2026-08-22 非路径研究证明命令恢复/验证规则有效、项目约束负结果。
- 两份报告的"下一步"都点名了未测工况：**大记忆库/错误或过期教训是否造成净伤害**。均未测过。
- 本仓库 2026-09-09 新增了"记忆贡献评审器"（证据引用的逐条反馈 + 既有两次佐证失败隔离机制），其净效果未经实验验证。

## 2. 研究问题

- **RQ1（污染伤害）**：正确教训与污染物（过期路径/错误命令/无关条目）混入同一库时，相对干净库，任务成功、工具调用、token 净变化多少？
- **RQ2（自净）**：证据引用反馈闭环能否在多轮任务中把污染库净化——污染物停止被渲染、正确教训保持收益？
- **RQ3（评审器必要性）**：评审 ON（rule 模式）与 OFF（仅既有整轮反馈）的净化与任务表现差异？

## 3. 设计

### 3.1 家族（6 个，全部 seeded，无学习 turn）

| 家族 | 类别 | 任务 | 正确教训 M1 | 污染物 M2 | 无关条目 M3 |
|---|---|---|---|---|---|
| mpol-p1-auth-policy | 路径 | 定位政策文件并报告 marker | `docs/policies/auth-policy.md`（存在） | `policies/auth-policy.md`（不存在） | 测试风格约定（共享词 tests/policy） |
| mpol-p2-invoice-schema | 路径 | 定位账单 schema 并报告 marker | `services/billing/invoice_schema.yaml` | `services/invoice/schema.yaml` | 计费域笔记 |
| mpol-p3-release-manifest | 路径 | 定位发布清单并报告 marker | `build/release-manifest.json` | `release/manifest.json` | 发布流程笔记 |
| mpol-c1-ledger-verify | 命令 | 运行配置验证套件并报告 marker | `python -m unittest ledgerlib.tests.test_config` | `python -m unittest tests.test_config`（模块不存在） | 测试风格约定 |
| mpol-c2-normalizer-verify | 命令 | 运行归一化验证并报告 marker | `python -m unittest datalib.tests.test_normalizer` | 错误模块路径 | 数据管道笔记 |
| mpol-c3-registry-verify | 命令 | 运行注册表契约验证并报告 marker | `python -m unittest registrylib.tests.test_registry` | 错误模块路径 | 契约测试笔记 |

污染物的构造原则：**词法上足够接近以竞争检索排名**（共享域词），但主张在当前工作区必然失败（路径不存在/模块不存在）。

### 3.2 条件（3 臂）

| 臂 | 记忆库 | 评审器（case 级 env） | 对照含义 |
|---|---|---|---|
| `clean` | 仅 M1 | rule（默认） | 干净库参照（复刻前研究条件） |
| `polluted-off` | M1+M2+M3 | `off` | 本功能落地前的生产行为 |
| `polluted-rule` | M1+M2+M3 | `rule` | 评审器开启 |

COLD 基线省略：两轮前研究已确立，且本实验的对照轴是 clean vs polluted 与 on/off。

### 3.3 轮次

每 case 3 个连续目标 turn（round 1/2/3），**不携带对话历史**（`carryHistory=false`，新会话语义），轮间仅通过磁盘记忆衔接。每个 turn 结束时生产路径自动执行：反思写记忆 → 贡献评审 → 整轮反馈 →（两次佐证失败即隔离）。

设计预期（待检验）：round 1 模型可能读过期路径而失败一次；round 2 再失败即触发隔离；round 3 污染物不再被渲染。这正是要测的机制链。

### 3.4 规模与预算

6 家族 × 3 臂 × 3 轮 = **54 个目标 turn**，单 block（`blockSeeds=(202609091,)`），统计单位为家族（n=6）。推理定位为方向性机制验证；主结论依赖配对方向与机制证据（渲染构成、隔离状态），精确检验作参考。

## 4. 度量

- **任务层（每轮，来自 run journal 事件，客观配对计数）**：工具调用数、输入/输出 token、模型调用数、turn 级 task.outcome（自报，作次要指标）。
- **端点 oracle（外部）**：最终轮 response marker（`response_contains`）、验证命令通过（`command`，仅命令家族）、`no_source_edits`、warm 臂 round1 `memory_rendered(injected=true)`。
- **库演化层（新增快照）**：case 结束时对 PROJECT 记忆库做只读快照（私有 evidence）：每条 entry 的 `approval_status/lifecycle_status/success_count/failure_count/corroborated_*/injection_count/content_sha256`（不含正文）。公开结果只放聚合计数。每轮渲染构成：从各 run 的 `memory.rendered` 事件取 rendered ids 与 seeded ids 求交。
- **净化指标**：`pollutant_quarantined_rate`（M2 终态 lifecycle=rejected 的比例，按臂）、`pollutant_rendered_round3_rate`、`correct_lesson_rendered_rate`（M1 各轮仍被渲染）。

## 5. 组件与改动（全部 TDD）

1. **runner：case 级 env 白名单**（`scripts/run_north_star_live.py`）——manifest case 新增可选 `env` 字段，仅允许 `MINI_CODE_MEMORY_CONTRIBUTION_REVIEW ∈ {off,rule,llm}`；`_execute_case` 仿 `NORTH_STAR_PYTHON` 的 save/set/finally-restore 模式。
2. **runner：记忆库终态快照**——turn 循环后 `MemoryManager(readonly_load=True)` 读 PROJECT scope，写入私有 evidence（新键）与公开结果的聚合计数。
3. **离线机制测试（Layer A，免费可复跑）**：`tests/test_memory_pollution_mechanism.py`——真实 MemoryPipeline + 真实 MemoryManager，seed 污染库，脚本化轨迹（读过期路径失败 + 读正确路径成功）连续三轮走 `inject → review_injected_memories`，断言：M2 两轮后隔离、第三轮检索不再渲染 M2、M1 保持渲染、重放幂等。
4. **manifest 构建器**：`scripts/build_memory_pollution_study_manifest.py`（仿既有 builder：家族定义、marker 文件、seed 记忆、oracle 组装、冻结写入拒绝覆盖）。
5. **分析器**：`scripts/analyze_memory_pollution_study.py`——复用既有分析器的 exact Wilcoxon/Holm/家族聚类 bootstrap 实现（自 stdlib，无三方依赖）；输出 turn/pair/family CSV、`statistics.json`、`analysis-report.md`、复现索引。
6. **离线合同测试**：builder 产出结构校验 + analyzer 在合成结果上的端到端运行。

## 6. 执行计划

1. Layer A 机制测试先行（RED→GREEN）。
2. runner 两项扩展（RED→GREEN），回归 `tests/test_live_north_star_runner.py`。
3. builder + analyzer + 合同测试。
4. **live smoke**：先跑 1 个 case（`--limit 1`）探测配额/模型可用性；失败即冻结 manifest、写明待跑命令，不伪造结果。
5. smoke 通过则全量执行 54 turn（预计 ~1.5–2M 输入 token），跑分析器，写实验报告 `docs/2026-09-09--memory-pollution-self-correction--r1--robustness-check.md`（沿用既有报告结构：执行摘要/身份/设置/主要发现/统计/失败与局限/信念更新/产物索引）。
6. 汇报。

## 7. 诚实性约束（沿用仓库实验文化）

- 首轮意向分析，不补跑不改写；失败样本保留并在报告中分类。
- 不把 n=6 单 block 写成高置信功效结论；报告明确"机制验证 + 方向估计"。
- 公开结果不含记忆正文、prompt、密钥；marker/合成文件除外。
- 若模型在 round 1 根本不踩污染物（直接读正确路径），"无伤害 + 无净化信号"本身就是合法结果，如实报告。
