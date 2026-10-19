"""Completion transitions persist as bounded metadata, never source evidence."""
import json

import pytest

from minicode.run_events import emit_event_safely
from minicode.run_journal import RunJournal, RunJournalValidationError
from minicode.run_lifecycle import observe_run


OWNER = "implementation_" + "a" * 32
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
REQ = "req_" + "c" * 20


def test_rejected_final_then_edit_then_accepted_same_owner_survives_real_journal(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "journal")
    with observe_run(workspace=workspace, source="headless", title="Completion continuity",
                     journal_factory=lambda _: journal) as observation:
        run_id = observation.run_id
        emit_event_safely(observation, "implementation.completion_review_started", step=2,
                         payload={"executorId": OWNER, "attempt": 1, "patchDigest": DIGEST_A})
        emit_event_safely(observation, "implementation.completion_decided", step=2, payload={
            "completionVersion": 1, "executorId": OWNER, "attempt": 1,
            "patchDigest": DIGEST_A, "status": "changes_required", "continuesSameExecutor": True,
            "requirements": [{"id": REQ, "status": "missing", "text": "PRIVATE_SOURCE_BODY",
                              "sourceEvidence": [{"path": "/private/user/file", "code": "SECRET_CODE"}]}],
            "findings": ["SECRET_REQUIREMENT_GAP"],
            "checks": {"status": "passed", "stdout": "SOURCE_OUTPUT"},
        })
        journal.append_event(run_id, "tool.finished", step=3,
                             payload={"toolName": "edit_file", "isError": False})
        emit_event_safely(observation, "implementation.completion_review_started", step=4,
                         payload={"executorId": OWNER, "attempt": 2, "patchDigest": DIGEST_B})
        emit_event_safely(observation, "implementation.completion_decided", step=4, payload={
            "completionVersion": 1, "executorId": OWNER, "attempt": 2,
            "patchDigest": DIGEST_B, "status": "approved", "continuesSameExecutor": False,
            "requirements": [{"id": REQ, "status": "covered", "rationale": "PRIVATE_EXPLANATION"}],
            "findings": [],
        })
    reloaded = RunJournal(workspace, data_dir=tmp_path / "journal")
    events = reloaded.list_events(run_id, limit=100).items
    decisions = [event for event in events if event.type == "implementation.completion_decided"]
    assert [(e.step, e.payload["attempt"], e.payload["status"], e.payload["executorId"])
            for e in decisions] == [(2, 1, "changes_required", OWNER), (4, 2, "approved", OWNER)]
    edited = next(e for e in events if e.type == "tool.finished")
    assert decisions[0].sequence < edited.sequence < decisions[1].sequence
    assert decisions[0].payload["patchDigest"] != decisions[1].payload["patchDigest"]
    assert decisions[0].payload["findingCount"] == 1
    assert decisions[0].payload["publicChecksStatus"] == "passed"
    assert decisions[1].payload["requirements"] == [{"id": REQ, "status": "covered"}]
    text = json.dumps([e.to_dict() for e in events])
    for marker in ("PRIVATE_", "SECRET_", "SOURCE_OUTPUT", "/private/user/file"):
        assert marker not in text
    assert reloaded.get_run(run_id).status == "completed"


def test_oversized_private_evidence_cannot_drop_bounded_completion_transition(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "journal")
    run = journal.create_run(source="headless", title="Projection bounds")
    journal.transition(run.id, "running")
    event = journal.append_event(run.id, "implementation.completion_decided", payload={
        "executorId": OWNER, "attempt": 1001, "patchDigest": DIGEST_A,
        "status": "inconclusive", "continuesSameExecutor": True,
        "requirements": [{"id": f"req_{i:020x}", "status": "unknown", "text": "SECRET_CODE" * 10000}
                         for i in range(1000)],
        "findings": ["SENSITIVE_REASON" * 10000] * 200,
        "checks": {"status": "inconclusive", "source": "PRIVATE_FULL_REPORT" * 10000},
    })
    assert event.payload["requirementCount"] == 1000
    assert len(event.payload["requirements"]) == 64
    assert event.payload["requirementsOmitted"] == 936
    assert event.payload["findingCount"] == 200
    assert len(json.dumps(event.to_dict()).encode()) < 16000
    assert "SECRET_CODE" not in json.dumps(event.to_dict())
    reread = RunJournal(workspace, data_dir=tmp_path / "journal").list_events(run.id, limit=100).items[-1]
    assert reread.payload == event.payload
    journal.transition(run.id, "completed")


@pytest.mark.parametrize("changed", [
    {"executorId": "another_owner"}, {"attempt": True}, {"attempt": 0},
    {"patchDigest": "unsafe arbitrary content"}, {"status": "success"},
    {"status": "approved", "continuesSameExecutor": True},
    {"status": "approved", "continuesSameExecutor": False, "patchDigest": None},
])
def test_invalid_identity_or_false_continuation_state_rejected(tmp_path, changed):
    journal = RunJournal(tmp_path, data_dir=tmp_path / "journal")
    run = journal.create_run(source="headless", title="Invalid transition")
    payload = {"executorId": OWNER, "attempt": 1, "patchDigest": DIGEST_A,
               "status": "changes_required", "continuesSameExecutor": True, **changed}
    with pytest.raises(RunJournalValidationError):
        journal.append_event(run.id, "implementation.completion_decided", payload=payload)
    journal.transition(run.id, "failed")


def test_reviewer_activity_is_separate_from_parent_terminal_state_and_is_content_free(tmp_path):
    journal = RunJournal(tmp_path, data_dir=tmp_path / "journal")
    run = journal.create_run(source="headless", title="Independent review activity")
    journal.transition(run.id, "running")
    event = journal.append_event(run.id, "implementation.completion_review_activity", payload={
        "reviewId": "completion_review_" + "d" * 24, "role": "completion_reviewer",
        "eventType": "model.completed", "operationId": "modelop_" + "e" * 32,
        "durationMs": 250, "toolCallCount": 1, "resultType": "tool_calls",
        "usage": {"source": "provider", "inputTokens": 100, "outputTokens": 25,
                  "cacheReadTokens": None, "cacheCreationTokens": None,
                  "secret": "SECRET_USAGE_FIELD"},
        "content": "SECRET_CODE", "toolInput": {"source": "SECRET_TEST_SOURCE"},
        "stdout": "PRIVATE_REVIEW_DIAGNOSTICS" * 10000,
    })
    assert journal.get_run(run.id).status == "running"
    assert event.payload["usage"] == {"source": "provider", "inputTokens": 100,
        "outputTokens": 25, "cacheReadTokens": None, "cacheCreationTokens": None}
    assert "SECRET_" not in json.dumps(event.to_dict())
    reread = RunJournal(tmp_path, data_dir=tmp_path / "journal").list_events(run.id, limit=100).items[-1]
    assert reread.payload == event.payload
    for child_event in ("task.outcome", "run.completed", "assistant.completed"):
        with pytest.raises(RunJournalValidationError):
            journal.append_event(run.id, "implementation.completion_review_activity", payload={
                "reviewId": "completion_review_" + "d" * 24, "role": "completion_reviewer",
                "eventType": child_event})
    assert journal.get_run(run.id).status == "running"
    journal.transition(run.id, "completed")


def test_unavailable_completion_capability_can_stop_partial_without_losing_event(tmp_path):
    journal = RunJournal(tmp_path, data_dir=tmp_path / "journal")
    run = journal.create_run(source="headless", title="Cannot continue without a public check")
    event = journal.append_event(run.id, "implementation.completion_decided", payload={
        "executorId": OWNER, "attempt": 1, "patchDigest": None,
        "status": "changes_required", "continuesSameExecutor": False, "canContinue": False,
        "checks": {"status": "inconclusive", "reason": "completion_checks_unavailable"},
    })
    assert event.payload["canContinue"] is False
    assert event.payload["continuesSameExecutor"] is False
    assert event.payload["status"] == "changes_required"
    reread = RunJournal(tmp_path, data_dir=tmp_path / "journal").list_events(run.id, limit=100).items[-1]
    assert reread.payload == event.payload
    journal.transition(run.id, "failed")
