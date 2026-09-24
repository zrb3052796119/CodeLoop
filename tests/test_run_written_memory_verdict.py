"""Response feedback reaches written lessons as weak, version-bound evidence.

The user evaluates a whole response, not every claim later extracted from it.
That signal may update ordinary usefulness, but only a claim-specific verdict
may corroborate or reject a written Memory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from minicode.conversation import (
    _apply_written_run_outcome_feedback,
)
from minicode.memory import MemoryManager, MemoryScope
from minicode.run_journal import RunJournal, RunJournalOwnershipError


def _entry(manager: MemoryManager, entry_id: str):
    return manager.memories[MemoryScope.PROJECT]._id_index.get(entry_id)


def _stored_lesson(workspace: Path) -> tuple[str, str]:
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        category="task_context",
        content="Changed src/lease.py, after which pytest tests/test_lease.py passed.",
    )
    assert entry is not None
    return entry.id, entry.approval_content_hash


class _Journal:
    """Only the seam the verdict path uses."""

    def __init__(
        self,
        written: tuple[tuple[str, str], ...] | None,
    ) -> None:
        self._written = written

    def get_written_memory_versions(
        self,
        run_id: str,
    ) -> tuple[tuple[str, str], ...] | None:
        del run_id
        return self._written


@pytest.mark.parametrize("signal", ["reject", "correct"])
def test_a_wrong_turn_records_weak_failure_without_rejecting_each_lesson(
    tmp_path: Path, signal: str
) -> None:
    version = _stored_lesson(tmp_path)
    entry_id, _digest = version

    _apply_written_run_outcome_feedback(
        tmp_path, _Journal((version,)), "run-1", signal
    )

    entry = _entry(MemoryManager(project_root=tmp_path), entry_id)
    assert entry is not None
    assert entry.failure_count == 1
    assert entry.corroborated_failure_count == 0
    assert entry.approval_status == "approved"
    assert entry.lifecycle_status == "active"


def test_whole_response_failure_does_not_create_a_rejection_audit(
    tmp_path: Path,
) -> None:
    version = _stored_lesson(tmp_path)
    entry_id, _digest = version

    _apply_written_run_outcome_feedback(
        tmp_path, _Journal((version,)), "run-1", "reject"
    )

    audit = MemoryManager(project_root=tmp_path).get_approval_audit(entry_id)
    rejections = [
        item
        for item in audit
        if item.get("action") in {"reject", "feedback_quarantine"}
    ]
    assert rejections == []


def test_acceptance_is_ordinary_evidence_instead_of_corroboration(
    tmp_path: Path,
) -> None:
    version = _stored_lesson(tmp_path)
    entry_id, _digest = version

    _apply_written_run_outcome_feedback(
        tmp_path, _Journal((version,)), "run-1", "accept"
    )

    entry = _entry(MemoryManager(project_root=tmp_path), entry_id)
    assert entry is not None
    assert entry.approval_status != "rejected"
    assert entry.success_count == 1
    assert entry.corroborated_success_count == 0


def test_a_run_that_wrote_nothing_is_left_alone(tmp_path: Path) -> None:
    entry_id, _digest = _stored_lesson(tmp_path)

    _apply_written_run_outcome_feedback(
        tmp_path, _Journal(None), "run-1", "reject"
    )

    entry = _entry(MemoryManager(project_root=tmp_path), entry_id)
    assert entry is not None
    assert entry.approval_status != "rejected"


def test_a_broken_journal_never_breaks_recording_the_signal(tmp_path: Path) -> None:
    """The verdict is best-effort; feedback must succeed regardless."""

    class _Exploding:
        def get_written_memory_versions(
            self,
            run_id: str,
        ) -> tuple[tuple[str, str], ...]:
            raise RuntimeError("journal unavailable")

    entry_id, _digest = _stored_lesson(tmp_path)

    _apply_written_run_outcome_feedback(
        tmp_path, _Exploding(), "run-1", "reject"
    )

    entry = _entry(MemoryManager(project_root=tmp_path), entry_id)
    assert entry is not None
    assert entry.approval_status != "rejected"


# ── Journal storage contract ────────────────────────────────────────────


ENTRY_A = "project-1785082406796413000-b6ecf281"
ENTRY_B = "project-1785082406796413001-a1b2c3d4"


def _started_run(workspace: Path) -> tuple[RunJournal, str, Path]:
    data_dir = workspace / "home" / ".mini-code"
    project = workspace / "workspace"
    project.mkdir(exist_ok=True)
    journal = RunJournal(project, data_dir=data_dir)
    record = journal.create_run(title="Answer a user", source="gateway")
    journal.transition(record.id, "running")
    return journal, record.id, data_dir


def test_written_ids_round_trip_through_the_journal(tmp_path: Path) -> None:
    journal, run_id, _ = _started_run(tmp_path)

    journal.record_written_memory_ids(run_id, [ENTRY_A, ENTRY_B])

    assert journal.get_written_memory_ids(run_id) == (ENTRY_A, ENTRY_B)


def test_written_and_rendered_sidecars_do_not_collide(tmp_path: Path) -> None:
    """Two questions, two files: what was shown in, and what came out."""
    journal, run_id, _ = _started_run(tmp_path)

    journal.record_rendered_memory_ids(run_id, [ENTRY_A])
    journal.record_written_memory_ids(run_id, [ENTRY_B])

    assert journal.get_rendered_memory_ids(run_id) == (ENTRY_A,)
    assert journal.get_written_memory_ids(run_id) == (ENTRY_B,)


def test_an_unstarted_run_has_no_written_ids(tmp_path: Path) -> None:
    journal, run_id, _ = _started_run(tmp_path)

    assert journal.get_written_memory_ids(run_id) is None


def test_a_non_writer_process_cannot_record_written_ids(tmp_path: Path) -> None:
    journal, run_id, data_dir = _started_run(tmp_path)
    other = RunJournal(tmp_path / "workspace", data_dir=data_dir)

    with pytest.raises(RunJournalOwnershipError):
        other.record_written_memory_ids(run_id, [ENTRY_A])


def test_a_terminal_run_cannot_accept_written_ids(tmp_path: Path) -> None:
    """A terminal transition releases the writer mutex, so ownership fails first."""
    journal, run_id, _ = _started_run(tmp_path)
    journal.transition(run_id, "completed")

    with pytest.raises(RunJournalOwnershipError):
        journal.record_written_memory_ids(run_id, [ENTRY_A])


def test_written_id_storage_rejects_a_symlinked_sidecar(tmp_path: Path) -> None:
    """Same hardening as the rendered sidecar: it shares one implementation."""
    journal, run_id, _ = _started_run(tmp_path)
    journal.record_written_memory_ids(run_id, [ENTRY_A])

    run_dir = next(path for path in tmp_path.rglob("memory_written.json")).parent
    target = run_dir / "memory_written.json"
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    target.unlink()
    target.symlink_to(elsewhere)

    with pytest.raises(Exception) as caught:
        journal.get_written_memory_ids(run_id)
    assert "unsafe" in str(caught.value).lower()


def _sink_calls() -> tuple[Any, list[list[str]]]:
    seen: list[list[str]] = []

    class _Sink:
        def record_written_memory_ids(self, entry_ids: list[str]) -> None:
            seen.append(list(entry_ids))

    return _Sink(), seen


def test_the_sink_seam_forwards_a_written_id() -> None:
    from minicode.run_events import record_written_memory_id_safely

    sink, seen = _sink_calls()
    record_written_memory_id_safely(sink, ENTRY_A)

    assert seen == [[ENTRY_A]]


def test_the_sink_seam_forwards_all_written_claim_ids_together() -> None:
    from minicode.run_events import record_written_memory_ids_safely

    sink, seen = _sink_calls()
    record_written_memory_ids_safely(sink, [ENTRY_A, ENTRY_B, ENTRY_A])

    assert seen == [[ENTRY_A, ENTRY_B]]


@pytest.mark.parametrize("entry_id", [None, "", 42, [], {"id": "x"}])
def test_the_sink_seam_ignores_anything_that_is_not_an_id(entry_id: object) -> None:
    from minicode.run_events import record_written_memory_id_safely

    sink, seen = _sink_calls()
    record_written_memory_id_safely(sink, entry_id)

    assert seen == []


def test_the_sink_seam_tolerates_a_sink_without_the_hook() -> None:
    from minicode.run_events import record_written_memory_id_safely

    record_written_memory_id_safely(object(), ENTRY_A)


def test_the_real_observation_carries_a_written_id_into_the_journal(
    tmp_path: Path,
) -> None:
    """End-to-end over the live seam, not a double.

    agent_loop passes the RunObservation itself as the event sink, and
    reflection runs inside observe_run's body -- before the terminal
    transition that releases the writer mutex. Both have to hold or the
    binding is silently dropped and the user's verdict never lands.
    """
    from minicode.run_events import record_written_memory_id_safely
    from minicode.run_lifecycle import observe_run

    project = tmp_path / "workspace"
    project.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"

    def _factory(workspace: Path) -> RunJournal:
        return RunJournal(workspace, data_dir=data_dir)

    with observe_run(
        workspace=project,
        source="gateway",
        title="Answer a user",
        session_id=None,
        journal_factory=_factory,
    ) as observation:
        run_id = observation.run_id
        assert run_id is not None
        record_written_memory_id_safely(observation, ENTRY_A)

    assert RunJournal(project, data_dir=data_dir).get_written_memory_ids(run_id) == (
        ENTRY_A,
    )
