from __future__ import annotations

import json
import multiprocessing
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from minicode.run_journal import (
    RunEvent,
    RunJournal,
    RunJournalOwnershipError,
    RunJournalTransitionError,
    RunJournalValidationError,
    RunRecord,
)


def _create_run_in_process(workspace: str, data_dir: str, queue) -> None:
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="Concurrent", source="headless")
    queue.put(record.id)


def _cursor(*values: object) -> str:
    import base64

    raw = json.dumps(list(values), separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _runs_root(data_dir: Path, workspace_id: str) -> Path:
    return data_dir / "dashboard" / "workspaces" / workspace_id / "runs"


def test_create_run_persists_versioned_record_and_initial_event(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)

    record = journal.create_run(
        title="Implement password=hidden-value safely",
        source="unknown",
    )
    loaded = journal.get_run(record.id)
    events = journal.list_events(record.id)

    assert isinstance(record, RunRecord)
    assert record.schema_version == 1
    assert record.id.startswith("run_")
    assert record.workspace_id.startswith("ws_")
    assert record.status == "queued"
    assert record.title == "Implement password=[REDACTED] safely"
    assert record.last_sequence == 1
    assert record.event_count == 1
    assert loaded == record
    assert len(events.items) == 1
    assert isinstance(events.items[0], RunEvent)
    assert events.items[0].sequence == 1
    assert events.items[0].type == "run.queued"
    assert events.items[0].run_id == record.id
    assert events.items[0].workspace_id == record.workspace_id
    assert json.loads(json.dumps(record.to_dict())) == record.to_dict()
    assert json.loads(json.dumps(events.items[0].to_dict())) == events.items[0].to_dict()

    run_root = data_dir / "dashboard" / "workspaces" / record.workspace_id / "runs"
    run_dir = run_root / record.id
    assert (run_dir / "metadata.json").is_file()
    assert (run_dir / "events.ndjson").is_file()
    assert len(list(run_root.glob("*/events.ndjson"))) == 1
    persisted = (run_dir / "events.ndjson").read_text(encoding="utf-8")
    assert persisted.endswith("\n")
    assert "hidden-value" not in persisted


def test_append_transition_and_terminal_idempotency_preserve_one_writer(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="State machine", source="gateway")

    running = journal.transition(record.id, "running")
    model_event = journal.append_event(
        record.id,
        "model.completed",
        step=1,
        payload={
            "summary": "Bearer very-secret-token",
            "env": {"API_KEY": "sk-test-secret"},
        },
    )
    completed = journal.transition(record.id, "completed")
    repeated = journal.transition(record.id, "completed")

    assert running.status == "running"
    assert running.started_at is not None
    assert model_event.sequence == 3
    assert model_event.payload == {
        "summary": "Bearer [REDACTED]",
        "env": "[REDACTED]",
    }
    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert completed.last_sequence == 4
    assert repeated == completed
    assert [event.type for event in journal.list_events(record.id).items] == [
        "run.queued",
        "run.started",
        "model.completed",
        "run.completed",
    ]

    with pytest.raises(RunJournalTransitionError):
        journal.transition(record.id, "running")
    with pytest.raises(RunJournalOwnershipError):
        RunJournal(workspace, data_dir=data_dir).append_event(
            record.id, "model.started"
        )

    persisted = (
        data_dir
        / "dashboard"
        / "workspaces"
        / record.workspace_id
        / "runs"
        / record.id
        / "events.ndjson"
    ).read_text(encoding="utf-8")
    assert "very-secret-token" not in persisted
    assert "sk-test-secret" not in persisted


def test_list_runs_scans_canonical_records_filters_and_pages_without_index(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    now = [datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)]
    journal = RunJournal(workspace, data_dir=data_dir, clock=lambda: now[0])
    created = []
    for index, source in enumerate(("tui", "headless", "gateway", "unknown")):
        now[0] = datetime(2026, 7, 16, 12, index, tzinfo=timezone.utc)
        record = journal.create_run(title=f"Run {index}", source=source)
        if index == 0:
            record = journal.transition(record.id, "running")
            record = journal.transition(record.id, "completed")
        elif index == 1:
            record = journal.transition(record.id, "failed")
        created.append(record)

    root = _runs_root(data_dir, created[0].workspace_id)
    (root / "index.json").write_text("{broken index", encoding="utf-8")
    first = journal.list_runs(limit=2)
    second = journal.list_runs(limit=2, cursor=first.next_cursor)
    completed = journal.list_runs(status="completed", source="tui")
    (root / "index.json").unlink()
    recovered = journal.list_runs(limit=100)

    ids = [record.id for record in (*first.items, *second.items)]
    assert ids == [record.id for record in reversed(created)]
    assert len(ids) == len(set(ids))
    assert first.has_more is True
    assert second.has_more is False
    assert first.known_total == 4
    assert first.by_status == {
        "queued": 2,
        "running": 0,
        "completed": 1,
        "failed": 1,
        "interrupted": 0,
        "cancel_requested": 0,
        "cancelled": 0,
    }
    assert [record.id for record in completed.items] == [created[0].id]
    assert [record.id for record in recovered.items] == [
        record.id for record in reversed(created)
    ]

    with pytest.raises(ValueError, match="Cursor"):
        journal.list_runs(status="failed", cursor=first.next_cursor)


def test_reads_reconcile_metadata_and_isolate_corrupt_or_partial_events(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    healthy = journal.create_run(title="Healthy")
    healthy = journal.transition(healthy.id, "running")
    corrupt = journal.create_run(title="Corrupt")

    root = _runs_root(data_dir, healthy.workspace_id)
    healthy_dir = root / healthy.id
    metadata_path = healthy_dir / "metadata.json"
    advanced = json.loads(metadata_path.read_text(encoding="utf-8"))
    advanced.update(
        {
            "status": "completed",
            "completedAt": advanced["updatedAt"],
            "lastSequence": 99,
            "eventCount": 99,
            "errorCount": 99,
        }
    )
    metadata_path.write_text(json.dumps(advanced), encoding="utf-8")

    events_path = healthy_dir / "events.ndjson"
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)
    events_path.write_text(
        lines[0]
        + '{"password":"hidden-value"}\n'
        + lines[1]
        + '{"eventId":"evt_partial","sequence":3',
        encoding="utf-8",
    )
    corrupt_metadata = root / corrupt.id / "metadata.json"
    corrupt_metadata.write_text('{"Bearer":"very-secret-token"', encoding="utf-8")
    tracked = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (metadata_path, events_path, corrupt_metadata)
    }

    recovered = journal.get_run(healthy.id)
    event_page = journal.list_events(healthy.id)
    run_page = journal.list_runs(limit=100)

    assert recovered is not None
    assert recovered.status == "running"
    assert recovered.completed_at is None
    assert recovered.last_sequence == 2
    assert recovered.event_count == 2
    assert recovered.error_count == 0
    assert [event.sequence for event in event_page.items] == [1, 2]
    assert {item["code"] for item in event_page.diagnostics} == {
        "event_invalid",
        "partial_final_event",
    }
    assert len(event_page.diagnostics) == 2
    assert [record.id for record in run_page.items] == [healthy.id]
    assert any(item["code"] == "run_read_failed" for item in run_page.diagnostics)
    assert "hidden-value" not in json.dumps(event_page.diagnostics)
    assert "very-secret-token" not in json.dumps(run_page.diagnostics)
    for path, (content, mtime) in tracked.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime


def test_reader_rejects_illegal_lifecycle_events_and_oversized_lines(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="Tampered")
    events_path = _runs_root(data_dir, record.workspace_id) / record.id / "events.ndjson"
    queued = json.loads(events_path.read_text(encoding="utf-8"))
    illegal = {
        **queued,
        "eventId": "evt_" + "a" * 32,
        "sequence": 2,
        "type": "run.completed",
    }
    events_path.write_text(
        json.dumps(queued)
        + "\n"
        + json.dumps(illegal)
        + "\n"
        + "x" * (32 * 1024 + 100)
        + "\n",
        encoding="utf-8",
    )

    recovered = journal.get_run(record.id)
    page = journal.list_events(record.id)

    assert recovered is not None
    assert recovered.status == "queued"
    assert recovered.completed_at is None
    assert [event.type for event in page.items] == ["run.queued"]
    assert {item["code"] for item in page.diagnostics} == {
        "event_invalid",
        "event_oversized",
    }


def test_create_rejects_intermediate_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    data_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (data_dir / "dashboard").symlink_to(outside, target_is_directory=True)
    journal = RunJournal(workspace, data_dir=data_dir)

    with pytest.raises(Exception, match="unsafe"):
        journal.create_run(title="Must stay contained")

    assert list(outside.iterdir()) == []


def test_retention_is_explicit_and_only_removes_valid_old_terminal_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    now = [datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)]
    journal = RunJournal(
        workspace,
        data_dir=data_dir,
        clock=lambda: now[0],
        max_runs=10,
        terminal_max_age=timedelta(days=1),
    )
    now[0] -= timedelta(days=10)
    old_completed = journal.create_run(title="Old complete")
    journal.transition(old_completed.id, "running")
    journal.transition(old_completed.id, "completed")
    old_queued = journal.create_run(title="Old queued")
    old_running = journal.create_run(title="Old running")
    journal.transition(old_running.id, "running")
    old_failed = journal.create_run(title="Old failed but cleanup fails")
    journal.transition(old_failed.id, "failed")
    now[0] += timedelta(days=10)
    recent = journal.create_run(title="Recent")
    journal.transition(recent.id, "failed")

    outside = tmp_path / "outside-run"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    escaped_id = "run_" + "a" * 32
    root = _runs_root(data_dir, old_completed.workspace_id)
    (root / escaped_id).symlink_to(outside, target_is_directory=True)
    original_rmtree = __import__("shutil").rmtree

    def fail_one(path: str | Path, *args, **kwargs):
        if Path(path).name == old_failed.id:
            raise OSError("password=hidden-value")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("minicode.run_journal.shutil.rmtree", fail_one)

    # Listing is read-only and never invokes retention.
    assert journal.get_run(old_completed.id) is not None
    result = journal.enforce_retention(now=now[0])

    assert result.deleted_count == 1
    assert journal.get_run(old_completed.id) is None
    assert journal.get_run(old_queued.id) is not None
    assert journal.get_run(old_running.id) is not None
    assert journal.get_run(old_failed.id) is not None
    assert journal.get_run(recent.id) is not None
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert {item["code"] for item in result.diagnostics} == {
        "retention_delete_failed",
        "retention_path_unsafe",
    }
    assert "hidden-value" not in json.dumps(result.diagnostics)


@pytest.mark.parametrize(
    "operation",
    [
        lambda journal, run_id: journal.create_run(title="x", source="web"),
        lambda journal, run_id: journal.create_run(
            title="x", metadata={"arbitrary": "value"}
        ),
        lambda journal, run_id: journal.append_event(run_id, "unknown.event"),
        lambda journal, run_id: journal.append_event(
            run_id, "model.started", step=True
        ),
        lambda journal, run_id: journal.append_event(
            run_id, "model.started", payload={"path": Path("secret")}
        ),
        lambda journal, run_id: journal.append_event(
            run_id, "model.started", payload={"number": float("nan")}
        ),
        lambda journal, run_id: journal.append_event(
            run_id, "model.started", payload={"items": list(range(101))}
        ),
        lambda journal, run_id: journal.append_event(
            run_id, "model.started", payload={"text": "x" * 4_097}
        ),
    ],
)
def test_invalid_run_and_event_inputs_are_rejected_before_writing(
    tmp_path: Path, operation
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    record = journal.create_run(title="Valid")
    events_path = _runs_root(
        tmp_path / "home" / ".mini-code", record.workspace_id
    ) / record.id / "events.ndjson"
    before = events_path.read_bytes()

    with pytest.raises(RunJournalValidationError):
        operation(journal, record.id)

    assert events_path.read_bytes() == before


def test_model_costed_is_the_only_new_closed_event_and_remains_sanitized(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="Cost allowlist")
    journal.transition(record.id, "running")

    costed = journal.append_event(
        record.id,
        "model.costed",
        payload={
            "operationId": "modelop_" + "a" * 32,
            "status": "priced",
            "amountNanoUsd": 530_000,
            "prompt": "Bearer cost-prompt-secret",
            "env": {"API_KEY": "sk-cost-secret"},
        },
    )
    count_after_cost = len(journal.list_events(record.id).items)

    with pytest.raises(RunJournalValidationError):
        journal.append_event(record.id, "model.cost.repriced")
    for unsafe_payload in (
        {"path": Path("private")},
        {"amountNanoUsd": float("nan")},
        {"items": list(range(101))},
        {"text": "x" * 4_097},
    ):
        with pytest.raises(RunJournalValidationError):
            journal.append_event(
                record.id, "model.costed", payload=unsafe_payload
            )

    serialized = json.dumps(costed.to_dict())
    persisted = (
        _runs_root(data_dir, record.workspace_id)
        / record.id
        / "events.ndjson"
    ).read_text(encoding="utf-8")
    assert costed.type == "model.costed"
    assert costed.payload["amountNanoUsd"] == 530_000
    assert len(journal.list_events(record.id).items) == count_after_cost
    for secret in ("cost-prompt-secret", "sk-cost-secret"):
        assert secret not in serialized
        assert secret not in persisted


def test_working_memory_observed_is_closed_and_content_is_sanitized(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="WorkingMemory allowlist")
    journal.transition(record.id, "running")

    observed = journal.append_event(
        record.id,
        "working_memory.observed",
        payload={
            "workingMemoryVersion": 1,
            "action": "protected",
            "scope": "process",
            "entries": 1,
            "maxEntries": 15,
            "protectedTokens": 4,
            "maxTokens": 4_000,
            "content": "Bearer working-memory-secret",
            "token": "sk-working-memory-secret",
        },
    )

    with pytest.raises(RunJournalValidationError):
        journal.append_event(record.id, "working_memory.global")

    persisted = (
        _runs_root(data_dir, record.workspace_id)
        / record.id
        / "events.ndjson"
    ).read_text(encoding="utf-8")
    assert observed.type == "working_memory.observed"
    assert observed.payload["entries"] == 1
    assert "working-memory-secret" not in persisted


def test_mcp_runtime_observed_is_closed_and_rejects_sensitive_fields(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.create_run(title="MCP runtime")
    journal.transition(record.id, "running")

    observed = journal.append_event(
        record.id,
        "mcp.runtime.observed",
        step=2,
        payload={
            "mcpVersion": 1,
            "serverKey": "mcpsrv_" + "a" * 32,
            "transport": "stdio",
            "activity": "tool_request",
            "outcome": "request_succeeded",
            "connectionAttempted": False,
            "protocol": "newline-json",
        },
    )

    assert observed.payload["serverKey"] == "mcpsrv_" + "a" * 32
    assert observed.step == 2
    forbidden_payloads = [
        {"mcpVersion": 2, "serverKey": "mcpsrv_" + "a" * 32, "transport": "stdio", "activity": "tool_request", "outcome": "request_succeeded", "connectionAttempted": False},
        {"mcpVersion": 1, "serverKey": "fake", "transport": "stdio", "activity": "tool_request", "outcome": "request_succeeded", "connectionAttempted": False},
        {"mcpVersion": 1, "serverKey": "mcpsrv_" + "a" * 32, "transport": "stdio", "activity": "tool_request", "outcome": "connected", "connectionAttempted": False},
        {"mcpVersion": 1, "serverKey": "mcpsrv_" + "a" * 32, "transport": "stdio", "activity": "tool_request", "outcome": "request_succeeded", "connectionAttempted": 1},
        {"mcpVersion": 1, "serverKey": "mcpsrv_" + "a" * 32, "transport": "stdio", "activity": "tool_request", "outcome": "request_succeeded", "connectionAttempted": False, "protocol": "http"},
        {"mcpVersion": 1, "serverKey": "mcpsrv_" + "a" * 32, "transport": "stdio", "activity": "tool_request", "outcome": "request_failed", "connectionAttempted": False, "failureKind": "secret raw exception"},
        {"mcpVersion": 1, "serverKey": "mcpsrv_" + "a" * 32, "transport": "stdio", "activity": "tool_request", "outcome": "request_succeeded", "connectionAttempted": False, "command": "Bearer journal-secret"},
    ]
    for payload in forbidden_payloads:
        with pytest.raises(RunJournalValidationError):
            journal.append_event(record.id, "mcp.runtime.observed", payload=payload)

    persisted = (_runs_root(data_dir, record.workspace_id) / record.id / "events.ndjson").read_text(encoding="utf-8")
    assert "journal-secret" not in persisted


def test_event_pagination_binds_run_and_rejects_boolean_sequence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    record = journal.create_run(title="Paged")
    journal.transition(record.id, "running")
    for step in range(4):
        journal.append_event(record.id, "model.completed", step=step)

    first = journal.list_events(record.id, limit=2)
    second = journal.list_events(record.id, limit=2, cursor=first.next_cursor)
    third = journal.list_events(record.id, limit=2, cursor=second.next_cursor)

    sequences = [event.sequence for page in (first, second, third) for event in page.items]
    assert sequences == [1, 2, 3, 4, 5, 6]
    assert len(sequences) == len(set(sequences))
    with pytest.raises(RunJournalValidationError):
        journal.list_events(
            record.id,
            cursor=_cursor("run_events", record.id, True),
        )


def test_read_only_empty_and_workspace_isolation_do_not_create_storage(
    tmp_path: Path,
) -> None:
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal_a = RunJournal(workspace_a, data_dir=data_dir)
    journal_b = RunJournal(workspace_b, data_dir=data_dir)

    assert journal_a.list_runs().items == ()
    assert not data_dir.exists()
    record = journal_a.create_run(title="Workspace A")

    assert journal_b.get_run(record.id) is None
    assert journal_b.list_runs().items == ()
    assert record.id not in json.dumps(journal_b.list_runs().diagnostics)
    assert _runs_root(data_dir, journal_a.workspace_id) != _runs_root(
        data_dir, journal_b.workspace_id
    )


def test_two_processes_create_unique_independent_run_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_create_run_in_process,
            args=(str(workspace), str(data_dir), queue),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    run_ids = {queue.get(timeout=2) for _ in processes}

    journal = RunJournal(workspace, data_dir=data_dir)
    page = journal.list_runs(limit=100)

    assert len(run_ids) == 2
    assert {record.id for record in page.items} == run_ids
    root = _runs_root(data_dir, page.items[0].workspace_id)
    assert not (root / "events.ndjson").exists()
    assert {
        path.parent.name for path in root.glob("*/events.ndjson")
    } == run_ids


def test_atomic_metadata_failure_never_returns_a_successful_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")

    def fail_replace(_source, _target):
        raise OSError("Bearer very-secret-token")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError):
        journal.create_run(title="Must fail")
    assert journal.list_runs().items == ()
