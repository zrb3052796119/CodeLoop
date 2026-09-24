---
title: MiniCode 持久化记忆经验教训收益实验
date: 2026-09-09
report_type: robustness-check
round: r1
status: complete
suite_id: minicode-memory-lesson-benefit-2026-09-09-v1
decision: supported-with-scope-limits
model: glm-5.3-flash
reviewer_mode: llm (constant)
---

# 1. 执行摘要

本实验测量**全链路**的持久化记忆收益：先让模型真实踩错（误导到不存在的 unittest 模块）→ 自行恢复 → 反思沉淀教训，然后在新会话中配对执行 warm（有教训）/ cold（无教训）目标任务。与 2026-08-21 大样本研究的区别：全部教训由真实失败自学习产生（非预置）、贡献评审器以 **llm 模式恒定开启**、并显式校验教训内容覆盖正确目标。

规模：6 命令恢复家族 × 2 block × 2 条件 = 24 case / 36 目标+学习 turn；183 次模型调用、140 万输入 token、累计 17.7 分钟；23/24 case 通过（1 个 warm case 教训内容未覆盖正确目标）。

核心结果：

| 指标（目标轮家族均值） | warm（有教训） | cold（无教训） | 节省 |
|---|---:|---:|---:|
| 仓库工具调用 | 2.50 | 6.67 | **−62.5%**（CI −77%~−44%，精确 Wilcoxon p=0.031，Holm p=0.0625） |
| 任务输入 token | 26,365 | 43,405 | **−39.3%**（CI −16k~−7k，p=0.0625，Holm p=0.0625） |
| 目标轮耗时 | 22.2s | 26.5s | −16%（CI 跨 0，不显著） |
| 输出 token | 628 | 564 | +11%（不显著） |
| 严格成功 | 12/12 | 12/12 | 持平 |

学习链有效性：**误导真实命中 12/12，教训写入 12/12，内容覆盖正确目标 11/12，学习轮教训即目标轮渲染教训 11/12**。

一句话：**自学习的经验教训把同类新任务的探索成本砍了一多半（工具调用 −62.5%、token −39%），且不损失成功率；沉淀质量约 92%（11/12）；llm 评审在 live 下端到端工作。**

# 2. 实验身份

- 冻结套件：`minicode-memory-lesson-benefit-2026-09-09-v1`（manifest SHA-256 见产物索引）
- 家族：c1 ledger / c2 normalizer / c3 registry / c4 session / c5 schema / c6 invoice——每族独立 Python 包 + unittest（打印 marker），学习提示误导到不存在的模块
- 配对：warm（学习轮 + 目标轮，同工作区记忆跨轮）vs cold（同 fixture 同提示，空记忆库）；2 block，warm-first/cold-first 交替
- 评审器：全部 case `MINI_CODE_MEMORY_CONTRIBUTION_REVIEW=llm`
- runtime 契约：glm-5.3-flash @ bigmodel OpenAI 兼容端点（profileSha256 52f005…c2fb）
- 统计：家族为推断单位（n=6，block 均值后配对）；精确 Wilcoxon + 家族聚类 bootstrap（20,000 样本）+ Holm（主指标族 = 工具调用 + 输入 token）

# 3. 设计修订记录（首跑前）

初版含 3 个路径恢复家族。路径家族 smoke 显示：任务链成功，但反思写入被生产端价值门抑制（`single_observation_error_pattern`，reflection_synthesis.py）——**当前管线有意只沉淀有验证证据的恢复，单次观察的路径错误不沉淀**。命令家族 smoke 9/9 通过（错误命令失败→正确 unittest 通过 = 验证过的恢复）。修订为 6 命令恢复家族。该门槛行为本身是实验发现（见 §6 信念更新）。

# 4. 主要发现

## 4.1 收益（RQ1）

见摘要表。补充：warm 的总成本（含学习轮）为 874k 输入 token vs cold 528k——**单次复用不足以摊平学习成本**；按目标轮平均节省 17k token 计，盈亏平衡约需 2–3 次同类复用（与 2026-08-21 研究的 1.9–2.0 次估计同量级）。

## 4.2 沉淀有效性（RQ2）

| 环节 | 通过率 |
|---|---:|
| 误导目标被真实执行且失败（tool_failed） | 12/12 |
| 反思写入教训（memory_written） | 12/12 |
| 教训内容覆盖正确模块（memory_lesson_covers） | 11/12 |
| 学习轮写的教训 = 目标轮渲染的教训（memory_attributed） | 11/12 |

唯一失败（lb-b2-c2 warm）：教训写了、渲染了，但内容未包含精确模块串（反思措辞随机性）。复跑探针（lb-b1 warm，另一随机实例）同样出现内容不含精确模块的情况——**沉淀内容对"可机读的目标引用"无保证**，这是内容质量的真实波动，不是链路断裂。

## 4.3 llm 评审（RQ3）

首轮全量中评审的模型调用与判定事件因观测缺口未进 journal（agent loop 未把 event_sink 传给评审调用——实验后已修复并加测试）。通过池计数差分与修复后的单 case 探针确认：

1. **llm 评审客户端在 live 下真实工作**：探针观察到 `purpose=memory_contribution_review` 的模型调用进入 journal；该轮教训获得 success_count+1（评审通道 helped）且无误判。
2. **首轮全量中评审确已执行**：warm case 的教训出现整轮反馈之外的计数变化（含 1 例佐证失败）。
3. **发现并修复一个规则回退的真实缺陷**：恢复类教训同时记录"错误命令（会失败）"与"正确命令"；当模型在目标轮重试教训中记录的错误命令时，规则回退把该失败绑到教训上判 invalidated（首轮 1 例 corrF=1）。修复：多主张 + 双极性并存时规则判 undetermined（"无法归因到正确主张"），交由 llm 评审裁决——带回归测试。
4. 评审 token 成本：首轮不可见（观测缺口）；探针显示单次评审约 1.5k 输入 token 量级，相对目标轮 26k 占比小。

# 5. 失败与局限

1. 24 case 中 1 个失败（lesson-covers-target），保留为首轮意向分析证据。
2. n=6 家族、单模型、单日、合成命令恢复任务：方向与机制证据，非功效结论；Holm 后 p=0.0625 处于边界。
3. 全部为命令恢复形态；路径恢复因沉淀门行为（§3）未纳入——泛化到路径/代码修复教训需后续轮。
4. 首轮评审判定分布（helped/invalidated/undetermined 计数）不可恢复，仅能由计数差分推断；事件观测已修复，后续轮可测。
5. 学习轮与目标轮共享工作区文件状态：warm 目标轮的教训是在"已知该工作区"的语境下写的，真实跨仓库迁移未测。

# 6. 信念更新

- **加强**：自学习教训对同类新任务的探索成本削减可复现地成立（工具 −62.5% / token −39.3%，与 2026-08-21 预置教训研究的 −79%/−58% 同方向；差异来自任务形态与全部自学习）。
- **加强**：当前生产沉淀门是有意设计的"宁缺毋滥"——只有验证过的恢复才入库（单次路径错误不沉淀）。这解释了为何命令恢复类教训质量高：入库即带验证证据。
- **新发现**：恢复类教训的"文档化失败"会被规则评审误判为教训失效（已修复）；llm 评审在此场景的语义判断是该功能的核心价值场景。
- **新发现**：沉淀内容对精确目标引用无保证（11/12 与探针复发）——若下游（检索、评审的确定性匹配）依赖精确引用，需要沉淀时的结构化约束。

# 7. 下一步

1. 复跑一轮（评审事件观测已修复）获得完整判定分布与评审 token 成本。
2. 探针轮失败的教训内容做形态分析：反思提示是否应强制包含精确命令/路径串。
3. 把路径恢复重新纳入（设计"验证过的路径恢复"任务形态，绕开单次观察门）。
4. 跨工作区迁移测试：教训在仓库 A 学到、在结构相同但路径不同的仓库 B 使用。

# 8. 产物索引

- 冻结 manifest：`artifacts/memory-lesson-benefit-study-v1/manifest.json`
- 首轮结果：`artifacts/memory-lesson-benefit-study-v1/full-first-attempt.json`（23/24 passed）
- 私有证据：`artifacts/memory-lesson-benefit-study-v1/full-first-attempt-evidence/`
- 评审探针（修复后）：`artifacts/memory-lesson-benefit-study-v1/review-probe.json`
- 分析输出：`artifacts/memory-lesson-benefit-study-v1/analysis-output/`（statistics.json、analysis-report.md、turn-level.csv、pair-level.csv）
- 构建器/分析器：`scripts/build_memory_lesson_benefit_study_manifest.py`、`scripts/analyze_memory_lesson_benefit_study.py`
- 新 oracle：`memory_lesson_covers`（scripts/run_north_star_live.py，内容级沉淀校验，公开结果仅布尔）
- 测试：`tests/test_memory_lesson_benefit_runner_support.py`、`tests/test_memory_lesson_benefit_study.py`、评审混合极性回归（tests/test_memory_contribution_reviewer.py）
- 设计文档（含修订记录）：`docs/superpowers/specs/2026-09-09-memory-lesson-benefit-experiment-design.md`

复现分析：

```bash
python scripts/analyze_memory_lesson_benefit_study.py \
  --manifest artifacts/memory-lesson-benefit-study-v1/manifest.json \
  --result artifacts/memory-lesson-benefit-study-v1/full-first-attempt.json \
  --output-dir artifacts/memory-lesson-benefit-study-v1/analysis-output
```
