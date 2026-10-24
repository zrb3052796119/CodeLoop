from __future__ import annotations

import pytest

from minicode.central_scheduler import (
    CentralScheduler,
    DispatchStage,
    TaskKind,
    TaskProfile,
    TaskRisk,
    StageResult,
)


def test_simple_low_risk_read_only_task_stays_with_parent() -> None:
    scheduler = CentralScheduler()

    plan = scheduler.plan(
        TaskProfile(
            task_id="task_simple",
            kind=TaskKind.READ_ONLY,
            risk=TaskRisk.LOW,
            complex=False,
            parent_has_sufficient_context=True,
        )
    )

    assert plan.stages == (DispatchStage.PARENT,)
    assert plan.rule_id == "route.parent_only"
    assert plan.requires_human is False


def test_parent_only_execution_honors_an_already_expired_global_deadline() -> None:
    transitions = []

    outcome = CentralScheduler().execute(
        TaskProfile(
            task_id="task_parent_deadline",
            kind=TaskKind.READ_ONLY,
            risk=TaskRisk.LOW,
            complex=False,
            parent_has_sufficient_context=True,
        ),
        workflow_id="wf_parent_deadline",
        run_worker=lambda _assignment: pytest.fail("not reached"),
        run_verifier=lambda _assignment: pytest.fail("not reached"),
        commit=lambda _request: pytest.fail("not reached"),
        on_transition=transitions.append,
        deadline_monotonic=10.0,
        clock=lambda: 10.0,
    )

    assert outcome.status == "timed_out"
    assert outcome.completed_stages == ()
    assert outcome.committed is False
    assert outcome.failure_stage is DispatchStage.PARENT
    assert outcome.terminal_reason == "global_deadline_exceeded"
    assert len(transitions) == 1
    assert transitions[0].stage is DispatchStage.PARENT
    assert transitions[0].outcome == "timed_out"
    assert transitions[0].error_code == "global_deadline_exceeded"


def test_complex_read_only_task_uses_exactly_one_analyst() -> None:
    scheduler = CentralScheduler()

    plan = scheduler.plan(
        TaskProfile(
            task_id="task_investigate",
            kind=TaskKind.READ_ONLY,
            risk=TaskRisk.MEDIUM,
            complex=True,
            parent_has_sufficient_context=False,
        )
    )

    assert plan.stages == (DispatchStage.ANALYST,)
    assert plan.rule_id == "route.single_analyst"


def test_clear_low_risk_change_skips_unnecessary_analysis_and_review() -> None:
    scheduler = CentralScheduler()

    plan = scheduler.plan(
        TaskProfile(
            task_id="task_local_change",
            kind=TaskKind.CHANGE,
            risk=TaskRisk.LOW,
            complex=False,
            scope_clear=True,
            acceptance_executable=True,
            independent_review_required=False,
        )
    )

    assert plan.stages == (
        DispatchStage.EXECUTOR,
        DispatchStage.VERIFIER,
    )
    assert plan.rule_id == "route.verified_change"


def test_high_risk_change_enforces_separated_analysis_execution_and_review() -> None:
    scheduler = CentralScheduler()

    plan = scheduler.plan(
        TaskProfile(
            task_id="task_permission_change",
            kind=TaskKind.CHANGE,
            risk=TaskRisk.HIGH,
            complex=True,
            scope_clear=False,
            acceptance_executable=True,
        )
    )

    assert plan.stages == (
        DispatchStage.ANALYST,
        DispatchStage.EXECUTOR,
        DispatchStage.VERIFIER,
        DispatchStage.REVIEWER,
    )
    assert plan.rule_id == "route.reviewed_change"


def test_prevalidated_high_risk_approval_still_has_a_human_audit_stage() -> None:
    plan = CentralScheduler().plan(
        TaskProfile(
            task_id="task_high_prevalidated_approval",
            kind=TaskKind.CHANGE,
            risk=TaskRisk.HIGH,
            complex=True,
            human_confirmation_required=True,
            human_confirmation_granted=True,
            human_approval_id="approval_host_1234",
        )
    )

    assert plan.stages == (
        DispatchStage.ANALYST,
        DispatchStage.HUMAN,
        DispatchStage.EXECUTOR,
        DispatchStage.VERIFIER,
        DispatchStage.REVIEWER,
    )
    assert plan.rule_id == "route.high_risk_human_gate"
    assert plan.requires_human is True


def test_v2_high_risk_change_requires_human_before_execution() -> None:
    plan = CentralScheduler().plan(
        TaskProfile(
            task_id="task_high_human_gate",
            kind=TaskKind.CHANGE,
            risk=TaskRisk.HIGH,
            complex=True,
            human_confirmation_required=True,
        )
    )

    assert plan.stages == (
        DispatchStage.ANALYST,
        DispatchStage.HUMAN,
        DispatchStage.EXECUTOR,
        DispatchStage.VERIFIER,
        DispatchStage.REVIEWER,
    )
    assert plan.rule_id == "route.high_risk_human_gate"
    assert plan.requires_human is True


def test_critical_change_requires_human_before_execution() -> None:
    scheduler = CentralScheduler()

    plan = scheduler.plan(
        TaskProfile(
            task_id="task_irreversible",
            kind=TaskKind.CHANGE,
            risk=TaskRisk.CRITICAL,
            complex=True,
        )
    )

    assert plan.stages == (
        DispatchStage.ANALYST,
        DispatchStage.HUMAN,
        DispatchStage.EXECUTOR,
        DispatchStage.VERIFIER,
        DispatchStage.REVIEWER,
    )
    assert plan.rule_id == "route.critical_human_gate"
    assert plan.requires_human is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "read_only"},
        {"risk": "low"},
        {"complex": "false"},
        {"parent_has_sufficient_context": 1},
        {"scope_clear": None},
        {"acceptance_executable": "yes"},
        {"human_confirmation_required": "yes"},
    ],
)
def test_task_profile_rejects_values_that_could_change_routing_by_truthiness(
    overrides: dict[str, object],
) -> None:
    values = {
        "task_id": "task_strict_profile",
        "kind": TaskKind.READ_ONLY,
        "risk": TaskRisk.LOW,
        "complex": False,
        "parent_has_sufficient_context": True,
        "scope_clear": False,
        "acceptance_executable": False,
        "human_confirmation_required": False,
    }
    values.update(overrides)

    with pytest.raises(ValueError, match="task profile"):
        TaskProfile(**values)


def test_runtime_ids_are_namespaced_by_workflow() -> None:
    profile = TaskProfile(
        task_id="task_same_logical_input",
        kind=TaskKind.READ_ONLY,
        risk=TaskRisk.MEDIUM,
        complex=True,
    )
    assignments = []
    decisions = []

    def execute(workflow_id: str) -> None:
        CentralScheduler().execute(
            profile,
            workflow_id=workflow_id,
            run_worker=lambda assignment: assignments.append(assignment)
            or StageResult(
                status="completed",
                artifact_id=f"artifact_{workflow_id}",
            ),
            run_verifier=lambda _assignment: pytest.fail("not reached"),
            commit=lambda _request: pytest.fail("not reached"),
            on_transition=decisions.append,
        )

    execute("wf_namespace_one")
    execute("wf_namespace_two")

    first, second = assignments
    assert first.assignment_id != second.assignment_id
    assert first.attempt_id != second.attempt_id
    assert first.agent_run_id != second.agent_run_id
    assert decisions[0].decision_id != decisions[2].decision_id


def _gated_profile(task_id: str) -> TaskProfile:
    return TaskProfile(
        task_id=task_id,
        kind=TaskKind.CHANGE,
        risk=TaskRisk.HIGH,
        complex=True,
        human_confirmation_required=True,
    )


def test_gated_execution_runs_analysis_then_waits_without_host_human_port() -> None:
    worker_stages = []
    transitions = []

    def run_worker(assignment):
        worker_stages.append(assignment.stage)
        assert assignment.stage is DispatchStage.ANALYST
        return StageResult(status="completed", artifact_id="artifact_analysis")

    outcome = CentralScheduler().execute(
        _gated_profile("task_wait_for_human"),
        workflow_id="wf_wait_for_human",
        run_worker=run_worker,
        run_verifier=lambda _assignment: pytest.fail("verifier not reached"),
        commit=lambda _request: pytest.fail("commit not reached"),
        on_transition=transitions.append,
    )

    assert outcome.status == "waiting_human"
    assert outcome.completed_stages == (DispatchStage.ANALYST,)
    assert outcome.failure_stage is DispatchStage.HUMAN
    assert outcome.terminal_reason == "human_approval_required"
    assert worker_stages == [DispatchStage.ANALYST]
    assert [transition.outcome for transition in transitions] == [
        "dispatched",
        "completed",
        "waiting_human",
    ]
    assert transitions[-1].stage is DispatchStage.HUMAN


def test_host_human_approval_continues_full_reviewed_change_and_commit() -> None:
    assignments = []
    commit_requests = []

    def run_worker(assignment):
        assignments.append(assignment)
        if assignment.stage is DispatchStage.ANALYST:
            assert assignment.input_artifact_ids == ()
            return StageResult(
                status="completed",
                artifact_id="artifact_analysis",
            )
        if assignment.stage is DispatchStage.EXECUTOR:
            assert assignment.input_artifact_ids == (
                "artifact_analysis",
                "artifact_human_approval",
            )
            return StageResult(
                status="completed",
                artifact_id="artifact_execution",
                patch_digest="sha256:approved_patch",
            )
        assert assignment.stage is DispatchStage.REVIEWER
        assert assignment.input_artifact_ids == (
            "artifact_analysis",
            "artifact_human_approval",
            "artifact_execution",
            "artifact_verification",
        )
        return StageResult(
            status="completed",
            artifact_id="artifact_review",
            patch_digest="sha256:approved_patch",
            approved=True,
        )

    def run_human(assignment):
        assignments.append(assignment)
        assert assignment.stage is DispatchStage.HUMAN
        assert assignment.attempt == 1
        assert assignment.input_artifact_ids == ("artifact_analysis",)
        return StageResult(
            status="completed",
            artifact_id="artifact_human_approval",
            approved=True,
        )

    def run_verifier(assignment):
        assignments.append(assignment)
        assert assignment.stage is DispatchStage.VERIFIER
        assert assignment.input_artifact_ids == (
            "artifact_analysis",
            "artifact_human_approval",
            "artifact_execution",
        )
        return StageResult(
            status="completed",
            artifact_id="artifact_verification",
            patch_digest="sha256:approved_patch",
        )

    def commit(request):
        commit_requests.append(request)
        return True

    outcome = CentralScheduler().execute(
        _gated_profile("task_approved_by_human"),
        workflow_id="wf_approved_by_human",
        run_worker=run_worker,
        run_verifier=run_verifier,
        run_human=run_human,
        commit=commit,
    )

    assert outcome.status == "completed"
    assert outcome.committed is True
    assert outcome.completed_stages == (
        DispatchStage.ANALYST,
        DispatchStage.HUMAN,
        DispatchStage.EXECUTOR,
        DispatchStage.VERIFIER,
        DispatchStage.REVIEWER,
    )
    assert [assignment.stage for assignment in assignments] == [
        DispatchStage.ANALYST,
        DispatchStage.HUMAN,
        DispatchStage.EXECUTOR,
        DispatchStage.VERIFIER,
        DispatchStage.REVIEWER,
    ]
    assert len(commit_requests) == 1
    assert commit_requests[0].artifact_ids == (
        "artifact_analysis",
        "artifact_human_approval",
        "artifact_execution",
        "artifact_verification",
        "artifact_review",
    )


@pytest.mark.parametrize(
    ("approved", "expected_reason"),
    [
        (False, "human_approval_rejected"),
        (None, "human_approval_missing"),
    ],
)
def test_rejected_or_missing_human_approval_never_dispatches_executor(
    approved: bool | None,
    expected_reason: str,
) -> None:
    worker_stages = []
    human_calls = []

    def run_worker(assignment):
        worker_stages.append(assignment.stage)
        assert assignment.stage is DispatchStage.ANALYST
        return StageResult(status="completed", artifact_id="artifact_analysis")

    def run_human(assignment):
        human_calls.append(assignment)
        return StageResult(
            status="completed",
            artifact_id="artifact_human_decision",
            approved=approved,
            retryable=True,
        )

    outcome = CentralScheduler().execute(
        _gated_profile(f"task_{expected_reason}"),
        workflow_id=f"wf_{expected_reason}",
        run_worker=run_worker,
        run_verifier=lambda _assignment: pytest.fail("verifier not reached"),
        run_human=run_human,
        commit=lambda _request: pytest.fail("commit not reached"),
    )

    assert outcome.status == "failed"
    assert outcome.completed_stages == (DispatchStage.ANALYST,)
    assert outcome.failure_stage is DispatchStage.HUMAN
    assert outcome.terminal_reason == expected_reason
    assert worker_stages == [DispatchStage.ANALYST]
    assert len(human_calls) == 1
    assert human_calls[0].attempt == 1
