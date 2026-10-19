"""Primary-loop transport recovery preserves budgets, tools and evidence."""
from __future__ import annotations

import copy
import time
from types import SimpleNamespace

import pytest

import minicode.agent_loop as loop
from minicode.agent_budget import AgentTurnBudget
from minicode.agent_metrics import AgentMetricsCollector
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.task_outcome import AgentOutcomeCapture
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.write_file import write_file_tool
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from minicode.types import AgentStep, ModelUsage
from minicode.verification_observation import project_verification


class RecordingSink:
    def __init__(self):
        self.events = []

    def emit(self, event_type, *, step=None, payload=None):
        self.events.append((event_type, step, payload))


class Model:
    def __init__(self, results, before_call=None):
        self.results = results
        self.calls = 0
        self.inputs = []
        self.controls = []
        self.before_call = before_call

    def next(self, messages, on_stream_chunk=None, cancellation_token=None, deadline_monotonic=None):
        self.inputs.append(copy.deepcopy(messages))
        self.controls.append((cancellation_token, deadline_monotonic))
        self.calls += 1
        if self.before_call:
            self.before_call(self.calls, on_stream_chunk)
        result = self.results[self.calls - 1]
        if isinstance(result, BaseException):
            raise result
        return result


def call(name, **inputs):
    return AgentStep(type="tool_calls", calls=[{
        "id": f"call-{name}", "toolName": name, "input": inputs,
    }])


def run(tmp_path, model, tools=None, **options):
    return loop.run_agent_turn(
        model=model, tools=tools or ToolRegistry([]), cwd=str(tmp_path),
        messages=[{"role": "user", "content": "Fix the bug and report actual checks."}],
        enable_work_chain=False,
        enable_bounded_completion=options.pop("enable_bounded_completion", True),
        max_steps=options.pop("max_steps", 20), **options,
    )


def test_transient_timeout_retries_with_fresh_budget_and_paired_events(tmp_path):
    budget = AgentTurnBudget(max_total_tokens=100_000, max_model_calls=5)
    snapshots = []
    model = Model([
        TimeoutError("transport open timed out"),
        AgentStep(type="assistant", content="Here are the findings.", usage=ModelUsage(
            input_tokens=20, output_tokens=4, source="provider",
        )),
    ], before_call=lambda *_: snapshots.append(budget.snapshot()))
    sink = RecordingSink()
    metrics = AgentMetricsCollector()
    token = TurnCancellationToken("turn_" + "b" * 32)
    deadline = time.monotonic() + 60
    messages = run(tmp_path, model, agent_budget=budget, event_sink=sink,
                   metrics_collector=metrics, cancellation_token=token,
                   deadline_monotonic=deadline)

    assert model.calls == 2
    assert messages[-1]["content"] == "Here are the findings."
    assert not messages[-1].get("_partial")
    assert model.inputs[0] == model.inputs[1]
    assert model.controls == [(token, deadline), (token, deadline)]
    assert len(metrics._turns) == 2
    assert metrics._current_turn is None
    first_estimate = snapshots[0].reserved_total_tokens
    assert first_estimate > 0
    assert snapshots[1].used_total_tokens == first_estimate
    assert snapshots[1].reserved_total_tokens == first_estimate
    assert [s.used_model_calls for s in snapshots] == [1, 2]
    assert budget.snapshot().used_total_tokens == first_estimate + 24
    assert budget.snapshot().reserved_total_tokens == 0
    lifecycle = [(name, step, payload) for name, step, payload in sink.events
                 if name in {"model.started", "model.failed", "model.completed"}]
    assert [item[0] for item in lifecycle] == [
        "model.started", "model.failed", "model.started", "model.completed",
    ]
    assert lifecycle[0][2]["operationId"] == lifecycle[1][2]["operationId"]
    assert lifecycle[2][2]["operationId"] == lifecycle[3][2]["operationId"]
    assert lifecycle[0][2]["operationId"] != lifecycle[2][2]["operationId"]


def test_repeated_timeout_retains_real_changes_and_checks_without_third_call(tmp_path):
    checks = []

    def verify(inputs, _context):
        checks.append(inputs)
        return ToolResult(ok=True, output="1 passed", verification=project_verification(
            kind="tests", passed=True, source="run_command_exit",
        ))

    tools = ToolRegistry([write_file_tool, ToolDefinition(
        name="run_command", description="verify", input_schema={"type": "object"},
        validator=lambda value: value, run=verify,
    )])
    model = Model([
        call("write_file", path="fixed.py", content="fixed = True\n"),
        call("run_command", command="pytest", args=["tests/test_fix.py", "-q"]),
        TimeoutError("private transport payload"), TimeoutError("private transport payload"),
        AgentStep(type="assistant", content="This third provider call must not happen."),
    ])
    capture = AgentOutcomeCapture()
    budget = AgentTurnBudget(max_model_calls=10, max_total_tokens=100_000)
    sink = RecordingSink()
    messages = run(tmp_path, model, tools, agent_budget=budget,
                   outcome_capture=capture, event_sink=sink)

    assert model.calls == 4
    assert budget.snapshot().used_model_calls == 4
    assert (tmp_path / "fixed.py").read_text() == "fixed = True\n"
    assert len(checks) == 1
    assert len([m for m in messages if m.get("role") == "tool_result"]) == 2
    assert model.inputs[2] == model.inputs[3]
    final = messages[-1]
    assert final["_partial"] is True
    assert "fixed.py" in final["content"]
    assert "pytest tests/test_fix.py -q" in final["content"]
    assert "passed" in final["content"]
    assert "timeout" in final["content"].lower()
    assert "private transport payload" not in final["content"]
    assert capture.outcome.status == "unknown"
    assert capture.outcome.goal_achieved is False
    assert capture.outcome.verification_status == "verified"
    limited = [payload for name, _, payload in sink.events
               if name == "execution.completion_limited"]
    assert limited[-1]["modelSummary"] is False


def test_retry_allowance_is_not_reset_after_a_successful_model_step(tmp_path):
    model = Model([
        TimeoutError("first timeout"),
        call("write_file", path="fixed.py", content="fixed\n"),
        TimeoutError("later timeout"),
        AgentStep(type="assistant", content="Must not retry twice."),
    ])
    messages = run(tmp_path, model, ToolRegistry([write_file_tool]))
    assert model.calls == 3
    assert messages[-1]["_partial"] is True
    assert "fixed.py" in messages[-1]["content"]
    assert len([m for m in messages if m.get("role") == "tool_result"]) == 1


@pytest.mark.parametrize("limit_kind", ["calls", "tokens"])
def test_retry_budget_refusal_uses_host_summary_without_spending_closing_call(tmp_path, limit_kind):
    budget = AgentTurnBudget(max_total_tokens=100_000, max_model_calls=2 if limit_kind == "calls" else 5)

    def constrain_tokens(*_args):
        if limit_kind == "tokens":
            budget.max_total_tokens = budget.snapshot().reserved_total_tokens

    model = Model([TimeoutError("transport timeout")], before_call=constrain_tokens)
    messages = run(tmp_path, model, agent_budget=budget)
    assert model.calls == 1
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().used_total_tokens > 0
    assert budget.snapshot().reserved_total_tokens == 0
    assert messages[-1]["_partial"] is True
    assert "budget" in messages[-1]["content"]


def test_timeout_at_work_cap_uses_host_summary_without_a_closing_request(tmp_path):
    model = Model([TimeoutError("transport timeout")])
    messages = run(tmp_path, model, max_steps=2)
    assert model.calls == 1
    assert messages[-1]["_partial"] is True
    assert "timeout" in messages[-1]["content"].lower()


def test_cancellation_during_timeout_forbids_retry_and_keeps_terminal_event(tmp_path):
    token = TurnCancellationToken("turn_" + "a" * 32)
    sink = RecordingSink()
    model = Model([TimeoutError("transport timeout")], before_call=lambda *_: token.request())
    with pytest.raises(TurnCancellationRequested):
        run(tmp_path, model, cancellation_token=token, event_sink=sink)
    assert model.calls == 1
    assert len([e for e in sink.events if e[0] == "model.started"]) == 1
    assert len([e for e in sink.events if e[0] == "model.failed"]) == 1


def test_deadline_expiring_during_timeout_forbids_retry_and_closing(tmp_path, monkeypatch):
    now = time.monotonic()
    logical_clock = [now]
    monkeypatch.setattr(loop, "time", SimpleNamespace(
        monotonic=lambda: logical_clock[0], perf_counter=time.perf_counter, time=time.time,
    ))
    model = Model([TimeoutError("transport timeout")], before_call=lambda *_: logical_clock.__setitem__(0, now + 61))
    with pytest.raises(loop.AgentTurnDeadlineExceeded):
        run(tmp_path, model, deadline_monotonic=now + 60)
    assert model.calls == 1


def test_caller_deadline_exception_is_not_a_retryable_transport_timeout(tmp_path):
    model = Model([ModelCallDeadlineExceeded("caller deadline")])
    with pytest.raises(loop.AgentTurnDeadlineExceeded):
        run(tmp_path, model)
    assert model.calls == 1


def test_committed_receipt_is_preserved_without_reexecuting_its_tool(tmp_path):
    executed = []
    receipt = "immutable committed receipt"

    def commit(inputs, _context):
        executed.append(inputs)
        return ToolResult(ok=True, output=receipt, _must_deliver=True, _continue_parent=True)

    tools = ToolRegistry([ToolDefinition(
        name="commit", description="committed workflow", input_schema={"type": "object"},
        validator=lambda value: value, run=commit,
    )])
    model = Model([call("commit"), TimeoutError("transport timeout"), TimeoutError("transport timeout")])
    messages = run(tmp_path, model, tools)
    assert model.calls == 3
    assert len(executed) == 1
    assert any(m.get("role") == "tool_result" and m.get("content") == receipt for m in messages)
    assert messages[-1]["_partial"] is True
    assert "already committed" in messages[-1]["content"]
    assert "incomplete" in messages[-1]["content"]


def test_partial_stream_before_timeout_does_not_become_a_successful_final(tmp_path):
    streamed = []

    def stream_then_fail(_call, stream):
        stream("Starting analysis")

    model = Model([TimeoutError("transport timeout"), TimeoutError("transport timeout")], before_call=stream_then_fail)
    capture = AgentOutcomeCapture()
    messages = run(tmp_path, model, on_assistant_stream_chunk=streamed.append, outcome_capture=capture)
    assert model.calls == 2
    assert streamed == ["Starting analysis", "Starting analysis"]
    assert messages[-1]["_partial"] is True
    assert "Starting analysis" not in messages[-1]["content"]
    assert capture.outcome.goal_achieved is False


@pytest.mark.parametrize("options", [
    {"enable_bounded_completion": False},
    {"agent_depth": 1, "max_steps_soft_landing": True},
])
def test_non_bounded_primary_and_child_share_transport_recovery(tmp_path, monkeypatch, options):
    # Only the explicit bounded primary completion policy retains its historic
    # one-retry limit. Child and non-bounded loops share the connection burst.
    monkeypatch.setattr(loop, "controlled_retry_sleep", lambda *_args, **_kwargs: None)
    model = Model([TimeoutError("transport timeout"),
                   AgentStep(type="assistant", kind="final", content="Here are the findings.")])
    capture = AgentOutcomeCapture()
    budget = AgentTurnBudget(max_model_calls=5)
    messages = run(tmp_path, model, outcome_capture=capture, agent_budget=budget, **options)
    assert model.calls == budget.snapshot().used_model_calls == 2
    assert messages[-1]["content"] == "Here are the findings."
    assert not messages[-1].get("_partial")
    assert any(m.get("_model_transport_failure") for m in model.inputs[-1])
    assert budget.snapshot().limit_model_calls == 5
    assert budget.snapshot().usage_uncertain and budget.snapshot().reserved_total_tokens == 0
