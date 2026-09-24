"""Durable scheduler audit sidecars remain exact and Run-scoped."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from minicode.central_scheduler import (
    CentralScheduler,
    StageResult,
    TaskKind,
    TaskProfile,
    TaskRisk,
)
from minicode.multi_agent_contracts import (
    Assignment,
    AssignmentBudget,
    AssignmentRisk,
    AssignmentScope,
    ImplementationOrder,
    WorkerRole,
)
from minicode.run_journal import RunJournal, RunJournalStorageError
from minicode.run_lifecycle import observe_run
from minicode.scheduler_artifacts import SchedulerArtifactStore
from minicode.scheduler_audit import (
    SchedulerCommitReady,
    SchedulerAuditValidationError,
    _order_scope_within_assignment,
    parse_scheduler_commit_ready,
    project_scheduler_audit,
    project_scheduler_commit_ready,
)
from minicode.scheduler_trace import project_scheduler_trace


_WORKFLOW_ID = "wf_" + "a" * 32
_TASK_ID = "task_" + "b" * 32


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _commit_ready(**overrides: object) -> SchedulerCommitReady:
    values: dict[str, object] = {
        "workflow_id": _WORKFLOW_ID,
        "task_id": _TASK_ID,
        "rule_id": "route.full_review",
        "request_digest": _digest("a"),
        "base_digest": _digest("b"),
        "patch_digest": _digest("c"),
        "verification_digest": _digest("d"),
        "review_digest": _digest("e"),
        "approval_digest": None,
        "artifact_set_digest": _digest("f"),
    }
    values.update(overrides)
    return project_scheduler_commit_ready(**values)


def _audit(*, artifact_value: object | None = None):
    profile = TaskProfile(
        task_id=_TASK_ID,
        kind=TaskKind.READ_ONLY,
        risk=TaskRisk.HIGH,
        complex=True,
    )
    scheduler = CentralScheduler()
    plan = scheduler.plan(profile)
    transitions = []
    outcome = scheduler.execute(
        profile,
        workflow_id=_WORKFLOW_ID,
        run_worker=lambda _assignment: StageResult(
            status="completed",
            artifact_id="artifact_analyst_report",
        ),
        run_verifier=lambda _assignment: pytest.fail("verifier was dispatched"),
        commit=lambda _request: pytest.fail("commit was requested"),
        on_transition=transitions.append,
    )
    authority = object()
    store = SchedulerArtifactStore(scheduler_authority=authority)
    artifact = store.publish(
        "artifact_analyst_report",
        artifact_value
        if artifact_value is not None
        else {"finding": {"path": "src/app.py", "状态": "verified"}},
        authority=authority,
    )
    trace = project_scheduler_trace(plan, outcome, transitions)
    return project_scheduler_audit(
        profile=profile,
        plan=plan,
        trace=trace,
        artifacts=(artifact,),
    )


def _run_dir(data_dir: Path, record) -> Path:
    return (
        data_dir
        / "dashboard"
        / "workspaces"
        / record.workspace_id
        / "runs"
        / record.id
    )


def test_run_journal_round_trips_exact_scheduler_audit_after_terminal(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="scheduler audit", source="headless")
    journal.transition(record.id, "running")
    audit = _audit()

    journal.record_scheduler_audit(record.id, audit)
    journal.transition(record.id, "completed")

    reopened = RunJournal(workspace, data_dir=data_dir)
    loaded = reopened.get_scheduler_audit(record.id, _WORKFLOW_ID)
    summaries = reopened.list_scheduler_audits(record.id)

    assert loaded is not None
    assert loaded.profile == audit.profile
    assert loaded.plan == audit.plan
    assert loaded.trace == audit.trace
    assert loaded.artifacts == audit.artifacts
    assert loaded.artifacts[0].content == (
        '{"finding":{"path":"src/app.py","状态":"verified"}}'
    )
    assert len(summaries) == 1
    assert summaries[0].workflow_id == _WORKFLOW_ID
    assert summaries[0].status == "completed"
    assert summaries[0].rule_id == "route.single_analyst"
    assert summaries[0].artifact_count == 1
    assert [event.type for event in reopened.list_events(record.id).items] == [
        "run.queued",
        "run.started",
        "run.completed",
    ]

    audit_dir = _run_dir(data_dir, record) / "scheduler-workflows" / _WORKFLOW_ID
    if os.name == "posix":
        assert audit_dir.stat().st_mode & 0o777 == 0o700
        assert (audit_dir / "manifest.json").stat().st_mode & 0o777 == 0o600
        assert (
            audit_dir / "artifacts" / "artifact_analyst_report.json"
        ).stat().st_mode & 0o777 == 0o600


def test_scheduler_audit_write_is_idempotent_but_not_overwritable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    record = journal.create_run(title="write once", source="headless")
    journal.transition(record.id, "running")
    audit = _audit()

    journal.record_scheduler_audit(record.id, audit)
    journal.record_scheduler_audit(record.id, audit)

    with pytest.raises(RunJournalStorageError, match="audit"):
        journal.record_scheduler_audit(
            record.id,
            _audit(artifact_value={"finding": "changed"}),
        )


def test_scheduler_audit_detects_tampered_artifact_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="tamper", source="gateway")
    journal.transition(record.id, "running")
    journal.record_scheduler_audit(record.id, _audit())
    journal.transition(record.id, "completed")
    artifact_path = (
        _run_dir(data_dir, record)
        / "scheduler-workflows"
        / _WORKFLOW_ID
        / "artifacts"
        / "artifact_analyst_report.json"
    )
    artifact_path.write_text('{"finding":"tampered"}', encoding="utf-8")

    with pytest.raises(RunJournalStorageError, match="audit"):
        RunJournal(workspace, data_dir=data_dir).get_scheduler_audit(
            record.id,
            _WORKFLOW_ID,
        )


def test_scheduler_audit_rejects_symlinked_storage_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="symlink", source="headless")
    journal.transition(record.id, "running")
    run_dir = _run_dir(data_dir, record)
    outside = tmp_path / "outside"
    outside.mkdir()
    (run_dir / "scheduler-workflows").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RunJournalStorageError, match="audit"):
        journal.record_scheduler_audit(record.id, _audit())


def test_run_observation_exposes_only_best_effort_scheduler_audit_recording(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    audit = _audit()

    with observe_run(
        workspace=workspace,
        source="headless",
        title="observed audit",
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    ) as observation:
        assert observation.record_scheduler_audit(audit) is True
        run_id = observation.run_id

    assert run_id is not None
    journal = RunJournal(workspace, data_dir=data_dir)
    assert journal.get_scheduler_audit(run_id, _WORKFLOW_ID) == audit


def test_run_deletion_removes_scheduler_audit_with_its_parent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(
        title="delete audit",
        source="gateway",
        session_id="session_scheduler_audit",
    )
    journal.transition(record.id, "running")
    journal.record_scheduler_audit(record.id, _audit())
    journal.transition(record.id, "completed")
    run_dir = _run_dir(data_dir, record)

    assert journal.delete_terminal_for_session("session_scheduler_audit") == 1
    assert not run_dir.exists()


def test_scheduler_audit_rejects_dangling_transition_artifact_reference() -> None:
    audit = _audit()

    with pytest.raises(SchedulerAuditValidationError, match="unsealed artifact"):
        project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=audit.trace,
            artifacts=(),
        )


def test_v2_audit_requires_an_exact_assignment_for_every_model_dispatch() -> None:
    audit = _audit()

    with pytest.raises(SchedulerAuditValidationError, match="Assignment artifacts"):
        project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=audit.trace,
            artifacts=audit.artifacts,
            contract_mode="v2",
        )


def test_scheduler_stage_receipt_must_match_the_exact_transition() -> None:
    profile = TaskProfile(
        task_id=_TASK_ID,
        kind=TaskKind.READ_ONLY,
        risk=TaskRisk.MEDIUM,
        complex=True,
    )
    scheduler = CentralScheduler()
    plan = scheduler.plan(profile)
    transitions = []
    outcome = scheduler.execute(
        profile,
        workflow_id=_WORKFLOW_ID,
        run_worker=lambda _assignment: StageResult(
            status="failed",
            artifact_id="artifact_failure_receipt",
            error_code="worker_failed",
        ),
        run_verifier=lambda _assignment: pytest.fail("not reached"),
        commit=lambda _request: pytest.fail("not reached"),
        on_transition=transitions.append,
    )
    transition = transitions[-1]
    receipt = {
        "receiptVersion": 1,
        "resultType": "SchedulerStageReceipt.v1",
        "workflowId": transition.workflow_id,
        "taskId": transition.task_id,
        "assignmentId": transition.assignment_id,
        "attemptId": transition.attempt_id,
        "agentRunId": transition.agent_run_id,
        "decisionId": transition.decision_id,
        "parentDecisionId": transition.parent_decision_id,
        "ruleId": transition.rule_id,
        "stage": transition.stage.value,
        "outcome": transition.outcome,
        "errorCode": transition.error_code,
    }
    authority = object()
    store = SchedulerArtifactStore(scheduler_authority=authority)
    matching = store.publish(
        "artifact_failure_receipt",
        receipt,
        authority=authority,
    )
    trace = project_scheduler_trace(plan, outcome, transitions)

    project_scheduler_audit(
        profile=profile,
        plan=plan,
        trace=trace,
        artifacts=(matching,),
    )

    other_authority = object()
    other_store = SchedulerArtifactStore(scheduler_authority=other_authority)
    mismatched = other_store.publish(
        "artifact_failure_receipt",
        {**receipt, "attemptId": "attempt_wrong"},
        authority=other_authority,
    )
    with pytest.raises(SchedulerAuditValidationError, match="does not match"):
        project_scheduler_audit(
            profile=profile,
            plan=plan,
            trace=trace,
            artifacts=(mismatched,),
        )


def test_scheduler_commit_ready_is_a_closed_digest_bound_contract() -> None:
    ready = _commit_ready()
    document = ready.to_dict()
    identity = {key: value for key, value in document.items() if key != "commitReadyId"}
    expected_id = "commit_ready_" + hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    assert document == {
        "commitReadyVersion": 1,
        "resultType": "SchedulerCommitReady.v1",
        "commitReadyId": expected_id,
        "workflowId": _WORKFLOW_ID,
        "taskId": _TASK_ID,
        "ruleId": "route.full_review",
        "requestDigest": _digest("a"),
        "baseDigest": _digest("b"),
        "patchDigest": _digest("c"),
        "verificationDigest": _digest("d"),
        "reviewDigest": _digest("e"),
        "approvalDigest": None,
        "artifactSetDigest": _digest("f"),
    }
    assert parse_scheduler_commit_ready(document) == ready
    assert _commit_ready() == ready
    assert _commit_ready(patch_digest=_digest("0")).commit_ready_id != expected_id


def test_scheduler_commit_ready_parser_rejects_open_or_inconsistent_values() -> None:
    document = _commit_ready(approval_digest=_digest("1")).to_dict()
    missing = dict(document)
    missing.pop("reviewDigest")
    malformed = (
        {**document, "unexpected": "field"},
        missing,
        {**document, "commitReadyVersion": True},
        {**document, "resultType": "SchedulerCommitReady.v2"},
        {**document, "commitReadyId": "commit_ready_" + "0" * 64},
        {**document, "workflowId": "wf_../../outside"},
        {**document, "taskId": "../task"},
        {**document, "ruleId": "../route"},
        {**document, "requestDigest": "sha256:ABC"},
        {**document, "approvalDigest": 7},
    )

    for value in malformed:
        with pytest.raises(SchedulerAuditValidationError, match="commit-ready"):
            parse_scheduler_commit_ready(value)


def test_run_journal_persists_commit_ready_once_and_fsyncs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="commit ready", source="headless")
    journal.transition(record.id, "running")
    ready = _commit_ready()
    assert journal.get_scheduler_commit_ready(record.id, _WORKFLOW_ID) is None
    fsync_calls: list[int] = []
    original_fsync = os.fsync

    def recording_fsync(descriptor: int) -> None:
        fsync_calls.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr("minicode.scheduler_audit.os.fsync", recording_fsync)

    assert journal.record_scheduler_commit_ready(record.id, ready) is True
    assert len(fsync_calls) >= 2
    assert journal.record_scheduler_commit_ready(record.id, ready) is False
    journal.transition(record.id, "completed")

    reopened = RunJournal(workspace, data_dir=data_dir)
    assert reopened.get_scheduler_commit_ready(record.id, _WORKFLOW_ID) == ready
    pending = reopened.list_scheduler_audits(record.id)
    assert len(pending) == 1
    assert pending[0].status == "commit_state_unknown"
    assert pending[0].committed is None
    assert pending[0].terminal_reason == "final_audit_missing"
    target = (
        _run_dir(data_dir, record)
        / "scheduler-workflows"
        / _WORKFLOW_ID
        / "commit-ready.json"
    )
    assert json.loads(target.read_text(encoding="utf-8")) == ready.to_dict()
    if os.name == "posix":
        assert target.stat().st_mode & 0o777 == 0o600


def test_scheduler_commit_ready_write_once_rejects_a_different_binding(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    record = journal.create_run(title="commit conflict", source="headless")
    journal.transition(record.id, "running")

    assert journal.record_scheduler_commit_ready(record.id, _commit_ready()) is True
    with pytest.raises(RunJournalStorageError, match="commit-ready"):
        journal.record_scheduler_commit_ready(
            record.id,
            _commit_ready(patch_digest=_digest("0")),
        )


def test_commit_ready_remains_readable_after_the_final_audit_is_sealed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="commit then audit", source="headless")
    journal.transition(record.id, "running")
    audit = _audit()
    ready = _commit_ready(rule_id=audit.plan.rule_id)

    assert journal.record_scheduler_commit_ready(record.id, ready) is True
    journal.record_scheduler_audit(record.id, audit)
    journal.transition(record.id, "completed")

    reopened = RunJournal(workspace, data_dir=data_dir)
    assert reopened.get_scheduler_commit_ready(record.id, _WORKFLOW_ID) == ready
    assert reopened.get_scheduler_audit(record.id, _WORKFLOW_ID) == audit


def test_scheduler_commit_ready_read_fails_closed_after_tampering(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="commit tamper", source="gateway")
    journal.transition(record.id, "running")
    ready = _commit_ready()
    journal.record_scheduler_commit_ready(record.id, ready)
    target = (
        _run_dir(data_dir, record)
        / "scheduler-workflows"
        / _WORKFLOW_ID
        / "commit-ready.json"
    )
    target.write_text(
        json.dumps({**ready.to_dict(), "patchDigest": _digest("0")}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RunJournalStorageError, match="commit-ready"):
        journal.get_scheduler_commit_ready(record.id, _WORKFLOW_ID)


def test_scheduler_commit_ready_rejects_an_unsafe_storage_root(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="commit symlink", source="headless")
    journal.transition(record.id, "running")
    run_dir = _run_dir(data_dir, record)
    outside = tmp_path / "outside"
    outside.mkdir()
    (run_dir / "scheduler-workflows").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RunJournalStorageError, match="commit-ready"):
        journal.record_scheduler_commit_ready(record.id, _commit_ready())


def test_run_observation_returns_commit_ready_persistence_result(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    ready = _commit_ready()

    with observe_run(
        workspace=workspace,
        source="headless",
        title="observed commit ready",
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    ) as observation:
        assert observation.record_scheduler_commit_ready(ready) is True
        # Observation reports successful durability, including an idempotent
        # replay; only an unavailable or rejected write returns False.
        assert observation.record_scheduler_commit_ready(ready) is True
        assert (
            observation.record_scheduler_commit_ready(
                _commit_ready(patch_digest=_digest("0"))
            )
            is False
        )
        run_id = observation.run_id

    assert run_id is not None
    assert (
        RunJournal(workspace, data_dir=data_dir).get_scheduler_commit_ready(
            run_id,
            _WORKFLOW_ID,
        )
        == ready
    )


def _executor_assignment(write_paths: tuple[str, ...]) -> Assignment:
    return Assignment(
        workflow_id=_WORKFLOW_ID,
        task_id=_TASK_ID,
        assignment_id="assignment_executor_scope",
        role=WorkerRole.EXECUTOR,
        mode="implement",
        objective="Apply the bounded change.",
        scope=AssignmentScope(
            read_roots=(".",),
            write_paths=write_paths,
            forbidden_paths=(".git", ".env"),
        ),
        constraints=(),
        acceptance_criteria=(),
        input_refs=(),
        risk=AssignmentRisk.LOW,
        budget=AssignmentBudget(
            deadline_at="2999-01-01T00:00:00Z",
            max_model_calls=4,
            max_input_tokens=1024,
            max_output_tokens=1024,
        ),
        expected_result_type="ExecutionResult.v1",
    )


def _order(allowed_paths: tuple[str, ...]) -> ImplementationOrder:
    return ImplementationOrder(
        order_id="order_scope_regression",
        assignment_id="assignment_executor_scope",
        base_digest=_digest("b"),
        allowed_paths=allowed_paths,
        steps=(),
        criterion_ids=(),
        verification_requests=(),
    )


def test_executor_order_scope_may_narrow_assignment_write_scope() -> None:
    assignment = _executor_assignment(("src/app.py", "tests/test_app.py"))

    # Exact equality remains legal, and so does any lexical subset: the order
    # narrows (never widens) what the Assignment permits.
    assert _order_scope_within_assignment(
        _order(("src/app.py", "tests/test_app.py")),
        assignment,
    )
    assert _order_scope_within_assignment(_order(("src/app.py",)), assignment)


def test_executor_order_scope_still_rejects_paths_outside_assignment() -> None:
    assignment = _executor_assignment(("src/app.py", "tests/test_app.py"))

    # A superset or any path outside the Assignment write scope stays
    # fail-closed, exactly as the order contract parser enforces.
    assert not _order_scope_within_assignment(
        _order(("src/app.py", "docs/notes.md")),
        assignment,
    )
    assert not _order_scope_within_assignment(
        _order(("../outside.py",)),
        assignment,
    )


def test_executor_order_scope_empty_allowed_paths_writes_nothing() -> None:
    assignment = _executor_assignment(("src/app.py",))

    # An empty allowed_paths is the maximal narrowing: the order commits to
    # writing nothing at all, which can never exceed the Assignment scope.
    assert _order_scope_within_assignment(_order(()), assignment)
    assert _order_scope_within_assignment(
        _order(()),
        _executor_assignment(()),
    )
