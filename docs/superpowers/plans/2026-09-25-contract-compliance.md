# 修复 live 模型契约遵从 — 实施计划（2026-09-25）

**Goal:** 真实子 Agent 模型从不提交 typed 结果回执（2026-09-25 live A/B：20/20 fallback）。两个方向修复：强化指令措辞 + 一次有界收尾补救回合（缺表→提醒→给一次补交机会→溯源可见）。

**Design:** 补救回合在 task.py 进程内路径成功 outcome 之后、`project_subagent_result` 之前触发：条件（mailbox 有、回执缺、非 partial、outcome success）；消息 = 新 system（同指令）+ 首轮消息体 + 提醒 user 消息（含确切 key/形状）；`max_steps=2`、关软着陆、独立 outcome_capture、同 sink/budget/journal/cancel/deadline；恢复→reported，仍缺→fallback；header 溯源行两种措辞。跳过条件：无 mailbox / 已交 / partial / 非成功。

## Tasks
1. 强化 `_subagent_result_instruction`（形状块逐字节不动，前加强制措辞）+ 测试。提交 `feat(subagent): imperative framing for the typed result instruction`。
2. 收尾补救回合 + 5 个测试（恢复/拒交/partial 跳过/已交跳过/无 mailbox 跳过）+ 4 套件回归。提交 `feat(task): one bounded closing-reminder pass to recover the typed result note`。
3. live 验证：`run_explore_turn_ab.py --live --turns 12 --limit 5`，对照全 fallback 基线。artifacts/explore-turn-ab-contract-check-2026-09-25。
4. 文档（measurement-round 追加）+ 全量 ≥ 10431 + 质量门。提交 `docs: contract-compliance fix and live verification`。

**验收：** 新测试全绿；三种跳过与两种溯源措辞被单测证明；live 小样本显示 fallback→reported 改善（或如实报告未改善）。
