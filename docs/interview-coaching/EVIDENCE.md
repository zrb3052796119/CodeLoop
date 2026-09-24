# 简历证据核对 · 2026-09-07

核对来自当前工作区的源码、评测脚本、冻结工件与报告；本轮未重跑实验。代码可能比部分文档新，不能把当前实现与历史结果默认当作同一版本。

## Skill

- `artifacts/agent-quality-baseline.json:85`：60 例 = 40 正例 + 20 abstain；正例 Top-1 40/40，20/20 正确弃权，forbiddenSelectionRate=0。
- `scripts/agent_quality_evaluator.py:288/323`：Top-1 分母为正例，禁止选择率分母为全部 60 例。这两个指标不能混称同一分母。
- `docs/agent-quality-gates.md:43`：冻结离线 directory-level router contract。不是未知分布上的泛化准确率；确定性本身不保证正确率。
- 同文 `:62` 的 96 例 joined 是经过调试优化的开发挑战集，并非盲测。

## Memory

- `docs/2026-08-21--persistent-memory-large-study--r1--robustness-check.md:13/43/53`：16 合成路径恢复家族 × 3 重复 = 48 warm/cold 对，96 个目标 Turn；另有24个教训创建 Turn。8 learned 家族、8 seeded 家族。
- 复用阶段工具 50 vs 240（−79.2%），输入 token 652,911 vs 1,539,738（−57.6%），目标 Turn 耗时总和 150.6s vs 327.7s（−54.1%），成功 48/48 vs 47/48。
- 隔离工作区、相同任务文件/marker/提示、家族顺序随机、每个重复块 warm-first/cold-first 平衡。成功由完成状态、成功读取、精确 marker、无修改等联合 oracle 判定。
- 统计按16家族聚类；不能称48个独立样本。工具降幅95% CI 76.1%–81.3%，token降幅52.8%–61.4%。见 `artifacts/persistent-memory-large-study-v3/analysis-output/analysis-report.md:5`。
- 同报告 `:33`：将完整创建 Turn 保守计作额外成本，输入token摊销约1.93次复用，含运行后反思为2.03次。并非所有任务都保证2次回本。
- 范围为只读路径恢复，保证相关且已批准记忆存在。48/48 vs47/48不能证明总体成功率提高或非劣效；并非通用修复/重构实验。报告未冻结全部模型采样与远端版本条件。

## Context

- `docs/context-compaction-experiment-study.md:60`：4合成代码任务家族 × 3重复 × 2条件 = 12对/24 case；每case 5 turns。canonical为14k窗口/85%水位，control为同runtime的128k高窗口；deepseek-v4-pro。
- 同文 `:221`：provider input总量4,288,270 vs6,011,480，聚合−28.67%；配对降幅均值21.59%、中位数22.65%。不能称“每任务平均−28.7%”。
- 同文 `:248`：严格成功9/12 vs10/12；所有可执行测试和保护marker通过，5个失败涉及远端IncompleteRead。模型调用407 vs371（+9.70%），压缩成功/失败41/29。
- 证据支持这组任务上的输入token效率；未证明质量无损、速度提高或Prompt Cache金额收益。有效数据为attempt 3，前两次异常尝试另行保留。

## Multi-agent

- `docs/agent-quality-gates.md:101/131`：2026-08-28的12例live，custom/glm-5.3-flash，隔离workspace、每case独立进程、确定性oracle、case串行运行。
- 12/12由5个并行只读fan-out、3个direct-general拒绝契约、4个workflow完成构成。不是12个写任务，也不是12例均成功委派。
- 3拒绝类先前曾按“应成功委派”的契约失败，后改为符合生产支持范围的拒绝契约并重新live采集。应主动解释契约变更。
- 同文 `:214`：子Agent记录成功26/30（86.7%）。没有单Agent对照，因此不能据此推断多Agent更快或更省token。
- `minicode/tools/task.py:2061`：子进程分支仅用于general且非scheduler_executor、非workflow_phase；Executor及workflow阶段仍保留进程内执行。
- `minicode/agent_loop.py:872` 与 `minicode/tools/task.py:2753`：主循环启用central enforcement，direct general会被拒绝，要求workflow。因此当前生产写流水线不能宣称普遍享有子进程硬终止保证。
- `minicode/deterministic_verifier.py:149/218`：Verifier是确定性检查，不是第四个LLM投票。

## 其他易混淆概念

- `MemoryScope`的USER/PROJECT/LOCAL表示作用域；另有`MemoryTier`表示时效层级（`minicode/memory.py:1342/1363`）。
- `MemoryPipeline.feedback`当前canonical路径缺少可信的精确`verification_memory_ids`，不能声称每次独立验证均可因果归因到某条记忆（`minicode/memory_pipeline.py:3051`）。
- Directory/能力/工具亲和度背景分不能独立创造相关性；Skill显式授权也不意味着所有Skill都必须由用户点名。
- 不把“模型给最终回答”“进程正常结束”“外部oracle验证成功”当成同一事件。

## 后续口述练习

1. 用一次读取请求讲清Query Loop，失败结果如何回到下一次推理？
2. Skill路由与工具权限分别决定什么？为什么相关性不等于授权？
3. 用户点名Skill后文件被改，digest契约如何处理？
4. 记忆为什么不直接保存整段聊天？错误记忆如何撤销与停止注入？
5. 压缩怎样避免丢失约束、拆散工具轮次、重复总结放大错误？
6. 为什么执行超时不等于执行已经停止？如何区分线程、子进程和工作区隔离？
7. 验证失败后，哪份工作区被丢弃、哪份工作区可以晋升？
8. 每一组数字的分母、对照、随机化单位与反例是什么？
