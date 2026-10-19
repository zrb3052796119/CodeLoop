"""A successful edit must not erase the recovery need of a real failed check.

Scripted decisions exercise the real Executor, file tools, pytest feedback and
native declaration. Messages and schemas are charged as estimated. The V26
remaining ledger/floor is a controlled boundary, not a replay of its requests.
"""

import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from dataclasses import replace

import pytest
from test_executor_session_continuity import _context, _input, _session
from test_implementation_finalize_deadline import _submission_tools

from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import VerificationCheck
from minicode.executor_verification import ExecutorCheckCapability
from minicode.implementation_execution import ImplementationSpec
from minicode.tooling import ToolRegistry
from minicode.tools import task as task_module
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelUsage
from minicode.workflow_runner import _WorkerCallBudget


def _exercise_failed_check_recovery(tmp_path, monkeypatch, *, remaining_control):
    (tmp_path / "candidate.py").write_text(
        "from dependency_a import helper_a\nfrom dependency_b import helper_b\n\n"
        "def value():\n    return 1\n\n"
        "def preserve_helpers():\n    return helper_a(), helper_b()\n")
    for suffix in ("a", "b"):
        (tmp_path / f"dependency_{suffix}.py").write_text(
            f"def helper_{suffix}():\n    return 'unchanged-{suffix}'\n\n"
            + "\n".join(f"# Historical source window {line}: preserve the independent helper behavior."
                         for line in range(500)) + "\n")
    owner = object()
    session = _session(owner, tmp_path)
    deadline = time.monotonic() + 600
    context = _context(tmp_path, _task_deadline_monotonic=deadline,
        _logical_agent_max_model_calls=28, _implementation_analysis_complete=True,
        _logical_agent_scope={"read_roots": (".",),
            "write_paths": ("candidate.py", "test_candidate.py"), "forbidden_paths": (".git",)})
    budget = context._agent_budget
    worker = _WorkerCallBudget(budget, "assignment-failed-check", max_calls=28,
                              preserve_calls=10, preserve_tokens=73984)
    assignment, _order, store, channel, submit = _submission_tools(tmp_path)
    check = VerificationCheck("value", "tests",
        ("python", "-m", "pytest", "-q", "test_candidate.py"), 30)
    checks, seen, prices, models, bills, recovery_snapshots = [], [], [], [], [], []

    def run_check(public_check, tool_context):
        completed = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_candidate.py"],
            cwd=tool_context.cwd, capture_output=True, text=True, timeout=30, check=False,
            env={**os.environ, "PYTHONPATH": str(tmp_path), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                 "PYTEST_ADDOPTS": ""})
        checks.append(completed.returncode)
        if len(checks) == 1:
            assert completed.returncode != 0 and not (tmp_path / "test_candidate.py").exists()
            if remaining_control:
                # Account for prior host work without crediting back any fixture
                # model charge. Keep the real V26 remaining allowance and floor.
                prior = 368846 - budget.snapshot().used_total_tokens
                assert prior >= 0
                budget.record_model_call(input_tokens=prior)
            assert worker.snapshot().limit_total_tokens == 426016
            recovery_snapshots.append(worker.snapshot())
            if remaining_control:
                assert worker.snapshot().limit_total_tokens - worker.snapshot().used_total_tokens == 57170
            else:
                assert worker.snapshot().used_total_tokens == sum(i + o for i, o in bills)
                assert worker.snapshot().limit_total_tokens - worker.snapshot().used_total_tokens > 300000
        return {"checkId": public_check.check_id,
                "status": "passed" if completed.returncode == 0 else "failed",
                "stdout": completed.stdout, "stderr": completed.stderr,
                "verification": "implementation_feedback"}

    capability = ExecutorCheckCapability((check,), run_check,
        test_paths=("test_candidate.py",), spec=ImplementationSpec(
            ("candidate.py", "test_candidate.py"), (check,),
            "Make value return 2 and create its public regression test. Preserve helper_a and helper_b."))

    class Model:
        tools = None

        def next(self, messages, tools_override=None, **_kwargs):
            tools = tools_override or self.tools
            names = {tool.name for tool in tools.list()}
            seen.append((deepcopy(messages), names))
            number = len(seen)
            if number in {1, 2}:
                windows = [(10, 8000), (120, 8000), (230, 8000)] if number == 1 else [
                    (450, 160), (460, 160), (470, 160), (480, 160)]
                calls = [{"id": f"read-{number}-{index}", "toolName": "read_file",
                          "input": {"path": "dependency_a.py", "offset": offset, "limit": limit}}
                         for index, (offset, limit) in enumerate(windows)]
            elif number == 3:
                name, values = "edit_file", {"path": "candidate.py", "old": "return 1", "new": "return 2"}
            elif number == 4 and "executor_test" in names:
                name, values = "executor_test", {"path": "test_candidate.py",
                    "code": "from candidate import value\n\ndef test_value():\n    assert value() == 2\n"}
            else:
                assert number in {4, 5}
                completed = number == 5 and checks[-1] == 0
                name, values = "executor_submit", {
                    "status": "completed" if completed else "blocked",
                    "steps": [{"step": 1, "status": "completed" if completed else "failed"}],
                    "deviations": [], "blockers": [] if completed else ["The required test tool is unavailable."]}
            if number > 2:
                calls = [{"id": f"call-{number}", "toolName": name, "input": values}]
            step = AgentStep(type="tool_calls", calls=calls)
            schemas = [{"type": "function", "function": {"name": tool.name,
                "description": tool.description, "parameters": tool.input_schema}} for tool in tools.list()]
            sent = sum(estimate_message_tokens(message) for message in messages)
            input_tokens = sent + estimate_tokens(json.dumps(schemas))
            output_tokens = estimate_tokens(json.dumps(step.calls))
            step.usage = ModelUsage(input_tokens=input_tokens, output_tokens=output_tokens, source="estimated")
            bills.append((input_tokens, output_tokens))
            prices.append({"call": number, "requestInputEstimate": sent,
                "billedInputEstimateWithSchemas": input_tokens,
                "canonicalInputEstimate": sum(estimate_message_tokens(message)
                    for message in session.context_manager.messages)})
            return step

    def factory(*_args, **kwargs):
        model = Model()
        model.tools = kwargs["tools"]
        models.append(model)
        return model

    monkeypatch.setattr("minicode.tools.create_default_tool_registry", lambda *_a, **_k:
                        ToolRegistry([read_file_tool, edit_file_tool]))
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    try:
        data = {**_input(session, owner,
            "Make value() return 2 and add test_candidate.py asserting that public behavior. "
            + "Preserve all unrelated documented helper behavior. " * 1400),
            "_executor_submit_tool": submit, "_executor_check_capability": capability,
            "_result_key": channel.write_key, "_result_instruction": "Submit only after the actual check result."}
        result = task_module._run(data, replace(context, _agent_budget=worker, _subagent_mailbox=channel))
        print(json.dumps({"kind": "local-failed-check-admission-control" if remaining_control else "early-failed-check-view",
            "source": "estimated", "downstreamFloor": 73984,
            "remainingBeforeRecovery": recovery_snapshots[0].limit_total_tokens - recovery_snapshots[0].used_total_tokens,
            "prices": prices,
            "remainingAfter": 500000 - budget.snapshot().used_total_tokens}, sort_keys=True))
        assert len(seen) >= 4, result.output
        if "executor_test" not in seen[3][1]:
            assert json.loads(store.read(channel.write_key).content)["status"] == "blocked"
            assert checks == [4] and not (tmp_path / "test_candidate.py").exists()
        assert "executor_test" in seen[3][1], (
            "The real failed check must attempt the existing recovery view before submit-only closure.", prices)
        assert result.ok, result.output
        assert len(seen) == 5 and len(models) == 1
        assert checks == [4, 0]
        declaration = json.loads(store.read(channel.write_key).content)
        assert declaration["assignmentId"] == assignment.assignment_id
        assert declaration["status"] == "completed"
        canonical = session.context_manager.messages
        packet = next(message["content"] for message in canonical if message.get("_implementation_context"))
        assert "helper_a" in packet and "helper_b" in packet
        projected = next(message["content"] for message in seen[3][0] if message.get("_implementation_context"))
        assert projected.split("FILE dependency_a.py", 1)[1] == packet.split("FILE dependency_a.py", 1)[1]
        retired_read = next(message for message in seen[3][0]
            if message.get("role") == "tool_result" and message.get("toolUseId") == "read-1-0")
        assert "omitted from this repair request" in retired_read["content"]
        original_failure = next(message for message in canonical
            if message.get("role") == "tool_result" and message.get("toolUseId") == "call-3")
        assert original_failure in seen[3][0]
        assert original_failure.get("_implementation_checks_failed") is True
        assert "file or directory not found" in original_failure["content"]
        if remaining_control:
            assert budget.snapshot().used_total_tokens == 368846 + sum(i + o for i, o in bills[3:])
        else:
            assert budget.snapshot().used_total_tokens == sum(i + o for i, o in bills)
            assert prices[3]["requestInputEstimate"] < prices[3]["canonicalInputEstimate"] - 1500
            assert prices[4]["requestInputEstimate"] < prices[4]["canonicalInputEstimate"] - 1500
            # These are actual smaller requests with actual schema-based bills;
            # no synthetic historical charge overwrites the cumulative saving.
            assert budget.snapshot().used_total_tokens < sum(
                i + o + prices[index]["canonicalInputEstimate"] - prices[index]["requestInputEstimate"]
                for index, (i, o) in enumerate(bills))
        assert worker.snapshot().limit_total_tokens == 426016
        assert budget.snapshot().limit_total_tokens == 500000
        assert budget.snapshot().limit_model_calls == 40
        assert budget.snapshot().used_model_calls == 5
        assert budget.snapshot().reserved_total_tokens == 0
        assert 500000 - budget.snapshot().used_total_tokens >= 73984
        assert context._task_deadline_monotonic == deadline
    finally:
        capability.close()
        channel.close()
        session.close()


def test_successful_edit_with_failed_real_check_can_create_test_before_handoff(tmp_path, monkeypatch):
    _exercise_failed_check_recovery(tmp_path, monkeypatch, remaining_control=True)


def test_failed_real_check_retires_old_reads_before_budget_pressure(tmp_path, monkeypatch):
    _exercise_failed_check_recovery(tmp_path, monkeypatch, remaining_control=False)


def test_failed_check_remains_pending_after_successful_edit_but_not_after_pass_or_handoff():
    from types import SimpleNamespace

    from minicode.implementation_phases import ImplementationPhaseController

    snapshot = SimpleNamespace(limit_total_tokens=426016, used_total_tokens=368846,
        reserved_total_tokens=0, limit_model_calls=30, used_model_calls=15)
    policy = ImplementationPhaseController(bounded=True, snapshot=snapshot, max_steps=28, now=0,
        deadline=514, analysis_complete=True, allow_test_completion=True)
    assert not policy.repair_pending
    policy.observe_edit(path="candidate.py", anchor_failed=True, changed=False)
    policy.observe_verification(failed=True)
    policy.observe_edit(path="candidate.py", anchor_failed=False, changed=True)
    assert not policy.edit_repair_pending
    assert policy.repair_pending, "A successful edit cannot dismiss its failed public check."
    policy.observe_verification(failed=False)
    assert not policy.repair_pending
    with pytest.raises(ValueError, match="host boolean"):
        policy.observe_verification(failed="true")
    policy.observe_verification(failed=True)
    request = policy.request(snapshot, estimated_input=30000, now=367)
    assert request.handoff_only
    policy.admitted()
    assert not policy.repair_pending
    assert policy.exhausted


def test_late_projection_keeps_unmodified_initial_evidence_and_recent_failure(tmp_path):
    from test_implementation_progress_projection import _pair, _source_packet

    from minicode.implementation_recovery import (
        project_implementation_progress_messages,
        project_recovery_messages,
    )

    packet = _source_packet(tmp_path)
    messages = [{"role": "user", "content": packet, "_implementation_context": True}]
    messages += _pair("old-window", "read_file", "app.py", "old window " * 1000)
    for index in range(5):
        messages += _pair(f"recent-{index}", "read_file", "app.py", f"actual recent window {index}")
    messages += _pair("failure", "executor_check", "app.py", "actual failed test receipt", failed=True)
    before = deepcopy(messages)
    early = project_implementation_progress_messages(messages, changed_paths=frozenset({"app.py"}))
    late = project_recovery_messages(messages, changed_paths=frozenset({"app.py"}))
    assert late[0] == early[0]
    assert "retain this dependency" in late[0]["content"]
    assert late[-1] == messages[-1]
    assert "omitted from this repair request" in late[2]["content"]
    assert messages == before
    assert project_recovery_messages(late, changed_paths=frozenset({"app.py"})) == late
    unchanged = project_recovery_messages(messages, changed_paths=frozenset())
    assert unchanged[0] == messages[0]


def test_soft_landing_uses_the_same_retained_initial_evidence_view(tmp_path):
    from test_implementation_progress_projection import _source_packet

    from minicode.agent_budget import AgentTurnBudget
    from minicode.agent_loop import run_agent_turn
    from minicode.executor_session import ExecutorRequestViewState

    packet = _source_packet(tmp_path)
    seen = []

    class Model:
        def next(self, messages, **_kwargs):
            seen.append(deepcopy(messages))
            return AgentStep(type="assistant", content="Partial result: the work remains incomplete.",
                usage=ModelUsage(input_tokens=sum(estimate_message_tokens(message) for message in messages),
                                 output_tokens=20, source="estimated"))

    model = Model()
    state = ExecutorRequestViewState(model=model)
    # A host-owned request view can survive an Assignment boundary; its active
    # flag grants no tools, changed paths, budget, or permission here.
    state.recovery_view_active = True
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    canonical = run_agent_turn(model=model, tools=ToolRegistry([read_file_tool]),
        messages=[{"role": "user", "content": "Preserve the inspected API evidence."},
                  {"role": "user", "content": packet, "_implementation_context": True}],
        cwd=str(tmp_path), implementation_task=True, implementation_request_state=state,
        max_steps=0, max_steps_soft_landing=True, agent_depth=1,
        enable_work_chain=False, agent_budget=budget)
    assert len(seen) == 1
    assert next(message["content"] for message in seen[0] if message.get("_implementation_context")) == packet
    assert next(message["content"] for message in canonical if message.get("_implementation_context")) == packet
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().used_total_tokens == sum(estimate_message_tokens(message) for message in seen[0]) + 20
