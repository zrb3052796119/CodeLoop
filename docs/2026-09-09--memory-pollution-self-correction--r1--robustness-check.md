---
title: MiniCode 记忆污染与自净实验
date: 2026-09-09
report_type: robustness-check
round: r1
status: complete
suite_id: minicode-memory-pollution-self-correction-2026-09-09-v1
decision: supported-with-scope-limits
model: glm-5.3-flash
---

# 1. 执行摘要

这是第一轮针对**污染记忆库**的 live 实验：当正确教训与污染物（过期路径、错误命令、无关条目）同时入库时，任务是否受损、证据引用反馈闭环能否净化记忆库、评审器是否必要。此前两轮研究（2026-08-21 路径恢复、2026-08-22 非路径）都只在干净库上测量收益。

核心结论（6 家族 × 3 臂 × 3 轮 = 18 case / 54 目标 turn，全部严格通过）：

1. **污染没有伤害任务成功率**：三个臂 round-3 严格成功全部 6/6；模型在全部 18 个 case 中最终都找到了正确目标。
2. **污染的代价是探索成本，且集中在第一轮**：污染臂相对干净臂的 token 超额在 round 1 最大（off 臂平均 +17,779 输入 token），round 3 收敛到 +1,630（off）/ +226（rule）。所有对比的精确 Wilcoxon 在 Holm 校正后均不显著（n=6，本轮为方向性证据）。
3. **隔离阈值在本设计内结构性不可达**：两次独立佐证失败才隔离，而模型在 3 轮内对同一污染物的"踩踏"至多 1 次 → staleQuarantinedRate = 0/6（两臂均是）。这是设计约束，不是机制失败。
4. **评审器展现了整轮反馈不具备的区分力**：当模型真的执行了错误命令时，评审臂把失败精确记到 stale 条目上（corrFail=1，2/3 命令家族）；同一轮里，整轮反馈因为任务最终成功反而给同一条 stale 记了 success（succ=3 / succ=2）——整轮奖励污染、评审惩罚污染，方向相反。
5. **"无证据不判定"按设计工作**：路径家族的模型每轮直接读正确路径、从不踩 stale 路径 → 无证据 → 不加减分 → stale 一直被渲染（3/3 轮）。评审器没有发生任何误伤（0 次错误隔离/惩罚）。

一句话：**污染的实测伤害是 token 噪声与偶发探索弯路，不是任务失败；评审器的价值在于把"踩到污染物"的那几次精确转化为负证据，但它无法惩罚从未被踩过的污染物——这是证据驱动的代价。**

# 2. 实验身份与决策问题

- 冻结套件：`minicode-memory-pollution-self-correction-2026-09-09-v1`
- 主推断单位：任务家族（n=6：3 路径恢复 + 3 命令恢复，全部 seeded）
- 臂：`clean`（仅正确教训）/ `polluted-off`（+污染物，评审关闭=本功能落地前生产行为）/ `polluted-rule`（+污染物，评审 rule 模式开启）
- 轮次：每 case 3 个连续目标 turn，不携带对话历史，记忆经磁盘跨轮
- runtime：glm-5.3-flash，OpenAI 兼容端点，manifest 冻结 v2 wire-route 契约（profileSha256=52f005…c2fb）
- 执行：140 次模型调用、1,122,003 输入 token、31,280 输出 token、case 累计 1,106.5 秒；18/18 case 通过全部 oracle
- 污染物构造：词法接近正确教训（竞争检索），但主张必败（路径不存在 / unittest 模块不存在）

# 3. 主要发现

## 3.0 各臂每轮原始指标（家族均值，n=6）

| 臂 | 轮 | 自报成功 | 工具调用 | 工具失败 | 任务模型调用 | 侧模型调用 | 输入 token | 输出 token | 耗时(秒) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| clean | 1 | 6/6 | 1.00 | 0.00 | 2.00 | 0.00 | 15,466 | 244 | 12.5 |
| clean | 2 | 6/6 | 1.33 | 0.00 | 2.33 | 0.00 | 18,205 | 373 | 17.3 |
| clean | 3 | 6/6 | 1.00 | 0.00 | 2.00 | 0.00 | 15,508 | 266 | 12.7 |
| polluted-off | 1 | 6/6 | 3.17 | 0.50 | 3.83 | 0.17 | 33,244 | 1,236 | 40.4 |
| polluted-off | 2 | 6/6 | 1.50 | 0.00 | 2.33 | 0.00 | 18,682 | 627 | 19.6 |
| polluted-off | 3 | 6/6 | 1.17 | 0.00 | 2.17 | 0.00 | 17,138 | 368 | 13.7 |
| polluted-rule | 1 | 6/6 | 2.00 | 0.17 | 2.67 | 0.17 | 22,285 | 654 | 22.6 |
| polluted-rule | 2 | 6/6 | 2.67 | 0.33 | 3.50 | 0.17 | 29,414 | 832 | 29.8 |
| polluted-rule | 3 | 6/6 | 1.00 | 0.00 | 2.00 | 0.00 | 15,735 | 360 | 15.5 |

读法：干净库基线约 1 次工具调用、1.5–1.8 万输入 token、12–17 秒每轮。污染的第一轮代价最大——off 臂 3.17 次工具（3 倍）、3.3 万 token（2.2 倍）、40.4 秒（3.2 倍）；两污染臂都在第 2/3 轮收敛回基线。工具失败只出现在污染臂的第 1/2 轮（踩到错误命令），且模型全部自行恢复。侧模型调用（反思/评审通道）极少（每 cell 至多 0.17 次），评审 rule 模式本身零额外模型调用。

## 3.1 任务层：污染不改变成败，改变成本

| 指标（家族均值） | clean | polluted-off | polluted-rule |
|---|---:|---:|---:|
| round-3 严格成功 | 6/6 | 6/6 | 6/6 |
| round-1 输入 token 超额（vs clean） | — | +17,779 | +6,820 |
| round-3 输入 token 超额（vs clean） | — | +1,630 | +226 |
| round-1 工具调用超额 | — | +2.17 | +1.00 |
| round-3 工具调用超额 | — | +0.17 | 0.00 |

超额随轮次收敛，与"模型第一轮消化多条记忆、后续轮依赖已验证目标"的行为一致。所有 12 个主检验（2 指标 × 3 对比 × 2 轮次）经 Holm 校正后 p≥0.375——方向一致但样本不足，本轮只声明方向。

## 3.2 行为层：模型如何对待污染物

- **路径家族（3/3）**：模型每轮第一个动作就是读正确路径（记忆渲染 3 条，工具调用 1 次成功）。stale 路径 18 轮中 0 次被读取。
- **命令家族**：部分轮次出现 1 次工具失败（off 臂 round1 三个家族各 1 次；rule 臂 round1/round2 共 3 次），随后模型改用正确命令并成功——污染物造成的是**弯路**而非失败。

## 3.3 记忆库层：反馈闭环的实测行为

| 终态（stale 条目） | polluted-off | polluted-rule |
|---|---:|---:|
| 被隔离（lifecycle=rejected） | 0/6 | 0/6 |
| 记录到佐证失败（corrFail≥1） | 0/6 | 2/6（c1、c3） |
| round-3 仍被渲染 | 4/6 | 3/6 |
| 被整轮反馈记 success | 3/6 | 3/6 |

三个关键观察：

1. **评审臂的两次佐证失败都与真实的错误命令执行对应**（c1 round2、c3 round1），且其后 stale 不再被渲染；off 臂同样踩了污染物（3 次）却 0 记录——只靠整轮反馈，踩踏事件完全丢失。
2. **同一 case 内两种反馈方向相反**：p1-rule 的 stale 被整轮反馈记了 3 次 success（任务成功均摊奖励），而 c1/c3-rule 的 stale 被评审记了 1 次佐证失败（执行证据）。这正是评审器存在理由的实测展示。
3. **渲染衰减部分是自然的**：off 臂的 c1/c2 stale 在无任何负反馈的情况下也从 round 2 起不再被渲染（检索竞争波动）。因此"rule 臂渲染更低"不能全部归因评审器，n=6 无法分离。

# 4. 统计方法

家族为推断单位；每 cell 一个 case（3 轮）。主检验：2 指标（工具调用、任务输入 token）× 3 对比（clean vs 两污染臂、污染臂互比）× 轮次 {1,3}，精确 Wilcoxon + 家族聚类百分位 bootstrap（20,000 样本，seed 202609091）+ Holm 校正。池演化端点为描述性比率。全部自 stdlib 实现，复用 2026-08-21 研究的分析组件。

# 5. 失败、负结果与局限

1. **隔离阈值为 2，但 3 轮内同一污染物至多被踩 1 次** → 隔离率结构性为 0。要测隔离需要：更多轮次、或强制踩踏的任务设计（如只给 stale 不给 correct 的探针轮）。Layer A 离线机制测试（tests/test_memory_pollution_mechanism.py）已证明踩两次时隔离与检索排除在真实管线上成立——live 轮次不足是设计约束，非机制缺陷。
2. **c2-rule 的踩踏未被关联**（有工具失败但 corrFail=0）：失败来自 shell 包装被权限拒绝（沿袭 2026-08-22 研究的发现），工具输入与 stale 的模块主张不匹配，确定性匹配器正确地拒绝猜测。LLM 评审模式可能能关联此类证据，本轮未开启。
3. **单 block、n=6、单模型（glm-5.3-flash）**：所有 p 值经校正后不显著；结论限于方向与机制行为。
4. **任务全为合成只读/验证型**：污染对代码修改类任务的伤害未测。
5. stale 的词法竞争强度未标定：`correctRenderedAnyRoundRate` 三臂均为 1.0（正确教训始终渲染），说明本设计的污染物未挤掉正确教训；更强的污染未测。

# 6. 信念更新

- **加强**：污染的主要实测代价是 token 噪声与首轮探索弯路，不是任务失败（在本任务族内）。
- **加强**：证据引用反馈把"踩踏事件"转化为条目级负证据（2/6），而整轮反馈在同样场景奖励了污染条目（3/6）——两者方向相反，评审器提供了不可替代的信号。
- **加强**："无证据不判定"零误伤：18 case × 3 轮中评审器没有惩罚过任何未被踩踏的记忆。
- **受限**：隔离机制在真实节奏下需要 ≥2 次踩踏，短任务序列内难以触发；其价值主要在长期重复使用场景。
- **未知**：LLM 评审模式能否关联权限拒绝类的间接失败；更强污染（挤掉正确教训）下的系统行为。

# 7. 下一步

1. 延长轮次（5–8 轮）或加入"只有 stale"的探针轮，使隔离阈值可达，直接测 round-N 净化率。
2. 开启 llm 评审模式的对照臂，测间接失败（shell 包装、权限拒绝）的关联率。
3. 标定污染物竞争强度（把 stale 排到 correct 之前），测"正确教训被挤出"的最坏情况。
4. 跟踪 stale 条目的 injection_count 与渲染排名随佐证失败的逐轮变化（本轮快照只有终态）。

# 8. 产物索引

- 冻结 manifest：`artifacts/memory-pollution-study-v1/manifest.json`（SHA-256 前缀 3ef23b4b9743d52a）
- 首轮完整结果：`artifacts/memory-pollution-study-v1/full-first-attempt.json`（18/18 passed）
- 私有证据：`artifacts/memory-pollution-study-v1/full-first-attempt-evidence/`
- 分析输出：`artifacts/memory-pollution-study-v1/analysis-output/`（turn-level.csv、pool-evolution.csv、statistics.json、analysis-report.md）
- 构建器：`scripts/build_memory_pollution_study_manifest.py`
- 分析器：`scripts/analyze_memory_pollution_study.py`
- 离线机制测试：`tests/test_memory_pollution_mechanism.py`、`tests/test_memory_pollution_study.py`、`tests/test_memory_pollution_runner_support.py`
- 设计文档：`docs/superpowers/specs/2026-09-09-memory-pollution-self-correction-experiment-design.md`

复现分析：

```bash
python scripts/analyze_memory_pollution_study.py \
  --manifest artifacts/memory-pollution-study-v1/manifest.json \
  --result artifacts/memory-pollution-study-v1/full-first-attempt.json \
  --output-dir artifacts/memory-pollution-study-v1/analysis-output
```

基础设施修复（本实验顺带完成，已带测试）：runner 支持 case 级 `MINI_CODE_MEMORY_CONTRIBUTION_REVIEW` 白名单、case 终态记忆池快照进私有证据、忽略记忆库跨进程锁文件对 no-source-edits 的干扰。
