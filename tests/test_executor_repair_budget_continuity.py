"""Spend a fitting final repair inside unchanged shared/downstream allowances.

Historical counts locate the extended-time-v1 budget boundary. Scripted model
choices are generic toy code, and new messages/schemas are honestly metered as
estimated usage. This is not a replay of its unrecorded next input estimate.
"""

import json
import subprocess
import sys
import threading
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_executor_session_continuity import _context, _input, _session
from test_implementation_finalize_deadline import _submission_tools

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_phases import ImplementationPhaseController
from minicode.implementation_recovery import InputTokenCalibration
from minicode.permissions import PermissionManager
from minicode.reviewer_verification import ReviewerProbeLedger
from minicode.tooling import ToolRegistry
from minicode.tools import task as task_module
from minicode.tools.edit_file import edit_file_tool
from minicode.types import AgentStep, ModelUsage
from minicode.workflow_runner import _WorkerCallBudget, _WorkflowRunSession
from minicode.workflow_workspace import WorkflowWorkspaceTransaction

PREFIX_USED = 443427
DOWNSTREAM_FLOOR = 17684
EFFECTIVE_REMAINING = 38889


def _budget_policy(*, used=PREFIX_USED, deadline=1800, output_limit=8192, limited=True):
    budget = AgentTurnBudget(max_model_calls=40,
                             max_total_tokens=500000 if limited else None)
    budget.record_model_call(input_tokens=used)
    worker = _WorkerCallBudget(budget, "executor", max_calls=12, preserve_calls=10,
                              preserve_tokens=DOWNSTREAM_FLOOR)
    policy = ImplementationPhaseController(bounded=True, snapshot=worker.snapshot(), max_steps=12,
        now=450, deadline=deadline, analysis_complete=True, output_limit=output_limit,
        allow_test_completion=True, allow_checked_source_completion=True,
        allow_conditional_handoff=True, require_structured_handoff=True)
    policy.observe_change(True)
    policy.observe_verification(failed=True)
    return budget, worker, policy


def test_final_repair_uses_available_output_space_without_borrowing_downstream_floor():
    budget, worker, policy = _budget_policy()
    request = policy.request(worker.snapshot(), estimated_input=36500, now=451)
    assert request.conditional_handoff
    lease = worker.reserve_model_call(36500, preserve_model_calls=request.preserve_calls,
                                      preserve_total_tokens=request.preserve_tokens)
    policy.admitted()
    budget.record_model_call(input_tokens=36500, output_tokens=request.output_limit,
                             reservation=lease)
    assert request.output_limit == 2389
    assert budget.snapshot().used_total_tokens == 500000 - DOWNSTREAM_FLOOR
    assert budget.snapshot().reserved_total_tokens == 0
    assert request.deadline <= policy.handoff_start < policy.deadline == 1800


@pytest.mark.parametrize("remaining", [0, -1, 1])
def test_no_input_or_response_space_stays_fail_closed(remaining):
    budget, worker, policy = _budget_policy()
    input_tokens = EFFECTIVE_REMAINING - remaining
    request = policy.request(worker.snapshot(), estimated_input=input_tokens, now=451)
    assert 1 <= request.output_limit <= 4096
    before = budget.snapshot()
    if remaining <= 0:
        with pytest.raises(AgentBudgetExceeded, match="token budget"):
            worker.reserve_model_call(input_tokens, preserve_total_tokens=request.preserve_tokens)
        assert budget.snapshot() == before
    else:
        lease = worker.reserve_model_call(input_tokens, preserve_total_tokens=request.preserve_tokens)
        assert request.output_limit == 1
        budget.fail_model_call(lease)
        assert budget.snapshot().reserved_total_tokens == 0


@pytest.mark.parametrize("deadline,limited,configured", [(None, False, 8192), (1800, True, 8192),
                                                         (None, True, 1024)])
def test_sufficient_budget_keeps_the_existing_output_ceiling(deadline, limited, configured):
    _, worker, policy = _budget_policy(used=0, deadline=deadline,
                                      output_limit=configured, limited=limited)
    request = policy.request(worker.snapshot(), estimated_input=1000, now=451)
    assert 0 < request.output_limit <= configured
    assert request.output_limit == configured


def test_calibrated_input_and_current_reservations_are_not_spendable_output():
    budget, worker, policy = _budget_policy()
    reservation = budget.reserve_model_call(1000)
    calibration = InputTokenCalibration()
    calibration.observe(36500, 30000)
    estimated = calibration.estimate(30000)
    request = policy.request(worker.snapshot(), estimated_input=estimated, now=451)
    assert request.output_limit == EFFECTIVE_REMAINING - 1000 - estimated
    lease = worker.reserve_model_call(estimated, preserve_total_tokens=request.preserve_tokens)
    budget.record_model_call(input_tokens=estimated, output_tokens=request.output_limit,
                             reservation=lease)
    assert budget.snapshot().used_total_tokens + budget.snapshot().reserved_total_tokens == 482316
    budget.fail_model_call(reservation)


def test_atomic_admission_rechecks_concurrent_consumers_after_output_pricing():
    budget, worker, policy = _budget_policy()
    request = policy.request(worker.snapshot(), estimated_input=36500, now=451)
    other = budget.reserve_model_call(1)
    before = budget.snapshot()
    with pytest.raises(AgentBudgetExceeded, match="token budget"):
        worker.reserve_model_call(36500, preserve_total_tokens=request.preserve_tokens)
    assert budget.snapshot() == before
    budget.fail_model_call(other)


def test_expired_work_window_never_reopens_repairs():
    _, worker, policy = _budget_policy(deadline=452)
    policy.request(worker.snapshot(), estimated_input=36500, now=451)
    boundary = policy.handoff_start
    request = policy.request(worker.snapshot(), estimated_input=36500, now=boundary)
    assert request.handoff_only and not request.conditional_handoff
    assert not policy.allowed_name("edit_file")
    assert request.deadline == 452 and policy.handoff_start == boundary


def test_same_executor_real_failed_check_then_fitting_repair_and_native_submit(tmp_path, monkeypatch):
    actual_turn = task_module.run_agent_turn

    def bounded_turn(**kwargs):
        kwargs["bounded_implementation_phases"] = True
        return actual_turn(**kwargs)

    monkeypatch.setattr(task_module, "run_agent_turn", bounded_turn)
    (tmp_path / "candidate.py").write_text("def value():\n    return 1\n")
    (tmp_path / "test_candidate.py").write_text(
        "from pathlib import Path\ndef test_value():\n"
        "    namespace = {}\n    exec(Path('candidate.py').read_text(), namespace)\n"
        "    assert namespace['value']() == 2\n")
    deadline = time.monotonic() + 1800
    parent = _context(tmp_path, _task_deadline_monotonic=deadline)
    budget = parent._agent_budget
    for _ in range(17):
        lease = budget.reserve_model_call()
        budget.record_model_call(reservation=lease)
    worker = _WorkerCallBudget(budget, "same-executor", max_calls=12, preserve_calls=10,
                              preserve_tokens=DOWNSTREAM_FLOOR)
    assignment, _order, store, channel, submit = _submission_tools(tmp_path)
    check = VerificationCheck("value", "tests", ("pytest", "-q", "test_candidate.py"), 30)
    checks, observed, bills, models, decisions = [], [], [], [], []

    def command(argv, **kwargs):
        completed = subprocess.run([sys.executable, "-m", *argv],
                                   check=kwargs.pop("check", False), **kwargs)
        checks.append(completed.returncode)
        if len(checks) == 1:
            assert completed.returncode == 1
            prior = PREFIX_USED - budget.snapshot().used_total_tokens
            assert prior > 0
            budget.record_model_call(input_tokens=prior)
        return completed

    original_request = ImplementationPhaseController.request

    def observed_request(self, snapshot, **kwargs):
        decision = original_request(self, snapshot, **kwargs)
        if snapshot.used_total_tokens == PREFIX_USED:
            decisions.append({"estimatedInput": kwargs["estimated_input"],
                              "outputLimit": decision.output_limit,
                              "preserveTokens": decision.preserve_tokens})
        return decision

    monkeypatch.setattr(ImplementationPhaseController, "request", observed_request)
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command))
    monkeypatch.setattr("minicode.tools.create_default_tool_registry",
                        lambda *_a, **_k: ToolRegistry([edit_file_tool]))

    class Model:
        tools = None

        def next(self, messages, tools_override=None, max_output_tokens_override=None, **_kwargs):
            tools = tools_override or self.tools
            number = len(observed) + 1
            assert number <= 2
            observed.append((deepcopy(messages), max_output_tokens_override))
            calls = [{"id": f"edit-{number}", "toolName": "edit_file", "input": {
                "path": "candidate.py", "old": f"return {1 if number == 1 else 0}",
                "new": f"return {0 if number == 1 else 2}"}}]
            if number == 2:
                assert any(m.get("_implementation_checks_failed") for m in messages)
                calls.append({"id": "submit", "toolName": "executor_submit", "input": {
                    "status": "completed", "steps": [{"step": 1, "status": "completed"}],
                    "deviations": [], "blockers": []}})
            schemas = [{"type": "function", "function": {"name": t.name,
                "description": t.description, "parameters": t.input_schema}} for t in tools.list()]
            sent = sum(estimate_message_tokens(m) for m in messages)
            input_tokens = sent + estimate_tokens(json.dumps(schemas))
            output_tokens = estimate_tokens(json.dumps(calls))
            bills.append({"sentInput": sent, "inputWithSchemas": input_tokens,
                          "outputEstimate": output_tokens})
            if number == 1:
                # Exercise the existing host calibration using these honestly
                # metered local estimates; never label them provider usage.
                session.request_view().calibration.observe(input_tokens, sent)
            assert output_tokens < max_output_tokens_override
            return AgentStep(type="tool_calls", calls=calls, usage=ModelUsage(
                input_tokens=input_tokens, output_tokens=output_tokens, source="estimated"))

    def factory(*_a, **kwargs):
        model = Model()
        model.tools = kwargs["tools"]
        models.append(model)
        return model

    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    with WorkflowWorkspaceTransaction(str(tmp_path), deadline_monotonic=deadline) as transaction:
        runner = _WorkflowRunSession.__new__(_WorkflowRunSession)
        runner._verification_outputs = None
        runner._workflow_journal = None
        runner._state = SimpleNamespace(transaction=transaction, context=parent,
            requested_checks=(check,), locked_write_paths=("candidate.py", "test_candidate.py"),
            workflow_request=SimpleNamespace(scope=assignment.scope),
            workflow_id=assignment.workflow_id, task_id=assignment.task_id,
            task_prompt="Repair value and pass the approved test.", implementation_check_runs=0,
            implementation_check_statuses={}, implementation_check_latest={},
            reviewer_probe_lock=threading.RLock(), reviewer_probes=ReviewerProbeLedger())
        capability = runner._executor_check_capability(assignment)
        owner = object()
        session = _session(owner, transaction.cwd)
        context = _context(transaction.cwd, _agent_budget=worker,
            _task_deadline_monotonic=deadline, _logical_agent_max_model_calls=12,
            _implementation_analysis_complete=True, _logical_agent_scope={"read_roots": (".",),
                "write_paths": ("candidate.py", "test_candidate.py"), "forbidden_paths": (".git",)})
        try:
            prompt = ("Repair value() to return 2; use its actual failed test. "
                      + "Shared authorized requirement: preserve unrelated interfaces. " * 2200)
            result = task_module._run({**_input(session, owner, prompt), "_executor_submit_tool": submit,
                "_executor_check_capability": capability, "_result_key": channel.write_key,
                "_result_instruction": "Use the bound native result channel."},
                replace(context, _subagent_mailbox=channel))
            print(json.dumps({"bills": bills, "decisions": decisions, "result": result.output[:200]}, sort_keys=True))
            assert len(observed) == 2, "The fitting repair was rejected before reaching the same model."
            assert result.ok, result.output
            assert len(models) == 1 and checks == [1, 0]
            assert 0 < observed[1][1] < 4096
            assert bills[1]["inputWithSchemas"] + observed[1][1] <= EFFECTIVE_REMAINING
            assert json.loads(store.read(channel.write_key).content)["status"] == "completed"
            assert "return 2" in Path(transaction.cwd, "candidate.py").read_text()
            assert (tmp_path / "candidate.py").read_text() == "def value():\n    return 1\n"
            assert budget.snapshot().used_total_tokens == PREFIX_USED + sum(bills[1][k] for k in ("inputWithSchemas", "outputEstimate"))
            assert budget.snapshot().used_model_calls == 19
            assert budget.snapshot().limit_total_tokens == 500000 and budget.snapshot().limit_model_calls == 40
            assert budget.snapshot().reserved_total_tokens == 0
            assert 500000 - budget.snapshot().used_total_tokens >= DOWNSTREAM_FLOOR
            assert context._task_deadline_monotonic == deadline
        finally:
            capability.close()
            runner._verification_outputs.close()
            channel.close()
            session.close()
