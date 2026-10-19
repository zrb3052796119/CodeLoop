"""V22's admitted test response must not lose a second, fractional time slice.

The model response timing is deterministic. File tools, approved pytest feedback,
the shared budget, and the native result declaration below are real production
boundaries; this test does not claim a live model will produce the fixture code.
"""

import json
import os
import subprocess
import sys
import time

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.deterministic_verifier import VerificationCheck
from minicode.executor_result_submission import ExecutorResultChannel, create_executor_submit_tool
from minicode.executor_verification import ExecutorCheckCapability
from minicode.implementation_phases import (
    ImplementationPhase, ImplementationPhaseController, PhaseCallDeadlineExceeded,
)
from minicode.multi_agent_contracts import (
    parse_assignment, parse_implementation_order, validate_executor_declaration,
)
from minicode.scheduler_artifacts import SchedulerArtifactStore
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.edit_file import edit_file_tool


# Relative host timing reported for V22. The first FINALIZE admission locks
# 31.001s for delivery; R13 must not halve the remaining owner time again.
OWNER = 516.035
FINALIZE_ENTRY = 423.032
TEST_REQUEST = 439.209
OLD_REQUEST_END = 477.623
HANDOFF = 485.034
GLOBAL = 600.0


def _policy(*, base=0.0):
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    # Represent earlier real work without giving the focused reproduction a
    # new ledger or replenishing any of its reservations.
    for _ in range(10):
        lease = budget.reserve_model_call(25000)
        budget.record_model_call(input_tokens=25000, reservation=lease)
    policy = ImplementationPhaseController(bounded=True,
        snapshot=budget.snapshot(), max_steps=27, now=base + 41.368,
        deadline=base + OWNER, analysis_complete=True, allow_test_completion=True,
        allow_checked_source_completion=True, require_structured_handoff=True,
    )
    return policy, budget


def _admit(policy, budget, request, *, input_tokens, output_tokens):
    lease = budget.reserve_model_call(input_tokens,
        preserve_model_calls=request.preserve_calls,
        preserve_total_tokens=request.preserve_tokens)
    policy.admitted()
    budget.record_model_call(input_tokens=input_tokens, output_tokens=output_tokens,
                             reservation=lease)


def _enter_final(policy, budget, *, base=0.0):
    policy.observe_verification(failed=True)
    first = policy.request(budget.snapshot(), estimated_input=21564, now=base + FINALIZE_ENTRY)
    assert first.phase is ImplementationPhase.FINALIZE
    assert policy.handoff_start == pytest.approx(base + HANDOFF)
    _admit(policy, budget, first, input_tokens=21564, output_tokens=384)
    return first


def test_v22_test_generation_uses_the_already_committed_work_window():
    policy, budget = _policy()
    _enter_final(policy, budget)
    request = policy.request(budget.snapshot(), estimated_input=22487, now=TEST_REQUEST)
    assert not request.handoff_only
    assert request.deadline == pytest.approx(HANDOFF), (
        "healthy executor_test generation was cut at a fresh remaining-time/calls slice",
        request.deadline,
    )
    assert request.deadline > OLD_REQUEST_END
    assert request.output_limit == 8192
    assert request.preserve_calls == 1
    assert policy.deadline == OWNER < GLOBAL


def test_final_request_limit_does_not_resplit_the_locked_time_window():
    policy, budget = _policy()
    policy.observe_verification(failed=False)
    first = policy.request(budget.snapshot(), estimated_input=1000, now=FINALIZE_ENTRY)
    _admit(policy, budget, first, input_tokens=1000, output_tokens=100)
    second = policy.request(budget.snapshot(), estimated_input=1000, now=TEST_REQUEST)
    assert first.preserve_calls == 2
    assert second.preserve_calls == 1
    assert first.deadline == second.deadline == pytest.approx(HANDOFF)
    assert policy.handoff_start == pytest.approx(HANDOFF)
    # Later failures may reuse remaining work, but never move the promise.
    for offset in range(8):
        policy.observe_verification(failed=True)
        followup = policy.request(budget.snapshot(), estimated_input=1000, now=478 + offset)
        assert policy.handoff_start == pytest.approx(HANDOFF)
        assert followup.deadline <= (OWNER if followup.handoff_only else HANDOFF)
    closing = policy.request(budget.snapshot(), estimated_input=1000, now=HANDOFF)
    assert closing.handoff_only and closing.deadline == OWNER


def _submission_tools(tmp_path):
    assignment = parse_assignment({
        "assignmentVersion": 1, "workflowId": "wf_phase_deadline", "taskId": "task_phase_deadline",
        "assignmentId": "asg_phase_deadline", "role": "executor", "mode": "implement",
        "objective": "Return the corrected value and test it.",
        "scope": {"readRoots": ["."], "writePaths": ["candidate.py", "test_candidate.py"], "forbiddenPaths": [".git", ".env"]},
        "constraints": [], "acceptanceCriteria": [{"id": "ac_value", "kind": "behavior", "text": "value returns 2"}],
        "inputRefs": [], "risk": "low",
        "budget": {"deadlineAt": "2026-10-16T12:00:00Z", "maxModelCalls": 27, "maxInputTokens": 500000, "maxOutputTokens": 8192},
        "expectedResultType": "ExecutionResult.v1",
    })
    order = parse_implementation_order(assignment, {
        "orderVersion": 1, "orderId": "order_phase_deadline", "assignmentId": assignment.assignment_id,
        "baseDigest": "sha256:" + "a" * 64, "allowedPaths": ["candidate.py", "test_candidate.py"],
        "steps": [{"id": "step_value", "action": "Correct value and add its regression test.",
                   "allowedPaths": ["candidate.py", "test_candidate.py"], "criterionIds": ["ac_value"]}],
        "criterionIds": ["ac_value"], "verificationRequests": ["pytest -q test_candidate.py"],
    })
    store = SchedulerArtifactStore(scheduler_authority=object())
    channel = ExecutorResultChannel(store.create_assignment_channel(
        readable_keys=(), write_key="artifact_phase_result"), assignment, order)
    submit = create_executor_submit_tool(assignment, order, channel)
    return assignment, order, store, channel, submit


def test_v22_healthy_test_response_runs_real_check_then_native_handoff(tmp_path):
    (tmp_path / "candidate.py").write_text("def value():\n    return 1\n")
    base = time.monotonic()
    policy, budget = _policy(base=base)
    assignment, order, store, channel, submit = _submission_tools(tmp_path)
    checks = []

    def run_check(check, context):
        assert context._task_deadline_monotonic == pytest.approx(base + HANDOFF)
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_candidate.py"],
            cwd=context.cwd, capture_output=True, text=True, timeout=30,
            env={**os.environ, "PYTHONPATH": str(tmp_path), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""})
        checks.append(result.returncode)
        return {"checkId": check.check_id, "status": "passed" if result.returncode == 0 else "failed",
                "stdout": result.stdout, "stderr": result.stderr, "verification": "implementation_feedback"}

    capability = ExecutorCheckCapability((VerificationCheck(
        "public_value", "tests", ("python", "-m", "pytest", "-q", "test_candidate.py"), 30),),
        run_check, test_paths=("test_candidate.py",))
    registry = capability.bind_registry(ToolRegistry([edit_file_tool, submit]))
    context = ToolContext(cwd=str(tmp_path), _subagent_mailbox=channel,
                          _task_deadline_monotonic=base + OWNER)
    _enter_final(policy, budget, base=base)
    capability.reserve_handoff_time(policy.handoff_start)
    assert "edit_file" in {tool.name for tool in policy.tool_view(registry).list()}
    edited = registry.execute("edit_file", {"path": "candidate.py", "old": "return 1", "new": "return 2"}, context)
    assert edited.ok and edited._file_changed
    assert edited._implementation_checks_failed, "the declared test path is still absent"
    policy.observe_verification(failed=edited._implementation_checks_failed)

    request = policy.request(budget.snapshot(), estimated_input=22487, now=base + TEST_REQUEST)
    assert "executor_test" in {tool.name for tool in policy.tool_view(registry).list()}
    assert request.output_limit == 8192 and request.preserve_calls == 1
    _admit(policy, budget, request, input_tokens=22487, output_tokens=1000)
    # One healthy response needs 42.791s, still inside the precommitted work
    # window. The V22 controller cancels it after 38.414s and never sees code.
    response_finished = base + 482.0
    if response_finished >= request.deadline:
        raise PhaseCallDeadlineExceeded("healthy executor_test response cut before the fixed handoff floor")
    tested = registry.execute("executor_test", {
        "path": "test_candidate.py",
        "code": "from candidate import value\n\ndef test_value():\n    assert value() == 2\n",
    }, context)
    assert tested.ok, tested.output
    assert tested._file_changed and tested._implementation_checks_failed is False
    assert checks == [4, 0]
    policy.observe_verification(failed=False)

    handoff = policy.request(budget.snapshot(), estimated_input=23931, now=base + 483.5)
    assert handoff.handoff_only
    assert handoff.output_limit == 2048
    assert handoff.deadline == base + OWNER
    assert policy.handoff_start == pytest.approx(base + HANDOFF)
    assert {tool.name for tool in policy.tool_view(registry).list()} == {"executor_submit"}
    _admit(policy, budget, handoff, input_tokens=23931, output_tokens=100)
    submitted = registry.execute("executor_submit", {
        "status": "completed", "steps": [{"step": 1, "status": "completed"}],
        "deviations": [], "blockers": [],
    }, context)
    assert submitted.ok and submitted._implementation_submitted
    declaration = json.loads(store.read(channel.write_key).content)
    assert validate_executor_declaration(assignment, declaration, implementation_order=order)["status"] == "completed"
    assert policy.exhausted
    assert budget.snapshot().used_model_calls == 13
    assert budget.snapshot().used_total_tokens == 250000 + 21564 + 384 + 22487 + 1000 + 23931 + 100
    assert budget.snapshot().limit_model_calls == 40
    assert budget.snapshot().limit_total_tokens == 500000
    assert policy.deadline == base + OWNER < base + GLOBAL
    capability.close()
    channel.close()


def test_finalization_keeps_normal_request_ceiling_when_owner_window_is_long():
    policy, budget = _policy()
    policy.calls = policy.call_limit - policy.final_call_limit
    request = policy.request(budget.snapshot(), estimated_input=1000, now=50.0)
    assert request.phase is ImplementationPhase.FINALIZE and not request.handoff_only
    assert request.deadline == 230.0
    assert request.output_limit == 8192
    assert policy.handoff_start == OWNER - 45
