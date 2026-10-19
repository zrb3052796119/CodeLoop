"""Tests for sub-agent step-limit soft landing and host turn-limit overrides.

A sub-agent that exhausts its model-turn limit used to be judged ``failed``
and its completed exploration discarded. The soft landing spends one
tool-free closing turn on a partial summary, returns the run as
``completed`` with a typed ``partial`` marker through the ToolResult text,
the structured result contract, the observation event, and the sidecar
journal — and only falls back to the old hard failure when the closing turn
itself produces nothing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import minicode.tools.task as task_module
from minicode.agent_loop import MAX_STEPS_SOFT_LANDING_PROMPT, run_agent_turn
from minicode.subagent_journal import (
    SubagentJournalError,
    SubagentRunJournal,
)
from minicode.subagent_observation import (
    normalize_subagent_payload,
    project_subagent_event,
)
from minicode.subagent_result import (
    extract_subagent_result,
    project_subagent_result,
)
from minicode.subagent_types import resolve_max_turns
from minicode.task_outcome import AgentOutcomeCapture, canonicalize_task_outcome
from minicode.tooling import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)
from minicode.tools.task import task_tool
from minicode.types import AgentStep, ChatMessage, ModelAdapter


class ScriptedModel(ModelAdapter):
    def __init__(self, steps: list[AgentStep]) -> None:
        self._steps = steps
        self.calls = 0

    def next(self, messages: list[ChatMessage], on_stream_chunk=None) -> AgentStep:
        step = self._steps[self.calls]
        self.calls += 1
        return step


def _counting_echo_tool(counter: dict[str, int]) -> ToolRegistry:
    def run_echo(input_data: dict, _context) -> ToolResult:
        counter["calls"] += 1
        return ToolResult(ok=True, output=f"echo:{input_data.get('text', '')}")

    return ToolRegistry(
        [
            ToolDefinition(
                name="echo",
                description="echo tool",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=run_echo,
            )
        ]
    )


def _echo_call(index: int) -> AgentStep:
    return AgentStep(
        type="tool_calls",
        calls=[{"id": str(index), "toolName": "echo", "input": {"text": "x"}}],
    )


# ---------------------------------------------------------------------------
# Loop-level soft landing
# ---------------------------------------------------------------------------


def test_soft_landing_converts_step_limit_into_marked_partial_success() -> None:
    counter = {"calls": 0}
    registry = _counting_echo_tool(counter)
    model = ScriptedModel(
        [_echo_call(1), _echo_call(2)]
        # The closing turn still asks for a tool; it must be declined.
        + [
            AgentStep(
                type="tool_calls",
                content="Partial findings: found the entry points.",
                calls=[{"id": "3", "toolName": "echo", "input": {"text": "y"}}],
            )
        ]
    )
    capture = AgentOutcomeCapture()

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        max_steps=2,
        max_steps_soft_landing=True,
        outcome_capture=capture,
    )

    assert capture.outcome is not None
    assert capture.outcome.status == "success"
    # Two bounded tool steps executed; the closing turn's tool call did not.
    assert counter["calls"] == 2
    assert model.calls == 3
    final = messages[-1]
    assert final["role"] == "assistant"
    assert final["content"] == "Partial findings: found the entry points."
    assert final["_partial"] is True
    # The closing turn was prompted with the tool-free instruction.
    assert messages[-2] == {
        "role": "user",
        "content": MAX_STEPS_SOFT_LANDING_PROMPT,
    }


def test_step_limit_without_soft_landing_keeps_the_hard_failure() -> None:
    counter = {"calls": 0}
    registry = _counting_echo_tool(counter)
    model = ScriptedModel([_echo_call(1), _echo_call(2)])
    capture = AgentOutcomeCapture()

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        max_steps=2,
        outcome_capture=capture,
    )

    assert capture.outcome is not None
    assert capture.outcome.status == "failed"
    assert model.calls == 2
    assert messages[-1]["content"] == (
        "Reached the maximum tool step limit for this turn."
    )
    assert "_partial" not in messages[-1]


def test_soft_landing_falls_back_to_failed_when_the_closing_turn_is_empty() -> None:
    counter = {"calls": 0}
    registry = _counting_echo_tool(counter)
    model = ScriptedModel(
        [_echo_call(1), _echo_call(2), AgentStep(type="assistant", content="")]
    )
    capture = AgentOutcomeCapture()

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        max_steps=2,
        max_steps_soft_landing=True,
        outcome_capture=capture,
    )

    assert capture.outcome is not None
    assert capture.outcome.status == "failed"
    assert messages[-1]["content"] == (
        "Reached the maximum tool step limit for this turn."
    )


def test_soft_landing_falls_back_to_failed_when_the_closing_call_raises() -> None:
    class FailingClosingModel(ModelAdapter):
        def __init__(self) -> None:
            self.calls = 0

        def next(
            self, messages: list[ChatMessage], on_stream_chunk=None
        ) -> AgentStep:
            self.calls += 1
            if self.calls <= 2:
                return _echo_call(self.calls)
            raise ConnectionError("provider dropped the closing turn")

    counter = {"calls": 0}
    registry = _counting_echo_tool(counter)
    capture = AgentOutcomeCapture()

    messages = run_agent_turn(
        model=FailingClosingModel(),
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        max_steps=2,
        max_steps_soft_landing=True,
        outcome_capture=capture,
    )

    assert capture.outcome is not None
    assert capture.outcome.status == "failed"
    assert messages[-1]["content"] == (
        "Reached the maximum tool step limit for this turn."
    )


# ---------------------------------------------------------------------------
# Host turn-limit overrides
# ---------------------------------------------------------------------------


def test_resolve_max_turns_defaults_to_no_cumulative_limit() -> None:
    assert resolve_max_turns("explore") is None
    assert resolve_max_turns("plan") is None
    assert resolve_max_turns("general") is None


def test_resolve_max_turns_accepts_a_valid_host_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINI_CODE_SUBAGENT_EXPLORE_MAX_TURNS", "25")
    assert resolve_max_turns("explore") == 25
    # Other types keep their defaults.
    assert resolve_max_turns("plan") is None


@pytest.mark.parametrize("raw", ["abc", "-3", "1.5", "true"])
def test_resolve_max_turns_fails_closed_on_invalid_overrides(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", raw)
    with pytest.raises(ValueError, match="MAX_TURNS"):
        resolve_max_turns("general")


@pytest.mark.parametrize("raw,expected", [("0", None), ("201", 201), ("  ", None)])
def test_explicit_turn_override_is_not_clamped(monkeypatch, raw, expected):
    monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", raw)
    assert resolve_max_turns("general") == expected


def test_task_dispatch_applies_the_host_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def fake_run_agent_turn(**kwargs):
        captured.update(kwargs)
        kwargs["outcome_capture"].record(
            canonicalize_task_outcome("success", 0)
        )
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(task_module, "run_agent_turn", fake_run_agent_turn)
    monkeypatch.setenv("MINI_CODE_SUBAGENT_EXPLORE_MAX_TURNS", "20")

    result = task_tool.run(
        {"description": "explore", "prompt": "explore", "agent_type": "explore"},
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
    )

    assert result.ok is True
    assert captured["max_steps"] == 20
    assert captured["max_steps_soft_landing"] is True


def test_task_dispatch_rejects_an_invalid_host_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def fake_run_agent_turn(**kwargs):
        captured.update(kwargs)
        kwargs["outcome_capture"].record(
            canonicalize_task_outcome("success", 0)
        )
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(task_module, "run_agent_turn", fake_run_agent_turn)
    monkeypatch.setenv("MINI_CODE_SUBAGENT_EXPLORE_MAX_TURNS", "not-a-number")

    with pytest.raises(ValueError, match="MAX_TURNS"):
        task_tool.run(
            {"description": "explore", "prompt": "explore", "agent_type": "explore"},
            ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
        )
    assert not captured


# ---------------------------------------------------------------------------
# In-process task path: fake model exhausts the limit, partial comes back
# ---------------------------------------------------------------------------


def _stub_child_model_factory(monkeypatch: pytest.MonkeyPatch, model) -> None:
    import minicode.subagent_model_routing as routing_module

    monkeypatch.setattr(
        routing_module,
        "create_subagent_model_adapter",
        lambda runtime, agent_type, *, tools: model,
    )


def test_explore_soft_landing_returns_a_marked_partial_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "target.txt").write_text("hello", encoding="utf-8")
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "1",
                        "toolName": "read_file",
                        "input": {"path": "target.txt"},
                    }
                ],
            ),
            AgentStep(
                type="assistant",
                content="PARTIAL SURVEY: target.txt contains a greeting.",
            ),
        ]
    )
    _stub_child_model_factory(monkeypatch, model)
    monkeypatch.setenv("MINI_CODE_SUBAGENT_EXPLORE_MAX_TURNS", "1")
    events: list[tuple[str, object]] = []

    class RecordingSink:
        def emit(self, event_type, *, step=None, payload=None):
            events.append((event_type, payload))

    result = task_tool.run(
        {
            "description": "explore",
            "prompt": "read target.txt and summarize",
            "agent_type": "explore",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _event_sink=RecordingSink(),
        ),
    )

    assert result.ok is True
    assert "[Sub-agent Explore completed]" in result.output
    assert "Result: PARTIAL" in result.output
    assert "Model turns: 2" in result.output  # 1 bounded turn + closing turn
    assert "Max turns: 1" in result.output
    assert "PARTIAL SURVEY" in result.output
    structured = extract_subagent_result(result.output)
    assert structured is not None
    assert structured["partial"] is True
    assert structured["outcome"] == "completed"
    (summary_event,) = [p for t, p in events if t == "subagent.completed"]
    assert summary_event["subagentVersion"] == 4
    assert summary_event["resultPartial"] is True
    assert summary_event["outcome"] == "completed"
    assert summary_event["modelTurns"] == 2
    assert summary_event["modelTurnLimit"] == 1


def test_explore_closing_failure_keeps_the_failed_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "target.txt").write_text("hello", encoding="utf-8")
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "1",
                        "toolName": "read_file",
                        "input": {"path": "target.txt"},
                    }
                ],
            ),
            AgentStep(type="assistant", content=""),
        ]
    )
    _stub_child_model_factory(monkeypatch, model)
    monkeypatch.setenv("MINI_CODE_SUBAGENT_EXPLORE_MAX_TURNS", "1")

    result = task_tool.run(
        {
            "description": "explore",
            "prompt": "read target.txt and summarize",
            "agent_type": "explore",
        },
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
    )

    assert result.ok is False
    assert "sub_agent_failed" in result.output
    assert "PARTIAL" not in result.output


# ---------------------------------------------------------------------------
# Subprocess worker path
# ---------------------------------------------------------------------------


def test_worker_soft_landing_marks_the_result_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import minicode.subagent_worker as worker_module
    from minicode.intent_parser import parse_intent as real_parse_intent

    captured: dict = {}
    semantic_adapter = type(
        "SemanticAdapter",
        (),
        {"infer": lambda self, _request, **_kwargs: ()},
    )()

    def build_semantic_adapter(model, *, runtime):
        captured["semantic_model"] = model
        captured["semantic_runtime"] = runtime
        return semantic_adapter

    def capture_parse_intent(prompt, **kwargs):
        captured["intent_call"] = kwargs
        return real_parse_intent(prompt, **kwargs)

    monkeypatch.setattr(
        "minicode.intent_semantic_classifier.build_semantic_intent_adapter",
        build_semantic_adapter,
    )
    monkeypatch.setattr(
        "minicode.intent_parser.parse_intent",
        capture_parse_intent,
    )

    def fake_run_agent_turn(**kwargs):
        captured.update(kwargs)
        kwargs["outcome_capture"].record(
            canonicalize_task_outcome("success", 0)
        )
        return [
            {
                "role": "assistant",
                "content": "partial findings from the closing turn",
                "_partial": True,
            }
        ]

    monkeypatch.setattr(worker_module, "run_agent_turn", fake_run_agent_turn)
    request_path = tmp_path / "worker-request.json"
    result_path = tmp_path / "worker-result.json"
    request_path.write_text(
        json.dumps(
            {
                "version": 1,
                "cwd": str(tmp_path),
                "prompt": "broad work",
                "subagent_id": "sub_" + "1" * 32,
                "result_key": "subagent_result_" + "1" * 32,
                "result_instruction": "",
                "max_turns": 2,
                "timeout_seconds": 30.0,
                "runtime": {"model": "fake"},
                "inject_memory": False,
                "budget_lease": {
                    "budgetLeaseVersion": 2,
                    "leaseId": 1,
                    "leaseNonce": "a" * 64,
                    "maxTotalTokens": 10_000,
                    "maxModelCalls": 5,
                    "maxCostUsd": "1.0",
                },
            }
        ),
        encoding="utf-8",
    )

    original_cwd = os.getcwd()
    try:
        code = worker_module.run_worker(str(request_path), str(result_path))
    finally:
        os.chdir(original_cwd)

    assert code == 0
    assert captured["max_steps_soft_landing"] is True
    assert captured["semantic_model"] is captured["model"]
    assert captured["semantic_runtime"]["model"] == "fake"
    intent_call = captured["intent_call"]
    assert intent_call["semantic_adapter"] is semantic_adapter
    intent_call_context = intent_call["call_context"]
    assert intent_call_context["agent_budget"] is captured["agent_budget"]
    assert intent_call_context["event_sink"] is captured["event_sink"]
    assert intent_call_context["cancellation_token"] is None
    assert intent_call_context["deadline_monotonic"] is captured[
        "deadline_monotonic"
    ]
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["partial"] is True


def test_general_subprocess_partial_reaches_the_parent_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from minicode.subagent_supervisor import SubagentProcessOutcome

    monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", "15")

    def fake_process(request, **_kwargs) -> SubagentProcessOutcome:
        return SubagentProcessOutcome(
            status="completed",
            result={
                "status": "completed",
                "final_message": "partial write-up from the child",
                "report_content": None,
                "model_turns": 16,
                "tool_calls": 14,
                "result_truncated": False,
                "partial": True,
                "error": None,
            },
            error=None,
            duration_ms=5,
            pid=None,
        )

    monkeypatch.setattr(task_module, "run_subagent_process", fake_process)
    events: list[tuple[str, object]] = []

    class RecordingSink:
        def emit(self, event_type, *, step=None, payload=None):
            events.append((event_type, payload))

    result = task_tool.run(
        {
            "description": "legacy general",
            "prompt": "do broad legacy work",
            "agent_type": "general",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _event_sink=RecordingSink(),
        ),
    )

    assert result.ok is True
    assert "Result: PARTIAL" in result.output
    structured = extract_subagent_result(result.output)
    assert structured is not None
    assert structured["partial"] is True
    (summary_event,) = [p for t, p in events if t == "subagent.completed"]
    assert summary_event["subagentVersion"] == 4
    assert summary_event["resultPartial"] is True
    assert summary_event["modelTurns"] == 16
    assert summary_event["modelTurnLimit"] == 15


# ---------------------------------------------------------------------------
# Contract-level partial semantics
# ---------------------------------------------------------------------------


def _observation_fields(**overrides) -> dict:
    fields = {
        "agent_type": "explore",
        "outcome": "completed",
        "model_turns": 13,
        "tool_calls": 11,
        "duration_ms": 1000,
        "max_turns": 12,
        "result_truncated": False,
        "subagent_id": "sub_" + "a" * 32,
        "result_contract_status": "fallback",
    }
    fields.update(overrides)
    return fields


def test_observation_v4_marks_partial_and_tolerates_retry_inflation() -> None:
    payload = project_subagent_event(
        **_observation_fields(), result_partial=True
    )

    assert payload is not None
    assert payload["subagentVersion"] == 4
    assert payload["resultPartial"] is True
    assert payload["modelTurns"] == payload["modelTurnLimit"] + 1
    # In-step retries plus the closing turn push provider calls further past
    # the step limit on real runs; only the generic count bound applies.
    inflated = project_subagent_event(
        **_observation_fields(model_turns=16), result_partial=True
    )
    assert inflated is not None
    assert inflated["modelTurns"] == 16
    assert (
        project_subagent_event(
            **_observation_fields(model_turns=100_001), result_partial=True
        )
        is None
    )


def test_observation_v4_requires_the_marker_and_parent_identity() -> None:
    assert (
        project_subagent_event(**_observation_fields(), result_partial=False)
        is not None
    )
    forged = {
        "subagentVersion": 4,
        "subagentId": "sub_" + "a" * 32,
        "agentType": "explore",
        "outcome": "completed",
        "modelTurns": 12,
        "toolCalls": 10,
        "durationMs": 100,
        "modelTurnLimit": 12,
        "phaseCount": 1,
        "maxPhases": 1,
        "resultTruncated": False,
        "resultContractStatus": "fallback",
        "resultPartial": False,
    }
    assert normalize_subagent_payload(forged) is None
    # Legacy schemas remain byte-for-byte stable.
    forged_v3 = dict(forged)
    forged_v3["subagentVersion"] = 3
    del forged_v3["resultPartial"]
    assert normalize_subagent_payload(forged_v3) is not None


def test_subagent_result_partial_round_trips_and_stays_optional() -> None:
    partial_result = project_subagent_result(
        None,
        subagent_id="sub_" + "f" * 32,
        agent_type="explore",
        outcome="completed",
        fallback_summary="partial survey",
        partial=True,
    )
    assert partial_result["partial"] is True

    from minicode.subagent_result import render_subagent_result

    rendered = render_subagent_result(partial_result)
    assert extract_subagent_result(rendered) == partial_result

    # Pre-partial projections carry no marker and still normalize.
    full_result = project_subagent_result(
        None,
        subagent_id="sub_" + "f" * 32,
        agent_type="explore",
        outcome="completed",
        fallback_summary="full survey",
    )
    assert "partial" not in full_result
    assert extract_subagent_result(render_subagent_result(full_result)) == (
        full_result
    )


def test_journal_finish_records_and_reads_back_the_partial_marker(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / ("run_" + "0" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    subagent_id = "sub_" + "b" * 32
    journal.start(
        subagent_id=subagent_id,
        agent_type="explore",
        max_turns=12,
        budget=None,
    )
    journal.finish(
        subagent_id,
        outcome="completed",
        model_turns=13,
        tool_calls=11,
        duration_ms=100,
        max_turns=12,
        result_truncated=False,
        budget=None,
        partial=True,
    )

    (summary,) = journal.list_runs()
    assert summary.partial is True
    assert summary.to_dict()["partial"] is True


def test_journal_without_the_marker_reads_as_not_partial(tmp_path: Path) -> None:
    run_dir = tmp_path / ("run_" + "0" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    subagent_id = "sub_" + "c" * 32
    journal.start(
        subagent_id=subagent_id,
        agent_type="explore",
        max_turns=12,
        budget=None,
    )
    journal.finish(
        subagent_id,
        outcome="completed",
        model_turns=5,
        tool_calls=4,
        duration_ms=100,
        max_turns=12,
        result_truncated=False,
        budget=None,
    )

    (summary,) = journal.list_runs()
    assert summary.partial is False
    assert "partial" not in summary.to_dict()


def test_journal_rejects_a_partial_marker_on_a_failed_run(tmp_path: Path) -> None:
    run_dir = tmp_path / ("run_" + "0" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    subagent_id = "sub_" + "d" * 32
    journal.start(
        subagent_id=subagent_id,
        agent_type="explore",
        max_turns=12,
        budget=None,
    )
    with pytest.raises(SubagentJournalError):
        journal.finish(
            subagent_id,
            outcome="failed",
            model_turns=12,
            tool_calls=10,
            duration_ms=100,
            max_turns=12,
            result_truncated=False,
            budget=None,
            partial=True,
        )
