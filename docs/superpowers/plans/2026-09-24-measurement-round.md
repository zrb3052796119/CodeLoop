# 测量轮：续聊节省基准 + 采用率可测量性 + explore 8v12 harness — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans / subagent-driven-development. Steps use checkbox syntax.

**Goal:** 给续聊与 explore 轮数两个已上线能力补"拿数字说话"的证据链：① 离线确定性续聊节省基准；② journal/观察事件的无内容续聊标记 + 采用率分析器；③ explore 轮数 8v12 对比 harness（默认零 API 调用）。顺手修复 subagent_journal.start 的闭合类型门（自定义 profile 类型被静默拒录）。

**Key decisions:** 观察 v5 = v4 字段 + `continuedFromSubagentId`（仅 sub_ id 或 None，content-free；v1-v4 形状不动）；journal start 记录同名字段；A/B runner 同一代码路径支持 scripted（测试）/live（用户触发）双模式；花钱边界：默认零 API 调用，live 仅 ≤2 次冒烟且可跳过。

## Tasks

1. **Task 1 标记**：`subagent_journal.start(continued_from=...)` + 类型门放宽（内置或 slug）；`subagent_observation` v5（`_V5_FIELDS`、`_normalize_v5` id 校验、`project_subagent_event(continued_from_subagent_id=...)` 非 workflow 升级 v5）；task.py 两处传 `continue_id`。提交 `feat(subagent): content-free continuation marker in journal and observation v5`。
2. **Task 2 节省基准**：`scripts/evaluate_continuation_savings.py`——fixture 仓库 + ScriptedModel 驱动真实 task_tool.run：初次探索（4 次工具调用）→ 续聊追问（0 次）vs 重新 explore（4 次）；解析 header 的 model turns/tool calls；报告含 savingsRatio，写 `artifacts/continuation-savings-2026-09-24/report.json`。提交 `feat(scripts): deterministic continuation-savings benchmark`。
3. **Task 3 采用率分析器**：`scripts/analyze_continuation_adoption.py` 读 Run journal 目录：explore/plan 总数、续聊数、采用率、续聊 vs 非续聊 modelTurns 均值。提交 `feat(scripts): continuation adoption analyzer over run journals`。
4. **Task 4 8v12 harness**：`scripts/run_explore_turn_ab.py` + `tests/fixtures/explore_turn_ab_cases.json`（10 用例）。CLI `--turns 8,12 --repeats --scripted/--live --out`；load_runtime_config + env 覆盖 MINI_CODE_SUBAGENT_EXPLORE_MAX_TURNS；AgentTurnBudget 记 token；报告 per-case 行 + 成功/fallback/partial/token 汇总。提交 `feat(scripts): explore turn-count A/B harness`。
5. **Task 5 收尾**：跑节省基准出报告；live 冒烟（可用则 ≤2 次，否则如实标注）；`docs/measurement-round.md`；全量 pytest ≥ 10416 基线 + 质量门。提交 `docs: measurement round results and harness usage`。

**验收：** 新测试全绿；全量不低于基线；artifacts 有可复现节省报告；journal 可区分续聊；分析器样例正确；8v12 一条命令可跑。
