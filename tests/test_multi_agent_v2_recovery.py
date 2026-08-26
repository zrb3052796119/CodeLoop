"""Bounded retry, timeout, and fail-closed scheduler recovery."""

from __future__ import annotations

import pytest

from minicode.central_scheduler import (
    CentralScheduler,
    DispatchStage,
    SchedulerExecutionPolicy,
    StageResult,
    TaskKind,
    TaskProfile,
    TaskRisk,
)


PATCH = "sha256:patch"


def _profile() -> TaskProfile:
    return TaskProfile(
        task_id="task_recovery1",
        kind=TaskKind.CHANGE,
        risk=TaskRisk.HIGH,
        complex=True,
        scope_clear=True,
        acceptance_executable=True,
    )


def _success(stage: DispatchStage) -> StageResult:
    return StageResult(
        status="completed",
        artifact_id=f"artifact_{stage.value}",
        patch_digest=PATCH if stage is not DispatchStage.ANALYST else None,
        approved=True if stage is DispatchStage.REVIEWER else None,
    )


def test_retryable_analyst_failure_reuses_assignment_and_increments_attempt() -> None:
    assignments = []

    def run_worker(assignment):
        assignments.append(assignment)
        if assignment.stage is DispatchStage.ANALYST and assignment.attempt == 1:
            return StageResult(
                status="failed",
                artifact_id="artifact_transient",
                retryable=True,
                error_code="provider_unavailable",
            )
        return _success(assignment.stage)

    outcome = CentralScheduler().execute(
        _profile(),
        workflow_id="wf_recovery1",
        run_worker=run_worker,
        run_verifier=lambda assignment: _success(assignment.stage),
        commit=lambda _request: True,
    )

    analyst_attempts = [
        item for item in assignments if item.stage is DispatchStage.ANALYST
    ]
    assert outcome.status == "completed"
    assert [item.attempt for item in analyst_attempts] == [1, 2]
    assert analyst_attempts[0].assignment_id == analyst_attempts[1].assignment_id
    assert analyst_attempts[0].agent_run_id != analyst_attempts[1].agent_run_id


def test_nonretryable_scope_violation_stops_immediately() -> None:
    calls = []

    def run_worker(assignment):
        calls.append(assignment.stage)
        return StageResult(
            status="failed",
            artifact_id="artifact_scope_violation",
            retryable=False,
            error_code="scope_violation",
        )

    outcome = CentralScheduler().execute(
        _profile(),
        workflow_id="wf_recovery1",
        run_worker=run_worker,
        run_verifier=lambda assignment: _success(assignment.stage),
        commit=lambda _request: True,
    )

    assert calls == [DispatchStage.ANALYST]
    assert outcome.status == "failed"
    assert outcome.terminal_reason == "scope_violation"


def test_inconclusive_verifier_gets_one_infrastructure_retry() -> None:
    attempts = []

    def run_verifier(assignment):
        attempts.append(assignment.attempt)
        if assignment.attempt == 1:
            return StageResult(
                status="inconclusive",
                artifact_id="artifact_verifier_unavailable",
                patch_digest=PATCH,
                retryable=True,
                error_code="verifier_unavailable",
            )
        return _success(DispatchStage.VERIFIER)

    outcome = CentralScheduler().execute(
        _profile(),
        workflow_id="wf_recovery1",
        run_worker=lambda assignment: _success(assignment.stage),
        run_verifier=run_verifier,
        commit=lambda _request: True,
    )

    assert attempts == [1, 2]
    assert outcome.status == "completed"


def test_global_deadline_stops_new_dispatch_and_never_commits() -> None:
    now = [0.0]
    calls = []

    def run_worker(assignment):
        calls.append(assignment.stage)
        if assignment.stage is DispatchStage.EXECUTOR:
            now[0] = 11.0
        return _success(assignment.stage)

    outcome = CentralScheduler().execute(
        _profile(),
        workflow_id="wf_recovery1",
        run_worker=run_worker,
        run_verifier=lambda assignment: _success(assignment.stage),
        commit=lambda _request: calls.append(DispatchStage.COMMIT) is None,
        deadline_monotonic=10.0,
        clock=lambda: now[0],
        policy=SchedulerExecutionPolicy(),
    )

    assert calls == [DispatchStage.ANALYST, DispatchStage.EXECUTOR]
    assert outcome.status == "timed_out"
    assert outcome.failure_stage is DispatchStage.EXECUTOR
    assert outcome.completed_stages == (DispatchStage.ANALYST,)
    assert outcome.committed is False
    assert outcome.terminal_reason == "global_deadline_exceeded"


@pytest.mark.parametrize(
    "late_stage",
    [
        DispatchStage.ANALYST,
        DispatchStage.EXECUTOR,
        DispatchStage.VERIFIER,
        DispatchStage.REVIEWER,
    ],
)
def test_late_stage_result_is_rejected_before_gate_or_next_dispatch(
    late_stage: DispatchStage,
) -> None:
    now = [0.0]
    calls: list[DispatchStage] = []
    transitions = []
    ordered_stages = [
        DispatchStage.ANALYST,
        DispatchStage.EXECUTOR,
        DispatchStage.VERIFIER,
        DispatchStage.REVIEWER,
    ]

    def finish_stage(stage: DispatchStage) -> StageResult:
        calls.append(stage)
        if stage is late_stage:
            now[0] = 10.0
        return _success(stage)

    outcome = CentralScheduler().execute(
        _profile(),
        workflow_id=f"wf_late_{late_stage.value}",
        run_worker=lambda assignment: finish_stage(assignment.stage),
        run_verifier=lambda assignment: finish_stage(assignment.stage),
        commit=lambda _request: calls.append(DispatchStage.COMMIT) is None,
        on_transition=transitions.append,
        deadline_monotonic=10.0,
        clock=lambda: now[0],
    )

    late_index = ordered_stages.index(late_stage)
    assert calls == ordered_stages[: late_index + 1]
    assert outcome.status == "timed_out"
    assert outcome.failure_stage is late_stage
    assert outcome.completed_stages == tuple(ordered_stages[:late_index])
    assert outcome.committed is False
    assert outcome.terminal_reason == "global_deadline_exceeded"

    late_transitions = [
        item for item in transitions if item.stage is late_stage
    ]
    assert [item.outcome for item in late_transitions] == [
        "dispatched",
        "timed_out",
    ]
    assert late_transitions[-1].artifact_id is None
    assert late_transitions[-1].error_code == "global_deadline_exceeded"


def test_expired_deadline_prevents_the_first_worker_dispatch() -> None:
    calls = []

    outcome = CentralScheduler().execute(
        _profile(),
        workflow_id="wf_expired_before_start",
        run_worker=lambda assignment: calls.append(assignment) or _success(
            assignment.stage
        ),
        run_verifier=lambda assignment: _success(assignment.stage),
        commit=lambda _request: True,
        deadline_monotonic=10.0,
        clock=lambda: 10.0,
    )

    assert calls == []
    assert outcome.status == "timed_out"
    assert outcome.failure_stage is DispatchStage.ANALYST
    assert outcome.terminal_reason == "global_deadline_exceeded"


def test_untyped_worker_exception_is_not_implicitly_retried() -> None:
    attempts = []

    def broken_port(assignment):
        attempts.append(assignment.attempt)
        raise RuntimeError("provider exploded without a typed retry decision")

    outcome = CentralScheduler().execute(
        _profile(),
        workflow_id="wf_untyped_port_failure",
        run_worker=broken_port,
        run_verifier=lambda assignment: _success(assignment.stage),
        commit=lambda _request: True,
    )

    assert attempts == [1]
    assert outcome.status == "failed"
    assert outcome.terminal_reason == "stage_port_failed"


def test_configured_abort_exception_propagates_without_conversion() -> None:
    class Cancellation(RuntimeError):
        pass

    def cancelled(_assignment):
        raise Cancellation("stop")

    with pytest.raises(Cancellation, match="stop"):
        CentralScheduler().execute(
            _profile(),
            workflow_id="wf_cancelled",
            run_worker=cancelled,
            run_verifier=lambda assignment: _success(assignment.stage),
            commit=lambda _request: True,
            abort_exceptions=(Cancellation,),
        )


def test_stage_can_dynamically_escalate_to_human_authority() -> None:
    outcome = CentralScheduler().execute(
        _profile(),
        workflow_id="wf_dynamic_human_gate",
        run_worker=lambda _assignment: StageResult(
            status="inconclusive",
            artifact_id="artifact_analyst_unknowns",
            requires_human=True,
            error_code="analyst_unknowns_require_human",
        ),
        run_verifier=lambda _assignment: _success(DispatchStage.VERIFIER),
        commit=lambda _request: True,
    )

    assert outcome.status == "waiting_human"
    assert outcome.failure_stage is DispatchStage.ANALYST
    assert outcome.terminal_reason == "analyst_unknowns_require_human"


def test_commit_ready_is_persisted_after_review_and_before_commit() -> None:
    calls: list[str] = []

    def run_worker(assignment):
        calls.append(assignment.stage.value)
        return _success(assignment.stage)

    outcome = CentralScheduler().execute(
        _profile(),
        workflow_id="wf_commit_ready_order",
        run_worker=run_worker,
        run_verifier=lambda assignment: calls.append("verifier") or _success(
            assignment.stage
        ),
        prepare_commit=lambda _request: calls.append("prepare") or True,
        commit=lambda _request: calls.append("commit") or True,
    )

    assert outcome.status == "completed"
    assert calls == [
        "analyst",
        "executor",
        "verifier",
        "reviewer",
        "prepare",
        "commit",
    ]


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_commit_ready_persistence_failure_never_calls_commit(mode: str) -> None:
    commit_calls: list[str] = []

    def prepare(_request):
        if mode == "exception":
            raise OSError("storage unavailable")
        return False

    outcome = CentralScheduler().execute(
        _profile(),
        workflow_id=f"wf_commit_ready_{mode}",
        run_worker=lambda assignment: _success(assignment.stage),
        run_verifier=lambda assignment: _success(assignment.stage),
        prepare_commit=prepare,
        commit=lambda _request: commit_calls.append("commit") or True,
    )

    assert commit_calls == []
    assert outcome.status == "failed"
    assert outcome.failure_stage is DispatchStage.COMMIT
    assert outcome.terminal_reason == "commit_ready_persistence_failed"


def test_deadline_after_commit_ready_persistence_never_calls_commit() -> None:
    now = [0.0]
    commit_calls: list[str] = []

    def prepare(_request):
        now[0] = 10.0
        return True

    outcome = CentralScheduler().execute(
        _profile(),
        workflow_id="wf_commit_ready_deadline",
        run_worker=lambda assignment: _success(assignment.stage),
        run_verifier=lambda assignment: _success(assignment.stage),
        prepare_commit=prepare,
        commit=lambda _request: commit_calls.append("commit") or True,
        deadline_monotonic=10.0,
        clock=lambda: now[0],
    )

    assert commit_calls == []
    assert outcome.status == "timed_out"
    assert outcome.failure_stage is DispatchStage.COMMIT
    assert outcome.terminal_reason == "global_deadline_exceeded"


def test_prepare_false_after_deadline_is_reported_as_timeout() -> None:
    now = [0.0]
    commit_calls: list[str] = []

    def prepare(_request):
        now[0] = 10.0
        return False

    outcome = CentralScheduler().execute(
        _profile(),
        workflow_id="wf_commit_ready_false_deadline",
        run_worker=lambda assignment: _success(assignment.stage),
        run_verifier=lambda assignment: _success(assignment.stage),
        prepare_commit=prepare,
        commit=lambda _request: commit_calls.append("commit") or True,
        deadline_monotonic=10.0,
        clock=lambda: now[0],
    )

    assert commit_calls == []
    assert outcome.status == "timed_out"
    assert outcome.failure_stage is DispatchStage.COMMIT
    assert outcome.terminal_reason == "global_deadline_exceeded"


def test_prepare_exception_after_deadline_is_reported_as_timeout() -> None:
    now = [0.0]
    commit_calls: list[str] = []

    def prepare(_request):
        now[0] = 10.0
        raise OSError("late storage failure")

    outcome = CentralScheduler().execute(
        _profile(),
        workflow_id="wf_commit_ready_exception_deadline",
        run_worker=lambda assignment: _success(assignment.stage),
        run_verifier=lambda assignment: _success(assignment.stage),
        prepare_commit=prepare,
        commit=lambda _request: commit_calls.append("commit") or True,
        deadline_monotonic=10.0,
        clock=lambda: now[0],
    )

    assert commit_calls == []
    assert outcome.status == "timed_out"
    assert outcome.failure_stage is DispatchStage.COMMIT
    assert outcome.terminal_reason == "global_deadline_exceeded"
