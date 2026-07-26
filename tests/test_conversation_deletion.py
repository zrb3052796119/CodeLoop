from __future__ import annotations

import hashlib
import multiprocessing
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from minicode.conversation_deletion import (
    ConversationDeletionAuthority,
    ConversationDeletionError,
)
from minicode.conversation_turn_store import ConversationTurnStore, create_turn_id
from minicode.conversation_turn_store import TurnStoreError
from minicode.run_journal import RunJournal, RunJournalStorageError
from minicode.session import create_new_session, load_session, save_session
from minicode.session_store import SessionWriteConflictError


def _tree_facts(root: Path) -> dict[str, tuple[str, int, int]]:
    if not root.exists():
        return {}
    facts: dict[str, tuple[str, int, int]] = {}
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            raw = path.read_bytes()
            facts[path.relative_to(root).as_posix()] = (
                hashlib.sha256(raw).hexdigest(),
                len(raw),
                path.stat().st_mtime_ns,
            )
    return facts


def _exit_after_conversation_turn_cleanup(
    workspace: str,
    data_dir: str,
    session_id: str,
    revision: str,
) -> None:
    import minicode.session as child_session_module

    child_data_dir = Path(data_dir)
    child_session_module.MINI_CODE_DIR = child_data_dir
    child_session_module.SESSIONS_DIR = child_data_dir / "sessions"
    authority = ConversationDeletionAuthority(workspace, data_dir=child_data_dir)
    with authority._ledger.coordination():
        authority._ledger.start("conversation", session_id, revision)
    authority._turns.delete_terminal_for_session(session_id)
    os._exit(73)


def _save_fenced_session_in_process(
    workspace: str,
    data_dir: str,
    session_id: str,
    results: object,
) -> None:
    import minicode.session as child_session_module

    child_data_dir = Path(data_dir)
    child_session_module.MINI_CODE_DIR = child_data_dir
    child_session_module.SESSIONS_DIR = child_data_dir / "sessions"
    session = child_session_module.load_session(session_id)
    assert session is not None and session.workspace == workspace
    session.messages.append({"role": "assistant", "content": "must be blocked"})
    try:
        child_session_module.save_session(session, force_full=True)
        results.put("saved")  # type: ignore[attr-defined]
    except SessionWriteConflictError:
        results.put("blocked")  # type: ignore[attr-defined]


@pytest.fixture
def deletion_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr("minicode.session.MINI_CODE_DIR", data_dir)
    monkeypatch.setattr("minicode.session.SESSIONS_DIR", data_dir / "sessions")
    return workspace, data_dir


def _terminal_turn(
    store: ConversationTurnStore,
    *,
    session_id: str,
    run_id: str,
) -> str:
    turn_id = create_turn_id()
    assert store.claim(turn_id=turn_id, fingerprint="sha256:" + "a" * 64).disposition == "claimed"
    assert store.mark_running(turn_id).execution_started is True
    store.attach_session(turn_id, session_id=session_id, created_session=False)
    store.attach_run(turn_id, run_id=run_id)
    assert store.begin_commit(turn_id).commit_allowed is True
    store.mark_completed(
        turn_id,
        commit_marker={
            "schemaVersion": 1,
            "userMessageIndex": 0,
            "assistantMessageIndex": 1,
        },
    )
    return turn_id


def test_conversation_authority_previews_and_deletes_complete_saved_conversation(
    deletion_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    session.messages = [
        {"role": "user", "content": "private request must disappear"},
        {"role": "assistant", "content": "private answer must disappear"},
    ]
    save_session(session, force_full=True)

    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(
        title="private run title must disappear",
        source="gateway",
        session_id=session.session_id,
    )
    journal.transition(run.id, "running")
    journal.transition(run.id, "completed")
    turn_store = ConversationTurnStore(workspace, data_dir=data_dir)
    turn_id = _terminal_turn(
        turn_store,
        session_id=session.session_id,
        run_id=run.id,
    )

    authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority.snapshot(session.session_id)

    assert preview == {
        "schemaVersion": 1,
        "generatedAt": preview["generatedAt"],
        "mode": "read-write",
        "kind": "conversation",
        "target": {"sessionId": session.session_id},
        "status": "ready",
        "deletionRevision": preview["deletionRevision"],
        "affected": {"sessions": 1, "turns": 1, "runs": 1},
        "blockers": [],
        "diagnostics": [],
    }
    assert str(preview["deletionRevision"]).startswith("delrev_")
    assert "private request" not in str(preview)
    assert str(workspace) not in str(preview)

    result = authority.delete(session.session_id, str(preview["deletionRevision"]))

    assert result["status"] == "completed"
    assert result["deleted"] == {"sessions": 1, "turns": 1, "runs": 1}
    assert load_session(session.session_id) is None
    assert turn_store.get(turn_id) is None
    assert journal.get_run(run.id) is None


def test_conversation_preview_is_strictly_read_only(
    deletion_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "preview only"}]
    save_session(session, force_full=True)
    authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    before_workspace = _tree_facts(workspace)
    before_data = _tree_facts(data_dir)
    before_paths = {path.relative_to(data_dir) for path in data_dir.rglob("*")}

    preview = authority.snapshot(session.session_id)

    assert preview["status"] == "ready"
    assert _tree_facts(workspace) == before_workspace
    assert _tree_facts(data_dir) == before_data
    assert {path.relative_to(data_dir) for path in data_dir.rglob("*")} == before_paths
    assert authority._ledger.read_fence("conversation", session.session_id) is None
    assert authority._ledger.read_receipt("conversation", session.session_id) is None


def test_conversation_deletion_preserves_adjacent_unlinked_and_other_workspace_data(
    deletion_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = deletion_workspace
    other_workspace = workspace.parent / "other-workspace"
    other_workspace.mkdir()
    target = create_new_session(str(workspace))
    neighbor = create_new_session(str(workspace))
    foreign = create_new_session(str(other_workspace))
    for session in (target, neighbor, foreign):
        session.messages = [{"role": "user", "content": session.session_id}]
        save_session(session, force_full=True)

    journal = RunJournal(workspace, data_dir=data_dir)
    target_run = journal.create_run(
        title="target", source="gateway", session_id=target.session_id
    )
    neighbor_run = journal.create_run(
        title="neighbor", source="gateway", session_id=neighbor.session_id
    )
    unlinked_run = journal.create_run(
        title="unlinked", source="gateway", session_id=None
    )
    for run in (target_run, neighbor_run, unlinked_run):
        journal.transition(run.id, "running")
        journal.transition(run.id, "completed")
    turns = ConversationTurnStore(workspace, data_dir=data_dir)
    target_turn = _terminal_turn(
        turns,
        session_id=target.session_id,
        run_id=target_run.id,
    )
    neighbor_turn = _terminal_turn(
        turns,
        session_id=neighbor.session_id,
        run_id=neighbor_run.id,
    )
    foreign_journal = RunJournal(other_workspace, data_dir=data_dir)
    foreign_run = foreign_journal.create_run(
        title="foreign", source="gateway", session_id=foreign.session_id
    )
    foreign_journal.transition(foreign_run.id, "running")
    foreign_journal.transition(foreign_run.id, "completed")

    authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority.snapshot(target.session_id)
    result = authority.delete(target.session_id, str(preview["deletionRevision"]))

    assert result["status"] == "completed"
    assert load_session(target.session_id) is None
    assert turns.get(target_turn) is None
    assert journal.get_run(target_run.id) is None
    assert load_session(neighbor.session_id) is not None
    assert turns.get(neighbor_turn) is not None
    assert journal.get_run(neighbor_run.id) is not None
    assert journal.get_run(unlinked_run.id) is not None
    assert load_session(foreign.session_id) is not None
    assert foreign_journal.get_run(foreign_run.id) is not None


def test_active_turn_and_run_block_before_any_conversation_data_is_deleted(
    deletion_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "keep while active"}]
    save_session(session, force_full=True)

    turns = ConversationTurnStore(workspace, data_dir=data_dir)
    turn_id = create_turn_id()
    turns.claim(turn_id=turn_id, fingerprint="sha256:" + "b" * 64)
    turns.mark_running(turn_id)
    turns.attach_session(turn_id, session_id=session.session_id, created_session=False)
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(
        title="active",
        source="gateway",
        session_id=session.session_id,
    )
    journal.transition(run.id, "running")
    turns.attach_run(turn_id, run_id=run.id)

    authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority.snapshot(session.session_id)

    assert preview["status"] == "busy"
    assert preview["affected"] == {"sessions": 1, "turns": 0, "runs": 0}
    assert preview["blockers"] == [
        {"code": "active_turn"},
        {"code": "active_run"},
    ]
    with pytest.raises(ConversationDeletionError) as blocked:
        authority.delete(session.session_id, str(preview["deletionRevision"]))
    assert blocked.value.code == "deletion_target_busy"
    assert load_session(session.session_id) is not None
    assert turns.get(turn_id) is not None
    assert journal.get_run(run.id) is not None


def test_conversation_partial_failure_is_visible_and_retryable_after_restart(
    deletion_workspace: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "delete after retry"}]
    save_session(session, force_full=True)
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(
        title="terminal", source="gateway", session_id=session.session_id
    )
    journal.transition(run.id, "running")
    journal.transition(run.id, "completed")
    turns = ConversationTurnStore(workspace, data_dir=data_dir)
    turn_id = _terminal_turn(turns, session_id=session.session_id, run_id=run.id)
    authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority.snapshot(session.session_id)

    def fail_once(_session_id: str) -> int:
        raise RunJournalStorageError("private path and secret must not escape")

    monkeypatch.setattr(authority._runs, "delete_terminal_for_session", fail_once)
    partial = authority.delete(
        session.session_id, str(preview["deletionRevision"])
    )

    assert partial["status"] == "partial"
    assert partial["remaining"] == {"sessions": 1, "turns": 0, "runs": 1}
    assert turns.get(turn_id) is None
    assert load_session(session.session_id) is not None
    restarted = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    restart_preview = restarted.snapshot(session.session_id)
    assert restart_preview["status"] == "partial"
    assert restart_preview["deletionRevision"] == preview["deletionRevision"]

    completed = restarted.delete(
        session.session_id, str(restart_preview["deletionRevision"])
    )
    assert completed["status"] == "completed"
    assert load_session(session.session_id) is None
    assert journal.get_run(run.id) is None


def test_conversation_receipt_distinguishes_duplicate_from_forged_target(
    deletion_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "one"}]
    save_session(session, force_full=True)
    authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority.snapshot(session.session_id)
    authority.delete(session.session_id, str(preview["deletionRevision"]))

    duplicate = authority.delete(
        session.session_id, str(preview["deletionRevision"])
    )
    assert duplicate["status"] == "already_absent"
    with pytest.raises(ConversationDeletionError) as forged:
        authority.delete("forged_session", str(preview["deletionRevision"]))
    assert forged.value.code == "deletion_target_not_found"


def test_conversation_fence_blocks_all_writers_that_can_expand_target_set(
    deletion_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "original"}]
    save_session(session, force_full=True)
    authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority.snapshot(session.session_id)
    with authority._ledger.coordination():
        authority._ledger.start(
            "conversation",
            session.session_id,
            str(preview["deletionRevision"]),
        )

    session.messages.append({"role": "assistant", "content": "blocked"})
    with pytest.raises(SessionWriteConflictError):
        save_session(session, force_full=True)
    with pytest.raises(RunJournalStorageError):
        RunJournal(workspace, data_dir=data_dir).create_run(
            title="blocked", source="gateway", session_id=session.session_id
        )
    turns = ConversationTurnStore(workspace, data_dir=data_dir)
    turn_id = create_turn_id()
    turns.claim(turn_id=turn_id, fingerprint="sha256:" + "c" * 64)
    turns.mark_running(turn_id)
    with pytest.raises(TurnStoreError):
        turns.attach_session(
            turn_id,
            session_id=session.session_id,
            created_session=False,
        )


def test_conversation_fence_blocks_cross_process_session_save(
    deletion_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "original"}]
    save_session(session, force_full=True)
    authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    revision = str(authority.snapshot(session.session_id)["deletionRevision"])
    with authority._ledger.coordination():
        authority._ledger.start("conversation", session.session_id, revision)
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    process = context.Process(
        target=_save_fenced_session_in_process,
        args=(str(workspace), str(data_dir), session.session_id, results),
    )

    process.start()
    process.join(timeout=15)

    assert not process.is_alive()
    assert process.exitcode == 0
    assert results.get(timeout=2) == "blocked"
    reloaded = load_session(session.session_id)
    assert reloaded is not None
    assert reloaded.messages == session.messages


def test_conversation_deletion_store_busy_is_fixed_and_fail_closed(
    deletion_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    save_session(session, force_full=True)
    authority = ConversationDeletionAuthority(
        workspace,
        data_dir=data_dir,
        store_timeout=0.02,
    )
    lock_held = threading.Event()
    release_lock = threading.Event()

    def hold_coordination() -> None:
        with authority._ledger.coordination():
            lock_held.set()
            assert release_lock.wait(timeout=5)

    holder = threading.Thread(target=hold_coordination)
    holder.start()
    assert lock_held.wait(timeout=5)
    try:
        with pytest.raises(ConversationDeletionError) as busy:
            authority.delete(
                session.session_id,
                "delrev_" + "a" * 64,
            )
        assert busy.value.code == "deletion_store_busy"
        assert load_session(session.session_id) is not None
    finally:
        release_lock.set()
        holder.join(timeout=5)
    assert not holder.is_alive()


def test_conversation_revision_becomes_stale_after_session_generation_change(
    deletion_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "first"}]
    save_session(session, force_full=True)
    authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority.snapshot(session.session_id)
    session.messages.append({"role": "assistant", "content": "new generation"})
    save_session(session, force_full=True)

    with pytest.raises(ConversationDeletionError) as stale:
        authority.delete(session.session_id, str(preview["deletionRevision"]))
    assert stale.value.code == "deletion_revision_stale"
    assert load_session(session.session_id) is not None


@pytest.mark.parametrize("new_record", ["turn", "run"])
def test_conversation_revision_becomes_stale_when_linked_record_is_added(
    deletion_workspace: tuple[Path, Path],
    new_record: str,
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "stable preview"}]
    save_session(session, force_full=True)
    authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority.snapshot(session.session_id)
    journal = RunJournal(workspace, data_dir=data_dir)

    run = journal.create_run(
        title="newly linked",
        source="gateway",
        session_id=session.session_id,
    )
    journal.transition(run.id, "running")
    journal.transition(run.id, "completed")
    if new_record == "turn":
        _terminal_turn(
            ConversationTurnStore(workspace, data_dir=data_dir),
            session_id=session.session_id,
            run_id=run.id,
        )

    with pytest.raises(ConversationDeletionError) as stale:
        authority.delete(session.session_id, str(preview["deletionRevision"]))
    assert stale.value.code == "deletion_revision_stale"
    assert load_session(session.session_id) is not None


def test_committing_turn_blocks_conversation_deletion_before_mutation(
    deletion_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "commit in progress"}]
    save_session(session, force_full=True)
    turns = ConversationTurnStore(workspace, data_dir=data_dir)
    turn_id = create_turn_id()
    turns.claim(turn_id=turn_id, fingerprint="sha256:" + "d" * 64)
    turns.mark_running(turn_id)
    turns.attach_session(
        turn_id,
        session_id=session.session_id,
        created_session=False,
    )
    assert turns.begin_commit(turn_id).commit_allowed is True

    authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority.snapshot(session.session_id)

    assert preview["status"] == "busy"
    assert preview["blockers"] == [{"code": "active_turn"}]
    with pytest.raises(ConversationDeletionError) as busy:
        authority.delete(session.session_id, str(preview["deletionRevision"]))
    assert busy.value.code == "deletion_target_busy"
    assert load_session(session.session_id) is not None


def test_linked_turn_transition_from_accepted_to_running_stales_preview(
    deletion_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    save_session(session, force_full=True)
    turns = ConversationTurnStore(workspace, data_dir=data_dir)
    turn_id = create_turn_id()
    turns.claim(turn_id=turn_id, fingerprint="sha256:" + "e" * 64)
    authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    accepted_preview = authority.snapshot(session.session_id)
    assert accepted_preview["status"] == "ready"

    turns.mark_running(turn_id)
    turns.attach_session(
        turn_id,
        session_id=session.session_id,
        created_session=False,
    )

    with pytest.raises(ConversationDeletionError) as stale:
        authority.delete(
            session.session_id,
            str(accepted_preview["deletionRevision"]),
        )
    assert stale.value.code == "deletion_revision_stale"


@pytest.mark.parametrize("initial_status", ["queued", "running"])
def test_linked_run_transition_to_terminal_stales_busy_preview(
    deletion_workspace: tuple[Path, Path],
    initial_status: str,
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    save_session(session, force_full=True)
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(
        title="transitioning run",
        source="gateway",
        session_id=session.session_id,
    )
    if initial_status == "running":
        journal.transition(run.id, "running")
    authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    active_preview = authority.snapshot(session.session_id)
    assert active_preview["status"] == "busy"

    if initial_status == "queued":
        journal.transition(run.id, "running")
    journal.transition(run.id, "completed")

    with pytest.raises(ConversationDeletionError) as stale:
        authority.delete(
            session.session_id,
            str(active_preview["deletionRevision"]),
        )
    assert stale.value.code == "deletion_revision_stale"
    assert authority.snapshot(session.session_id)["status"] == "ready"


def test_corrupt_turn_record_fails_closed_without_leaking_details(
    deletion_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "must remain"}]
    save_session(session, force_full=True)
    turns = ConversationTurnStore(workspace, data_dir=data_dir)
    turns._ensure_storage_root()
    corrupt = turns._turns_root / ("turn_" + "f" * 32 + ".json")
    corrupt.write_text("private malformed record", encoding="utf-8")
    authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)

    preview = authority.snapshot(session.session_id)

    assert preview["status"] == "unavailable"
    assert preview["diagnostics"] == [{"code": "turn_record_invalid"}]
    assert "private malformed" not in str(preview)
    with pytest.raises(ConversationDeletionError) as unavailable:
        authority.delete(session.session_id, str(preview["deletionRevision"]))
    assert unavailable.value.code == "deletion_unavailable"
    assert load_session(session.session_id) is not None


def test_conversation_receipt_expires_and_does_not_become_a_tombstone(
    deletion_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "finite receipt"}]
    save_session(session, force_full=True)
    now = [datetime(2026, 7, 23, 10, 0, tzinfo=timezone.utc)]
    authority = ConversationDeletionAuthority(
        workspace,
        data_dir=data_dir,
        clock=lambda: now[0],
    )
    preview = authority.snapshot(session.session_id)
    authority.delete(session.session_id, str(preview["deletionRevision"]))
    assert authority.snapshot(session.session_id)["status"] == "completed"

    now[0] += timedelta(minutes=11)
    with pytest.raises(ConversationDeletionError) as expired:
        authority.snapshot(session.session_id)
    assert expired.value.code == "deletion_target_not_found"


def test_conversation_recovers_after_process_exit_during_cleanup(
    deletion_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = deletion_workspace
    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "recover after exit"}]
    save_session(session, force_full=True)
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(
        title="survives until retry",
        source="gateway",
        session_id=session.session_id,
    )
    journal.transition(run.id, "running")
    journal.transition(run.id, "completed")
    turns = ConversationTurnStore(workspace, data_dir=data_dir)
    turn_id = _terminal_turn(
        turns,
        session_id=session.session_id,
        run_id=run.id,
    )
    authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    revision = str(authority.snapshot(session.session_id)["deletionRevision"])
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_exit_after_conversation_turn_cleanup,
        args=(str(workspace), str(data_dir), session.session_id, revision),
    )

    process.start()
    process.join(timeout=15)

    assert not process.is_alive()
    assert process.exitcode == 73
    restarted = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    partial = restarted.snapshot(session.session_id)
    assert partial["status"] == "partial"
    assert partial["deletionRevision"] == revision
    assert partial["affected"] == {"sessions": 1, "turns": 0, "runs": 1}
    assert turns.get(turn_id) is None
    assert load_session(session.session_id) is not None
    assert journal.get_run(run.id) is not None

    result = restarted.delete(session.session_id, revision)
    assert result["status"] == "completed"
    assert load_session(session.session_id) is None
    assert journal.get_run(run.id) is None
