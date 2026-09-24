# 持久化记忆经验教训收益实验（Memory Lesson Benefit）设计

日期：2026-09-09
suite：`minicode-memory-lesson-benefit-2026-09-09-v1`
类型：paired warm/cold 效益测量 + 学习链有效性验证（robustness-check r1）

## 1. 研究问题

1. **RQ1（收益）**：由真实失败自动沉淀的经验教训，在新会话中重新执行同类任务时，能否减少工具调用、输入/输出 token 与耗时？（warm vs cold 配对）
2. **RQ2（沉淀有效性）**：真实构造的失败（先走错目标→失败→探索→恢复）能否被反思链正确沉淀为教训？判据：写入条目存在、且内容覆盖正确目标（精确路径/命令模块）。
3. **RQ3（llm 评审行为）**：评审器 llm 模式恒定开启时，对自学习教训的判定分布（helped/invalidated/undetermined）、降级原因、额外模型调用成本。

与 2026-08-21 大样本研究的差异：该研究 8/16 家族为预置（seeded）教训；本实验**全部教训由真实失败链自学习产生**，且全程 llm 评审开启（该研究无评审器），并显式校验教训内容覆盖正确目标。

## 2. 设计

### 2.1 家族（6 个，全部 learned）

| 家族 | 类别 | 正确目标 | 学习轮误导目标（必败） | marker |
|---|---|---|---|---|
| lb-p1-auth-policy | 路径 | `docs/policies/auth-policy.md` | `policies/auth-policy.md` | AUTH-POLICY-771 |
| lb-p2-invoice-schema | 路径 | `services/billing/invoice_schema.yaml` | `services/invoice/schema.yaml` | INVOICE-SCHEMA-623 |
| lb-p3-release-manifest | 路径 | `build/release-manifest.json` | `release/manifest.json` | RELEASE-MANIFEST-509 |
| lb-c1-ledger-verify | 命令 | `python -m unittest ledgerlib.tests.test_config` | `python -m unittest tests.test_config` | VERIFY-LEDGER-642 |
| lb-c2-normalizer-verify | 命令 | `python -m unittest datalib.tests.test_normalizer` | `python -m unittest test_normalizer` | VERIFY-NORMALIZER-773 |
| lb-c3-registry-verify | 命令 | `python -m unittest registrylib.tests.test_registry` | `python -m unittest registrylib.test_registry` | VERIFY-REGISTRY-824 |

fixture 与污染实验同构（marker 文件 + 干扰邻居文件 + 可运行 unittest 包），误导目标不落盘。

### 2.2 条件与轮次

- **warm case**（2 turn）：turn 0 学习轮——任务提示先指向误导目标（"a previous note says check {wrong}，start there"），模型真实踩错（read 失败 / 命令模块不存在）→ 自行探索恢复 → 报告 marker → 轮末反思沉淀教训；turn 1 目标轮——新会话同一工作区，相同语义任务（提示不指向任何路径），教训应被检索注入。
- **cold case**（1 turn）：同一 fixture（无学习轮），目标轮提示与 warm 完全一致，记忆库为空。
- 2 个 block（seed 202609092 / 202609093），block 内 warm-first/cold-first 按 (pair_order+block) 奇偶交替。
- 规模：6 家族 × 2 block × (warm 2 turn + cold 1 turn) = **36 个 live turn，24 个 case**。统计单位：家族（n=6，block 为嵌套重复）。

### 2.3 评审器

全部 case（含 cold）设 `env.MINI_CODE_MEMORY_CONTRIBUTION_REVIEW = "llm"`。cold 无注入 → 评审自然 no-op；warm 学习轮无注入 → no-op；warm 目标轮有注入 → llm 评审真实执行（其模型调用计入 side_model_calls，判定进入 run journal 的 `memory.contribution_reviewed` 事件）。

### 2.4 Oracle

学习轮（warm case, turn 0）：
- `source-failure`：tool_failed（路径家族 read_file / 命令家族 run_command，min=1）
- `recovery-marker`：response_contains marker（证明真实恢复并读到目标）
- `lesson-written`：memory_written（反思写入）
- `lesson-covers-target`（**新增 oracle kind `memory_lesson_covers`**）：PROJECT 记忆库中存在未隔离条目，其内容包含精确正确目标串（路径或 `pkg.tests.test_x` 模块）；可选 claimType 过滤。公开结果只记 pass/fail，不落教训文本。

目标轮（warm turn 1 / cold turn 0 共用）：
- `canonical-success`、`target-marker`（response_contains）、`no-source-edits`、命令家族 `target-verifier`（command argv + exitCode=0）
- warm 独有：`lesson-attributed`（memory_attributed：source=written、sourceTurn=0、renderedTurn=1、min=1——学习轮写入的教训正是目标轮渲染的那条）

## 3. 组件（TDD）

1. **runner：新 oracle kind `memory_lesson_covers`**——manifest 合同（text 非空 ≤200 字符、可选 claimType ∈ 已知集）；评估时只读加载工作区记忆库做内容包含检查；公开结果仅布尔。测试：接受/拒绝/空库。
2. **构建器** `scripts/build_memory_lesson_benefit_study_manifest.py`——家族定义、误导学习提示、warm/cold 配对、env=llm、冻结 runtime 契约（与污染实验同一 glm-5.3-flash profile）、拒绝覆盖冻结产物。
3. **分析器** `scripts/analyze_memory_lesson_benefit_study.py`——复用污染实验分析器的 `_load_events`/`_run_metrics` 与大样本研究的统计组件（exact Wilcoxon / 聚类 bootstrap / Holm）；输出：
   - **原始指标表**（每条件每 block：工具调用/失败/任务与侧模型调用/输入输出 token/耗时/成功——上轮教训，必须先给原始值）；
   - 主检验：cold−warm 家族配对差（tool_calls、task_input_tokens，Holm 校正）；次要：output tokens、耗时（探索性）；
   - 学习有效性率：lesson-written / lesson-covers-target 通过率；
   - llm 评审行为：判定分布、降级原因、reviewSource（llm vs rule_fallback）、评审模型调用次数与 token；
   - 产物：turn-level.csv、pair-level.csv、family-summary.csv、statistics.json、analysis-report.md。
4. **合同测试** `tests/test_memory_lesson_benefit_*.py`——builder 不变量（24 case/36 turn、误导目标不在 files、warm/cold 目标提示逐字一致、env=llm）+ 分析器在合成证据上的端到端断言（已知 warm/cold 模式 → 节省方向与率值）。

## 4. 执行

1. TDD：oracle kind → runner 回归 → builder → analyzer → 合同测试。
2. live smoke：1 个 warm case（`--case-id` 选 `lb-p1-auth-policy-warm`）验证学习链 + llm 评审端到端；失败即修或冻结。
3. 全量 24 case（36 turn）；预计 ≤1.5M 输入 token。
4. 分析 + 报告 `docs/2026-09-09--memory-lesson-benefit--r1--robustness-check.md`（含原始指标表、配对统计、学习有效性、llm 评审行为、局限）。

## 5. 诚实性约束

- 首轮意向分析；warm 学习轮失败（未恢复/未沉淀）保留为失败证据，不补跑。
- 家族为推断单位（n=6）；2 block 用于观察 provider 随机性，不当成 12 个独立样本。
- 教训"覆盖正确目标"是内容包含检查，不等于教训语义完全正确；报告如实表述。
- llm 评审把任务摘要+教训+轨迹摘要发给与主模型同一端点（与任务提示本身同披露面）；报告记录此事实。

## 6. 设计修订记录（2026-09-09，smoke 后）

初版设计含 3 个路径恢复家族。路径家族 smoke（`lb-b1-p1-auth-policy-warm`）显示：任务链成功（误导被踩、目标找到），但反思写入被生产端反噪声门抑制——`skipped reflection write reasons=single_observation_error_pattern`（`reflection_synthesis.py` 的价值门：仅含 error_pattern/warning 的单次观察、且无验证过的恢复时不沉淀）。这是当前代码的**有意设计**，不是缺陷；命令家族 smoke（`lb-b1-c1-ledger-verify-warm`）9/9 通过——错误命令失败→正确 unittest 通过构成"验证过的恢复"，沉淀门打开。

修订：6 个家族全部改为命令恢复形态（c1–c6，各自独立包/测试/marker），学习轮提示误导到不存在的模块。该发现本身作为实验结果记录：**当前生产管线只沉淀有验证证据的恢复，不沉淀单次观察的路径错误**。
