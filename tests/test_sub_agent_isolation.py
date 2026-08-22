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

import json
import re
from pathlib import Path

import pytest

import minicode.tools.task as task_module
from minicode.tooling import ToolContext, ToolResult
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


def _approve_workflow_review(mailbox, prompt: str) -> None:
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


def _capture_sub_turn(monkeypatch) -> dict:
    captured: dict = {}

    def fake_run_agent_turn(**kwargs):
        captured.update(kwargs)
        from minicode.task_outcome import canonicalize_task_outcome

        kwargs["outcome_capture"].record(
            canonicalize_task_outcome("success", 0)
        )
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
    assert "ask_user" not in tool_names
    assert "read_file" in tool_names
    assert captured["allow_user_interaction"] is False
    assert "ask_user is unavailable" in captured["messages"][0]["content"]


def test_sub_agent_runs_one_level_deeper_than_its_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_sub_turn(monkeypatch)

    task_tool.run(
        {"description": "explore", "prompt": "explore", "agent_type": "explore"},
        _context(tmp_path, _agent_depth=0),
    )

    assert captured["agent_depth"] == 1


def test_sub_agent_uses_its_agent_type_qwen_route_instead_of_parent_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_sub_turn(monkeypatch)
    runtime = {
        "model": "parent-model",
        "subagentRoutingEnabled": True,
        "subagentProvider": "openai-compatible",
        "subagentBaseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "subagentApiKey": "child-key",
        "subagentModels": {
            "default": "qwen3.6-flash",
            "explore": "qwen3.6-flash",
        },
    }

    result = task_tool.run(
        {
            "description": "qwen explore",
            "prompt": "inspect the auth module",
            "agent_type": "explore",
        },
        _context(tmp_path, _runtime=runtime),
    )

    assert result.ok is True
    assert captured["model"].runtime["model"] == "qwen3.6-flash"
    assert captured["model"].runtime["openaiBaseUrl"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )


def test_explicit_sub_agent_route_without_key_fails_closed_before_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_sub_turn(monkeypatch)
    runtime = {
        "model": "parent-model",
        "subagentRoutingEnabled": True,
        "subagentProvider": "openai-compatible",
        "subagentBaseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "subagentApiKey": "",
        "subagentModels": {"default": "qwen3.6-flash"},
    }

    result = task_tool.run(
        {
            "description": "missing key",
            "prompt": "inspect the auth module",
            "agent_type": "explore",
        },
        _context(tmp_path, _runtime=runtime),
    )

    assert result.ok is False
    assert result.output == (
        "error[subagent_model_route_invalid]: subagent_api_key_missing"
    )
    assert captured == {}


def test_sub_agent_fallback_text_cannot_be_misreported_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from minicode.task_outcome import canonicalize_task_outcome

    def failed_turn(**kwargs):
        kwargs["outcome_capture"].record(
            canonicalize_task_outcome("failed", 0)
        )
        return [
            {
                "role": "assistant",
                "content": "Model API timeout: provider deadline",
            }
        ]

    monkeypatch.setattr(task_module, "run_agent_turn", failed_turn)

    result = task_tool.run(
        {"description": "probe", "prompt": "probe", "agent_type": "explore"},
        _context(tmp_path, _agent_depth=0),
    )

    assert result.ok is False
    assert "sub_agent_failed" in result.output
    assert "Model API timeout" in result.output


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
    tool_names = {tool.name for tool in captured["tools"].list()}
    assert "load_skill" in system_prompt
    assert "load_skill" in tool_names
    assert "subagent_note_write" in tool_names
    assert "SKILL USAGE GUIDE" in system_prompt
    assert "codebase-explanation" in system_prompt
    # ...without losing the agent-type role or the hand-back protocol.
    assert "exploration agent" in system_prompt
    assert "<final>" in system_prompt
    # Semantic recommendations remain visible in the prompt but do not veto
    # a correct sub-agent answer when the user did not name the Skill.
    assert captured["required_skill_names"] == []


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
    """A batched tool response is one model turn, not one turn per call."""

    def fake_run(**kwargs):
        from minicode.task_outcome import canonicalize_task_outcome

        kwargs["event_sink"].emit("model.started", step=1, payload={})
        kwargs["event_sink"].emit("model.started", step=2, payload={})
        kwargs["outcome_capture"].record(
            canonicalize_task_outcome("success", 0)
        )
        return [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "task"},
            {"role": "assistant_tool_call", "content": ""},
            {"role": "assistant_tool_call", "content": ""},
            {"role": "user", "content": "tool results"},
            {"role": "assistant", "content": "final answer"},
        ]

    monkeypatch.setattr(task_module, "run_agent_turn", fake_run)

    result = task_tool.run(
        {"description": "explore", "prompt": "look around", "agent_type": "explore"},
        _context(tmp_path, _agent_depth=0),
    )

    assert "Model turns: 2 (tool calls: 2)" in result.output


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
    assert payload["subagentVersion"] == 3
    assert re.fullmatch(r"sub_[0-9a-f]{32}", payload["subagentId"])
    assert payload["agentType"] == "explore"
    assert payload["outcome"] == "completed"
    # Content-free: no prompt, findings, paths, or tool arguments.
    serialized = str(payload)
    assert "auth" not in serialized
    assert set(payload) == {
        "subagentVersion",
        "subagentId",
        "agentType",
        "outcome",
        "modelTurns",
        "toolCalls",
        "durationMs",
        "modelTurnLimit",
        "phaseCount",
        "maxPhases",
        "resultTruncated",
        "resultContractStatus",
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


def test_sub_agent_tool_progress_reaches_the_ui_but_not_the_run_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live progress belongs on the presentation channel. Routing it through
    the parent's tool callbacks instead would write nested tool.started /
    tool.finished events into the parent Run and disturb the approval
    session's tool tracking."""
    ui: list[tuple] = []
    journal: list[str] = []

    class Presentation:
        def assistant_delta(self, text: str) -> None: ...

        def tool_started(self, tool_name: str) -> None:
            ui.append(("start", tool_name))

        def tool_finished(self, tool_name: str, *, is_error: bool) -> None:
            ui.append(("finish", tool_name, is_error))

    class Sink:
        def emit(self, event_type, *, step=None, payload=None) -> None:
            journal.append(event_type)

    def fake_run(**kwargs):
        from minicode.task_outcome import canonicalize_task_outcome

        kwargs["outcome_capture"].record(
            canonicalize_task_outcome("success", 0)
        )
        kwargs["on_tool_start"]("read_file", {})
        kwargs["on_tool_result"]("read_file", "out", False)
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(task_module, "run_agent_turn", fake_run)

    task_tool.run(
        {"description": "d", "prompt": "a full brief", "agent_type": "explore"},
        _context(
            tmp_path,
            _agent_depth=0,
            _presentation=Presentation(),
            _event_sink=Sink(),
        ),
    )

    # Prefixed so concurrent read-only sub-agents stay distinguishable.
    assert ui == [
        ("start", "explore▸read_file"),
        ("finish", "explore▸read_file", False),
    ]
    # The Run stream still carries only the single bounded summary.
    assert journal == ["subagent.completed"]


def test_sub_agent_without_a_presentation_channel_passes_no_callbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_sub_turn(monkeypatch)

    task_tool.run(
        {"description": "d", "prompt": "a full brief", "agent_type": "explore"},
        _context(tmp_path, _agent_depth=0),
    )

    assert captured["on_tool_start"] is None
    assert captured["on_tool_result"] is None


def _task_call(agent_type: str, index: int = 0) -> dict:
    return {
        "id": str(index),
        "toolName": "task",
        "input": {
            "agent_type": agent_type,
            "description": "d",
            "prompt": "a full self-contained task brief",
        },
    }


def test_read_only_sub_agents_are_scheduled_concurrently() -> None:
    """`explore`/`plan` carry no writers, run with prompt=None so they never
    raise a permission prompt from a worker thread, and cannot touch the
    working tree — so they may share the concurrent batch."""
    from minicode.agent_intelligence import ToolScheduler
    from minicode.tools import create_default_tool_registry

    tools = create_default_tool_registry(".", runtime=None)
    concurrent_calls, serial_calls = ToolScheduler().schedule_calls(
        [_task_call("explore", 0), _task_call("plan", 1)], tools
    )

    assert len(concurrent_calls) == 2
    assert serial_calls == []


def test_general_sub_agents_stay_serial() -> None:
    """`general` can write files and inherits the parent's permission prompt,
    so two in flight could interleave edits and prompt concurrently."""
    from minicode.agent_intelligence import ToolScheduler
    from minicode.tools import create_default_tool_registry

    tools = create_default_tool_registry(".", runtime=None)
    concurrent_calls, serial_calls = ToolScheduler().schedule_calls(
        [_task_call("general", 0), _task_call("general", 1)], tools
    )

    assert concurrent_calls == []
    assert len(serial_calls) == 2


def test_mixed_sub_agent_batch_splits_by_agent_type() -> None:
    from minicode.agent_intelligence import ToolScheduler
    from minicode.tools import create_default_tool_registry

    tools = create_default_tool_registry(".", runtime=None)
    concurrent_calls, serial_calls = ToolScheduler().schedule_calls(
        [_task_call("explore", 0), _task_call("general", 1), _task_call("plan", 2)],
        tools,
    )

    assert sorted(c["input"]["agent_type"] for c in concurrent_calls) == [
        "explore",
        "plan",
    ]
    assert [c["input"]["agent_type"] for c in serial_calls] == ["general"]


def test_undecidable_task_call_input_falls_back_to_serial() -> None:
    """An input the concurrency predicate cannot classify must not be
    optimistically parallelized."""
    from minicode.tools.task import task_tool as definition

    assert definition.call_is_concurrency_safe({"agent_type": "explore"}) is True
    assert definition.call_is_concurrency_safe({"agent_type": "general"}) is False
    assert definition.call_is_concurrency_safe({}) is False
    assert definition.call_is_concurrency_safe(None) is False


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


def test_sub_agent_written_lessons_can_bind_to_parent_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_sub_turn(monkeypatch)

    class ParentSink:
        def __init__(self) -> None:
            self.written: list[list[str]] = []

        def record_written_memory_ids(self, entry_ids: list[str]) -> None:
            self.written.append(list(entry_ids))

    sink = ParentSink()
    task_tool.run(
        {
            "description": "explore",
            "prompt": "explore",
            "agent_type": "explore",
        },
        _context(tmp_path, _agent_depth=0, _event_sink=sink),
    )

    recorder = captured.get("on_memory_written")
    assert callable(recorder)
    recorder("project-lesson-id")
    assert sink.written == [["project-lesson-id"]]

    recorder("project-second-lesson-id")
    assert sink.written[-1] == [
        "project-lesson-id",
        "project-second-lesson-id",
    ]


def test_sub_agent_receives_parent_tool_abandonment_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    captured = _capture_sub_turn(monkeypatch)
    abandoned = threading.Event()

    task_tool.run(
        {
            "description": "explore",
            "prompt": "look around",
            "agent_type": "explore",
        },
        _context(tmp_path, _agent_depth=0, _tool_abandoned=abandoned),
    )

    assert captured["abandoned_event"] is abandoned


def test_workflow_runs_plan_execute_review_phases_with_shared_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from minicode.agent_budget import AgentTurnBudget
    from minicode.subagent_mailbox import SubagentMailbox

    captured: list[tuple[str, str, int, object]] = []
    mailbox = SubagentMailbox()

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
    budget = AgentTurnBudget(max_model_calls=9)
    result = task_module.task_tool.run(
        {
            "description": "auth refactor",
            "prompt": "Refactor the auth module",
            "agent_type": "workflow",
        },
        _context(
            tmp_path,
            _agent_depth=0,
            _agent_budget=budget,
            _subagent_mailbox=mailbox,
        ),
    )

    assert result.ok is True
    assert [call[0] for call in captured] == ["plan", "general", "plan"]
    assert all(call[2] == 1 for call in captured)
    assert all(call[3] is budget for call in captured)
    assert "=== PLAN ===" in result.output
    assert "=== EXECUTE ===" in result.output
    assert "=== REVIEW VERDICT ===" in result.output
    assert "=== REVIEW NARRATIVE ===" in result.output
    assert result.verification == {
        "verificationVersion": 1,
        "kind": "review",
        "outcome": "passed",
        "source": "workflow_review",
    }
    assert "Refactor the auth module" in captured[1][1]


def test_workflow_observation_separates_model_limit_from_phase_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from minicode.agent_budget import AgentTurnBudget
    from minicode.subagent_mailbox import SubagentMailbox

    mailbox = SubagentMailbox()
    payloads: list[dict] = []

    class RecordingSink:
        def emit(self, event_type, *, step=None, payload=None):
            if event_type == "subagent.completed":
                payloads.append(payload)

    def fake_run(input_data: dict, _context) -> ToolResult:
        if input_data["description"].startswith("review:"):
            _approve_workflow_review(mailbox, input_data["prompt"])
        return ToolResult(
            ok=True,
            output=(
                "[Sub-agent complete]\n"
                "  Model turns: 4 (tool calls: 2)\n"
                "phase complete"
            ),
        )

    monkeypatch.setattr(task_module, "_run", fake_run)
    budget = AgentTurnBudget(max_model_calls=20)
    budget.reserve_model_call()
    result = task_module.task_tool.run(
        {
            "description": "auth refactor",
            "prompt": "Refactor the auth module",
            "agent_type": "workflow",
        },
        _context(
            tmp_path,
            _agent_depth=0,
            _agent_budget=budget,
            _subagent_mailbox=mailbox,
            _event_sink=RecordingSink(),
        ),
    )

    assert result.ok is True
    assert payloads == [
        {
            "subagentVersion": 3,
            "subagentId": payloads[0]["subagentId"],
            "agentType": "workflow",
            "outcome": "completed",
            "modelTurns": 12,
            "toolCalls": 6,
            "durationMs": payloads[0]["durationMs"],
            "modelTurnLimit": 19,
            "phaseCount": 3,
            "maxPhases": 4,
            "resultTruncated": False,
            "resultContractStatus": "derived",
        }
    ]
    assert payloads[0]["modelTurns"] <= payloads[0]["modelTurnLimit"]


def test_workflow_v2_observation_rejects_mixed_or_impossible_limits() -> None:
    from minicode.subagent_observation import normalize_subagent_payload

    payload = {
        "subagentVersion": 2,
        "agentType": "workflow",
        "outcome": "completed",
        "modelTurns": 12,
        "toolCalls": 6,
        "durationMs": 100,
        "modelTurnLimit": 19,
        "phaseCount": 3,
        "maxPhases": 4,
        "resultTruncated": False,
    }

    assert normalize_subagent_payload(payload) == payload
    assert normalize_subagent_payload({**payload, "maxTurns": 3}) is None
    assert normalize_subagent_payload({**payload, "modelTurnLimit": 11}) is None
    assert normalize_subagent_payload({**payload, "phaseCount": 5}) is None


def test_run_journal_accepts_workflow_v2_observation(tmp_path: Path) -> None:
    from minicode.run_journal import RunJournal

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )
    record = journal.create_run(title="parent", source="headless")
    journal.transition(record.id, "running")
    payload = {
        "subagentVersion": 2,
        "agentType": "workflow",
        "outcome": "completed",
        "modelTurns": 12,
        "toolCalls": 6,
        "durationMs": 100,
        "modelTurnLimit": 19,
        "phaseCount": 3,
        "maxPhases": 4,
        "resultTruncated": False,
    }

    event = journal.append_event(
        record.id,
        "subagent.completed",
        step=1,
        payload=payload,
    )

    assert event.payload == payload


def test_standalone_workflow_creates_one_budget_shared_by_all_phases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from minicode.subagent_mailbox import SubagentMailbox

    mailbox = SubagentMailbox()
    budgets: list[object] = []

    def fake_run(input_data: dict, context) -> ToolResult:
        budgets.append(context._agent_budget)
        if input_data["description"].startswith("review:"):
            _approve_workflow_review(mailbox, input_data["prompt"])
        return ToolResult(ok=True, output="phase complete")

    monkeypatch.setattr(task_module, "_run", fake_run)
    result = task_module.task_tool.run(
        {
            "description": "auth refactor",
            "prompt": "Refactor the auth module",
            "agent_type": "workflow",
        },
        _context(
            tmp_path,
            _agent_depth=0,
            _subagent_mailbox=mailbox,
        ),
    )

    assert result.ok is True
    assert budgets[0] is not None
    assert all(budget is budgets[0] for budget in budgets)
    assert budgets[0].snapshot().limit_model_calls == 80


def test_workflow_review_failure_is_a_typed_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fake_run(_input_data: dict, _context) -> ToolResult:
        nonlocal calls
        calls += 1
        if calls == 3:
            return ToolResult(ok=False, output="review unavailable")
        return ToolResult(ok=True, output="phase complete")

    monkeypatch.setattr(task_module, "_run", fake_run)

    result = task_module.task_tool.run(
        {
            "description": "review gate",
            "prompt": "Implement and review the change",
            "agent_type": "workflow",
        },
        _context(tmp_path, _agent_depth=0),
    )

    assert result.ok is False
    assert result.output.startswith("[Workflow review gate failed]")
    assert "Workflow review phase failed" in result.output


def test_workflow_blocking_typed_review_fails_and_stays_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import re

    from minicode.subagent_mailbox import SubagentMailbox

    calls = 0
    mailbox = SubagentMailbox()

    def fake_run(input_data: dict, _context) -> ToolResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ToolResult(ok=True, output="P" * 6000)
        if calls == 2:
            return ToolResult(ok=True, output="E" * 10000)
        match = re.search(r"review verdict key `([^`]+)`", input_data["prompt"])
        assert match is not None
        mailbox.write(
            match.group(1),
            json.dumps(
                {
                    "reviewVersion": 1,
                    "verdict": "changes_required",
                    "blockingFindings": ["BLOCKING_REVIEW_FINDING"],
                    "warnings": [],
                }
            ),
            author="reviewer",
        )
        return ToolResult(ok=True, output="review completed normally")

    monkeypatch.setattr(task_module, "_run", fake_run)
    result = task_module.task_tool.run(
        {
            "description": "review gate",
            "prompt": "Implement and review the change",
            "agent_type": "workflow",
        },
        _context(
            tmp_path,
            _agent_depth=0,
            _subagent_mailbox=mailbox,
        ),
    )

    assert result.ok is False
    assert "changes_required" in result.output
    assert "BLOCKING_REVIEW_FINDING" in result.output
    assert result.verification is None


def test_workflow_approved_review_is_preserved_when_phase_output_is_long(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import re

    from minicode.subagent_mailbox import SubagentMailbox

    calls = 0
    mailbox = SubagentMailbox()

    def fake_run(input_data: dict, _context) -> ToolResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ToolResult(ok=True, output="P" * 6000)
        if calls == 2:
            return ToolResult(ok=True, output="E" * 10000)
        match = re.search(r"review verdict key `([^`]+)`", input_data["prompt"])
        assert match is not None
        mailbox.write(
            match.group(1),
            json.dumps(
                {
                    "reviewVersion": 1,
                    "verdict": "approved",
                    "blockingFindings": [],
                    "warnings": ["APPROVED_REVIEW_VISIBLE"],
                }
            ),
            author="reviewer",
        )
        return ToolResult(ok=True, output="REVIEW_NARRATIVE_VISIBLE")

    monkeypatch.setattr(task_module, "_run", fake_run)
    result = task_module.task_tool.run(
        {
            "description": "review gate",
            "prompt": "Implement and review the change",
            "agent_type": "workflow",
        },
        _context(
            tmp_path,
            _agent_depth=0,
            _subagent_mailbox=mailbox,
        ),
    )

    assert result.ok is True
    assert "approved" in result.output
    assert "APPROVED_REVIEW_VISIBLE" in result.output
    assert "REVIEW_NARRATIVE_VISIBLE" in result.output
    assert len(result.output) <= 8000


def test_workflow_missing_typed_review_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from minicode.subagent_mailbox import SubagentMailbox

    monkeypatch.setattr(
        task_module,
        "_run",
        lambda _input, _context: ToolResult(ok=True, output="phase complete"),
    )

    result = task_module.task_tool.run(
        {
            "description": "review gate",
            "prompt": "Implement and review the change",
            "agent_type": "workflow",
        },
        _context(
            tmp_path,
            _agent_depth=0,
            _subagent_mailbox=SubagentMailbox(),
        ),
    )

    assert result.ok is False
    assert "review_verdict_missing" in result.output


def test_workflow_is_not_concurrency_safe() -> None:
    assert task_module.task_tool.call_is_concurrency_safe(
        {"agent_type": "workflow"}
    ) is False
