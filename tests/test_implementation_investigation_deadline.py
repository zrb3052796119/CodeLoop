"""Healthy investigation keeps its owned request window through delivery.

Only model response latency/content are deterministic. The production loop,
deadline checkpoint, file tools, pytest, shared ledger and host-bound submission
are real. This is a scheduling regression, not evidence of model task quality.
"""

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from test_agent_model_events import RecordingSink
from test_implementation_finalize_deadline import _submission_tools

from minicode import agent_loop, model_call_control
from minicode.agent_budget import AgentTurnBudget
from minicode.deterministic_verifier import VerificationCheck
from minicode.executor_verification import ExecutorCheckCapability
from minicode.implementation_phases import (
    ImplementationPhase,
    ImplementationPhaseController,
)
from minicode.multi_agent_contracts import validate_executor_declaration
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelUsage


def test_investigation_uses_normal_cap_and_only_next_admission_crosses_phase():
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    policy = ImplementationPhaseController(bounded=True, snapshot=budget.snapshot(), max_steps=36,
        now=0, deadline=600)
    fixed_handoff = policy.handoff_start
    request = policy.request(budget.snapshot(), estimated_input=1000, now=65)
    assert request.phase is ImplementationPhase.INVESTIGATE
    assert request.deadline == 245, "A healthy investigation must not retain the old 45-second cutoff."
    assert request.output_limit == 2048 and request.preserve_calls == 2
    assert "next request" in request.message
    lease = budget.reserve_model_call(1100, preserve_model_calls=request.preserve_calls,
                                      preserve_total_tokens=request.preserve_tokens)
    policy.admitted()
    budget.record_model_call(input_tokens=1000, output_tokens=100, reservation=lease)
    assert policy.phase is ImplementationPhase.INVESTIGATE
    following = policy.request(budget.snapshot(), estimated_input=1000, now=140)
    assert following.phase is ImplementationPhase.IMPLEMENT
    assert policy.handoff_start == fixed_handoff == 555
    assert following.deadline == 320 < fixed_handoff < policy.deadline == 600
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().used_total_tokens == 1100
    assert budget.snapshot().limit_model_calls == 40
    assert budget.snapshot().limit_total_tokens == 500000


@pytest.mark.parametrize("owner", [20.0, 90.0, 200.0, 600.0])
def test_investigation_preserves_owner_and_fixed_handoff_floor(owner):
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    policy = ImplementationPhaseController(bounded=True, snapshot=budget.snapshot(), max_steps=36,
        now=0, deadline=owner)
    handoff = policy.handoff_start
    admitted_at = policy.investigation_end - 0.01
    request = policy.request(budget.snapshot(), estimated_input=1000, now=admitted_at)
    assert request.phase is ImplementationPhase.INVESTIGATE
    assert request.deadline == min(admitted_at + 180, handoff)
    policy.admitted()
    policy.timeout(now=request.deadline)
    assert policy.handoff_start == handoff
    assert policy.deadline == owner
    assert request.deadline <= handoff < owner
    assert budget.snapshot().limit_model_calls == 40
    assert budget.snapshot().limit_total_tokens == 500000


def test_unowned_investigation_keeps_compatibility_timeout():
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    policy = ImplementationPhaseController(bounded=True, snapshot=budget.snapshot(), max_steps=36,
        now=0, deadline=None)
    assert policy.request(budget.snapshot(), estimated_input=1000, now=65).deadline == 110


def _clock(monkeypatch, *, start):
    clock = SimpleNamespace(value=start)
    facade = SimpleNamespace(monotonic=lambda: clock.value, perf_counter=time.perf_counter,
                             time=time.time, sleep=time.sleep)
    monkeypatch.setattr(agent_loop, "time", facade)
    monkeypatch.setattr(model_call_control, "time", facade)
    return clock


def test_healthy_investigation_response_reaches_real_edit_pytest_and_native_handoff(tmp_path, monkeypatch):
    (tmp_path / "candidate.py").write_text("def value():\n    return 1\n")
    (tmp_path / "test_candidate.py").write_text(
        "from candidate import value\n\ndef test_value():\n    assert value() == 2\n")
    base = time.monotonic()
    clock = _clock(monkeypatch, start=base + 65.45)
    owner, global_end = base + 520, base + 600
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    # Parent work remains charged on this same ledger; no retry allowance is added.
    for _ in range(4):
        lease = budget.reserve_model_call(12000)
        budget.record_model_call(input_tokens=12000, reservation=lease)
    assignment, order, store, channel, submit = _submission_tools(tmp_path)
    check_results = []
    tool_deadlines = []

    def run_check(check, context):
        tool_deadlines.append(context._task_deadline_monotonic)
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_candidate.py"],
            cwd=context.cwd, capture_output=True, text=True, timeout=30, check=False,
            env={**os.environ, "PYTHONPATH": str(tmp_path),
                 "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""})
        check_results.append(result.returncode)
        return {"checkId": check.check_id, "status": "passed" if result.returncode == 0 else "failed",
                "stdout": result.stdout, "stderr": result.stderr, "verification": "implementation_feedback"}

    capability = ExecutorCheckCapability((VerificationCheck(
        "public_value", "tests", ("python", "-m", "pytest", "-q", "test_candidate.py"), 30),),
        run_check, test_paths=("test_candidate.py",))
    registry = capability.bind_registry(ToolRegistry([read_file_tool, edit_file_tool, submit]))
    observations = []

    class Model:
        def next(self, messages, *, deadline_monotonic=None, **_kwargs):
            observations.append((deadline_monotonic, list(messages)))
            if len(observations) == 1:
                clock.value += 135  # Healthy response crosses both 45s and the phase admission threshold.
                name, identity, values = "read_file", "inspect", {"path": "candidate.py"}
            elif len(observations) == 2:
                assert any(m.get("role") == "tool_result" and m.get("toolUseId") == "inspect"
                           and not m.get("isError") for m in messages), (
                    "Healthy investigation response was discarded before its real read could execute.")
                assert any("phase: implement" in str(m.get("content")) for m in messages)
                clock.value += 1
                name, identity, values = "edit_file", "edit", {
                    "path": "candidate.py", "old": "return 1", "new": "return 2"}
            else:
                assert len(observations) == 3, "The same worker must end immediately after submission."
                assert check_results == [0]
                assert any(m.get("role") == "tool_result" and m.get("toolUseId") == "edit"
                           and not m.get("isError") for m in messages)
                clock.value += 1
                name, identity, values = "executor_submit", "deliver", {
                    "status": "completed", "steps": [{"step": 1, "status": "completed"}],
                    "deviations": [], "blockers": []}
            return AgentStep(type="tool_calls", calls=[{
                "id": identity, "toolName": name, "input": values}],
                usage=ModelUsage(input_tokens=1000, output_tokens=100, source="estimated"))

    sink = RecordingSink()
    try:
        canonical = agent_loop.run_agent_turn(bounded_implementation_phases=True, model=Model(), tools=registry,
            messages=[{"role": "user", "content": "Correct value, run the public test, then submit."}],
            cwd=str(tmp_path), implementation_task=True, enable_work_chain=False,
            max_steps=36, agent_budget=budget, deadline_monotonic=owner,
            subagent_mailbox=channel, event_sink=sink)
        assert len(observations) == 3
        assert all(deadline <= owner - 45 for deadline, _messages in observations)
        assert tool_deadlines and all(deadline <= owner - 45 for deadline in tool_deadlines)
        assert (tmp_path / "candidate.py").read_text() == "def value():\n    return 2\n"
        results = {m["toolUseId"]: m for m in canonical if m.get("role") == "tool_result"}
        assert set(results) == {"inspect", "edit", "deliver"}
        assert not any(m.get("isError") for m in results.values())
        assert not any(e.event_type == "model.failed" for e in sink.events)
        declaration = json.loads(store.read(channel.write_key).content)
        assert validate_executor_declaration(assignment, declaration,
            implementation_order=order)["status"] == "completed"
        assert budget.snapshot().used_model_calls == 7
        assert budget.snapshot().used_total_tokens == 48000 + 3300
        assert budget.snapshot().limit_model_calls == 40
        assert budget.snapshot().limit_total_tokens == 500000
        assert clock.value < owner < global_end
    finally:
        capability.close()
        channel.close()


def test_expired_owner_still_rejects_response_before_any_edit(tmp_path, monkeypatch):
    (tmp_path / "candidate.py").write_text("value = 1\n")
    base = time.monotonic()
    clock = _clock(monkeypatch, start=base)
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)

    class LateModel:
        def next(self, _messages, **_kwargs):
            clock.value = base + 91
            return AgentStep(type="tool_calls", calls=[{"id": "late", "toolName": "edit_file",
                "input": {"path": "candidate.py", "old": "value = 1", "new": "value = 2"}}])

    with pytest.raises(agent_loop.AgentTurnDeadlineExceeded):
        agent_loop.run_agent_turn(bounded_implementation_phases=True, model=LateModel(), tools=ToolRegistry([edit_file_tool]),
            messages=[{"role": "user", "content": "Change the value."}], cwd=str(tmp_path),
            implementation_task=True, enable_work_chain=False, max_steps=36,
            agent_budget=budget, deadline_monotonic=base + 90)
    assert (tmp_path / "candidate.py").read_text() == "value = 1\n"
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().limit_model_calls == 40
    assert budget.snapshot().limit_total_tokens == 500000
