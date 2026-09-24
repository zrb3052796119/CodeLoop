# 子 Agent 类型外置：`.mini-code/agents/*.md` 声明式 Profile — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增只读子 Agent 类型 = 在项目 `.mini-code/agents/` 放一个 Markdown 文件（frontmatter 元数据 + 正文系统提示词），task 工具即接受该类型。自定义类型一律只读、仅同步 run；写路径仍归 workflow 管制。

**Architecture:** 新模块 `minicode/subagent_profiles.py` 负责解析/校验（fail-closed）/合并（内置权威，保留名不可覆盖）与 mtime 签名缓存；task 派发按 `context.cwd` 解析合并表；三处闭合集合（result/observation/model_routing）按 slug 正则放行自定义类型；每 turn 用 `dataclasses.replace(task_tool, ...)` 生成带动态类型清单的工具描述与 schema 枚举。

**Tech Stack:** Python 3 标准库（无 PyYAML——自写封闭键集 frontmatter 解析）、pytest。

**Spec:** 本文件（源自 2026-09-24 ZCode 对比后续会话）。

## 关键设计决策

1. 目录 `.mini-code/agents/*.md`（项目级；`.mini-code/` 为既有约定）。frontmatter 封闭键集：name / description / max_turns / allowed_tools。
2. 保留名 `explore/plan/general/workflow` 拒绝加载（fail-closed，刻意比 ZCode 收紧：防覆盖写权限管制）。
3. 三处闭合集合放行：`subagent_result` / `subagent_observation` / `subagent_model_routing` 的 `_AGENT_TYPES` 判断改为"内置名或 `^[a-z][a-z0-9-]{1,31}$`"。模型路由天然支持 `subagentModels["<自定义名>"] or default`。
4. 动态工具描述：`build_task_tool_for_project(cwd)` 用 `replace(task_tool, description=..., input_schema=...)`；`create_default_tool_registry(..., task_tool_definition=...)`；`agent_runtime` 每 turn 传入。单例不污染。
5. v1 不做：spawn 异步、续聊、用户级/插件级目录、环境变量覆盖自定义轮数。

## 任务清单（每任务 TDD + 提交）

### Task 1: `minicode/subagent_profiles.py` 加载器
- Create `minicode/subagent_profiles.py`；Modify `minicode/subagent_types.py` 导出 `READ_ONLY_TOOL_WHITELIST = explore ∪ plan 工具集`；Test `tests/test_subagent_profiles.py`。
- 接口：`load_agent_profiles(root) -> (profiles, errors)`（mtime 缓存）、`resolve_agent_types(root) -> dict`、`AGENT_NAME_RE`、`RESERVED_AGENT_NAMES`。
- 校验：slug、保留名、工具⊆白名单且非空、max_turns 1..200、description 1..400、正文非空、未知键/坏行拒绝；错误带文件名。
- 提交 `feat(subagent): markdown agent profiles in .mini-code/agents`。

### Task 2: 三处闭合集合放行
- Modify `subagent_result.py` / `subagent_observation.py` / `subagent_model_routing.py`；测试：custom 类型过 result 契约、路由 inherit、坏名仍拒。
- 提交 `feat(subagent): typed contracts accept project-defined agent types`。

### Task 3: task 派发接受自定义类型
- Modify `tools/task.py`：`_validate` slug 校验；`_run` 用 `resolve_agent_types(context.cwd)`，未知类型报错列出可用类型与加载错误；自定义 max_turns 直用 profile；并发安全判定放行只读自定义类型。
- 提交 `feat(task): dispatch project-defined read-only agent profiles`。

### Task 4: 动态工具描述与 schema 枚举
- `tools/task.py` 增 `build_task_tool_for_project(cwd)`；`tools/__init__.py` 的 `create_default_tool_registry` 加 `task_tool_definition` kwarg；`agent_runtime` 接线。
- 提交 `feat(task): per-turn task-tool description from project agent profiles`。

### Task 5: 端到端真实注册表测试
- ScriptedModel 走真实子代理循环：白名单工具可见/写工具不可见；spawn 拒绝；续聊拒绝；结果契约投影。
- 提交 `test(subagent): end-to-end custom profile dispatch and boundaries`。

### Task 6: 文档 + 全量回归 + 质量门
- `docs/subagent-profiles.md`；全量 pytest 不低于基线 10385 通过；质量门通过。
- 提交 `docs: project agent profiles format and boundaries`。

## 验收标准

1. 新测试全绿；全量回归不低于基线；质量门通过。
2. 手工验收：放 `.mini-code/agents/test-locator.md`，下一 turn 工具描述可见该类型；`task(agent_type="test-locator")` 走只读子代理；spawn/写工具/续聊对该类型 fail-closed。
