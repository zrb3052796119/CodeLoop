from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import logging

import pytest

from minicode.run_lifecycle import observe_run
from minicode.run_journal import RunJournal


class RecordingJournal:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.transitions: list[tuple[str, str, str | None]] = []

    def create_run(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id="run_" + "a" * 32)

    def transition(self, run_id: str, status: str, *, reason: str | None = None):
        self.transitions.append((run_id, status, reason))


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
