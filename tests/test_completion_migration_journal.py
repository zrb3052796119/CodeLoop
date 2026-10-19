"""Migration progress remains observable without storing private conversation text."""
import json

import pytest

from minicode.run_journal import RunJournal, RunJournalValidationError


def metadata(tmp_path):
    return {"reviewId": "completion_review_" + "a" * 24,
        "candidateDigest": "sha256:" + "b" * 64,
        "completedCount": 1, "totalCount": 2, "remainingCount": 1,
        "evidenceRef": str(tmp_path / "00001-completed-unit.json")}


def test_migration_metadata_survives_disk_without_changing_run_outcome(tmp_path):
    journal = RunJournal(tmp_path, data_dir=tmp_path / "journal")
    run = journal.create_run(source="headless", title="Migration progress")
    journal.transition(run.id, "running")
    progress = metadata(tmp_path)
    checkpoint = {k: v for k, v in progress.items() if k != "evidenceRef"}
    checkpoint.update(checkpointRef=str(tmp_path / "00002-continuation.json"),
        checkpointDigest="sha256:" + "c" * 64, messageCount=8)
    stale = {"executorId": "implementation_" + "d" * 32, "reason": "candidate_changed"}
    expected = []
    for event_type, payload in [
        ("implementation.completion_migration_progress", progress),
        ("implementation.completion_migration_checkpoint", checkpoint),
        ("implementation.migration_checkpoint_stale", stale),
    ]:
        event = journal.append_event(run.id, event_type, payload={**payload,
            "messages": [{"content": "SECRET_CONVERSATION" * 10000}],
            "code": "SECRET_CODE", "output": "SECRET_TOOL_RESULT"})
        assert event.payload == payload
        expected.append((event_type, payload))
    reopened = RunJournal(tmp_path, data_dir=tmp_path / "journal")
    events = reopened.list_events(run.id, limit=100).items
    assert [(event.type, event.payload) for event in events if event.type.startswith("implementation.")] == expected
    assert "SECRET_" not in json.dumps([event.to_dict() for event in events])
    assert reopened.get_run(run.id).status == "running"
    journal.transition(run.id, "completed")


@pytest.mark.parametrize("change", [
    {"completedCount": True}, {"remainingCount": 0}, {"totalCount": -1},
    {"reviewId": "foreign"}, {"candidateDigest": "arbitrary text"},
    {"evidenceRef": "relative.json"},
])
def test_invalid_migration_metadata_cannot_be_recorded_as_progress(tmp_path, change):
    journal = RunJournal(tmp_path, data_dir=tmp_path / "journal")
    run = journal.create_run(source="headless", title="Invalid progress")
    with pytest.raises(RunJournalValidationError):
        journal.append_event(run.id, "implementation.completion_migration_progress",
            payload={**metadata(tmp_path), **change})
    journal.transition(run.id, "failed")
