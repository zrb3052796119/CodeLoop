"""Per-loop todo state: read-only sub-agents self-organize without clobbering.

todo_write used one process-global list with replace semantics, so sharing
it with (parallel) sub-agents would have destroyed the parent's list and
let siblings overwrite each other. These tests pin the loop-scoped state:
each agent loop owns its own task list, read-only agents get the tool, and
direct/embedder calls without a context keep the legacy global fallback.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

import minicode.tools.todo_write as todo_module
from minicode.tools.todo_write import todo_write_tool
from minicode.tooling import ToolContext


@pytest.fixture(autouse=True)
def isolated_legacy_state(monkeypatch):
    monkeypatch.setattr(todo_module, "_tasks", [])
    monkeypatch.setattr(todo_module, "_globals_holder", {"counter": 0})


def _todos(*items):
    return [{"content": item, "status": "pending"} for item in items]


def test_context_todo_state_isolates_from_global():
    sentinel = todo_write_tool.run({"todos": _todos("parent task")}, ToolContext())
    assert sentinel.ok
    before = deepcopy(todo_module._tasks)
    before_counter = todo_module._globals_holder["counter"]
    state = {"tasks": [], "counter": 0}
    context = ToolContext(_todo_state=state)
    result = todo_write_tool.run({"todos": _todos("child task")}, context)
    assert result.ok
    assert [task["content"] for task in state["tasks"]] == ["child task"]
    assert todo_module._tasks == before
    assert todo_module._globals_holder["counter"] == before_counter


def test_two_contexts_do_not_share_state():
    first = {"tasks": [], "counter": 0}
    second = {"tasks": [], "counter": 0}
    todo_write_tool.run({"todos": _todos("one")}, ToolContext(_todo_state=first))
    todo_write_tool.run({"todos": _todos("two")}, ToolContext(_todo_state=second))
    assert [t["content"] for t in first["tasks"]] == ["one"]
    assert [t["content"] for t in second["tasks"]] == ["two"]


def test_context_state_replaces_within_itself():
    state = {"tasks": [], "counter": 0}
    context = ToolContext(_todo_state=state)
    todo_write_tool.run({"todos": _todos("a", "b")}, context)
    todo_write_tool.run({"todos": _todos("b")}, context)
    assert [t["content"] for t in state["tasks"]] == ["b"]


def test_no_context_falls_back_to_global():
    result = todo_write_tool.run({"todos": _todos("legacy")}, ToolContext())
    assert result.ok
    assert [task["content"] for task in todo_module._tasks] == ["legacy"]


def test_explore_and_plan_allowlists_include_todo_write():
    from minicode.subagent_types import AGENT_TYPES, READ_ONLY_TOOL_WHITELIST

    for agent_type in ("explore", "plan"):
        assert "todo_write" in AGENT_TYPES[agent_type]["allowed_tools"]
    assert "todo_write" in READ_ONLY_TOOL_WHITELIST


def test_sub_agent_registry_exposes_todo_write(tmp_path):
    from minicode.tools import create_default_tool_registry
    from minicode.subagent_types import AGENT_TYPES

    allowed = AGENT_TYPES["explore"]["allowed_tools"]
    registry = create_default_tool_registry(str(tmp_path), include_mcp=False)
    child = registry.filtered(lambda tool: tool.name in allowed)
    assert child.find("todo_write") is not None
