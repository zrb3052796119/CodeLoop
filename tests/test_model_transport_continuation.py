"""A failed provider exchange must not discard completed Executor tool work.

Scripted model decisions, real file tools/pytest/native channel, and the shared
ledger exercise the transport seam. No live-provider success or usage is claimed.
"""

import hashlib
import http.client
import json
import os
import ssl
import subprocess
import sys
import urllib.error
from copy import deepcopy

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
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from minicode.types import AgentStep, ModelUsage
from test_implementation_finalize_deadline import _submission_tools
from test_primary_model_timeout_recovery import RecordingSink


def _no_sleep(monkeypatch):
    waits = []
    # Patch the existing shared primitive, not a new production-only API, so
    # this exact test also runs against the actual frozen implementation.
    import minicode.model_call_control as control
    monkeypatch.setattr(control, "controlled_retry_sleep", lambda seconds, **kwargs: waits.append(seconds))
    if hasattr(loop, "controlled_retry_sleep"):
        monkeypatch.setattr(loop, "controlled_retry_sleep", control.controlled_retry_sleep)
    return waits


@pytest.mark.parametrize("error", [http.client.RemoteDisconnected("closed"),
    urllib.error.URLError(ConnectionResetError("reset")), http.client.IncompleteRead(b"partial", 20),
    urllib.error.URLError(OSError("Tunnel connection failed: 503 Service Unavailable"))])
def test_same_executor_keeps_real_edit_checks_and_channel_after_disconnect(tmp_path, monkeypatch, error):
    waits = _no_sleep(monkeypatch)
    (tmp_path / "candidate.py").write_text("def value():\n    return 1\n")
    (tmp_path / "test_candidate.py").write_text("from candidate import value\n\ndef test_value():\n    assert value() == 2\n")
    _, _, store, channel, submit = _submission_tools(tmp_path)
    checks = []

    def digest(_context):
        return "sha256:" + hashlib.sha256((tmp_path / "candidate.py").read_bytes()).hexdigest()

    def check(check, context):
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_candidate.py"],
            cwd=context.cwd, capture_output=True, text=True, timeout=30,
            env={**os.environ, "PYTHONPATH": str(tmp_path), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""})
        checks.append(result.returncode)
        return {"checkId": check.check_id, "status": "passed" if result.returncode == 0 else "failed",
                "patchDigest": digest(context), "stdout": result.stdout, "stderr": result.stderr,
                "verification": "implementation_feedback"}

    capability = ExecutorCheckCapability((VerificationCheck("value", "tests",
        ("python", "-m", "pytest", "-q", "test_candidate.py"), 30),), check,
        test_paths=("test_candidate.py",), current_patch_digest=digest)
    tools = capability.bind_registry(ToolRegistry([read_file_tool, edit_file_tool, submit]))
    budget = AgentTurnBudget(max_total_tokens=500000, max_model_calls=40)
    requests, billed, leases = [], [], []
    sink = RecordingSink()

    class Executor:
        def next(self, messages, tools_override=None, **_kwargs):
            number = len(requests)
            requests.append(deepcopy(messages))
            leases.append(budget.snapshot().reserved_total_tokens)
            actions = [
                {"id": "read", "toolName": "read_file", "input": {"path": "candidate.py"}},
                {"id": "edit", "toolName": "edit_file", "input": {"path": "candidate.py", "old": "return 1", "new": "return 2"}},
                {"id": "check", "toolName": "executor_check", "input": {"check": 1}},
            ]
            if number == 3:
                assert checks == [0, 0], "Real tests must have completed before the failed exchange."
                raise error
            if number == 4:
                for call_id in ("read", "edit", "check"):
                    results = [m for m in messages if m.get("role") == "tool_result" and m.get("toolUseId") == call_id]
                    assert len(results) == 1 and not results[0].get("isError")
                failures = [m for m in messages if m.get("_model_transport_failure")]
                assert failures and all(m["role"] == "system" for m in failures)
                action = {"id": "submit", "toolName": "executor_submit", "input": {
                    "status": "completed", "steps": [{"step": 1, "status": "completed"}],
                    "deviations": [], "blockers": []}}
            else:
                assert number < 3, "No replacement Executor or replay of completed tools is allowed."
                action = actions[number]
            schemas = [{"name": t.name, "parameters": t.input_schema} for t in (tools_override or tools).list()]
            inputs = sum(estimate_message_tokens(m) for m in messages) + estimate_tokens(json.dumps(schemas))
            outputs = estimate_tokens(json.dumps(action))
            billed.append(inputs + outputs)
            return AgentStep(type="tool_calls", calls=[action], usage=ModelUsage(
                input_tokens=inputs, output_tokens=outputs, source="estimated"))

    model = Executor()
    messages = loop.run_agent_turn(model=model, tools=tools, cwd=str(tmp_path),
        messages=[{"role": "user", "content": "Correct value(), run the approved check and hand off the candidate."}],
        max_steps=None, agent_depth=1, enable_work_chain=False, agent_budget=budget,
        subagent_mailbox=channel, event_sink=sink)
    artifact = store.read(channel.write_key)
    assert artifact is not None, "A transient disconnected response ended the original Executor before its native handoff."
    assert len(requests) == 5 and checks == [0, 0]
    assert "return 2" in (tmp_path / "candidate.py").read_text()
    assert len(waits) == 1 and waits[0] > 0
    assert json.loads(artifact.content)["status"] == "completed"
    assert sum(m.get("role") == "tool_result" and m.get("toolUseId") == "edit" for m in messages) == 1
    snapshot = budget.snapshot()
    assert snapshot.used_model_calls == 5 and snapshot.reserved_total_tokens == 0
    assert snapshot.usage_uncertain and leases[3] > 0
    assert snapshot.used_total_tokens == sum(billed) + leases[3]
    assert snapshot.limit_total_tokens == 500000 and snapshot.limit_model_calls == 40
    failures = [payload for name, _, payload in sink.events if name == "model.failed"]
    assert len(failures) == 1 and failures[0]["failureKind"] == "network"


def _run_errors(tmp_path, errors, *, budget=None, **options):
    calls = []
    class Model:
        def next(self, messages, **kwargs):
            calls.append(deepcopy(messages))
            value = errors[len(calls) - 1]
            if isinstance(value, BaseException):
                raise value
            return AgentStep(type="assistant", content=value,
                usage=ModelUsage(input_tokens=10, output_tokens=2, source="provider"))
    messages = loop.run_agent_turn(model=Model(), tools=ToolRegistry([]), cwd=str(tmp_path),
        messages=[{"role": "user", "content": "Inspect the public result."}], agent_depth=1,
        max_steps=None, enable_work_chain=False, agent_budget=budget, **options)
    return calls, messages


def test_connection_retry_uses_backoff_and_stops_a_continuous_outage(tmp_path, monkeypatch):
    waits = _no_sleep(monkeypatch)
    budget = AgentTurnBudget()
    calls, messages = _run_errors(tmp_path, [ConnectionResetError("reset")] * 10, budget=budget)
    assert len(calls) == 4 and len(waits) == 3
    assert all(delay > 0 for delay in waits)
    assert budget.snapshot().used_model_calls == 4 and budget.snapshot().usage_uncertain
    assert budget.snapshot().reserved_total_tokens == 0
    assert "transport" in messages[-1]["content"].lower() and messages[-1].get("_partial")


def test_retry_reenters_the_original_atomic_call_limit(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    budget = AgentTurnBudget(max_model_calls=1)
    calls, messages = _run_errors(tmp_path, [ConnectionResetError("reset"), "must not run"], budget=budget)
    assert len(calls) == 1 and budget.snapshot().used_model_calls == 1
    assert any("budget" in str(m.get("content", "")).lower() for m in messages)
    assert budget.snapshot().reserved_total_tokens == 0 and budget.snapshot().usage_uncertain


@pytest.mark.parametrize("error", [PermissionError("denied"), ValueError("invalid request"),
    ssl.SSLCertVerificationError("certificate rejected"),
    urllib.error.URLError(ssl.SSLCertVerificationError("certificate rejected")),
    urllib.error.HTTPError("https://provider.invalid", 401, "unauthorized", {}, None)])
def test_permanent_or_invalid_failures_do_not_retry(tmp_path, monkeypatch, error):
    waits = _no_sleep(monkeypatch)
    calls, _ = _run_errors(tmp_path, [error, "must not run"])
    assert len(calls) == 1 and waits == []


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_backoff_honors_owner_control_before_any_new_call(tmp_path, monkeypatch, stop):
    _no_sleep(monkeypatch)
    token = TurnCancellationToken("turn_" + "c" * 32)
    budget = AgentTurnBudget(max_model_calls=40)
    def wait(*_args, **_kwargs):
        if stop == "cancel":
            token.request()
            token.raise_if_requested()
        raise ModelCallDeadlineExceeded("owner deadline")
    import minicode.model_call_control as control
    monkeypatch.setattr(control, "controlled_retry_sleep", wait)
    if hasattr(loop, "controlled_retry_sleep"):
        monkeypatch.setattr(loop, "controlled_retry_sleep", wait)
    with pytest.raises(TurnCancellationRequested if stop == "cancel" else loop.AgentTurnDeadlineExceeded):
        _run_errors(tmp_path, [ConnectionResetError("reset"), "must not run"],
                    budget=budget, cancellation_token=token)
    assert budget.snapshot().used_model_calls == 1 and budget.snapshot().reserved_total_tokens == 0
