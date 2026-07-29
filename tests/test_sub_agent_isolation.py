"""Regression tests for the `task` sub-agent tool's containment guarantees.

These lock in three properties that were previously missing entirely:

1. Bounded recursion — `general` sub-agents are granted the full tool
   registry, which contains the `task` tool itself. Without a depth limit a
   sub-agent could spawn sub-agents indefinitely, and every level rebuilds a
   complete tool registry (MCP server processes included).
2. Cancellation propagation — cancelling the parent Turn must stop nested
   sub-agent work instead of leaving it calling the model in the background.
3. Context governance — a sub-agent without a ContextManager has no
   compaction at all, so a long exploration grows the prompt until the
   provider rejects it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import minicode.tools.task as task_module
from minicode.tooling import ToolContext
from minicode.tools.task import MAX_AGENT_DEPTH, task_tool
from minicode.turn_cancellation import (
    TurnCancellationRequested,
    TurnCancellationToken,
)


TURN_ID = "turn_" + "a" * 32


def _context(tmp_path: Path, **overrides) -> ToolContext:
    values = {
        "cwd": str(tmp_path),
        "_runtime": {"model": "fake"},
    }
    values.update(overrides)
    return ToolContext(**values)


def _capture_sub_turn(monkeypatch) -> dict:
    captured: dict = {}

    def fake_run_agent_turn(**kwargs):
        captured.update(kwargs)
        return [{"role": "assistant", "content": "sub-agent done"}]

    monkeypatch.setattr(task_module, "run_agent_turn", fake_run_agent_turn)
    return captured


def test_sub_agent_cannot_spawn_another_sub_agent(tmp_path: Path) -> None:
    result = task_tool.run(
        {"description": "nested", "prompt": "nested work", "agent_type": "general"},
        _context(tmp_path, _agent_depth=MAX_AGENT_DEPTH),
    )

    assert result.ok is False
    assert "sub_agent_depth_exceeded" in result.output


def test_general_sub_agent_never_receives_the_task_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`general` gets the full registry; the recursion entry point must be
    withheld rather than advertised and then refused on every call."""
    captured = _capture_sub_turn(monkeypatch)

    result = task_tool.run(
        {"description": "broad", "prompt": "broad work", "agent_type": "general"},
        _context(tmp_path, _agent_depth=0),
    )

    assert result.ok is True
    tool_names = [tool.name for tool in captured["tools"].list()]
    assert "task" not in tool_names
    assert "read_file" in tool_names


def test_sub_agent_runs_one_level_deeper_than_its_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_sub_turn(monkeypatch)

    task_tool.run(
        {"description": "explore", "prompt": "explore", "agent_type": "explore"},
        _context(tmp_path, _agent_depth=0),
    )

    assert captured["agent_depth"] == 1


def test_parent_cancellation_token_reaches_the_sub_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_sub_turn(monkeypatch)
    token = TurnCancellationToken(TURN_ID)

    task_tool.run(
        {"description": "explore", "prompt": "explore", "agent_type": "explore"},
        _context(tmp_path, _agent_depth=0, _cancellation_token=token),
    )

    assert captured["cancellation_token"] is token


def test_cancellation_propagates_instead_of_becoming_a_tool_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled parent Turn is a control-flow decision, not a sub-agent
    error: it must unwind rather than be reported as `ok=False`."""

    def cancelled_run(**_kwargs):
        raise TurnCancellationRequested(TURN_ID)

    monkeypatch.setattr(task_module, "run_agent_turn", cancelled_run)

    with pytest.raises(TurnCancellationRequested):
        task_tool.run(
            {"description": "explore", "prompt": "explore", "agent_type": "explore"},
            _context(tmp_path, _agent_depth=0),
        )


def test_sub_agent_receives_its_own_context_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_sub_turn(monkeypatch)

    task_tool.run(
        {"description": "explore", "prompt": "explore", "agent_type": "explore"},
        _context(tmp_path, _agent_depth=0),
    )

    context_manager = captured["context_manager"]
    assert context_manager is not None
    assert context_manager.context_window > 0
    # Seeded with the sub-agent's own prompt, not the parent conversation.
    assert context_manager.messages == captured["messages"]


def test_prompt_is_required_rather_than_defaulting_to_a_short_description() -> None:
    """`description` is specified as 3-5 words. Silently using it as the whole
    task brief produced near-useless sub-agent runs."""
    with pytest.raises(ValueError, match="prompt is required"):
        task_tool.validator({"description": "look at auth", "agent_type": "explore"})

    validated = task_tool.validator(
        {
            "description": "look at auth",
            "prompt": "Trace how login tokens are validated in auth/.",
            "agent_type": "explore",
        }
    )
    assert validated["prompt"] == "Trace how login tokens are validated in auth/."


def test_sub_agent_system_prompt_is_skill_aware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sub-agent prompt must carry the shared Skill-aware base, not only
    the hardcoded role blurb — otherwise it never learns Skills exist."""
    captured = _capture_sub_turn(monkeypatch)
    skill_dir = tmp_path / ".mini-code" / "skills" / "codebase-explanation"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: codebase-explanation\n"
        "description: Explain codebase architecture, agent loop flow, and tool calling.\n"
        "domains: [code, file, analysis]\n"
        "scopes: [readonly]\n"
        "tools: [read_file, grep_files]\n"
        "keywords: [agent_loop, architecture, tool calling, explain]\n"
        "---\n\n# Codebase Explanation\n",
        encoding="utf-8",
    )

    task_tool.run(
        {
            "description": "explore",
            "prompt": "explain how the agent loop handles tool calling",
            "agent_type": "explore",
        },
        _context(tmp_path, _agent_depth=0),
    )

    system_prompt = captured["messages"][0]["content"]
    assert "load_skill" in system_prompt
    assert "SKILL USAGE GUIDE" in system_prompt
    assert "codebase-explanation" in system_prompt
    # ...without losing the agent-type role or the hand-back protocol.
    assert "exploration agent" in system_prompt
    assert "<final>" in system_prompt


def test_sub_agent_receives_the_project_memory_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_sub_turn(monkeypatch)

    task_tool.run(
        {"description": "explore", "prompt": "look at the auth module", "agent_type": "explore"},
        _context(tmp_path, _agent_depth=0),
    )

    assert captured["memory_manager"] is not None


def test_sub_agent_reports_model_turns_not_user_message_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tool results also arrive with role="user", so the old count was
    neither turns nor tool calls."""

    def fake_run(**_kwargs):
        return [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "task"},
            {"role": "assistant_tool_call", "content": ""},
            {"role": "user", "content": "tool result"},
            {"role": "assistant", "content": "final answer"},
        ]

    monkeypatch.setattr(task_module, "run_agent_turn", fake_run)

    result = task_tool.run(
        {"description": "explore", "prompt": "look around", "agent_type": "explore"},
        _context(tmp_path, _agent_depth=0),
    )

    assert "Model turns: 2 (tool calls: 1)" in result.output


def test_sub_agent_records_one_bounded_summary_event_in_the_parent_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forwarding the nested loop's own events would break the parent Run's
    single-`task.outcome` invariant, so exactly one content-free summary is
    recorded instead."""
    _capture_sub_turn(monkeypatch)
    events: list[tuple[str, object]] = []

    class RecordingSink:
        def emit(self, event_type, *, step=None, payload=None):
            events.append((event_type, payload))

    task_tool.run(
        {"description": "explore", "prompt": "look at the auth module", "agent_type": "explore"},
        _context(tmp_path, _agent_depth=0, _event_sink=RecordingSink()),
    )

    assert [event_type for event_type, _ in events] == ["subagent.completed"]
    payload = events[0][1]
    assert payload["subagentVersion"] == 1
    assert payload["agentType"] == "explore"
    assert payload["outcome"] == "completed"
    # Content-free: no prompt, findings, paths, or tool arguments.
    serialized = str(payload)
    assert "auth" not in serialized
    assert set(payload) == {
        "subagentVersion",
        "agentType",
        "outcome",
        "modelTurns",
        "toolCalls",
        "durationMs",
        "maxTurns",
        "resultTruncated",
    }


def test_depth_rejection_is_also_recorded_in_the_parent_run(
    tmp_path: Path,
) -> None:
    events: list[tuple[str, object]] = []

    class RecordingSink:
        def emit(self, event_type, *, step=None, payload=None):
            events.append((event_type, payload))

    task_tool.run(
        {"description": "nested", "prompt": "nested work", "agent_type": "general"},
        _context(
            tmp_path,
            _agent_depth=MAX_AGENT_DEPTH,
            _event_sink=RecordingSink(),
        ),
    )

    assert len(events) == 1
    assert events[0][1]["outcome"] == "depth_rejected"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"outcome": "whatever"}, "unknown outcome"),
        ({"agentType": "rogue"}, "unknown agent type"),
        ({"findings": "secret content"}, "extra field"),
        ({"modelTurns": -1}, "negative count"),
        ({"resultTruncated": "yes"}, "wrong type"),
    ],
)
def test_run_journal_rejects_non_canonical_subagent_payloads(
    tmp_path: Path, mutation: dict, reason: str
) -> None:
    """The parent Run's stream must stay bounded and content-free, so an
    unexpected field is rejected outright rather than quietly dropped."""
    from minicode.run_journal import RunJournal, RunJournalValidationError

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    record = journal.create_run(title="parent", source="headless")
    journal.transition(record.id, "running")
    payload = {
        "subagentVersion": 1,
        "agentType": "explore",
        "outcome": "completed",
        "modelTurns": 3,
        "toolCalls": 2,
        "durationMs": 1500,
        "maxTurns": 12,
        "resultTruncated": False,
    }

    assert journal.append_event(
        record.id, "subagent.completed", step=1, payload=payload
    ).payload == payload

    with pytest.raises(RunJournalValidationError):
        journal.append_event(
            record.id, "subagent.completed", step=2, payload={**payload, **mutation}
        )


def test_sub_agent_tool_registry_still_exposes_discovered_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rebuilding a filtered registry must not silently drop the Skill
    catalog, which the sub-agent's prompt layer depends on."""
    captured = _capture_sub_turn(monkeypatch)
    skill_dir = tmp_path / ".mini-code" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\n---\n\n# Demo\n",
        encoding="utf-8",
    )

    task_tool.run(
        {"description": "explore", "prompt": "explore", "agent_type": "explore"},
        _context(tmp_path, _agent_depth=0),
    )

    skill_names = [
        str(skill.get("name", "")) for skill in captured["tools"].get_skills()
    ]
    assert "demo" in skill_names
