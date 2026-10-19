"""Repair cannot reset budgets, bypass gates, or turn safety failures into edits."""

from __future__ import annotations

from pathlib import Path

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.central_scheduler import (
    CentralScheduler, DispatchStage, SchedulerExecutionPolicy, StageResult,
    TaskKind, TaskProfile, TaskRisk,
)
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.tooling import ToolContext, ToolResult
from minicode.workflow_repair import (
    review_supports_implementation_repair,
    verification_supports_implementation_repair,
)


class RepairPorts:
    """Deterministic stage results; the real scheduler owns all repeat decisions."""

    def __init__(self, *, unsafe_error=None, expire=False):
        self.executors = []
        self.events = []
        self.commits = []
        self.patch = None
        self.clock = 0.0
        self.unsafe_error = unsafe_error
        self.expire = expire

    def worker(self, assignment):
        stage = assignment.stage
        if stage is DispatchStage.EXECUTOR:
            self.executors.append(assignment)
            self.patch = "sha256:" + str(len(self.executors)) * 64
        return StageResult(
            status="completed", artifact_id=f"artifact_{assignment.assignment_id}",
            patch_digest=self.patch,
            approved=(len(self.executors) != 2 if stage is DispatchStage.REVIEWER else None),
            repairable=stage is DispatchStage.REVIEWER and len(self.executors) == 2,
        )

    def verifier(self, assignment):
        failed = len(self.executors) == 1
        if self.expire:
            self.clock = 11.0
        return StageResult(
            status="failed" if failed else "completed",
            artifact_id=f"artifact_{assignment.assignment_id}",
            patch_digest=self.patch, repairable=failed,
            error_code=(self.unsafe_error or "verification_failed") if failed else None,
        )

    def human(self, assignment):
        return StageResult(
            status="completed", artifact_id=f"artifact_{assignment.assignment_id}",
            approved=True,
        )

    def commit(self, request):
        self.commits.append(request)
        return True

    def execute(self, *, limit=1, human=False):
        return CentralScheduler().execute(
            TaskProfile(
                task_id="task_repair_safety", kind=TaskKind.CHANGE,
                risk=TaskRisk.HIGH if human else TaskRisk.MEDIUM,
                complex=True, scope_clear=True, acceptance_executable=True,
                human_confirmation_required=human,
            ),
            workflow_id="wf_repair_safety", run_worker=self.worker,
            run_verifier=self.verifier, run_human=self.human, commit=self.commit,
            on_transition=self.events.append,
            policy=SchedulerExecutionPolicy(implementation_max_repairs=limit),
            deadline_monotonic=10.0, clock=lambda: self.clock,
        )


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_verifier_and_reviewer_share_one_workflow_repair_allowance(limit):
    ports = RepairPorts()
    outcome = ports.execute(limit=limit)
    # Revision 0 fails verification, revision 1 fails review, revision 2 passes.
    assert len(ports.executors) == limit + 1
    assert [item.repair_index for item in ports.executors] == list(range(limit + 1))
    assert len({item.assignment_id for item in ports.executors}) == limit + 1
    repairs = [event for event in ports.events if event.outcome == "repairing"]
    assert len(repairs) == limit
    assert all(
        assignment.repair_source_artifact_id == event.artifact_id
        for assignment, event in zip(ports.executors[1:], repairs)
    )
    assert outcome.committed is (limit == 2)
    assert len(ports.commits) == (1 if limit == 2 else 0)


@pytest.mark.parametrize("value", [-1, 3, True, False, "1"])
def test_repair_policy_rejects_unbounded_or_ambiguous_limits(value):
    with pytest.raises(ValueError):
        SchedulerExecutionPolicy(implementation_max_repairs=value)


@pytest.mark.parametrize("error_code", [
    "permission_denied", "verification_sandbox_unavailable",
    "verification_timeout", "verification_patch_digest_mismatch",
    "path_assignment_scope_denied",
])
def test_safety_failure_cannot_repair_even_if_port_mislabels_it(error_code):
    ports = RepairPorts(unsafe_error=error_code)
    outcome = ports.execute(limit=2)
    assert not outcome.committed
    assert len(ports.executors) == 1
    assert not any(event.outcome == "repairing" for event in ports.events)


def test_prior_human_approval_does_not_authorize_automatic_repair():
    ports = RepairPorts()
    outcome = ports.execute(limit=2, human=True)
    assert any(event.stage is DispatchStage.HUMAN and event.outcome == "completed"
               for event in ports.events)
    assert not outcome.committed
    assert len(ports.executors) == 1
    assert not any(event.outcome == "repairing" for event in ports.events)


def test_expired_global_deadline_cannot_start_a_repair():
    ports = RepairPorts(expire=True)
    outcome = ports.execute(limit=2)
    assert outcome.status == "timed_out"
    assert not outcome.committed
    assert len(ports.executors) == 1
    assert not any(event.outcome == "repairing" for event in ports.events)


@pytest.mark.parametrize("failure", [
    "timeout", "permission", "missing_evidence", "security", "inconclusive",
])
def test_only_executed_failing_commands_can_supply_repair_feedback(failure):
    result = {"status": "failed", "checks": [
        {"kind": "tests", "status": "failed", "exitCode": 1},
    ]}
    evidence = [{"status": "failed", "exitCode": 1, "failureReason": None}]
    assert verification_supports_implementation_repair(result, evidence)
    if failure in {"timeout", "permission"}:
        evidence[0]["failureReason"] = failure
    elif failure == "missing_evidence":
        evidence.clear()
    elif failure == "security":
        result["checks"][0]["kind"] = "security"
    else:
        result["checks"][0].update(status="inconclusive", exitCode=None)
        evidence[0].update(status="inconclusive", exitCode=None)
    assert not verification_supports_implementation_repair(result, evidence)


@pytest.mark.parametrize("failure", ["unknown", "new_check", "critical", "inconclusive"])
def test_review_reassessment_requests_cannot_become_implementation_repairs(failure):
    result = {
        "verdict": "changes_required", "criteria": [{"status": "failed"}],
        "requestedChecks": [], "blockingFindings": [],
    }
    assert review_supports_implementation_repair(result)
    if failure == "unknown":
        result["criteria"][0]["status"] = "unknown"
    elif failure == "new_check":
        result["requestedChecks"] = ["new acceptance check"]
    elif failure == "critical":
        result["blockingFindings"] = [{"severity": "critical"}]
    else:
        result["verdict"] = "inconclusive"
    assert not review_supports_implementation_repair(result)


@pytest.mark.parametrize("dimension", ["model_calls", "tokens", "cost"])
def test_failed_verification_cannot_reset_an_exhausted_shared_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dimension: str,
) -> None:
    import minicode.tools.task as task_module
    import minicode.workflow_runner as runner_module
    from test_workflow_v2_structured_routing import (
        _request, _verification_result, _write_executor_declaration,
    )

    budget = AgentTurnBudget(
        # Two Executor calls plus the v4 semantic Reviewer's work/closing
        # allowance and parent handoff reserve. Exhaust this same ledger
        # during verification; the test must reach the intended repair gate.
        max_model_calls=12,
        max_total_tokens=10000, max_cost_usd="1",
    )
    roles = []
    verifier_runs = []
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    def executor(input_data, context):
        roles.append(context._logical_agent_role)
        Path(context.cwd, "allowed.txt").write_text("candidate", encoding="utf-8")
        _write_executor_declaration(input_data, context)
        return ToolResult(ok=True, output="candidate ready")

    class FailingVerifier:
        def run(self, **request):
            verifier_runs.append(request["patch_digest"])
            publish = request["evidence_sink"]

            def failing_evidence(identity, document):
                publish(identity, {**document, "status": "failed", "exitCode": 1})

            result = _verification_result({**request, "evidence_sink": failing_evidence})
            result["status"] = "failed"
            result["checks"][0].update(status="failed", exitCode=1)
            # An independently completed sibling can exhaust the shared budget
            # while this verifier runs; repair must use the same authority.
            for _ in range(budget.snapshot().limit_model_calls if dimension == "model_calls" else 1):
                reservation = budget.reserve_model_call(0)
                budget.record_model_call(
                    input_tokens=10000 if dimension == "tokens" else 0,
                    cost_usd="1" if dimension == "cost" else "0",
                    reservation=reservation,
                )
            return result

    monkeypatch.setattr(task_module, "_run", executor)
    monkeypatch.setattr(runner_module, "_deterministic_verifier_for_context",
                        lambda *args, **kwargs: FailingVerifier())
    result = task_module.task_tool.run(
        task_module.task_tool.validator({
            "agent_type": "workflow", "description": "budget safety",
            "prompt": "Apply the scoped change.",
            "workflow_request": _request(risk="low", complex=False),
        }),
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"},
                    _agent_budget=budget, _workflow_commit_audit_waived=True),
    )
    assert not result.ok
    assert roles == ["executor"]
    assert len(verifier_runs) == 1, result.output
    assert target.read_text(encoding="utf-8") == "before"
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    assert trace["transitions"][-1]["stage"] == "verifier"
    assert trace["terminalReason"] in {"verification_failed", "acceptance_evidence_incomplete"}
    assert not any(event["outcome"] == "repairing" for event in trace["transitions"])
