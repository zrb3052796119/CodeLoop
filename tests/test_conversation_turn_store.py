from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import minicode.conversation_turn_store as turn_store_module
from minicode.conversation_turn_store import (
    ConversationTurnStore,
    TurnStoreCorruptError,
    TurnStoreError,
    create_turn_id,
    request_fingerprint,
)


TURN_ID = "turn_" + "a" * 32


def _clock() -> datetime:
    return datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path, *, owner: str = "1" * 32) -> ConversationTurnStore:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return ConversationTurnStore(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        owner_id=owner,
        clock=_clock,
    )


def _fingerprint(store: ConversationTurnStore, message: str = "safe request") -> str:
    return request_fingerprint(
        workspace_id=store.workspace_id,
        session_id=None,
        message=message,
    )


def _complete_turn(
    store: ConversationTurnStore,
    turn_id: str,
    *,
    session_id: str = "session_01",
    run_id: str = "run_" + "b" * 32,
) -> None:
    store.claim(turn_id=turn_id, fingerprint=_fingerprint(store, turn_id))
    store.mark_running(turn_id)
    store.attach_session(
        turn_id,
        session_id=session_id,
        created_session=True,
    )
    store.attach_run(turn_id, run_id=run_id)
    store.begin_commit(turn_id)
    store.mark_completed(
        turn_id,
        commit_marker={
            "schemaVersion": 1,
            "userMessageIndex": 1,
            "assistantMessageIndex": 2,
        },
    )


def test_windows_turn_write_does_not_require_fchmod(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    temporary_descriptors: set[int] = set()
    real_mkstemp = turn_store_module.tempfile.mkstemp
    real_fchmod = turn_store_module.os.fchmod

    def tracked_mkstemp(*args, **kwargs):
        descriptor, path = real_mkstemp(*args, **kwargs)
        temporary_descriptors.add(descriptor)
        return descriptor, path

    def reject_turn_temporary_fchmod(descriptor: int, mode: int) -> None:
        if descriptor in temporary_descriptors:
            raise AssertionError("Turn temporary descriptor used fchmod")
        real_fchmod(descriptor, mode)

    monkeypatch.setattr(turn_store_module, "_platform_name", lambda: "nt")
    monkeypatch.setattr(turn_store_module.tempfile, "mkstemp", tracked_mkstemp)
    monkeypatch.setattr(
        turn_store_module.os,
        "fchmod",
        reject_turn_temporary_fchmod,
        raising=False,
    )

    claim = store.claim(turn_id=TURN_ID, fingerprint=_fingerprint(store))

    assert claim.disposition == "claimed"
    assert store.get(TURN_ID) == claim.record


def test_turn_write_closes_temporary_descriptor_before_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    opened_descriptors: list[int] = []
    real_mkstemp = turn_store_module.tempfile.mkstemp
    real_replace = turn_store_module.os.replace

    def tracked_mkstemp(*args, **kwargs):
        descriptor, path = real_mkstemp(*args, **kwargs)
        opened_descriptors.append(descriptor)
        return descriptor, path

    def windows_style_replace(source, target) -> None:
        assert opened_descriptors
        with pytest.raises(OSError):
            os.fstat(opened_descriptors[-1])
        real_replace(source, target)

    monkeypatch.setattr(turn_store_module.tempfile, "mkstemp", tracked_mkstemp)
    monkeypatch.setattr(turn_store_module.os, "replace", windows_style_replace)

    claim = store.claim(turn_id=TURN_ID, fingerprint=_fingerprint(store))

    assert claim.disposition == "claimed"


def test_turn_id_and_fingerprint_are_closed_secure_hash_contracts(tmp_path: Path) -> None:
    store = _store(tmp_path)

    generated = {create_turn_id() for _ in range(20)}
    assert len(generated) == 20
    assert all(re.fullmatch(r"turn_[0-9a-f]{32}", value) for value in generated)
    assert _fingerprint(store) == _fingerprint(store)
    assert _fingerprint(store, "safe request ") != _fingerprint(store, "safe request")
    continued = request_fingerprint(
        workspace_id=store.workspace_id,
        session_id="session_01",
        message="safe request",
    )
    assert continued != _fingerprint(store)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", continued)


def test_claim_transition_and_record_never_persist_content_or_paths(tmp_path: Path) -> None:
    store = _store(tmp_path)
    secret = "safe request Bearer fixture-secret"
    fingerprint = _fingerprint(store, secret)

    claim = store.claim(turn_id=TURN_ID, fingerprint=fingerprint)
    assert claim.disposition == "claimed"
    assert claim.record.status == "accepted"
    start = store.mark_running(TURN_ID)
    assert start.execution_started is True
    assert start.record.status == "running"
    store.attach_session(TURN_ID, session_id="session_01", created_session=True)
    store.attach_run(TURN_ID, run_id="run_" + "b" * 32)
    assert store.begin_commit(TURN_ID).commit_allowed is True
    completed = store.mark_completed(
        TURN_ID,
        commit_marker={
            "schemaVersion": 1,
            "userMessageIndex": 1,
            "assistantMessageIndex": 2,
        },
    )

    assert completed.status == "completed"
    assert completed.completed_at == "2026-07-19T12:00:00.000Z"
    record_path = store.record_path(TURN_ID)
    raw = record_path.read_text(encoding="utf-8")
    assert secret not in raw
    assert "fixture-secret" not in raw
    assert str(tmp_path) not in raw
    assert set(json.loads(raw)) == {
        "schemaVersion",
        "turnId",
        "workspaceId",
        "requestFingerprint",
        "status",
        "sessionId",
        "createdSession",
        "runId",
        "createdAt",
        "updatedAt",
        "completedAt",
        "errorCode",
        "commitMarker",
        "ownerId",
        "curatorOutboxVersion",
        "curatorReceiptRecorded",
        "curatorReceiptSettled",
    }


def test_committed_curator_receipt_ack_is_durable_idempotent_and_legacy_safe(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    fingerprint = _fingerprint(store)
    store.claim(turn_id=TURN_ID, fingerprint=fingerprint)
    store.mark_running(TURN_ID)
    store.attach_session(TURN_ID, session_id="session_01", created_session=True)
    store.attach_run(TURN_ID, run_id="run_" + "b" * 32)
    store.begin_commit(TURN_ID)

    acknowledged = store.mark_curator_receipt_recorded(TURN_ID)
    assert acknowledged.curator_receipt_recorded is True
    assert acknowledged.curator_receipt_settled is False
    assert store.mark_curator_receipt_recorded(TURN_ID) == acknowledged
    settled = store.mark_curator_receipt_settled(TURN_ID)
    assert settled.curator_receipt_settled is True
    completed = store.mark_completed(
        TURN_ID,
        commit_marker={
            "schemaVersion": 1,
            "userMessageIndex": 1,
            "assistantMessageIndex": 2,
        },
    )
    assert completed.curator_receipt_recorded is True

    path = store.record_path(TURN_ID)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("curatorOutboxVersion")
    payload.pop("curatorReceiptSettled")
    path.write_text(json.dumps(payload), encoding="utf-8")
    legacy = store.get(TURN_ID)
    assert legacy is not None
    assert legacy.curator_outbox_version == 1
    assert legacy.curator_receipt_recorded is True
    assert legacy.curator_receipt_settled is True

    payload["curatorReceiptRecorded"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")
    restarted = _store(tmp_path, owner="2" * 32)
    replay_ack = restarted.mark_curator_receipt_recorded(TURN_ID)
    assert replay_ack.curator_receipt_recorded is True
    assert replay_ack.curator_receipt_settled is False
    assert replay_ack.owner_id == "2" * 32


def test_duplicate_claim_distinguishes_same_live_stale_terminal_and_conflict(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    fingerprint = _fingerprint(store)
    assert store.claim(turn_id=TURN_ID, fingerprint=fingerprint).disposition == "claimed"
    store.mark_running(TURN_ID)

    assert store.claim(turn_id=TURN_ID, fingerprint=fingerprint).disposition == "in_progress"
    assert store.claim(
        turn_id=TURN_ID,
        fingerprint=_fingerprint(store, "different"),
    ).disposition == "conflict"

    restarted = _store(tmp_path, owner="2" * 32)
    assert restarted.claim(turn_id=TURN_ID, fingerprint=fingerprint).disposition == "in_progress"

    # Releasing the execution lease models the old Gateway process exiting.
    store.release_claim(TURN_ID)
    assert restarted.claim(turn_id=TURN_ID, fingerprint=fingerprint).disposition == "recover"
    interrupted = restarted.mark_interrupted(TURN_ID)
    assert interrupted.status == "interrupted"
    assert restarted.claim(turn_id=TURN_ID, fingerprint=fingerprint).disposition == "terminal"


def test_reused_owner_id_cannot_transition_without_the_execution_lease(
    tmp_path: Path,
) -> None:
    owner = "1" * 32
    first = _store(tmp_path, owner=owner)
    assert first.claim(
        turn_id=TURN_ID,
        fingerprint=_fingerprint(first),
    ).disposition == "claimed"
    impostor = _store(tmp_path, owner=owner)

    with pytest.raises(TurnStoreError, match="turn store unavailable"):
        impostor.mark_running(TURN_ID)

    assert first.mark_running(TURN_ID).execution_started is True
    first.release_claim(TURN_ID)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schemaVersion", True),
        ("createdSession", 1),
        ("createdAt", True),
        ("status", "unknown"),
        ("turnId", "../escape"),
        ("requestFingerprint", "safe request"),
    ],
)
def test_corrupt_schema_is_localized_and_never_treated_as_a_new_claim(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    store = _store(tmp_path)
    fingerprint = _fingerprint(store)
    store.claim(turn_id=TURN_ID, fingerprint=fingerprint)
    path = store.record_path(TURN_ID)
    record = json.loads(path.read_text(encoding="utf-8"))
    record[field] = value
    path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(TurnStoreCorruptError):
        store.get(TURN_ID)
    other = "turn_" + "c" * 32
    assert store.claim(turn_id=other, fingerprint=fingerprint).disposition == "claimed"


def test_oversize_symlink_and_path_inputs_are_rejected_without_escape(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    fingerprint = _fingerprint(store)
    store.claim(turn_id=TURN_ID, fingerprint=fingerprint)
    path = store.record_path(TURN_ID)
    path.write_bytes(b"{" + b"x" * 20_000)
    with pytest.raises(TurnStoreCorruptError):
        store.get(TURN_ID)

    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(TurnStoreError):
        store.get(TURN_ID)
    assert outside.read_text(encoding="utf-8") == "outside"

    for invalid in ("../escape", "turn_" + "A" * 32, " turn_" + "a" * 32):
        with pytest.raises(ValueError):
            store.get(invalid)


def test_state_machine_forbids_terminal_to_running(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.claim(turn_id=TURN_ID, fingerprint=_fingerprint(store))
    store.mark_running(TURN_ID)
    failure = store.mark_failed(TURN_ID, error_code="turn_failed")

    assert failure.failure_recorded is True
    assert failure.record.status == "failed"

    with pytest.raises(TurnStoreError):
        store.mark_running(TURN_ID)


@pytest.mark.parametrize("transition", ["failed", "interrupted"])
def test_unsettled_recorded_commit_rejects_terminalization_without_mutation(
    tmp_path: Path,
    transition: str,
) -> None:
    """A scheduler pin must be released before leaving committing."""

    store = _store(tmp_path)
    store.claim(turn_id=TURN_ID, fingerprint=_fingerprint(store))
    store.mark_running(TURN_ID)
    store.attach_session(
        TURN_ID,
        session_id="session_pinned",
        created_session=True,
    )
    store.attach_run(TURN_ID, run_id="run_" + "c" * 32)
    store.begin_commit(TURN_ID)
    before_record = store.mark_curator_receipt_recorded(TURN_ID)
    before_bytes = store.record_path(TURN_ID).read_bytes()

    with pytest.raises(TurnStoreError, match="invalid turn transition"):
        if transition == "failed":
            store.mark_failed(TURN_ID, error_code="turn_failed")
        else:
            store.mark_interrupted(TURN_ID)

    assert store.record_path(TURN_ID).read_bytes() == before_bytes
    assert store.get(TURN_ID) == before_record
    assert before_record.status == "committing"
    assert before_record.curator_receipt_recorded is True
    assert before_record.curator_receipt_settled is False


def test_running_cancel_wins_before_commit_and_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.claim(turn_id=TURN_ID, fingerprint=_fingerprint(store))
    store.mark_running(TURN_ID)

    first = store.request_cancel(TURN_ID)
    second = store.request_cancel(TURN_ID)
    gate = store.begin_commit(TURN_ID)

    assert first.cancellation_accepted is True
    assert first.record.status == "cancel_requested"
    assert second == first
    assert gate.commit_allowed is False
    assert gate.record.status == "cancel_requested"
    cancelled = store.mark_cancelled(TURN_ID)
    assert cancelled.status == "cancelled"
    assert cancelled.error_code == "turn_cancelled"


def test_accepted_cancel_is_atomically_finalized_by_start_decision(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.claim(turn_id=TURN_ID, fingerprint=_fingerprint(store))
    cancellation = store.request_cancel(TURN_ID)

    start = store.mark_running(TURN_ID)

    assert cancellation.cancellation_accepted is True
    assert cancellation.record.status == "cancel_requested"
    assert start.execution_started is False
    assert start.record.status == "cancelled"
    assert start.record.error_code == "turn_cancelled"
    assert store.is_active(TURN_ID) is False
    repeated = store.request_cancel(TURN_ID)
    assert repeated.cancellation_accepted is False
    assert repeated.record.status == "cancelled"
    with pytest.raises(TurnStoreError):
        store.mark_failed(TURN_ID, error_code="turn_failed")


def test_commit_gate_wins_before_cancel_and_completion_stays_authoritative(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.claim(turn_id=TURN_ID, fingerprint=_fingerprint(store))
    store.mark_running(TURN_ID)
    store.attach_session(TURN_ID, session_id="session_01", created_session=True)

    gate = store.begin_commit(TURN_ID)
    cancellation = store.request_cancel(TURN_ID)

    assert gate.commit_allowed is True
    assert gate.record.status == "committing"
    assert cancellation.cancellation_accepted is False
    assert cancellation.record.status == "committing"
    completed = store.mark_completed(
        TURN_ID,
        commit_marker={
            "schemaVersion": 1,
            "userMessageIndex": 1,
            "assistantMessageIndex": 2,
        },
    )
    assert completed.status == "completed"
    repeated = store.request_cancel(TURN_ID)
    assert repeated.cancellation_accepted is False
    assert repeated.record.status == "completed"


@pytest.mark.parametrize("terminal", ["failed", "interrupted", "cancelled"])
def test_terminal_turn_cancel_is_safe_and_never_changes_terminal_state(
    tmp_path: Path,
    terminal: str,
) -> None:
    store = _store(tmp_path)
    store.claim(turn_id=TURN_ID, fingerprint=_fingerprint(store))
    store.mark_running(TURN_ID)
    if terminal == "failed":
        store.mark_failed(TURN_ID, error_code="turn_failed")
    elif terminal == "interrupted":
        store.mark_interrupted(TURN_ID)
    else:
        store.request_cancel(TURN_ID)
        store.mark_cancelled(TURN_ID)

    decision = store.request_cancel(TURN_ID)

    assert decision.cancellation_accepted is False
    assert decision.record.status == terminal


def test_retention_removes_only_old_terminal_records_and_stale_temps(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    current = [datetime(2026, 7, 1, tzinfo=timezone.utc)]
    store = ConversationTurnStore(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        owner_id="1" * 32,
        clock=lambda: current[0],
        max_records=2,
        scan_limit=10,
        terminal_max_age=timedelta(days=1),
    )
    store.claim(turn_id=TURN_ID, fingerprint=_fingerprint(store))
    store.mark_running(TURN_ID)
    store.mark_failed(TURN_ID, error_code="turn_failed")
    stale_temp = store.record_path(TURN_ID).parent / ".turn-stale.tmp"
    stale_temp.write_text("temp", encoding="utf-8")
    old = (current[0] - timedelta(days=2)).timestamp()
    os.utime(stale_temp, (old, old))

    current[0] += timedelta(days=2)
    active_id = "turn_" + "d" * 32
    store.claim(turn_id=active_id, fingerprint=_fingerprint(store, "active"))

    assert store.get(TURN_ID) is None
    assert store.get(active_id).status == "accepted"
    assert not stale_temp.exists()


def test_retention_preserves_deliverable_unacked_turn_but_not_missing_run_identity(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    current = [datetime(2026, 7, 1, tzinfo=timezone.utc)]
    store = ConversationTurnStore(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        owner_id="1" * 32,
        clock=lambda: current[0],
        max_records=1,
        scan_limit=20,
        terminal_max_age=timedelta(days=1),
    )

    def complete(turn_id: str, *, run_id: str | None) -> None:
        store.claim(turn_id=turn_id, fingerprint=_fingerprint(store, turn_id))
        store.mark_running(turn_id)
        store.attach_session(turn_id, session_id="session_01", created_session=True)
        store.attach_run(turn_id, run_id=run_id)
        store.begin_commit(turn_id)
        store.mark_completed(
            turn_id,
            commit_marker={
                "schemaVersion": 1,
                "userMessageIndex": 1,
                "assistantMessageIndex": 2,
            },
        )

    pending_id = "turn_" + "1" * 32
    no_run_id = "turn_" + "2" * 32
    complete(pending_id, run_id="run_" + "a" * 32)
    complete(no_run_id, run_id=None)
    current[0] += timedelta(days=2)
    current_id = "turn_" + "3" * 32
    store.claim(turn_id=current_id, fingerprint=_fingerprint(store, current_id))

    assert store.get(pending_id) is not None
    assert store.get(no_run_id) is None
    assert store.get(current_id) is not None

    store.mark_curator_receipt_recorded(pending_id)
    store.mark_curator_receipt_settled(pending_id)
    another_id = "turn_" + "4" * 32
    store.claim(turn_id=another_id, fingerprint=_fingerprint(store, another_id))
    assert store.get(pending_id) is None


def test_symlinked_turn_root_is_rejected_without_writing_outside(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    store = ConversationTurnStore(
        workspace,
        data_dir=data_dir,
        owner_id="1" * 32,
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    turns_root = store.record_path(TURN_ID).parent
    turns_root.parent.mkdir(parents=True)
    turns_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(TurnStoreError):
        store.claim(turn_id=TURN_ID, fingerprint=_fingerprint(store))
    assert list(outside.iterdir()) == []


def test_legacy_completed_turn_without_outbox_capability_is_not_recovered(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _complete_turn(store, TURN_ID)
    path = store.record_path(TURN_ID)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("curatorOutboxVersion")
    payload.pop("curatorReceiptRecorded")
    payload.pop("curatorReceiptSettled")
    path.write_text(json.dumps(payload), encoding="utf-8")

    restarted = _store(tmp_path, owner="2" * 32)
    legacy = restarted.get(TURN_ID)
    assert legacy is not None
    assert legacy.curator_outbox_version is None
    scan = restarted.curator_receipt_recovery_records()
    assert scan.items == ()
    assert scan.exhaustive is True
    assert scan.diagnostics == ()


def test_corrupt_turn_does_not_block_recovery_of_a_healthy_receipt(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    healthy_id = "turn_" + "1" * 32
    corrupt_id = "turn_" + "2" * 32
    _complete_turn(store, healthy_id, run_id="run_" + "1" * 32)
    store.record_path(corrupt_id).write_text("{malformed", encoding="utf-8")

    scan = store.curator_receipt_recovery_records()
    assert tuple(record.turn_id for record in scan.items) == (healthy_id,)
    assert scan.exhaustive is False
    assert scan.diagnostics == ("turn_record_invalid",)


def test_recorded_release_receipts_sort_before_undelivered_receipts(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    pending_id = "turn_" + "0" * 32
    release_id = "turn_" + "f" * 32
    _complete_turn(store, pending_id, run_id="run_" + "0" * 32)
    _complete_turn(store, release_id, run_id="run_" + "f" * 32)
    store.mark_curator_receipt_recorded(release_id)

    scan = store.curator_receipt_recovery_records()
    assert tuple(record.turn_id for record in scan.items) == (
        release_id,
        pending_id,
    )
    assert scan.items[0].curator_receipt_recorded is True
    assert scan.items[1].curator_receipt_recorded is False


def test_settled_committing_turn_remains_available_for_session_recovery(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    run_id = "run_" + "c" * 32
    store.claim(turn_id=TURN_ID, fingerprint=_fingerprint(store))
    store.mark_running(TURN_ID)
    store.attach_session(
        TURN_ID,
        session_id="session_committed",
        created_session=True,
    )
    store.attach_run(TURN_ID, run_id=run_id)
    store.begin_commit(TURN_ID)
    store.mark_curator_receipt_recorded(TURN_ID)
    store.mark_curator_receipt_settled(TURN_ID)
    store.release_claim(TURN_ID)

    # The shared outbox reconciler has no more work, but Conversation still
    # needs the record to project an authoritative Session commit to the Turn.
    assert store.curator_receipt_recovery_records().items == ()
    content_scan = store.curator_receipt_recovery_records(
        include_settled_committing=True,
    )
    assert tuple(record.turn_id for record in content_scan.items) == (TURN_ID,)
    assert content_scan.items[0].status == "committing"
    assert content_scan.items[0].curator_receipt_settled is True


def test_live_lease_artifacts_do_not_consume_turn_scan_budget(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = ConversationTurnStore(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        owner_id="1" * 32,
        clock=_clock,
        max_records=2,
        scan_limit=2,
    )
    live_id = "turn_" + "1" * 32
    completed_id = "turn_" + "2" * 32
    assert store.claim(
        turn_id=live_id,
        fingerprint=_fingerprint(store, "live"),
    ).disposition == "claimed"
    store.mark_running(live_id)
    _complete_turn(store, completed_id, run_id="run_" + "2" * 32)

    scan = store.curator_receipt_recovery_records()

    assert tuple(record.turn_id for record in scan.items) == (completed_id,)
    assert scan.exhaustive is True
    store.release_claim(live_id)


def test_unsettled_completed_turn_blocks_explicit_session_deletion(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _complete_turn(store, TURN_ID, session_id="session_delete")

    with pytest.raises(TurnStoreError, match="deletion unavailable"):
        store.delete_terminal_for_session("session_delete")
    assert store.get(TURN_ID) is not None

    recorded = store.mark_curator_receipt_recorded(TURN_ID)
    assert recorded.curator_receipt_settled is False
    with pytest.raises(TurnStoreError, match="deletion unavailable"):
        store.delete_terminal_for_session("session_delete")
    assert store.get(TURN_ID) is not None

    store.mark_curator_receipt_settled(TURN_ID)
    assert store.delete_terminal_for_session("session_delete") == 1
    assert store.get(TURN_ID) is None
