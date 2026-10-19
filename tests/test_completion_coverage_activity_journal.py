"""Independent coverage activity is durable metadata, never a parent outcome."""

import json

import pytest

from minicode.run_journal import RunJournal, RunJournalValidationError


@pytest.mark.parametrize("role,prefix", [
    ("completion_reviewer", "completion_review_"),
    ("coverage_auditor", "coverage_audit_"),
])
def test_review_scopes_persist_activity_without_content_or_parent_completion(tmp_path, role, prefix):
    journal = RunJournal(tmp_path, data_dir=tmp_path / "journal")
    run = journal.create_run(source="headless", title="Independent audit activity")
    journal.transition(run.id, "running")
    expected = []
    for kind in ("model.started", "model.costed", "tool.finished"):
        event = journal.append_event(run.id, "implementation.completion_review_activity", payload={
            "reviewId": prefix + "a" * 24, "role": role, "eventType": kind,
            "operationId": "modelop_" + "b" * 32, "toolName": "completion_coverage_audit_submit",
            "usage": {"source": "provider", "inputTokens": 42, "outputTokens": 7,
                      "cacheReadTokens": None, "cacheCreationTokens": None,
                      "prompt": "SECRET_PROMPT"},
            "durationMs": 250, "isError": False,
            "messages": [{"content": "SECRET_MESSAGES"}], "code": "SECRET_CODE",
        })
        expected.append(event.payload)
    reread = RunJournal(tmp_path, data_dir=tmp_path / "journal")
    actual = [event.payload for event in reread.list_events(run.id, limit=100).items
              if event.type == "implementation.completion_review_activity"]
    assert actual == expected
    assert [event["eventType"] for event in actual] == ["model.started", "model.costed", "tool.finished"]
    assert all(event["role"] == role and event["reviewId"] == prefix + "a" * 24 for event in actual)
    assert actual[1]["usage"] == {"source": "provider", "inputTokens": 42, "outputTokens": 7,
                                  "cacheReadTokens": None, "cacheCreationTokens": None}
    assert "SECRET_" not in json.dumps(actual)
    assert reread.get_run(run.id).status == "running"
    journal.transition(run.id, "completed")


@pytest.mark.parametrize("role,review_id,event_type", [
    ("completion_reviewer", "coverage_audit_" + "a" * 24, "model.started"),
    ("coverage_auditor", "completion_review_" + "a" * 24, "model.started"),
    ("unknown_auditor", "coverage_audit_" + "a" * 24, "model.started"),
    ([], "coverage_audit_" + "a" * 24, "model.started"),
    ("coverage_auditor", "coverage_audit_" + "a" * 23, "model.started"),
    ("coverage_auditor", "coverage_audit_" + "A" * 24, "model.started"),
    ("coverage_auditor", "coverage_audit_" + "a" * 24, "run.completed"),
    ("coverage_auditor", "coverage_audit_" + "a" * 24, "task.outcome"),
])
def test_mixed_or_unknown_scopes_and_child_terminals_are_rejected(tmp_path, role, review_id, event_type):
    journal = RunJournal(tmp_path, data_dir=tmp_path / "journal")
    run = journal.create_run(source="headless", title="Reject invalid audit scope")
    journal.transition(run.id, "running")
    before = journal.list_events(run.id, limit=100).items
    with pytest.raises(RunJournalValidationError):
        journal.append_event(run.id, "implementation.completion_review_activity", payload={
            "reviewId": review_id, "role": role, "eventType": event_type})
    assert journal.list_events(run.id, limit=100).items == before
    assert journal.get_run(run.id).status == "running"
    journal.transition(run.id, "completed")
