# 子 Agent 协作优化（ZCode 学习落地）：提示层 + 续聊

## 目标与范围

落地两项已确认的优化：**(1) 提示层**——把委托启发式（何时委托/何时直接搜/委托后不重复搜索/广度档位）写进 `task` 工具描述（ZCode 的经验：模型在决定调用工具那一刻最可靠能看到的是工具描述）；**(2) 续聊**——新增 `continue_subagent_id`，按 turn 保存已完成的 explore/plan 子 Agent 会话，续聊恢复历史、只付增量成本（对标 ZCode 的 SendMessage/resume，我们目前每次 task 调用都是一次性的）。

**本轮明确不做**：profile 外置（独立子系统，下一轮）；explore turn 数 8v12 A/B（单独安排）；提示缓存排序（已核实两个 adapter 均无 `cache_control` 支持，现在重排无收益）。

## 全局约束

- 只改 `minicode/tools/task.py`、`minicode/subagent_types.py`、`minicode/prompt.py`、`minicode/agent_loop.py`、`minicode/tooling.py`；新增 `minicode/subagent_sessions.py`、两个测试文件、一个文档。
- 所有校验 fail-closed：未知 id / 类型不匹配 / store 缺失都返回明确 error，绝不静默降级为新会话。
- 续聊仅支持 `action='run'` + `agent_type in ('explore','plan')`；spawn 异步路径与 general/workflow 拒绝。
- 预算/深度/journal/observation 语义不变（续聊是一次新的 `_run`，observation 保持 content-free）。
- 每任务 TDD：失败测试 → 实现 → 通过 → 提交，提交信息沿用 `feat()/test()/docs()` 惯例。

## 任务间接口总览

- Task 3 产出：`SubagentSession(subagent_id, agent_type, messages, partial)`（frozen dataclass）、`SubagentSessionStore(max_sessions=8)`（`.save/.get/.retained_ids`，FIFO 驱逐，线程安全）、`ToolContext._subagent_sessions` 字段（缺省 None）。
- Task 4 消费上述 API；schema 新属性 `continue_subagent_id`（pattern `^sub_[0-9a-f]{32}$`，与 `_validate` 正则一致）。

---

### Task 1: task 工具描述内嵌委托启发式

**Files:** Modify `minicode/tools/task.py:3031-3051`（description）、`minicode/prompt.py:332-333`（加一行）；Test `tests/test_task_tool_guidance.py`（新增）。

- [ ] Step 1 失败测试：

```python
"""Prompt-layer guidance markers for task-tool delegation heuristics."""

from minicode.subagent_types import AGENT_TYPES
from minicode.tools.task import task_tool


def test_task_tool_description_carries_delegation_heuristics() -> None:
    text = task_tool.description
    assert "conclusion, not the file dumps" in text
    assert "Once you have delegated a search, do not also run it yourself" in text
    assert "single-fact lookup" in text
    assert "self-contained" in text
    assert "'medium' or 'very thorough'" in text


def test_system_prompt_forbids_duplicating_delegated_search() -> None:
    from minicode.prompt import build_system_prompt

    text = build_system_prompt("/tmp", [], {"skills": []})
    assert "do not repeat that search yourself" in text
```

- [ ] Step 2 `python -m pytest tests/test_task_tool_guidance.py -q` 确认 FAIL。
- [ ] Step 3 实现：`task_tool.description` 保留全部现有事实，在 `(read-only).` 之后插入：`"When to delegate: answering means reading across several files, or you have independent work that can run in parallel — delegate it and keep the conclusion, not the file dumps. When NOT to delegate: a single-fact lookup where you already know the file, symbol, or value — use read_file/grep_files/find_symbols directly. Once you have delegated a search, do not also run it yourself — wait for the result. Every call starts in a fresh context: the prompt must be self-contained. In the 'explore'/'plan' prompt you may state the breadth ('medium' or 'very thorough'); the sub-agent adapts its search accordingly. "`。`prompt.py` 在 `"Do NOT use the task tool for simple lookups..."` 行后插入：`"If you did delegate exploration to a sub-agent, do not repeat that search yourself in the main context — wait for its result and build on it.\n"`。
- [ ] Step 4 测试 PASS。
- [ ] Step 5 提交 `feat(task): embed delegation heuristics in the task tool description`。

### Task 2: explore 广度档位

**Files:** Modify `minicode/subagent_types.py:20-31`；Test 同上一文件追加。

- [ ] Step 1 追加失败测试：

```python
def test_explore_system_prompt_defines_breadth_levels() -> None:
    text = AGENT_TYPES["explore"]["system_prompt"]
    assert "very thorough" in text
    assert "alternate naming" in text
    assert "medium" in text
```

- [ ] Step 2 确认 FAIL。
- [ ] Step 3 在 `"You can only use read-only tools. "` 后插入：`"Adapt your search breadth to the thoroughness the caller states in the task prompt: default 'medium' covers the primary locations and naming conventions; 'very thorough' additionally sweeps alternate naming variants, adjacent directories, and near-duplicate call sites before concluding. "`（与 ZCode 一致：纯 prompt 约定，无 schema 参数、无结构保证）。
- [ ] Step 4 该文件 + `tests/test_subagent_soft_landing.py` 全 PASS。
- [ ] Step 5 提交 `feat(subagent): explore agent adapts search breadth to caller-specified thoroughness`。

### Task 3: SubagentSessionStore + 运行时接线

**Files:** Create `minicode/subagent_sessions.py`；Modify `minicode/tooling.py:669`（ToolContext）、`minicode/agent_loop.py:1972`（创建）与 `:884` 附近（传入）；Test `tests/test_subagent_continuation.py`（新增）。

- [ ] Step 1 失败测试：

```python
"""Continuation sessions for read-only sub-agents (task tool)."""

import pytest

from minicode.subagent_sessions import (
    MAX_RETAINED_SESSIONS,
    SubagentSession,
    SubagentSessionStore,
)


def _session(n: int, agent_type: str = "explore") -> SubagentSession:
    return SubagentSession(
        subagent_id=f"sub_{n:032x}",
        agent_type=agent_type,
        messages=[
            {"role": "user", "content": f"q{n}"},
            {"role": "assistant", "content": f"a{n}"},
        ],
        partial=False,
    )


def test_store_roundtrip() -> None:
    store = SubagentSessionStore()
    session = _session(1)
    store.save(session)
    assert store.get(session.subagent_id) is session
    assert store.retained_ids() == (session.subagent_id,)


def test_store_evicts_oldest_beyond_cap() -> None:
    store = SubagentSessionStore()
    first = _session(1)
    for n in range(1, MAX_RETAINED_SESSIONS + 2):
        store.save(_session(n))
    assert store.get(first.subagent_id) is None
    assert len(store.retained_ids()) == MAX_RETAINED_SESSIONS


def test_store_rejects_non_positive_cap() -> None:
    with pytest.raises(ValueError):
        SubagentSessionStore(max_sessions=0)
```

- [ ] Step 2 确认 FAIL（模块不存在）。
- [ ] Step 3 实现 `minicode/subagent_sessions.py`：

```python
"""Turn-scoped continuation sessions for read-only sub-agents.

A continuation lets the parent model ask a follow-up question of an
'explore'/'plan' sub-agent that already finished, reusing the child's
stored conversation instead of paying for a fresh exploration. Sessions
are per user turn (they die with the turn, exactly like the sub-agent
mailbox), bounded in count, and never leave the process.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass

# Eight retained sessions covers one turn's parallel fan-out plus
# follow-ups; older sessions evict FIFO so repeated dispatch cannot grow
# the store without bound.
MAX_RETAINED_SESSIONS = 8


@dataclass(frozen=True)
class SubagentSession:
    subagent_id: str
    agent_type: str
    messages: list[dict]  # full child transcript, in order; treat as read-only
    partial: bool


class SubagentSessionStore:
    """Thread-safe, bounded map of subagent_id -> finished conversation."""

    def __init__(self, max_sessions: int = MAX_RETAINED_SESSIONS) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, SubagentSession] = OrderedDict()
        self._max_sessions = max_sessions

    def save(self, session: SubagentSession) -> None:
        with self._lock:
            self._sessions[session.subagent_id] = session
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)

    def get(self, subagent_id: str) -> SubagentSession | None:
        with self._lock:
            return self._sessions.get(subagent_id)

    def retained_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._sessions.keys())


__all__ = (
    "MAX_RETAINED_SESSIONS",
    "SubagentSession",
    "SubagentSessionStore",
)
```

- [ ] Step 4 接线：`ToolContext` 加字段 `_subagent_sessions: object | None = None`（随 `_subagent_lifecycle` 同组风格）；`agent_loop.py:1972` `SubagentMailbox()` 后加 `subagent_sessions = SubagentSessionStore()`（import 置顶）；`:884` 附近 `ToolContext(...)` 加 `_subagent_sessions=subagent_sessions,`。
- [ ] Step 5 新测试 + `tests/test_agent_loop.py` PASS。
- [ ] Step 6 提交 `feat(subagent): turn-scoped bounded store for sub-agent continuation sessions`。

### Task 4: continue_subagent_id（schema + 校验 + 派发 + 结果提示）

**Files:** Modify `minicode/tools/task.py`：`_validate`(:445)、`_run` 早期解析(约:1985 后)、`sub_messages` 构造(:2404-2413)、成功路径存 session(:2609 后)、header(:2754-2765)、schema properties(:3059 起)、description 末句；Test 同 Task 3 文件追加。

- [ ] Step 1 追加失败测试。**先读 `tests/test_subagent_soft_landing.py:239-262`，逐字复用其 dispatch harness**（stub context + `monkeypatch.setattr(task_module, "run_agent_turn", fake)`，其 fake 返回值已解决 typed-outcome 门——本任务的 fake 沿用同一构造）。测试：

```python
def test_validate_rejects_continue_with_spawn_action() -> None:
    from minicode.tools.task import _validate

    with pytest.raises(ValueError, match="action='run'"):
        _validate({
            "action": "spawn",
            "description": "auth follow-up",
            "prompt": "which function issues the token?",
            "agent_type": "explore",
            "continue_subagent_id": "sub_" + "1" * 32,
        })


def test_validate_rejects_continue_with_workflow_type() -> None:
    from minicode.tools.task import _validate

    with pytest.raises(ValueError, match="explore/'plan'"):
        _validate({
            "description": "change things",
            "prompt": "edit the file",
            "agent_type": "workflow",
            "write_paths": ["a.py"],
            "verification_commands": [["python3", "-m", "compileall", "a.py"]],
            "continue_subagent_id": "sub_" + "1" * 32,
        })


def test_run_continuation_error_lists_retained_sessions(tmp_path):
    # harness 同 soft-landing；store 为空
    result = task_module._run(
        {
            "description": "auth follow-up",
            "prompt": "which function issues the token?",
            "agent_type": "explore",
            "continue_subagent_id": "sub_" + "9" * 32,
        },
        _context(SubagentSessionStore(), cwd=str(tmp_path)),
    )
    assert not result.ok
    assert "unknown_continue_subagent_id" in result.output
    assert "none" in result.output


def test_run_seeds_history_from_stored_session(monkeypatch, tmp_path):
    store = SubagentSessionStore()
    store.save(SubagentSession(
        subagent_id="sub_" + "1" * 32,
        agent_type="explore",
        messages=[
            {"role": "system", "content": "old-system"},
            {"role": "user", "content": "find auth code"},
            {"role": "assistant", "content": "auth.py handles it"},
        ],
        partial=False,
    ))
    captured = {}

    def fake_run_agent_turn(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _successful_transcript(kwargs)  # soft-landing harness 的成功形状

    monkeypatch.setattr(task_module, "run_agent_turn", fake_run_agent_turn)
    result = task_module._run(
        {
            "description": "auth follow-up",
            "prompt": "which function issues the token?",
            "agent_type": "explore",
            "continue_subagent_id": "sub_" + "1" * 32,
        },
        _context(store, cwd=str(tmp_path)),
    )
    assert result.ok
    msgs = captured["messages"]
    assert msgs[0]["role"] == "system" and msgs[0]["content"] != "old-system"
    assert {"role": "user", "content": "find auth code"} in msgs
    assert {"role": "assistant", "content": "auth.py handles it"} in msgs
    assert msgs[-1]["role"] == "user"
    assert "continuation of sub-agent" in msgs[-1]["content"]
```

另加两个同型测试：`test_successful_run_saves_new_session`（成功后 store 出现新 subagent_id，从 header 行 `Continue: pass continue_subagent_id='...'` 提取）；`test_type_mismatch_fails_closed`（store 存 explore、声明 plan → `not ok` 且含 `subagent_type_mismatch`）。

- [ ] Step 2 确认 FAIL。
- [ ] Step 3 实现（全部 task.py）：
  (a) `_validate` 在 action/agent_type 校验后加：

```python
    continue_id = input_data.get("continue_subagent_id")
    if continue_id is not None:
        if (
            not isinstance(continue_id, str)
            or not re.fullmatch(r"sub_[0-9a-f]{32}", continue_id)
        ):
            raise ValueError(
                "continue_subagent_id must be a sub_ id returned by a "
                "completed sub-agent this turn"
            )
        if input_data.get("action", "run") != "run":
            raise ValueError(
                "continue_subagent_id requires action='run'; asynchronous "
                "spawn does not support continuation"
            )
        if input_data.get("agent_type") not in ("explore", "plan"):
            raise ValueError(
                "continue_subagent_id is only valid for read-only "
                "'explore'/'plan' sub-agents"
            )
```

  (b) `_run` 在 `agent_def["max_turns"] = resolve_max_turns(agent_type)` 之后（昂贵装配之前）加早期解析：

```python
    continue_id = input_data.get("continue_subagent_id")
    session_store = getattr(context, "_subagent_sessions", None)
    prior_messages: list[dict] | None = None
    if continue_id is not None:
        if session_store is None:
            return ToolResult(
                ok=False,
                output=(
                    "error[subagent_continuation_unavailable]: "
                    "continuation sessions are unavailable in this "
                    "runtime; run a fresh explore/plan task instead"
                ),
            )
        prior = session_store.get(continue_id)
        if prior is None:
            retained = ", ".join(session_store.retained_ids()) or "none"
            return ToolResult(
                ok=False,
                output=(
                    f"error[unknown_continue_subagent_id]: no stored "
                    f"session for {continue_id}. Retained sessions this "
                    f"turn: {retained}"
                ),
            )
        if prior.agent_type != agent_type:
            return ToolResult(
                ok=False,
                output=(
                    f"error[subagent_type_mismatch]: session "
                    f"{continue_id} ran as '{prior.agent_type}' but this "
                    f"call declares '{agent_type}'"
                ),
            )
        prior_messages = prior.messages
```

  (c) `sub_messages` 构造（:2404-2413）替换为：

```python
        fresh_user_prompt = task_prompt
        if prior_messages is not None:
            fresh_user_prompt = (
                f"[continuation of sub-agent {continue_id}; its earlier "
                f"conversation is preserved above — answer the follow-up "
                f"without re-exploring what it already found]\n\n{task_prompt}"
            )
        sub_messages = [
            {
                "role": "system",
                "content": sub_system_prompt + result_instruction,
            }
        ]
        if prior_messages is not None:
            # Skip the stored system message: this run has a new
            # subagent_id and therefore a new result-key instruction.
            for msg in prior_messages[1:]:
                sub_messages.append(dict(msg))
        sub_messages.append({"role": "user", "content": fresh_user_prompt})
```

  (d) 成功路径（`structured_result = project_subagent_result(...)` 后）：

```python
            if session_store is not None and agent_type in ("explore", "plan"):
                session_store.save(
                    SubagentSession(
                        subagent_id=subagent_id,
                        agent_type=agent_type,
                        messages=list(result_messages),
                        partial=final_partial,
                    )
                )
```

  顶部 import `from minicode.subagent_sessions import SubagentSession`（该模块无 minicode 依赖，无循环导入）。
  (e) header（partial 行后）：

```python
    if agent_type in ("explore", "plan"):
        header += (
            f"  Continue: pass continue_subagent_id='{subagent_id}' in a "
            f"new task call to ask follow-ups without re-exploring.\n"
        )
```

  (f) schema properties（`prompt` 之后）：

```python
            "continue_subagent_id": {
                "type": "string",
                "pattern": "^sub_[0-9a-f]{32}$",
                "description": (
                    "Continue a completed read-only ('explore'/'plan') "
                    "sub-agent from this same turn: pass the subagent_id "
                    "from its result and put only the follow-up question "
                    "in prompt. The stored conversation resumes — no "
                    "fresh exploration. Requires action='run'."
                ),
            },
```

  (g) description 末句（`"The sub-agent's final result is returned to you."` 后）：`" Completed read-only ('explore'/'plan') runs also expose their subagent_id: pass it back via continue_subagent_id to ask follow-ups without re-exploring."`

- [ ] Step 4 新测试 + 回归全绿：`python -m pytest tests/test_subagent_continuation.py tests/test_task_tool_guidance.py tests/test_subagent_soft_landing.py tests/test_subagent_structured_protocol.py tests/test_subagent_result.py -q`。
- [ ] Step 5 提交 `feat(task): continue_subagent_id resumes finished explore/plan sub-agents`。

### Task 5: 文档 + 全量回归 + 质量门

**Files:** Create `docs/subagent-continuation.md`。

- [ ] Step 1 写文档：动机（ZCode SendMessage/resume 对标）、契约（fail-closed 清单）、边界（turn 级 / 8 会话 FIFO / 仅同步 explore/plan）、预算语义（按新 run 计费）。
- [ ] Step 2 回归：`python -m pytest tests/ -q`（过慢则至少覆盖 test_agent_loop、test_subagent_*、test_task_*、test_central_multi_agent_scheduler）。
- [ ] Step 3 质量门：`python -m scripts.evaluate_agent_quality`（参数不符则读其 argparse 后重跑），对照 `artifacts/agent-quality-baseline.json` 无劣化。
- [ ] Step 4 提交 `docs: sub-agent continuation contract and bounds`。

## 验收标准

1. 两个新测试文件全绿；相关套件无回归；质量门不低于基线。
2. 手工验收：同一 turn 内先 `task(explore, "…very thorough…")`，再 `task(explore, continue_subagent_id=<id>, "追问")`——第二次的子 Agent 不重做第一轮搜索（用 journal 的 tool_calls 数显著低于首轮验证）。

## Self-Review

- 覆盖：两项获批优化均有任务承载；三项不做的已注明原因。✓
- 占位符：唯二"沿用 soft-losing harness 构造"处是引用仓库既有成熟 harness，执行时先读该测试。✓
- 类型一致：store API 在 Task 3 定义与 Task 4 消费一致；schema pattern 与 `_validate` 正则一致。✓

批准后第一步：把本计划全文存为 `docs/superpowers/plans/2026-09-24-zcode-subagent-learnings.md`，再按任务执行。