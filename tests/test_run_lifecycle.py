from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from minicode.memory_provenance import MemoryProvenanceBatch
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run


class RecordingJournal:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.transitions: list[tuple[str, str, str | None]] = []

    def create_run(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id="run_" + "a" * 32)

    def transition(self, run_id: str, status: str, *, reason: str | None = None):
        self.transitions.append((run_id, status, reason))


class CuratorOutboxJournal(RecordingJournal):
    def __init__(self, pending: list[tuple[str, str]]) -> None:
        super().__init__()
        self.pending = pending
        self.acknowledged: list[tuple[str, str]] = []
        self.fail_ack = False

    def pending_curator_completions(self, *, limit: int = 1_000):
        return tuple(self.pending[:limit])

    def acknowledge_curator_completion(
        self,
        run_id: str,
        completion_id: str,
    ) -> bool:
        if self.fail_ack:
            return False
        item = (run_id, completion_id)
        if item in self.pending:
            self.pending.remove(item)
            self.acknowledged.append(item)
            return True
        return False


class IncompleteCuratorOutboxJournal(CuratorOutboxJournal):
    def scan_pending_curator_completions(self, *, limit: int = 1_000):
        return SimpleNamespace(
            items=tuple(self.pending[:limit]),
            exhaustive=False,
        )


def test_observe_run_preserves_success_and_records_one_lifecycle(
    tmp_path: Path,
) -> None:
    journal = RecordingJournal()

    with observe_run(
        workspace=tmp_path,
        source="headless",
        title="Inspect password=hidden-value",
        session_id=None,
        journal_factory=lambda _workspace: journal,
    ):
        result = {"answer": "unchanged"}

    assert result == {"answer": "unchanged"}
    assert journal.created == [
        {
            "title": "Inspect password=hidden-value",
            "source": "headless",
            "session_id": None,
        }
    ]
    assert journal.transitions == [
        ("run_" + "a" * 32, "running", None),
        ("run_" + "a" * 32, "completed", None),
    ]


def test_observe_run_records_fixed_failed_reason_and_reraises_same_exception(
    tmp_path: Path,
) -> None:
    journal = RecordingJournal()
    business_error = RuntimeError("Bearer very-secret-token")

    with pytest.raises(RuntimeError) as raised:
        with observe_run(
            workspace=tmp_path,
            source="gateway",
            title="Task",
            journal_factory=lambda _workspace: journal,
        ):
            raise business_error

    assert raised.value is business_error
    assert journal.transitions == [
        ("run_" + "a" * 32, "running", None),
        ("run_" + "a" * 32, "failed", "execution_failed"),
    ]


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit(7)])
def test_observe_run_records_interrupted_and_preserves_base_exception(
    tmp_path: Path,
    interrupt: BaseException,
) -> None:
    journal = RecordingJournal()

    with pytest.raises(type(interrupt)) as raised:
        with observe_run(
            workspace=tmp_path,
            source="tui",
            title="Task",
            session_id="session_01",
            journal_factory=lambda _workspace: journal,
        ):
            raise interrupt

    assert raised.value is interrupt
    assert journal.created[0]["session_id"] == "session_01"
    assert journal.transitions == [
        ("run_" + "a" * 32, "running", None),
        ("run_" + "a" * 32, "interrupted", "execution_interrupted"),
    ]


@pytest.mark.parametrize(
    "failure_phase",
    ["factory", "create", "running", "completed", "failed", "interrupted"],
)
def test_journal_failures_never_change_success_or_business_exceptions(
    tmp_path: Path,
    failure_phase: str,
) -> None:
    class BrokenJournal(RecordingJournal):
        def create_run(self, **kwargs):
            if failure_phase == "create":
                raise OSError("password=journal-secret")
            return super().create_run(**kwargs)

        def transition(
            self, run_id: str, status: str, *, reason: str | None = None
        ):
            if status == failure_phase:
                raise OSError("Bearer journal-secret")
            return super().transition(run_id, status, reason=reason)

    journal = BrokenJournal()

    def factory(_workspace: Path):
        if failure_phase == "factory":
            raise OSError("api_key=journal-secret")
        return journal

    if failure_phase in {"failed", "interrupted"}:
        expected = (
            RuntimeError("business result")
            if failure_phase == "failed"
            else KeyboardInterrupt()
        )
        with pytest.raises(type(expected)) as raised:
            with observe_run(
                workspace=tmp_path,
                source="headless",
                title="Task",
                journal_factory=factory,
            ):
                raise expected
        assert raised.value is expected
    else:
        with observe_run(
            workspace=tmp_path,
            source="headless",
            title="Task",
            journal_factory=factory,
        ):
            result = "unchanged"
        assert result == "unchanged"


def test_disabled_observation_is_a_noop(tmp_path: Path) -> None:
    calls: list[Path] = []

    with observe_run(
        workspace=tmp_path,
        source="headless",
        title="Task",
        journal_factory=lambda workspace: calls.append(workspace),  # type: ignore[arg-type,return-value]
        enabled=False,
    ):
        result = "same"

    assert result == "same"
    assert calls == []


def test_curator_outbox_ack_failure_stops_newer_deliveries(
    tmp_path: Path,
) -> None:
    first = ("run_" + "b" * 32, "1" * 64)
    second = ("run_" + "c" * 32, "2" * 64)
    journal = CuratorOutboxJournal([first, second])
    journal.fail_ack = True
    delivered: list[str] = []

    with observe_run(
        workspace=tmp_path,
        source="headless",
        title="Task",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        assert observation.replay_pending_curator_completions(
            lambda completion_id: delivered.append(completion_id)
        ) is False

    assert delivered == [first[1]]
    assert journal.pending == [first, second]


def test_curator_outbox_reports_drained_only_after_followup_scan(
    tmp_path: Path,
) -> None:
    item = ("run_" + "b" * 32, "1" * 64)
    journal = CuratorOutboxJournal([item])

    with observe_run(
        workspace=tmp_path,
        source="headless",
        title="Task",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        assert observation.replay_pending_curator_completions(
            lambda _completion_id: None
        ) is True

    assert journal.acknowledged == [item]
    assert journal.pending == []


def test_curator_outbox_incomplete_scan_drains_healthy_items_but_blocks_advance(
    tmp_path: Path,
) -> None:
    item = ("run_" + "b" * 32, "1" * 64)
    journal = IncompleteCuratorOutboxJournal([item])
    delivered: list[str] = []

    with observe_run(
        workspace=tmp_path,
        source="headless",
        title="Task",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        assert observation.replay_pending_curator_completions(
            lambda completion_id: delivered.append(completion_id)
        ) is False

    assert delivered == [item[1]]
    assert journal.acknowledged == [item]
    assert journal.pending == []


def test_real_run_observation_settles_repeated_curator_completion_once(
    tmp_path: Path,
) -> None:
    import json

    from minicode.curator_schedule import CuratorScheduleStore
    from minicode.memory import MemoryManager
    from minicode.memory_pipeline import MemoryPipeline

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    pipeline = MemoryPipeline(
        MemoryManager(project_root=workspace, data_root=data_dir)
    )
    pipeline.initialize(workspace_path=str(workspace))
    pipeline._curator._run_interval = 10

    with observe_run(
        workspace=workspace,
        source="headless",
        title="repeat durable maintenance",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        completion_id = observation.curator_completion_id
        run_id = observation.run_id
        assert completion_id is not None
        assert run_id is not None
        assert pipeline.maintain(
            completion_id=completion_id,
            event_sink=observation,
        ) is None
        assert pipeline.maintain(
            completion_id=completion_id,
            event_sink=observation,
        ) is None

    state = CuratorScheduleStore(workspace).snapshot()
    assert state.pending_task_count == 1
    assert state.pinned_completion_ids == ()
    receipt_path = (
        data_dir
        / "dashboard"
        / "workspaces"
        / journal.workspace_id
        / "runs"
        / run_id
        / "curator_receipt.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["schemaVersion"] == 3
    assert receipt["state"] == "settled"


def test_real_journal_persists_only_redacted_lifecycle_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"

    with observe_run(
        workspace=workspace,
        source="headless",
        title=(
            "Inspect sk-test-secret Bearer very-secret-token "
            "password=hidden-value api_key=hidden"
        ),
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    ):
        pass

    journal = RunJournal(workspace, data_dir=data_dir)
    page = journal.list_runs()
    record = page.items[0]
    events = journal.list_events(record.id)
    serialized = str(record.to_dict()) + str([event.to_dict() for event in events.items])

    assert record.status == "completed"
    assert [event.type for event in events.items] == [
        "run.queued",
        "run.started",
        "run.completed",
    ]
    for secret in (
        "sk-test-secret",
        "very-secret-token",
        "hidden-value",
        "api_key=hidden",
    ):
        assert secret not in serialized


def test_production_lifecycle_enforces_run_retention_automatically(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"

    def journal_factory(resolved: Path) -> RunJournal:
        return RunJournal(
            resolved,
            data_dir=data_dir,
            max_runs=1,
            terminal_max_age=None,
            max_total_bytes=None,
        )

    for index in range(3):
        with observe_run(
            workspace=workspace,
            source="gateway",
            title=f"retained {index}",
            journal_factory=journal_factory,
        ):
            pass

    journal = journal_factory(workspace)
    assert len(journal.list_runs(limit=100).items) == 1


def test_observation_persists_rendered_memory_ids_readable_after_the_run(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    entry_ids = ["project-1785082406796413000-b6ecf281"]

    with observe_run(
        workspace=workspace,
        source="headless",
        title="Render one Memory",
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    ) as observation:
        run_id = observation.run_id
        observation.record_rendered_memory_ids(entry_ids)

    journal = RunJournal(workspace, data_dir=data_dir)
    assert journal.get_rendered_memory_ids(run_id) == tuple(entry_ids)


def test_observation_persists_rendered_and_written_memory_versions(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    rendered = [
        ("project-1785082406796413000-b6ecf281", "a" * 64),
    ]
    written = [
        ("project-1785082406796413001-a1b2c3d4", "b" * 64),
    ]

    with observe_run(
        workspace=workspace,
        source="headless",
        title="Versioned Memory",
        journal_factory=lambda resolved: RunJournal(
            resolved,
            data_dir=data_dir,
        ),
    ) as observation:
        run_id = observation.run_id
        observation.record_rendered_memory_versions(rendered)
        observation.record_written_memory_versions(written)

    journal = RunJournal(workspace, data_dir=data_dir)
    assert journal.get_rendered_memory_versions(run_id) == tuple(rendered)
    assert journal.get_written_memory_versions(run_id) == tuple(written)


def test_root_provenance_replay_is_idempotent_and_keeps_new_digest_for_same_id(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    entry_id = "project-1785082406796413000-b6ecf281"
    first = MemoryProvenanceBatch(
        effect="rendered",
        origin="task_start",
        authority_versions=((entry_id, "a" * 64),),
        receipt_id="b" * 32,
    )
    replacement = MemoryProvenanceBatch(
        effect="rendered",
        origin="context_compaction",
        authority_versions=((entry_id, "c" * 64),),
        receipt_id="d" * 32,
        context_operation_id="ctxop_" + "e" * 32,
    )

    with observe_run(
        workspace=workspace,
        source="headless",
        title="Versioned Memory provenance",
        journal_factory=lambda resolved: RunJournal(
            resolved,
            data_dir=data_dir,
        ),
    ) as observation:
        run_id = observation.run_id
        observation.record_memory_provenance(first)
        observation.record_memory_provenance(first)
        observation.record_memory_provenance(replacement)

    journal = RunJournal(workspace, data_dir=data_dir)
    snapshot = journal.get_memory_provenance(run_id, "rendered")
    assert snapshot.materializations == (
        (entry_id, "a" * 64),
        (entry_id, "c" * 64),
    )
    assert tuple(record.batch for record in snapshot.records) == (
        first,
        replacement,
    )
    assert all(record.actor.kind == "run_root" for record in snapshot.records)
    assert all(record.actor.subagent_id is None for record in snapshot.records)


def test_provenance_is_retried_before_run_becomes_terminal(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"

    class FlakyJournal(RunJournal):
        def __init__(self) -> None:
            super().__init__(workspace, data_dir=data_dir)
            self.attempts = 0

        def record_memory_provenance(self, *args, **kwargs) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise OSError("transient provenance failure")
            return super().record_memory_provenance(*args, **kwargs)

    journal = FlakyJournal()
    batch = MemoryProvenanceBatch(
        effect="rendered",
        origin="task_start",
        authority_versions=(
            ("project-1785082406796413000-b6ecf281", "a" * 64),
        ),
        receipt_id="b" * 32,
    )

    with observe_run(
        workspace=workspace,
        source="headless",
        title="Retry provenance",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        run_id = observation.run_id
        assert observation.record_memory_provenance(batch) is False

    assert journal.attempts == 2
    snapshot = journal.get_memory_provenance(run_id, "rendered")
    assert snapshot.coverage == "complete"
    assert tuple(record.batch for record in snapshot.records) == (batch,)


def test_unacknowledged_exact_provenance_degrades_aggregate_at_terminal(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    lost_receipt = "c" * 32

    class SelectiveJournal(RunJournal):
        def record_memory_provenance(self, run_id, batch, **kwargs) -> None:
            if batch.receipt_id == lost_receipt:
                raise OSError("persistent batch-specific failure")
            return super().record_memory_provenance(
                run_id,
                batch,
                **kwargs,
            )

    journal = SelectiveJournal(workspace, data_dir=data_dir)
    lost = MemoryProvenanceBatch(
        effect="rendered",
        origin="task_start",
        authority_versions=(
            ("project-1785082406796413000-b6ecf281", "d" * 64),
        ),
        receipt_id=lost_receipt,
    )
    recorded = MemoryProvenanceBatch(
        effect="rendered",
        origin="context_compaction",
        authority_versions=(
            ("project-1785082406796413001-c6ecf282", "e" * 64),
        ),
        receipt_id="f" * 32,
        context_operation_id="ctxop_" + "1" * 32,
    )

    with observe_run(
        workspace=workspace,
        source="headless",
        title="Degrade missing provenance",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        run_id = observation.run_id
        assert observation.record_memory_provenance(lost) is False
        assert observation.record_memory_provenance(recorded) is True

    snapshot = journal.get_memory_provenance(run_id, "rendered")
    assert snapshot.materializations == recorded.authority_versions
    assert snapshot.coverage == "partial"
    assert any(record.batch.coverage == "unknown" for record in snapshot.records)


def test_terminal_marker_dominates_when_exact_and_unknown_sidecar_writes_fail(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"

    class PersistentlyFailingJournal(RunJournal):
        def record_memory_provenance(self, run_id, batch, **kwargs) -> None:
            if batch.origin == "task_start":
                raise OSError("task-start provenance sidecar unavailable")
            return super().record_memory_provenance(
                run_id,
                batch,
                **kwargs,
            )

    journal = PersistentlyFailingJournal(workspace, data_dir=data_dir)
    recorded = MemoryProvenanceBatch(
        effect="rendered",
        origin="context_compaction",
        authority_versions=(
            ("project-1785082406796413000-b6ecf281", "2" * 64),
        ),
        receipt_id="3" * 32,
        context_operation_id="ctxop_" + "4" * 32,
    )
    lost = MemoryProvenanceBatch(
        effect="rendered",
        origin="task_start",
        authority_versions=(
            ("project-1785082406796413001-c6ecf282", "5" * 64),
        ),
        receipt_id="6" * 32,
    )

    with observe_run(
        workspace=workspace,
        source="headless",
        title="Terminal provenance closure",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        run_id = observation.run_id
        assert observation.record_memory_provenance(recorded) is True
        assert observation.record_memory_provenance(lost) is False

    snapshot = journal.get_memory_provenance(run_id, "rendered")
    assert snapshot.materializations == recorded.authority_versions
    assert snapshot.coverage == "partial"
    terminal = journal.list_events(run_id, limit=100).items[-1]
    assert terminal.type == "run.completed"
    assert terminal.payload["memoryProvenanceGaps"] == ["rendered"]


def test_recording_journal_without_memory_binding_does_not_raise() -> None:
    journal = RecordingJournal()

    with observe_run(
        workspace=Path("."),
        source="headless",
        title="No Memory binding seam",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        observation.record_rendered_memory_ids(
            ["project-1785082406796413000-b6ecf281"]
        )  # must not raise even though RecordingJournal lacks this method


def test_logger_failure_is_isolated_with_journal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "minicode.run_lifecycle._logger.warning",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Cookie=logger-secret")
        ),
    )

    with observe_run(
        workspace=tmp_path,
        source="headless",
        title="Task",
        journal_factory=lambda _workspace: (_ for _ in ()).throw(
            OSError("password=journal-secret")
        ),
    ):
        result = "unchanged"

    assert result == "unchanged"


def test_journal_failure_log_is_generic_and_secret_free(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="minicode.run_lifecycle")

    with observe_run(
        workspace=tmp_path,
        source="gateway",
        title="Task",
        journal_factory=lambda _workspace: (_ for _ in ()).throw(
            OSError("Authorization=journal-secret")
        ),
    ):
        pass

    assert "observation unavailable during create" in caplog.text
    assert "journal-secret" not in caplog.text


def test_unwritable_shaped_data_path_degrades_to_noop(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_path = tmp_path / "not-a-directory"
    data_path.write_text("occupied", encoding="utf-8")

    with observe_run(
        workspace=workspace,
        source="headless",
        title="Task",
        journal_factory=lambda resolved: RunJournal(
            resolved, data_dir=data_path
        ),
    ):
        result = "same"

    assert result == "same"
    assert data_path.read_text(encoding="utf-8") == "occupied"
