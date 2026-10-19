"""Intermittent transport timeouts preserve the continuous actor and ledger.

Scripted provider failures are intentional; file tools, pytest, native channels
and atomic worker admission are real. No live-provider usage is fabricated.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
from copy import deepcopy
from types import SimpleNamespace

import pytest

import minicode.agent_loop as loop
from minicode.agent_budget import AgentTurnBudget
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import VerificationCheck
from minicode.executor_verification import ExecutorCheckCapability
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.tools.write_file import write_file_tool
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from minicode.types import AgentStep, ModelUsage
from minicode.workflow_runner import _WorkerCallBudget
from test_implementation_finalize_deadline import _submission_tools
from test_model_transport_continuation import _no_sleep, _run_errors
from test_primary_model_timeout_recovery import RecordingSink
from test_reviewer_result_submission import _bound, _rejection


def _action(name, number, **value):
    return {"id": f"action-{number}", "toolName": name, "input": value}


def _meter(messages, tools, calls):
    schemas = [{"name": t.name, "parameters": t.input_schema} for t in tools.list()]
    inputs = sum(estimate_message_tokens(m) for m in messages) + estimate_tokens(json.dumps(schemas))
    outputs = estimate_tokens(json.dumps(calls))
    return AgentStep(type="tool_calls", calls=calls, usage=ModelUsage(
        input_tokens=inputs, output_tokens=outputs, source="estimated"))


@pytest.mark.parametrize("wrapped", [False, True])
def test_executor_retries_separated_timeouts_without_replaying_real_work(tmp_path, monkeypatch, wrapped):
    waits = _no_sleep(monkeypatch)
    (tmp_path / "candidate.py").write_text("def value():\n    return 1\n")
    (tmp_path / "test_candidate.py").write_text("from candidate import value\n\ndef test_value():\n    assert value() == 2\n")
    _, _, store, channel, submit = _submission_tools(tmp_path)
    checks, requests, leases, metered, controls = [], [], [], [], []
    shared = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    worker = _WorkerCallBudget(shared, "executor", max_calls=10,
                              preserve_calls=2, preserve_tokens=10000)
    owner = time.monotonic() + 90

    def digest(_context):
        return "sha256:" + hashlib.sha256((tmp_path / "candidate.py").read_bytes()).hexdigest()

    def check(check, context):
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_candidate.py"],
            cwd=context.cwd, capture_output=True, text=True, timeout=30,
            env={**os.environ, "PYTHONPATH": str(tmp_path),
                 "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""})
        checks.append(result.returncode)
        return {"checkId": check.check_id, "status": "passed" if result.returncode == 0 else "failed",
                "patchDigest": digest(context), "stdout": result.stdout, "stderr": result.stderr,
                "verification": "implementation_feedback"}

    capability = ExecutorCheckCapability((VerificationCheck("value", "tests",
        ("python", "-m", "pytest", "-q", "test_candidate.py"), 30),), check,
        test_paths=("test_candidate.py",), current_patch_digest=digest)
    tools = capability.bind_registry(ToolRegistry([read_file_tool, edit_file_tool, submit]))
    actions = {
        0: _action("read_file", 0, path="candidate.py"),
        2: _action("edit_file", 2, path="candidate.py", old="return 1", new="return 2"),
        4: _action("executor_check", 4, check=1),
        5: _action("executor_submit", 5, status="completed",
                   steps=[{"step": 1, "status": "completed"}], deviations=[], blockers=[]),
    }

    class Executor:
        def next(self, messages, tools_override=None, **options):
            index = len(requests)
            requests.append(deepcopy(messages))
            leases.append(shared.snapshot().reserved_total_tokens)
            controls.append(options.get("deadline_monotonic"))
            if index in {1, 3}:
                error = TimeoutError("headers or stream idle")
                raise urllib.error.URLError(error) if wrapped else error
            assert index in actions, "No replacement actor or redundant closing model call is needed."
            if index == 4:
                assert checks == [0], "The completed edit's actual pytest must not be replayed."
                assert "return 2" in (tmp_path / "candidate.py").read_text()
            result = _meter(messages, tools_override or tools, [actions[index]])
            metered.append(result.usage.input_tokens + result.usage.output_tokens)
            return result

    sink = RecordingSink()
    messages = loop.run_agent_turn(model=Executor(), tools=tools, cwd=str(tmp_path),
        messages=[{"role": "user", "content": "Correct the value, check it, and submit."}],
        max_steps=None, agent_depth=1, enable_work_chain=False, agent_budget=worker,
        implementation_task=True,
        runtime={"modelTimeoutSeconds": 0}, deadline_monotonic=owner,
        subagent_mailbox=channel, event_sink=sink)
    artifact = store.read(channel.write_key)
    assert artifact is not None, "An intermittent timeout discarded the original Executor before native delivery."
    assert json.loads(artifact.content)["status"] == "completed"
    assert len(requests) == 6 and checks == [0, 0]
    assert controls == [owner] * 6
    for call_id in ("action-0", "action-2", "action-4", "action-5"):
        assert sum(m.get("role") == "tool_result" and m.get("toolUseId") == call_id for m in messages) == 1
    assert sum(bool(m.get("_model_transport_failure")) for m in requests[-1]) == 2
    retries = [payload for name, _, payload in sink.events if name == "model.retry_scheduled"]
    assert [p["attempt"] for p in retries] == [1, 1]
    assert all(p["failureKind"] == "timeout" and p["usage"] == "unknown" for p in retries)
    assert len(waits) == 2
    snapshot = shared.snapshot()
    assert snapshot.used_model_calls == 6 and snapshot.reserved_total_tokens == 0
    assert snapshot.usage_uncertain and snapshot.used_total_tokens == sum(metered) + leases[1] + leases[3]
    assert snapshot.limit_model_calls == 40 and snapshot.limit_total_tokens == 500000
    assert worker.snapshot().limit_total_tokens == 490000


@pytest.mark.parametrize("native_only", [False, True])
def test_reviewer_keeps_native_channel_and_feedback_across_separated_timeouts(tmp_path, monkeypatch, native_only):
    _no_sleep(monkeypatch)
    _, store, channel, submit, _ = _bound()
    (tmp_path / "evidence.py").write_text("observed = True\n")
    shared = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    # A finite worker view selects native-only without changing the global
    # ledger or borrowing the protected ancestor balance.
    floor = 480000 if native_only else 10000
    worker = _WorkerCallBudget(shared, "reviewer", max_calls=10,
                              preserve_calls=2, preserve_tokens=floor)
    tools = ToolRegistry([read_file_tool, submit])
    requests, views, leases, billed = [], [], [], []
    invalid = _rejection()
    invalid.pop("warnings")

    class Reviewer:
        def next(self, messages, tools_override=None, **_options):
            index = len(requests)
            requests.append(deepcopy(messages))
            views.append((tools_override or tools).list_all())
            leases.append(shared.snapshot().reserved_total_tokens)
            if index in {0, 2}:
                raise TimeoutError("temporary transport timeout")
            assert index in {1, 3}
            if index == 1:
                action = (_action("review_submit", 1, **invalid) if native_only else
                          _action("read_file", 1, path="evidence.py"))
            else:
                result = [m for m in messages if m.get("role") == "tool_result"
                          and m.get("toolUseId") == "action-1"]
                assert len(result) == 1
                assert bool(result[0].get("isError")) is native_only
                if native_only:
                    assert "warnings" in result[0]["content"]
                action = _action("review_submit", 3, **_rejection())
            step = _meter(messages, tools_override or tools, [action])
            billed.append(step.usage.input_tokens + step.usage.output_tokens)
            return step

    messages = loop.run_agent_turn(model=Reviewer(), tools=tools, cwd=str(tmp_path),
        messages=[{"role": "user", "content": "Inspect the evidence and submit a valid review."}],
        max_steps=None, agent_depth=1, enable_work_chain=False, agent_budget=worker,
        subagent_mailbox=channel, host_native_result_tool=submit)
    artifact = store.read(channel.write_key)
    assert artifact is not None, "The same Reviewer must receive its error and reach a native verdict."
    assert json.loads(artifact.content)["verdict"] == "changes_required"
    assert len(requests) == 4
    if native_only:
        assert views == [["review_submit"]] * 4, "Timeout recovery must not reopen inspection tools."
    assert sum(m.get("role") == "tool_result" and m.get("toolUseId") == "action-1" for m in messages) == 1
    assert shared.snapshot().used_total_tokens == sum(billed) + leases[0] + leases[2]
    assert shared.snapshot().used_total_tokens <= 500000 - floor
    assert shared.snapshot().used_model_calls == 4 and shared.snapshot().usage_uncertain
    assert shared.snapshot().reserved_total_tokens == 0


def test_continuous_primary_uses_the_same_resettable_transport_burst(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    requests = []

    class Primary:
        def next(self, messages, **_options):
            requests.append(deepcopy(messages))
            if len(requests) in {1, 3}:
                raise TimeoutError("temporary idle")
            if len(requests) == 2:
                return _meter(messages, tools, [_action("write_file", 2, path="result.txt", content="kept\n")])
            assert len(requests) == 4
            return AgentStep(type="assistant", kind="final", content="The saved result is ready.")

    tools = ToolRegistry([write_file_tool])
    budget = AgentTurnBudget()
    messages = loop.run_agent_turn(model=Primary(), tools=tools, cwd=str(tmp_path),
        messages=[{"role": "user", "content": "Save the result."}], max_steps=None,
        enable_bounded_completion=True, enable_work_chain=False, agent_budget=budget)
    assert len(requests) == budget.snapshot().used_model_calls == 4
    assert (tmp_path / "result.txt").read_text() == "kept\n"
    assert len([m for m in messages if m.get("role") == "tool_result"]) == 1
    assert messages[-1]["content"] == "The saved result is ready."
    assert budget.snapshot().usage_uncertain and budget.snapshot().reserved_total_tokens == 0


def test_timeout_and_connection_failures_share_one_continuous_outage_burst(tmp_path, monkeypatch):
    waits = _no_sleep(monkeypatch)
    errors = [TimeoutError("idle"), ConnectionResetError("reset"),
              urllib.error.URLError(TimeoutError("headers")), TimeoutError("idle")]
    budget = AgentTurnBudget()
    requests, messages = _run_errors(tmp_path, errors + ["must not run"], budget=budget)
    assert len(requests) == budget.snapshot().used_model_calls == 4
    assert len(waits) == 3 and messages[-1].get("_partial")
    assert budget.snapshot().usage_uncertain and budget.snapshot().reserved_total_tokens == 0


@pytest.mark.parametrize("limit", ["worker_calls", "shared_calls", "shared_tokens"])
def test_timeout_retry_must_reenter_the_original_atomic_budget(tmp_path, monkeypatch, limit):
    _no_sleep(monkeypatch)
    shared = AgentTurnBudget(max_model_calls=1 if limit == "shared_calls" else 40,
                             max_total_tokens=10000)
    worker = _WorkerCallBudget(shared, "executor", max_calls=1 if limit == "worker_calls" else 8,
                              preserve_calls=0, preserve_tokens=0)
    requests = []

    class Model:
        def next(self, messages, **_kwargs):
            requests.append(deepcopy(messages))
            if limit == "shared_tokens":
                shared.max_total_tokens = shared.snapshot().reserved_total_tokens
            raise TimeoutError("idle")

    result = loop.run_agent_turn(model=Model(), tools=ToolRegistry([]), cwd=str(tmp_path),
        messages=[{"role": "user", "content": "Keep the original allowance."}],
        max_steps=None, agent_depth=1, enable_work_chain=False, agent_budget=worker)
    assert len(requests) == shared.snapshot().used_model_calls == 1
    assert shared.snapshot().reserved_total_tokens == 0 and shared.snapshot().usage_uncertain
    assert "budget" in result[-1]["content"].lower()


@pytest.mark.parametrize("stop", ["cancel", "owner_deadline", "typed_deadline", "backoff_cancel"])
def test_timeout_recovery_does_not_override_owner_control(tmp_path, monkeypatch, stop):
    _no_sleep(monkeypatch)
    token = TurnCancellationToken("turn_" + "c" * 32)
    now = time.monotonic()
    clock = [now]
    monkeypatch.setattr(loop, "time", SimpleNamespace(**(vars(time) | {"monotonic": lambda: clock[0]})))
    calls = []
    shared = AgentTurnBudget(max_model_calls=40)

    def cancel_wait(*_args, **_kwargs):
        token.request()
        token.raise_if_requested()

    if stop == "backoff_cancel":
        monkeypatch.setattr(loop, "controlled_retry_sleep", cancel_wait)

    class Model:
        def next(self, messages, **_kwargs):
            calls.append(messages)
            if stop == "cancel":
                token.request()
            elif stop == "owner_deadline":
                clock[0] = now + 61
            elif stop == "typed_deadline":
                raise ModelCallDeadlineExceeded("real caller control")
            raise TimeoutError("socket timeout")

    expected = TurnCancellationRequested if "cancel" in stop else loop.AgentTurnDeadlineExceeded
    with pytest.raises(expected):
        loop.run_agent_turn(model=Model(), tools=ToolRegistry([]), cwd=str(tmp_path),
            messages=[{"role": "user", "content": "Observe original owner controls."}],
            max_steps=None, agent_depth=1, enable_work_chain=False, agent_budget=shared,
            cancellation_token=token, deadline_monotonic=now + 60)
    assert len(calls) == shared.snapshot().used_model_calls == 1
    assert shared.snapshot().reserved_total_tokens == 0
