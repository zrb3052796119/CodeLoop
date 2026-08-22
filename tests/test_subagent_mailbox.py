"""Sub-agent note mailbox: bounded collaboration without raw context sharing."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from minicode.subagent_mailbox import (
    SubagentMailbox,
    SubagentMailboxError,
)
from minicode.tooling import ToolContext, ToolResult
from minicode.tools.subagent_notes import (
    subagent_note_list_tool,
    subagent_note_read_tool,
    subagent_note_write_tool,
)


def _approve_workflow_review(mailbox: SubagentMailbox, prompt: str) -> None:
    match = re.search(r"review verdict key `([^`]+)`", prompt)
    assert match is not None
    mailbox.write(
        match.group(1),
        json.dumps(
            {
                "reviewVersion": 1,
                "verdict": "approved",
                "blockingFindings": [],
                "warnings": [],
            }
        ),
        author="reviewer",
    )


def test_mailbox_write_read_and_versions(tmp_path: Path) -> None:
    mailbox = SubagentMailbox()
    context = ToolContext(
        cwd=str(tmp_path),
        _subagent_mailbox=mailbox,
        _agent_depth=1,
    )

    write = subagent_note_write_tool.run(
        {"key": "workflow_plan", "content": "1. inspect\n2. implement"},
        context,
    )
    assert write.ok is True
    read = subagent_note_read_tool.run({"key": "workflow_plan"}, context)
    assert read.ok is True
    assert "1. inspect" in read.output
    assert "workflow_plan" in read.output

    write2 = subagent_note_write_tool.run(
        {"key": "workflow_plan", "content": "revised plan"},
        context,
    )
    assert write2.ok is True
    assert "v2" in subagent_note_read_tool.run(
        {"key": "workflow_plan"}, context
    ).output

    assert "workflow_plan" in subagent_note_list_tool.run({}, context).output


def test_mailbox_rejects_bad_keys_and_missing_notes(tmp_path: Path) -> None:
    mailbox = SubagentMailbox()
    context = ToolContext(cwd=str(tmp_path), _subagent_mailbox=mailbox)

    assert subagent_note_write_tool.run(
        {"key": "../../escape", "content": "bad"}, context
    ).ok is False
    assert subagent_note_read_tool.run({"key": "missing"}, context).ok is False
    with pytest.raises(SubagentMailboxError):
        mailbox.write("bad key", "value", author="test")


def test_mailbox_unavailable_is_closed_error(tmp_path: Path) -> None:
    context = ToolContext(cwd=str(tmp_path))

    result = subagent_note_write_tool.run(
        {"key": "workflow_plan", "content": "no mailbox"},
        context,
    )
    assert result.ok is False
    assert "subagent_mailbox_unavailable" in result.output


def test_workflow_writes_plan_and_result_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import minicode.tools.task as task_module
    from minicode.subagent_mailbox import SubagentMailbox

    def fake_run(input_data: dict, context) -> ToolResult:
        if input_data["description"].startswith("review:"):
            _approve_workflow_review(mailbox, input_data["prompt"])
        return ToolResult(
            ok=True,
            output=f"{input_data['agent_type']} phase complete",
        )

    monkeypatch.setattr(task_module, "_run", fake_run)
    mailbox = SubagentMailbox()
    result = task_module.task_tool.run(
        {
            "description": "auth refactor",
            "prompt": "Refactor the auth module",
            "agent_type": "workflow",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _agent_depth=0,
            _subagent_mailbox=mailbox,
        ),
    )

    assert result.ok is True
    assert mailbox.read("workflow_plan") is not None
    assert mailbox.read("workflow_result") is not None
    assert mailbox.read("workflow_plan").content == "plan phase complete"
    assert mailbox.read("workflow_result").content == "general phase complete"


def test_workflow_parallel_explore_writes_mailbox_and_feeds_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import minicode.tools.task as task_module
    from minicode.agent_budget import AgentTurnBudget
    from minicode.subagent_mailbox import SubagentMailbox

    captured: list[tuple[str, str, int, object]] = []

    def fake_run(input_data: dict, context) -> ToolResult:
        captured.append(
            (
                input_data["agent_type"],
                input_data["prompt"],
                context._agent_depth,
                context._agent_budget,
            )
        )
        if input_data["description"].startswith("review:"):
            _approve_workflow_review(mailbox, input_data["prompt"])
        return ToolResult(
            ok=True,
            output=f"{input_data['agent_type']} phase complete",
        )

    monkeypatch.setattr(task_module, "_run", fake_run)
    mailbox = SubagentMailbox()
    budget = AgentTurnBudget(max_model_calls=18)
    result = task_module.task_tool.run(
        {
            "description": "auth refactor",
            "prompt": "Refactor the auth module",
            "agent_type": "workflow",
            "parallel_explore": True,
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _agent_depth=0,
            _subagent_mailbox=mailbox,
            _agent_budget=budget,
        ),
    )

    assert result.ok is True
    types = [item[0] for item in captured]
    assert types.count("explore") == 3
    assert types.count("plan") == 2
    assert types.count("general") == 1
    assert all(item[2] == 1 for item in captured)
    assert all(item[3] is budget for item in captured)
    for key in (
        "workflow_explore_architecture",
        "workflow_explore_tests",
        "workflow_explore_risks",
    ):
        assert mailbox.read(key) is not None
    plan_prompt = next(
        item[1] for item in captured if item[0] == "plan"
    )
    assert "workflow_explore_architecture" in plan_prompt
    assert "=== RESEARCH ===" in result.output
