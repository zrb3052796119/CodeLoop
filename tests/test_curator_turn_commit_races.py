from __future__ import annotations

from pathlib import Path

import pytest

from minicode.conversation import ConversationTurnService
from minicode.conversation_turn_store import (
    ConversationTurnStore,
    request_fingerprint,
)
from minicode.curator_schedule import (
    CuratorScheduleStore,
    curator_completion_id,
)
from minicode.run_journal import RunJournal
from minicode.session import create_new_session, save_session


class _Permissions:
    def begin_turn(self) -> None:
        pass

    def end_turn(self) -> None:
        pass

    def get_summary(self) -> list[str]:
        return []


class _Tools:
    def get_skills(self) -> list[dict[str, object]]:
        return []

    def get_mcp_servers(self) -> list[dict[str, object]]:
        return []

    def dispose(self) -> None:
        pass


class _Runtime:
    def __init__(self, reply: str) -> None:
        self.system_prompt = "test system prompt"
        self.permissions = _Permissions()
        self.tools = _Tools()
        self.skill_routing = None
        self.reply = reply
        self.execute_calls = 0

    def execute(
        self,
        messages,
        _observation,
        *,
        defer_curator_maintenance: bool = False,
    ):
        assert defer_curator_maintenance is True
        self.execute_calls += 1
        return [*messages, {"role": "assistant", "content": self.reply}]

    def maintain_committed_turn(
        self,
        _observation,
        *,
        current_turn_id: str,
        current_run_id: str | None,
        completion_already_recorded: bool = False,
    ) -> None:
        assert current_turn_id
        assert current_run_id
        assert completion_already_recorded is True


class _RuntimeFactory:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.instances: list[_Runtime] = []

    def __call__(self, *, workspace: Path, prompt: str, **_kwargs) -> _Runtime:
        assert workspace.is_absolute()
        assert prompt
        runtime = _Runtime(self.reply)
        self.instances.append(runtime)
        return runtime


@pytest.fixture
def isolated_dashboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr("minicode.session.MINI_CODE_DIR", data_dir)
    monkeypatch.setattr("minicode.session.SESSIONS_DIR", data_dir / "sessions")
    return workspace, data_dir


def _journal_factory(data_dir: Path):
    return lambda workspace: RunJournal(workspace, data_dir=data_dir)


def _committing_turn(
    store: ConversationTurnStore,
    *,
    turn_id: str,
    session_id: str,
    run_id: str,
):
    fingerprint = request_fingerprint(
        workspace_id=store.workspace_id,
        session_id=None,
        message="race request",
    )
    assert store.claim(turn_id=turn_id, fingerprint=fingerprint).disposition == "claimed"
    assert store.mark_running(turn_id).execution_started is True
    store.attach_session(
        turn_id,
        session_id=session_id,
        created_session=True,
    )
    store.attach_run(turn_id, run_id=run_id)
    gate = store.begin_commit(turn_id)
    assert gate.commit_allowed is True
    return gate.record


def test_keyboard_interrupt_after_scheduler_pin_recovers_without_model_replay(
    isolated_dashboard,
) -> None:
    """The Session commit is authoritative across the pin-to-ack crash window."""

    workspace, data_dir = isolated_dashboard
    turn_id = "turn_" + "a" * 32
    factory = _RuntimeFactory("durably committed reply")
    store = ConversationTurnStore(workspace, data_dir=data_dir)
    service = ConversationTurnService(
        workspace,
        runtime_factory=factory,
        journal_factory=_journal_factory(data_dir),
        turn_store=store,
    )
    real_record = service._record_committed_curator_completion
    delivery_attempts = 0

    def interrupt_once_after_pin(*, turn_id: str, run_id: str | None) -> bool:
        nonlocal delivery_attempts
        delivery_attempts += 1
        assert real_record(turn_id=turn_id, run_id=run_id) is True
        if delivery_attempts == 1:
            raise KeyboardInterrupt()
        return True

    service._record_committed_curator_completion = interrupt_once_after_pin

    with pytest.raises(KeyboardInterrupt):
        service.turn(
            message="commit then lose power after scheduler pin",
            session_id=None,
            turn_id=turn_id,
        )

    recovered = store.get(turn_id)
    assert recovered is not None
    assert recovered.status == "completed"
    assert recovered.curator_receipt_recorded is True
    assert recovered.curator_receipt_settled is True
    schedule = CuratorScheduleStore(workspace).snapshot()
    assert schedule.pending_task_count == 1
    assert len(schedule.recent_completion_ids) == 1
    assert schedule.pinned_completion_ids == ()
    # Recovery joins the durable scheduler pin through the generic outbox
    # reconciler; it does not replay the interrupted producer callback.
    assert delivery_attempts == 1
    assert len(factory.instances) == 1
    assert factory.instances[0].execute_calls == 1

    retry = service.turn(
        message="commit then lose power after scheduler pin",
        session_id=None,
        turn_id=turn_id,
    )

    assert retry.assistant == "durably committed reply"
    assert len(factory.instances) == 1
    assert factory.instances[0].execute_calls == 1
    assert delivery_attempts == 1
    after_retry = CuratorScheduleStore(workspace).snapshot()
    assert after_retry.pending_task_count == 1
    assert after_retry.pinned_completion_ids == ()


def test_stale_same_marker_completion_cannot_roll_back_receipt_flags(
    isolated_dashboard,
) -> None:
    """A stale committer observes, rather than overwrites, a settled record."""

    workspace, data_dir = isolated_dashboard
    turn_id = "turn_" + "b" * 32
    run_id = "run_" + "c" * 32
    marker = {
        "schemaVersion": 1,
        "userMessageIndex": 0,
        "assistantMessageIndex": 1,
    }
    first = ConversationTurnStore(
        workspace,
        data_dir=data_dir,
        owner_id="1" * 32,
    )
    second = ConversationTurnStore(
        workspace,
        data_dir=data_dir,
        owner_id="2" * 32,
    )
    stale_committing = _committing_turn(
        first,
        turn_id=turn_id,
        session_id="session-race",
        run_id=run_id,
    )
    assert stale_committing.curator_receipt_recorded is False
    assert stale_committing.curator_receipt_settled is False

    recovered = second.recover_completed(turn_id, commit_marker=marker)
    assert recovered.status == "completed"
    completion_id = curator_completion_id(run_id, turn_id)
    assert completion_id is not None
    schedule = CuratorScheduleStore(workspace)
    with schedule.delivery_fence():
        schedule.record_completion(completion_id, pin=True)
        second.mark_curator_receipt_recorded(turn_id)
        schedule.acknowledge_completion(completion_id)
        settled = second.mark_curator_receipt_settled(turn_id)
    before_stale_writers = second.record_path(turn_id).read_bytes()

    completed_again = first.mark_completed(turn_id, commit_marker=marker)
    recovered_again = first.recover_completed(turn_id, commit_marker=marker)

    assert completed_again == settled
    assert recovered_again == settled
    assert completed_again.curator_receipt_recorded is True
    assert completed_again.curator_receipt_settled is True
    assert second.record_path(turn_id).read_bytes() == before_stale_writers
    state = schedule.snapshot()
    assert state.pending_task_count == 1
    assert state.pinned_completion_ids == ()


def test_stale_noncommitted_turn_cannot_create_scheduler_receipt(
    isolated_dashboard,
) -> None:
    """Even a stale record with a valid Run identity cannot invent completion."""

    workspace, data_dir = isolated_dashboard
    turn_id = "turn_" + "d" * 32
    run_id = "run_" + "e" * 32
    first = ConversationTurnStore(
        workspace,
        data_dir=data_dir,
        owner_id="3" * 32,
    )
    second = ConversationTurnStore(
        workspace,
        data_dir=data_dir,
        owner_id="4" * 32,
    )
    fingerprint = request_fingerprint(
        workspace_id=first.workspace_id,
        session_id=None,
        message="never committed request",
    )
    assert first.claim(turn_id=turn_id, fingerprint=fingerprint).disposition == "claimed"
    assert first.mark_running(turn_id).execution_started is True
    first.attach_session(
        turn_id,
        session_id="session-interrupted",
        created_session=True,
    )
    stale_running = first.attach_run(turn_id, run_id=run_id)
    assert stale_running.status == "running"
    first.release_claim(turn_id)
    assert second.mark_interrupted(turn_id).status == "interrupted"

    service = ConversationTurnService(
        workspace,
        runtime_factory=_RuntimeFactory("must not run"),
        journal_factory=_journal_factory(data_dir),
        turn_store=first,
    )
    observed = service._ensure_committed_curator_completion(stale_running)

    assert observed.status == "interrupted"
    completion_id = curator_completion_id(run_id, turn_id)
    assert completion_id is not None
    state = CuratorScheduleStore(workspace).snapshot()
    assert state.pending_task_count == 0
    assert completion_id not in state.recent_completion_ids
    assert completion_id not in state.pinned_completion_ids


def test_direct_status_settles_pinned_orphan_without_corrupting_turn(
    isolated_dashboard,
) -> None:
    """A missing Session result leaves a readable, truthful terminal record."""

    workspace, data_dir = isolated_dashboard
    turn_id = "turn_" + "1" * 32
    run_id = "run_" + "2" * 32
    first = ConversationTurnStore(
        workspace,
        data_dir=data_dir,
        owner_id="5" * 32,
    )
    _committing_turn(
        first,
        turn_id=turn_id,
        session_id="session_missing",
        run_id=run_id,
    )
    first.release_claim(turn_id)
    completion_id = curator_completion_id(run_id, turn_id)
    assert completion_id is not None
    schedule = CuratorScheduleStore(workspace)
    with schedule.delivery_fence():
        schedule.record_completion(completion_id, pin=True)

    restarted_store = ConversationTurnStore(
        workspace,
        data_dir=data_dir,
        owner_id="6" * 32,
    )
    restarted = ConversationTurnService(
        workspace,
        runtime_factory=_RuntimeFactory("must not run"),
        journal_factory=_journal_factory(data_dir),
        turn_store=restarted_store,
        session_loader=lambda _session_id: None,
    )
    first_status = restarted.status(turn_id)

    assert first_status.status == "interrupted"
    durable = restarted_store.get(turn_id)
    assert durable is not None
    assert durable.status == "interrupted"
    assert durable.curator_receipt_recorded is True
    assert durable.curator_receipt_settled is True
    state = schedule.snapshot()
    assert state.pending_task_count == 1
    assert state.pinned_completion_ids == ()
    scan = restarted_store.curator_receipt_recovery_records(
        include_settled_committing=True,
    )
    assert scan.items == ()
    assert scan.exhaustive is True
    assert scan.diagnostics == ()

    second_restart = ConversationTurnService(
        workspace,
        runtime_factory=_RuntimeFactory("must still not run"),
        journal_factory=_journal_factory(data_dir),
        turn_store=ConversationTurnStore(
            workspace,
            data_dir=data_dir,
            owner_id="7" * 32,
        ),
        session_loader=lambda _session_id: None,
    )
    assert second_restart.status(turn_id).status == "interrupted"
    after = CuratorScheduleStore(workspace).snapshot()
    assert after.pending_task_count == 1
    assert after.pinned_completion_ids == ()


def test_orphan_page_cannot_starve_later_authoritative_commit(
    isolated_dashboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content recovery drains stable orphans before scanning the next page."""

    workspace, data_dir = isolated_dashboard
    store = ConversationTurnStore(workspace, data_dir=data_dir)
    orphan_ids: list[str] = []
    for index in range(3):
        turn_id = "turn_" + str(index + 3) * 32
        orphan_ids.append(turn_id)
        _committing_turn(
            store,
            turn_id=turn_id,
            session_id=f"session_missing_{index}",
            run_id="run_" + str(index + 3) * 32,
        )
        store.mark_curator_receipt_recorded(turn_id)
        store.mark_curator_receipt_settled(turn_id)
        store.release_claim(turn_id)

    authoritative_id = "turn_" + "9" * 32
    session = create_new_session(str(workspace))
    session.messages = [
        {"role": "user", "content": "durable request"},
        {"role": "assistant", "content": "durable answer"},
    ]
    session.turn_commits = [
        {
            "schemaVersion": 1,
            "turnId": authoritative_id,
            "userMessageIndex": 0,
            "assistantMessageIndex": 1,
        }
    ]
    save_session(session, force_full=True)
    _committing_turn(
        store,
        turn_id=authoritative_id,
        session_id=session.session_id,
        run_id="run_" + "9" * 32,
    )
    store.mark_curator_receipt_recorded(authoritative_id)
    store.mark_curator_receipt_settled(authoritative_id)
    store.release_claim(authoritative_id)

    real_scan = store.curator_receipt_recovery_records

    def two_item_pages(
        *,
        limit: int = 1_000,
        include_settled_committing: bool = False,
    ):
        del limit
        return real_scan(
            limit=2,
            include_settled_committing=include_settled_committing,
        )

    monkeypatch.setattr(
        store,
        "curator_receipt_recovery_records",
        two_item_pages,
    )
    service = ConversationTurnService(
        workspace,
        runtime_factory=_RuntimeFactory("must not run"),
        journal_factory=_journal_factory(data_dir),
        turn_store=store,
    )

    assert service._reconcile_curator_receipts() is True
    for turn_id in orphan_ids:
        record = store.get(turn_id)
        assert record is not None
        assert record.status == "interrupted"
    authoritative = store.get(authoritative_id)
    assert authoritative is not None
    assert authoritative.status == "completed"
    assert authoritative.curator_receipt_settled is True
    final_scan = real_scan(include_settled_committing=True)
    assert final_scan.items == ()
    assert final_scan.exhaustive is True
