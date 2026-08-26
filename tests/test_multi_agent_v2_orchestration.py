from __future__ import annotations

from dataclasses import replace

import pytest

from minicode.central_scheduler import (
    CentralScheduler,
    DispatchStage,
    StageResult,
    TaskKind,
    TaskProfile,
    TaskRisk,
)
from minicode.tooling import ToolContext
from minicode.tools import task as task_module


_WORKFLOW_ID = "wf_orchestration_0001"
_TASK_ID = "task_orchestration_0001"
_PATCH_DIGEST = "sha256:patch-a"


def _profile(*, risk: TaskRisk = TaskRisk.HIGH) -> TaskProfile:
    return TaskProfile(
        task_id=_TASK_ID,
        kind=TaskKind.CHANGE,
        risk=risk,
        complex=True,
        scope_clear=True,
        acceptance_executable=True,
    )


def _stage_result(
    stage: DispatchStage,
    *,
    status: str = "completed",
    patch_digest: str | None = None,
    approved: bool | None = None,
) -> StageResult:
    return StageResult(
        status=status,
        artifact_id=f"artifact_{stage.value}",
        patch_digest=patch_digest,
        approved=approved,
    )


class _RecordingPorts:
    """Small boundary fake for scheduler-owned dispatch and promotion effects."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.transitions: list[object] = []
        self.commit_requests: list[object] = []
        self.results = {
            DispatchStage.ANALYST: _stage_result(DispatchStage.ANALYST),
            DispatchStage.EXECUTOR: _stage_result(
                DispatchStage.EXECUTOR,
                patch_digest=_PATCH_DIGEST,
            ),
            DispatchStage.VERIFIER: _stage_result(
                DispatchStage.VERIFIER,
                patch_digest=_PATCH_DIGEST,
            ),
            DispatchStage.REVIEWER: _stage_result(
                DispatchStage.REVIEWER,
                patch_digest=_PATCH_DIGEST,
                approved=True,
            ),
        }

    def run_worker(self, assignment) -> StageResult:
        self.calls.append(f"worker:{assignment.stage.value}")
        return self.results[assignment.stage]

    def run_verifier(self, assignment) -> StageResult:
        self.calls.append("verifier")
        assert assignment.stage is DispatchStage.VERIFIER
        return self.results[DispatchStage.VERIFIER]

    def commit(self, request) -> bool:
        self.calls.append("commit")
        self.commit_requests.append(request)
        return True

    def on_transition(self, transition) -> None:
        self.transitions.append(transition)


def _execute(ports: _RecordingPorts, *, profile: TaskProfile | None = None):
    return CentralScheduler().execute(
        profile or _profile(),
        workflow_id=_WORKFLOW_ID,
        run_worker=ports.run_worker,
        run_verifier=ports.run_verifier,
        commit=ports.commit,
        on_transition=ports.on_transition,
    )


def test_reviewed_change_runs_the_planned_stages_in_order_and_commits_once() -> None:
    ports = _RecordingPorts()

    outcome = _execute(ports)

    assert ports.calls == [
        "worker:analyst",
        "worker:executor",
        "verifier",
        "worker:reviewer",
        "commit",
    ]
    assert outcome.status == "completed"
    assert outcome.completed_stages == (
        DispatchStage.ANALYST,
        DispatchStage.EXECUTOR,
        DispatchStage.VERIFIER,
        DispatchStage.REVIEWER,
    )
    assert outcome.committed is True
    assert len(ports.commit_requests) == 1
    assert ports.commit_requests[0].patch_digest == _PATCH_DIGEST


@pytest.mark.parametrize(
    ("failing_stage", "expected_calls"),
    [
        (DispatchStage.ANALYST, ["worker:analyst"]),
        (
            DispatchStage.EXECUTOR,
            ["worker:analyst", "worker:executor"],
        ),
    ],
)
def test_failed_predecessor_stops_all_downstream_dispatch_and_promotion(
    failing_stage: DispatchStage,
    expected_calls: list[str],
) -> None:
    ports = _RecordingPorts()
    ports.results[failing_stage] = replace(
        ports.results[failing_stage],
        status="failed",
    )

    outcome = _execute(ports)

    assert ports.calls == expected_calls
    assert outcome.status == "failed"
    assert outcome.failure_stage is failing_stage
    assert outcome.committed is False
    assert ports.commit_requests == []


def test_critical_change_stops_at_human_gate_without_executing_or_committing() -> None:
    ports = _RecordingPorts()

    outcome = _execute(ports, profile=_profile(risk=TaskRisk.CRITICAL))

    assert ports.calls == ["worker:analyst"]
    assert outcome.status == "waiting_human"
    assert outcome.completed_stages == (DispatchStage.ANALYST,)
    assert outcome.committed is False
    assert ports.commit_requests == []


def test_failed_verifier_blocks_reviewer_and_commit() -> None:
    ports = _RecordingPorts()
    ports.results[DispatchStage.VERIFIER] = replace(
        ports.results[DispatchStage.VERIFIER],
        status="failed",
    )

    outcome = _execute(ports)

    assert ports.calls == [
        "worker:analyst",
        "worker:executor",
        "verifier",
    ]
    assert outcome.status == "failed"
    assert outcome.failure_stage is DispatchStage.VERIFIER
    assert outcome.committed is False
    assert ports.commit_requests == []


def test_commit_requires_both_passing_verification_and_reviewer_approval() -> None:
    ports = _RecordingPorts()
    ports.results[DispatchStage.REVIEWER] = replace(
        ports.results[DispatchStage.REVIEWER],
        approved=False,
    )

    outcome = _execute(ports)

    assert ports.calls == [
        "worker:analyst",
        "worker:executor",
        "verifier",
        "worker:reviewer",
    ]
    assert outcome.status == "failed"
    assert outcome.failure_stage is DispatchStage.REVIEWER
    assert outcome.terminal_reason == "review_not_approved"
    assert outcome.committed is False
    assert ports.commit_requests == []


@pytest.mark.parametrize(
    ("mismatched_stage", "expected_calls"),
    [
        (
            DispatchStage.VERIFIER,
            ["worker:analyst", "worker:executor", "verifier"],
        ),
        (
            DispatchStage.REVIEWER,
            [
                "worker:analyst",
                "worker:executor",
                "verifier",
                "worker:reviewer",
            ],
        ),
    ],
)
def test_patch_digest_mismatch_fails_closed_before_commit(
    mismatched_stage: DispatchStage,
    expected_calls: list[str],
) -> None:
    ports = _RecordingPorts()
    ports.results[mismatched_stage] = replace(
        ports.results[mismatched_stage],
        patch_digest="sha256:different-patch",
    )

    outcome = _execute(ports)

    assert ports.calls == expected_calls
    assert outcome.status == "failed"
    assert outcome.failure_stage is mismatched_stage
    assert outcome.committed is False
    assert ports.commit_requests == []


def test_every_transition_carries_a_complete_correlation_chain() -> None:
    ports = _RecordingPorts()

    outcome = _execute(ports)

    assert outcome.status == "completed"
    assert ports.transitions
    decision_ids = [transition.decision_id for transition in ports.transitions]
    assert len(decision_ids) == len(set(decision_ids))
    for index, transition in enumerate(ports.transitions):
        assert transition.workflow_id == _WORKFLOW_ID
        assert transition.task_id == _TASK_ID
        assert transition.assignment_id
        assert transition.attempt_id
        assert isinstance(transition.stage, DispatchStage)
        assert transition.rule_id == "route.reviewed_change"
        assert transition.decision_id
        if index == 0:
            assert transition.parent_decision_id is None
        else:
            assert transition.parent_decision_id == decision_ids[index - 1]


def test_deadline_crossed_inside_commit_is_reported_as_timed_out() -> None:
    ports = _RecordingPorts()
    # Each stage consumes two pre-dispatch checks and one post-runner check;
    # the final zero admits the commit request itself.
    clock_values = iter([0.0] * 13 + [2.0, 2.0])

    outcome = CentralScheduler().execute(
        _profile(),
        workflow_id=_WORKFLOW_ID,
        run_worker=ports.run_worker,
        run_verifier=ports.run_verifier,
        commit=lambda request: ports.commit_requests.append(request) or False,
        on_transition=ports.on_transition,
        deadline_monotonic=1.0,
        clock=lambda: next(clock_values),
    )

    assert outcome.status == "timed_out"
    assert outcome.failure_stage is DispatchStage.COMMIT
    assert outcome.terminal_reason == "global_deadline_exceeded"
    assert ports.transitions[-1].outcome == "timed_out"


def test_scheduler_does_not_swallow_cancellation_from_commit() -> None:
    class Cancelled(RuntimeError):
        pass

    ports = _RecordingPorts()

    with pytest.raises(Cancelled):
        CentralScheduler().execute(
            _profile(),
            workflow_id=_WORKFLOW_ID,
            run_worker=ports.run_worker,
            run_verifier=ports.run_verifier,
            commit=lambda _request: (_ for _ in ()).throw(Cancelled()),
            abort_exceptions=(Cancelled,),
        )


@pytest.mark.parametrize(
    ("agent_type", "expected_error"),
    [
        ("workflow", "workflow_request_required"),
        ("general", "central_scheduler_required"),
    ],
)
def test_production_context_cannot_bypass_central_v2_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
    agent_type: str,
    expected_error: str,
) -> None:
    monkeypatch.setattr(
        task_module,
        "_SYNC_RUN",
        lambda *_args, **_kwargs: pytest.fail("legacy runner was called"),
    )
    validated = task_module.task_tool.validator(
        {
            "description": "central gate probe",
            "prompt": "Attempt a direct state-changing sub-agent.",
            "agent_type": agent_type,
        }
    )

    result = task_module.task_tool.run(
        validated,
        ToolContext(_central_workflow_v2_enforced=True),
    )

    assert result.ok is False
    assert f"error[{expected_error}]" in result.output
