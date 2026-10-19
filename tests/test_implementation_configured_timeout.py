"""Configured request time is bounded by existing owner and handoff deadlines.

The loop test advances a local clock; it performs real file edits and never
calls a provider or treats simulated latency as a model-quality measurement.
"""

import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

from minicode import agent_loop
from minicode.agent_budget import AgentTurnBudget
from minicode.deterministic_verifier import VerificationCheck
from minicode.executor_verification import ExecutorCheckCapability
from minicode.implementation_phases import ImplementationPhase, ImplementationPhaseController
from minicode.multi_agent_contracts import validate_executor_declaration
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.types import AgentStep, ModelUsage
from test_agent_model_events import RecordingSink
from test_implementation_finalize_deadline import _submission_tools
from test_implementation_investigation_deadline import _clock


def _policy(phase, *, owner, timeout):
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    policy = ImplementationPhaseController(bounded=True, snapshot=budget.snapshot(), max_steps=36,
        now=0, deadline=owner, request_timeout_seconds=timeout)
    policy.phase = phase
    return policy, budget


@pytest.mark.parametrize("phase", list(ImplementationPhase))
@pytest.mark.parametrize("owner", [None, 1800])
@pytest.mark.parametrize("timeout", [300, "300", 17.5])
def test_configured_timeout_applies_to_each_phase_with_or_without_owner(phase, owner, timeout):
    policy, budget = _policy(phase, owner=owner, timeout=timeout)
    request = policy.request(budget.snapshot(), estimated_input=1000, now=1)
    assert request.phase is phase
    assert not request.handoff_only
    assert request.deadline == 1 + float(timeout)
    assert budget.snapshot().limit_model_calls == 40
    assert budget.snapshot().limit_total_tokens == 500000
    assert policy.deadline == owner


@pytest.mark.parametrize("phase", list(ImplementationPhase))
@pytest.mark.parametrize("owner", [None, 1800])
@pytest.mark.parametrize("timeout", [None, 0, -1, True, False, float("nan"), float("inf"),
                                    float("-inf"), "invalid", "", [], {}])
def test_invalid_timeout_preserves_existing_phase_defaults(phase, owner, timeout):
    policy, budget = _policy(phase, owner=owner, timeout=timeout)
    request = policy.request(budget.snapshot(), estimated_input=1000, now=1)
    default = 45 if owner is None and phase is not ImplementationPhase.IMPLEMENT else 180
    assert request.phase is phase
    assert request.deadline == 1 + default


@pytest.mark.parametrize("phase", list(ImplementationPhase))
def test_configured_timeout_never_extends_owner_or_fixed_work_floor(phase):
    policy, budget = _policy(phase, owner=200, timeout=300)
    request = policy.request(budget.snapshot(), estimated_input=1000, now=1)
    assert request.deadline == policy.handoff_start == 180
    assert policy.deadline == 200
    policy.admitted()
    following = policy.request(budget.snapshot(), estimated_input=1000, now=2)
    if phase is ImplementationPhase.FINALIZE:
        assert following.handoff_only and following.deadline == 200
    else:
        assert following.deadline == 180
    assert policy.handoff_start == 180 and policy.deadline == 200


@pytest.mark.parametrize("runtime_timeout,environment_timeout,expected", [
    (300, "20", 300), (None, "300", 300), (30, "300", 30),
    ("invalid", "300", 180), (None, None, 180),
])
def test_real_loop_delivers_configured_request_window_before_actual_edit(
    tmp_path, monkeypatch, runtime_timeout, environment_timeout, expected,
):
    if environment_timeout is None:
        monkeypatch.delenv("MINICODE_MODEL_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("MINICODE_MODEL_TIMEOUT", environment_timeout)
    target = tmp_path / "candidate.py"
    target.write_text("value = 1\n")
    (tmp_path / "test_candidate.py").write_text("from candidate import value\ndef test_value():\n    assert value == 2\n")
    start = time.monotonic()
    clock = _clock(monkeypatch, start=start)
    owner = start + 1800
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    windows = []
    check_results = []
    assignment, order, store, channel, submit = _submission_tools(tmp_path)

    def digest(_context):
        return "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()

    def run_check(check, context):
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_candidate.py"],
            cwd=context.cwd, capture_output=True, text=True, timeout=30, check=False,
            env={**os.environ, "PYTHONPATH": str(tmp_path),
                 "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""})
        check_results.append(result.returncode)
        return {"checkId": check.check_id, "status": "passed" if result.returncode == 0 else "failed",
                "patchDigest": digest(context), "stdout": result.stdout, "stderr": result.stderr}

    capability = ExecutorCheckCapability((VerificationCheck(
        "public_value", "tests", ("pytest", "-q", "test_candidate.py"), 30),),
        run_check, current_patch_digest=digest)
    registry = capability.bind_registry(ToolRegistry([edit_file_tool, submit]))

    class Model:
        def next(self, messages, *, deadline_monotonic=None, **_kwargs):
            windows.append(deadline_monotonic - clock.value)
            if len(windows) == 1:
                clock.value += 200 if expected == 300 else expected - 1
                return AgentStep(type="tool_calls", calls=[{
                    "id": "configured-edit", "toolName": "edit_file",
                    "input": {"path": "candidate.py", "old": "value = 1", "new": "value = 2"},
                }], usage=ModelUsage(input_tokens=1000, output_tokens=100, source="estimated"))
            assert len(windows) == 2
            assert target.read_text() == "value = 2\n", "a healthy response must reach the actual writer"
            assert any(m.get("toolUseId") == "configured-edit" and not m.get("isError") for m in messages)
            assert check_results == [0]
            return AgentStep(type="tool_calls", calls=[{
                "id": "configured-submit", "toolName": "executor_submit", "input": {
                    "status": "completed", "steps": [{"step": 1, "status": "completed"}],
                    "deviations": [], "blockers": [],
                },
            }], usage=ModelUsage(input_tokens=1000, output_tokens=100, source="estimated"))

    runtime = {"modelTimeoutSeconds": runtime_timeout} if runtime_timeout is not None else {}
    sink = RecordingSink()
    try:
        result = agent_loop.run_agent_turn(bounded_implementation_phases=True, model=Model(), tools=registry,
            messages=[{"role": "user", "content": "Update candidate.py so value is 2, test and submit."}],
            cwd=str(tmp_path), runtime=runtime, implementation_task=True, enable_work_chain=False,
            max_steps=36, agent_budget=budget, deadline_monotonic=owner,
            subagent_mailbox=channel, event_sink=sink)
        assert len(windows) == 2
        assert any(m.get("toolUseId") == "configured-edit" and not m.get("isError") for m in result), (
            "A healthy configured response was discarded before edit/test/submit", windows,
            [event.payload for event in sink.events if event.event_type == "model.failed"],
        )
        assert target.read_text() == "value = 2\n"
        assert windows[0] == pytest.approx(expected)
        assert check_results == [0]
        declaration = json.loads(store.read(channel.write_key).content)
        assert validate_executor_declaration(assignment, declaration,
            implementation_order=order)["status"] == "completed"
        assert not any(event.event_type == "model.failed" for event in sink.events)
        assert clock.value < owner - 45 < owner
        assert budget.snapshot().used_model_calls == 2
        assert budget.snapshot().limit_model_calls == 40
        assert budget.snapshot().limit_total_tokens == 500000
    finally:
        capability.close()
        channel.close()
