"""A committed child is durable evidence, not completion of the parent's goal."""
from __future__ import annotations

import threading
from itertools import chain, repeat
from pathlib import Path

import pytest
from test_workflow_analysis_convergence import _write_reviewer_result
from test_workflow_v2_structured_routing import (
    _request,
    _run,
    _verification_result,
    _write_executor_declaration,
)

import minicode.tools.task as task_module
from minicode.agent_loop import run_agent_turn
from minicode.agent_runtime import final_response_text
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.subagent_lifecycle import AsyncSubagentLifecycle
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.turn_cancellation import TurnCancellationToken
from minicode.types import AgentStep


@pytest.fixture
def committed_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ToolResult:
    (tmp_path / "allowed.txt").write_text("before\n")

    def executor(input_data, context):
        if context._logical_agent_role == "executor":
            Path(context.cwd, "allowed.txt").write_text("after\n")
            _write_executor_declaration(input_data, context)
        else:
            assert context._logical_agent_role == "reviewer"
            _write_reviewer_result(input_data, context)
        return ToolResult(ok=True, output="implemented")

    monkeypatch.setattr(task_module, "_run", executor)
    monkeypatch.setattr(
        DeterministicVerifier, "run", lambda self, **request: _verification_result(request)
    )
    result = _run(tmp_path, _request(risk="low", complex=False), monkeypatch, commit_audit_waived=True)
    assert result.ok and result._must_deliver
    assert (tmp_path / "allowed.txt").read_text() == "after\n"
    return result


class ScriptedModel:
    def __init__(self, steps):
        self.steps = iter(steps)
        self.calls = 0

    def next(self, messages, on_stream_chunk=None):
        self.calls += 1
        value = next(self.steps)
        return value(messages) if callable(value) else value


def _call(name: str, identity: str) -> AgentStep:
    return AgentStep(type="tool_calls", calls=[{"id": identity, "toolName": name, "input": {}}])


def _registry(committed_result, *, extra=None, before_return=None):
    def committed(_input, _context):
        if before_return is not None:
            before_return()
        return committed_result

    definitions = [ToolDefinition(
        name="workflow", description="committed workflow", input_schema={"type": "object"},
        validator=lambda value: value, run=committed,
    )]
    if extra is not None:
        definitions.append(ToolDefinition(
            name="remaining_work", description="second requested task", input_schema={"type": "object"},
            validator=lambda value: value, run=extra,
        ))
    return ToolRegistry(definitions)


def test_committed_workflow_returns_control_for_remaining_parent_work(tmp_path, committed_result):
    remaining = []
    model = ScriptedModel([
        _call("workflow", "first"), _call("remaining_work", "second"),
        AgentStep(type="assistant", content="Both requested tasks are complete."),
    ])
    messages = run_agent_turn(
        model=model,
        tools=_registry(committed_result, extra=lambda _i, _c: (
            remaining.append("completed") or ToolResult(ok=True, output="second task complete")
        )),
        messages=[{"role": "user", "content": "Complete both tasks, then summarize."}],
        cwd=str(tmp_path), enable_work_chain=False,
    )
    assert remaining == ["completed"]
    assert messages[-1]["content"] == "Both requested tasks are complete."
    assert any(m.get("role") == "tool_result" and m.get("content") == committed_result.output for m in messages)


def test_committed_workflow_still_waits_for_async_result_synthesis(tmp_path, committed_result):
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    release = threading.Event()
    entered = threading.Event()

    def investigation(_identity, _cancel):
        entered.set()
        assert release.wait(3)
        return ToolResult(ok=True, output="independent investigation evidence")

    lifecycle.spawn(agent_type="explore", runner=investigation)
    assert entered.wait(1)

    def premature_final(_messages):
        release.set()
        return AgentStep(type="assistant", content="Attempted early final.")

    def synthesize(messages):
        assert any("independent investigation evidence" in str(m.get("content")) for m in messages)
        return AgentStep(type="assistant", content="Combined result from both tasks.")

    # The concrete lifecycle's barrier must expose the terminal result before final.
    try:
        model = ScriptedModel([_call("workflow", "first"), premature_final, synthesize])
        messages = run_agent_turn(
            model=model, tools=_registry(committed_result),
            messages=[{"role": "user", "content": "Apply change and synthesize the investigation."}],
            cwd=str(tmp_path), enable_work_chain=False, subagent_lifecycle=lifecycle,
        )
        assert messages[-1]["content"] == "Combined result from both tasks."
        assert lifecycle.finalization_barrier()["ready"] is True
    finally:
        release.set()
        lifecycle.shutdown()


def test_cancellation_after_commit_preserves_receipt_without_more_work(tmp_path, committed_result):
    token = TurnCancellationToken("turn_" + "a" * 32)
    model = ScriptedModel([_call("workflow", "first")])
    messages = run_agent_turn(
        model=model, tools=_registry(committed_result, before_return=token.request),
        messages=[{"role": "user", "content": "Apply the change."}],
        cwd=str(tmp_path), enable_work_chain=False, cancellation_token=token,
    )
    assert model.calls == 1
    assert messages[-1]["content"] == committed_result.output


def test_current_workflow_receipt_takes_precedence_over_previous_turn_answer():
    messages = [
        {"role": "assistant", "content": "Previous answer."},
        {"role": "user", "content": "Apply another change."},
        {"role": "assistant_tool_call", "toolName": "task", "toolUseId": "current"},
        {"role": "tool_result", "toolName": "task", "toolUseId": "current", "content": "Current change committed."},
    ]
    assert final_response_text(messages) == "Current change committed."


def test_unanswered_current_turn_does_not_display_previous_answer():
    assert final_response_text([
        {"role": "assistant", "content": "Previous answer."},
        {"role": "user", "content": "New request."},
    ]) is None


@pytest.mark.parametrize("interruption", ["cancel", "deadline"])
def test_interrupted_parent_after_child_commit_keeps_receipt_and_partial_status(
    tmp_path, committed_result, interruption,
):
    from minicode.agent_loop import AgentTurnDeadlineExceeded

    token = TurnCancellationToken("turn_" + "b" * 32)

    def interrupt(_messages):
        if interruption == "cancel":
            token.request()
            token.raise_if_requested()
        raise AgentTurnDeadlineExceeded("deadline during remaining work")

    model = ScriptedModel([_call("workflow", "first"), interrupt])
    messages = run_agent_turn(
        model=model, tools=_registry(committed_result),
        messages=[{"role": "user", "content": "Complete the change and the follow-up."}],
        cwd=str(tmp_path), enable_work_chain=False, cancellation_token=token,
    )
    assert model.calls == 2
    assert any(m.get("role") == "tool_result" and m.get("content") == committed_result.output for m in messages)
    assert "already committed" in messages[-1]["content"]
    assert "remaining work" in messages[-1]["content"]
    assert "cancelled" in messages[-1]["content"] if interruption == "cancel" else "deadline" in messages[-1]["content"]


@pytest.mark.parametrize("failure", ["network", "budget"])
def test_failure_in_remaining_work_reports_committed_partial_progress(
    tmp_path, committed_result, failure, monkeypatch,
):
    from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget

    def fail(_messages):
        if failure == "budget":
            raise AgentBudgetExceeded("model_calls", AgentTurnBudget.from_runtime({}).snapshot())
        raise ConnectionError("provider unavailable")

    # A network outage persists through the existing transport retries; an
    # exhausted two-item iterator would test StopIteration instead of failure.
    monkeypatch.setattr("minicode.agent_loop.controlled_retry_sleep", lambda *_a, **_k: None)
    model = ScriptedModel(chain([_call("workflow", "first")], repeat(fail)))
    messages = run_agent_turn(
        model=model, tools=_registry(committed_result),
        messages=[{"role": "user", "content": "Apply change and complete follow-up."}],
        cwd=str(tmp_path), enable_work_chain=False, budget_exhausted_policy="raise",
    )
    assert any(m.get("content") == committed_result.output for m in messages)
    response = final_response_text(messages)
    assert "already committed" in response
    assert "remaining work" in response
    assert ("transport" if failure == "network" else "budget") in response.lower()
    if failure == "network":
        assert any(m.get("_model_transport_failure") for m in messages)


def test_final_observer_failure_cannot_hide_committed_child_receipt(tmp_path, committed_result):
    class BrokenOutcomeCapture:
        def record(self, _outcome):
            raise RuntimeError("observer unavailable")

    model = ScriptedModel([
        _call("workflow", "first"), AgentStep(type="assistant", content="All requested work complete."),
    ])
    messages = run_agent_turn(
        model=model, tools=_registry(committed_result),
        messages=[{"role": "user", "content": "Apply and summarize."}],
        cwd=str(tmp_path), enable_work_chain=False, outcome_capture=BrokenOutcomeCapture(),
    )
    assert messages[-1]["content"] == "All requested work complete."
    assert any(m.get("content") == committed_result.output for m in messages)


@pytest.mark.parametrize("rewrite", ["remove", "tamper"])
def test_continuation_restores_exact_committed_tool_pair(tmp_path, committed_result, rewrite):
    committed_call = _call("workflow", "first")
    committed_call.calls[0]["input"] = {"scope": {"paths": ["allowed.txt"]}}

    def compact_or_rewrite(messages):
        if rewrite == "remove":
            messages[:] = [m for m in messages if m.get("toolUseId") != "first"]
        else:
            for message in messages:
                if message.get("role") == "assistant_tool_call":
                    message["input"]["scope"]["paths"][0] = "different.txt"
                elif message.get("role") == "tool_result":
                    message["content"] = "rewritten receipt"
        return AgentStep(type="assistant", content="Completed and summarized.")

    messages = run_agent_turn(
        model=ScriptedModel([committed_call, compact_or_rewrite]), tools=_registry(committed_result),
        messages=[{"role": "user", "content": "Apply and summarize."}],
        cwd=str(tmp_path), enable_work_chain=False,
    )
    pair = [m for m in messages if m.get("toolUseId") == "first"]
    assert [m["role"] for m in pair] == ["assistant_tool_call", "tool_result"]
    assert pair[0]["input"] == {"scope": {"paths": ["allowed.txt"]}}
    assert pair[1]["content"] == committed_result.output
    assert messages[-1]["content"] == "Completed and summarized."
