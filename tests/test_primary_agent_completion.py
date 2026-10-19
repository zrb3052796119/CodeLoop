"""Exercise the actual loop, tool dispatch, shared budget and final outcome."""
from __future__ import annotations

import inspect
import time

import pytest

import minicode.agent_loop as loop
from minicode.agent_budget import AgentTurnBudget
from minicode.task_outcome import AgentOutcomeCapture
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.write_file import write_file_tool
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from minicode.types import AgentStep, ModelUsage
from minicode.verification_observation import project_verification


def call(name="echo", **inputs):
    return AgentStep(type="tool_calls", calls=[
        {"id": "call", "toolName": name, "input": inputs},
    ])


class Model:
    def __init__(self, steps=None, closing=None):
        self.steps = iter(steps) if steps is not None else None
        self.calls = 0
        self.closing = closing
        self.inputs = []

    def next(self, messages, on_stream_chunk=None):
        self.calls += 1
        self.inputs.append(list(messages))
        # React to the host's actual closing request, rather than prescribe
        # the number of normal calls. This also runs on pre-fix production.
        if "Do NOT call" in str(messages[-1].get("content", "")):
            if isinstance(self.closing, BaseException):
                raise self.closing
            return self.closing or AgentStep(type="assistant", content="Collected findings; remaining work is unverified.")
        return next(self.steps) if self.steps is not None else call()


def registry(executed, result=None):
    def echo(inputs, _context):
        executed.append(inputs)
        return result or ToolResult(ok=True, output="read evidence")
    return ToolRegistry([ToolDefinition(
        name="echo", description="read evidence", input_schema={"type": "object"},
        validator=lambda value: value, run=echo,
    ), write_file_tool])


def run(tmp_path, model, tools, **kwargs):
    # Allow exactly the same behavioral test to run against the saved old
    # production snapshot, where the new primary option did not exist yet.
    options = {"enable_bounded_completion": True} if (
        "enable_bounded_completion" in inspect.signature(loop.run_agent_turn).parameters
    ) else {}
    if "enable_implementation_phases" in inspect.signature(loop.run_agent_turn).parameters:
        # This suite isolates legacy soft completion; phase interaction is
        # exercised separately in test_implementation_phases.py.
        options["enable_implementation_phases"] = False
    return loop.run_agent_turn(
        model=model, tools=tools, cwd=str(tmp_path),
        messages=[{"role": "user", "content": "Fix the bug and report the checks."}],
        enable_work_chain=kwargs.pop("enable_work_chain", False), **options, **kwargs,
    )


def test_primary_limit_returns_findings_instead_of_discarding_them(tmp_path):
    executed = []
    model = Model()
    capture = AgentOutcomeCapture()
    messages = run(tmp_path, model, registry(executed), max_steps=3, outcome_capture=capture)
    assert "Collected findings" in messages[-1]["content"]
    assert messages[-1]["_partial"] is True
    assert len(executed) == 2
    assert model.calls == 3
    assert capture.outcome.status == "unknown"
    assert capture.outcome.goal_achieved is False


def test_closing_tool_calls_are_not_executed(tmp_path):
    executed = []
    closing = call(text="this tool must not run")
    closing.content = "Findings preserved."
    model = Model(closing=closing)
    messages = run(tmp_path, model, registry(executed), max_steps=3)
    assert len(executed) == 2
    assert "Findings preserved" in messages[-1]["content"]
    assert "not confirmed" in messages[-1]["content"]


@pytest.mark.parametrize("hard_limit", [1, 2, 5, 50])
def test_primary_closing_is_inside_the_caller_hard_limit(tmp_path, hard_limit):
    executed = []
    model = Model()
    messages = run(tmp_path, model, registry(executed), max_steps=hard_limit)
    assert model.calls == hard_limit
    assert len(executed) == hard_limit - 1
    assert messages[-1]["_partial"] is True


def test_last_shared_model_call_is_reserved_for_summary(tmp_path):
    budget = AgentTurnBudget(max_total_tokens=100_000, max_model_calls=3)
    executed = []
    model = Model()
    messages = run(tmp_path, model, registry(executed), max_steps=50, agent_budget=budget)
    assert model.calls == 3
    assert budget.snapshot().used_model_calls == 3
    assert len(executed) == 2
    assert "Collected findings" in messages[-1]["content"]
    assert "turn_budget_exceeded" in messages[-1]["content"]


@pytest.mark.parametrize("closing", [RuntimeError("provider unavailable"), AgentStep(type="assistant", content="")])
def test_failed_closing_preserves_host_observed_changes(tmp_path, closing):
    model = Model([call("write_file", path="fixed.py", content="fixed = True\n")], closing=closing)
    messages = run(tmp_path, model, registry([]), max_steps=2)
    assert (tmp_path / "fixed.py").read_text() == "fixed = True\n"
    assert messages[-1]["_partial"] is True
    assert "fixed.py" in messages[-1]["content"]
    assert "execution step limit" in messages[-1]["content"]
    assert model.calls == 2


def test_no_token_headroom_uses_host_summary_without_a_provider_call(tmp_path):
    budget = AgentTurnBudget(max_total_tokens=1, max_model_calls=3)
    model = Model()
    messages = run(tmp_path, model, registry([]), max_steps=50, agent_budget=budget)
    assert model.calls == 0
    assert "token budget" in messages[-1]["content"]
    assert messages[-1]["_partial"] is True


def test_passed_check_does_not_turn_a_partial_result_into_goal_success(tmp_path):
    def check(_inputs, _context):
        return ToolResult(ok=True, output="1 passed", verification=project_verification(
            kind="tests", passed=True, source="run_command_exit",
        ))
    tools = ToolRegistry([ToolDefinition(
        name="run_command", description="verifier", input_schema={"type": "object"},
        validator=lambda value: value, run=check,
    )])
    capture = AgentOutcomeCapture()
    model = Model([call("run_command", command="pytest", args=["tests/test_fix.py"])])
    messages = run(tmp_path, model, tools, max_steps=2, outcome_capture=capture)
    assert messages[-1]["_partial"] is True
    assert capture.outcome.verification_status == "verified"
    assert capture.outcome.goal_achieved is False


def test_recent_actual_write_extends_feedforward_window_once(tmp_path, monkeypatch):
    policy_class = loop.TurnCompletionPolicy
    monkeypatch.setattr(loop, "TurnCompletionPolicy", lambda **kwargs: policy_class(
        **{**kwargs, "soft_limit": 3},
    ))
    executed = []
    model = Model([call(), call(), call("write_file", path="fixed.py", content="fixed\n")] + [call()] * 10)
    messages = run(tmp_path, model, registry(executed), max_steps=15)
    # 3 -> 8 on the new mutation; reads cannot extend again at 8.
    assert model.calls == 9
    assert len(executed) == 7
    assert messages[-1]["_partial"] is True


def test_recovery_stop_does_not_spend_the_reserved_closing_call(tmp_path):
    executed = []
    model = Model()
    messages = run(tmp_path, model, registry(executed, ToolResult(ok=False, output="operation failed")), max_steps=50)
    assert model.calls < 49
    assert "Collected findings" not in messages[-1]["content"]
    assert not messages[-1].get("_partial")


def test_cancel_before_closing_does_not_invoke_model(tmp_path):
    token = TurnCancellationToken("turn_" + "a" * 32)
    def cancel(_inputs, _context):
        token.request()
        return ToolResult(ok=True, output="cancelled")
    tools = ToolRegistry([ToolDefinition(
        name="echo", description="cancel", input_schema={"type": "object"},
        validator=lambda value: value, run=cancel,
    )])
    model = Model()
    with pytest.raises(TurnCancellationRequested):
        run(tmp_path, model, tools, max_steps=2, cancellation_token=token)
    assert model.calls == 1


def test_expired_deadline_does_not_invoke_a_closing_model(tmp_path):
    model = Model()
    with pytest.raises(loop.AgentTurnDeadlineExceeded):
        run(tmp_path, model, registry([]), max_steps=2, deadline_monotonic=time.monotonic() - 1)
    assert model.calls == 0


def test_committed_receipt_survives_partial_closing(tmp_path):
    result = ToolResult(ok=True, output="immutable committed receipt", _must_deliver=True, _continue_parent=True)
    model = Model()
    messages = run(tmp_path, model, registry([], result), max_steps=2)
    assert any(m.get("role") == "tool_result" and m.get("content") == result.output for m in messages)
    assert messages[-1]["_partial"] is True
    assert "remaining work is incomplete" in messages[-1]["content"]


def test_normal_completion_is_not_forced_to_be_partial(tmp_path):
    model = Model([AgentStep(type="assistant", content="The requested answer.", usage=ModelUsage(input_tokens=20, output_tokens=5, source="provider"))])
    capture = AgentOutcomeCapture()
    messages = run(tmp_path, model, registry([]), max_steps=50, outcome_capture=capture)
    assert model.calls == 1
    assert messages[-1] == {"role": "assistant", "content": "The requested answer."}
    assert capture.outcome.status == "success"


def test_completion_observer_failure_cannot_drop_a_committed_receipt(tmp_path, monkeypatch):
    def broken_observer(*_args, **_kwargs):
        raise RuntimeError("telemetry unavailable")
    monkeypatch.setattr(loop.TurnCompletionPolicy, "observe_results", broken_observer)
    result = ToolResult(ok=True, output="committed despite telemetry failure", _must_deliver=True, _continue_parent=True)
    messages = run(tmp_path, Model(), registry([], result), max_steps=2)
    assert any(m.get("role") == "tool_result" and m.get("content") == result.output for m in messages)
    assert messages[-1]["_partial"] is True


def test_closing_provider_failure_has_a_terminal_model_event(tmp_path):
    from test_agent_model_events import RecordingSink
    sink = RecordingSink()
    model = Model(closing=TimeoutError("provider timeout"))
    messages = run(tmp_path, model, registry([]), max_steps=2, event_sink=sink)
    started = [e for e in sink.events if e.event_type == "model.started"]
    terminals = [e for e in sink.events if e.event_type in {"model.completed", "model.failed"}]
    assert len(started) == len(terminals) == 2
    assert terminals[-1].event_type == "model.failed"
    assert terminals[-1].payload["failureKind"] == "timeout"
    assert terminals[-1].payload["operationId"] == started[-1].payload["operationId"]
    assert messages[-1]["_partial"] is True


def test_feedforward_35_is_a_soft_window_below_caller_50(tmp_path, monkeypatch):
    # Feedforward owns the recommendation; it must not overwrite the caller
    # ceiling and thereby make a closing call impossible after step 35.
    monkeypatch.setattr(loop, "_preemptive_step_cap", lambda **_kwargs: 35)
    monkeypatch.setattr(loop, "_apply_control_signal", lambda **kwargs: kwargs["max_steps"])
    executed = []
    model = Model()
    messages = run(tmp_path, model, registry(executed), max_steps=50, enable_work_chain=True)
    assert len(executed) == 35
    assert model.calls == 36
    assert messages[-1]["_partial"] is True


@pytest.mark.parametrize("lowered_limit", [1, 2])
def test_feedback_lowered_limit_also_bounds_closing(tmp_path, monkeypatch, lowered_limit):
    monkeypatch.setattr(loop, "_apply_control_signal", lambda **_kwargs: lowered_limit)
    model = Model()
    messages = run(tmp_path, model, registry([]), max_steps=50, enable_work_chain=True)
    assert model.calls == lowered_limit
    assert messages[-1]["_partial"] is True
