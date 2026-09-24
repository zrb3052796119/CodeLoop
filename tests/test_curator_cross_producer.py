from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import minicode.curator_schedule as curator_schedule_module
from minicode.conversation import ConversationTurnService
from minicode.conversation_turn_store import (
    ConversationTurnStore,
    request_fingerprint,
)
from minicode.curator_schedule import (
    CuratorScheduleStore,
    curator_completion_id,
)
from minicode.memory import MemoryManager
from minicode.memory_pipeline import MemoryPipeline
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run


def _run_id(label: str) -> str:
    return "run_" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:32]


def _turn_id(label: str) -> str:
    return "turn_" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:32]


def _configure_session_storage(
    monkeypatch: pytest.MonkeyPatch,
    data_dir: Path,
) -> None:
    monkeypatch.setattr("minicode.session.MINI_CODE_DIR", data_dir)
    monkeypatch.setattr(
        "minicode.session.SESSIONS_DIR",
        data_dir / "sessions",
    )


def _pipeline(workspace: Path, data_dir: Path) -> MemoryPipeline:
    pipeline = MemoryPipeline(
        MemoryManager(project_root=workspace, data_root=data_dir)
    )
    pipeline.initialize(workspace_path=str(workspace))
    pipeline._curator._run_interval = 128
    return pipeline


def _leave_cli_completion_recorded(
    *,
    workspace: Path,
    journal: RunJournal,
    label: str,
) -> tuple[str, str]:
    schedule = CuratorScheduleStore(workspace)
    with observe_run(
        workspace=workspace,
        source="headless",
        title=label,
        journal_factory=lambda _workspace: journal,
    ) as observation:
        run_id = observation.run_id
        completion_id = observation.curator_completion_id
        assert run_id is not None
        assert completion_id is not None
        assert observation.prepare_curator_completion(completion_id) is True
        with schedule.delivery_fence():
            assert journal.begin_curator_completion_delivery(
                run_id,
                completion_id,
            ) is True
            schedule.record_completion(
                completion_id,
                pin=True,
                promote_existing=True,
            )
            assert observation.acknowledge_curator_completion(
                completion_id
            ) is True
    return run_id, completion_id


def _leave_dashboard_completion_recorded(
    *,
    store: ConversationTurnStore,
    schedule: CuratorScheduleStore,
    label: str,
) -> tuple[str, str, str]:
    turn_id = _turn_id(label)
    run_id = _run_id(label)
    fingerprint = request_fingerprint(
        workspace_id=store.workspace_id,
        session_id=None,
        message=label,
    )
    assert store.claim(
        turn_id=turn_id,
        fingerprint=fingerprint,
    ).disposition == "claimed"
    assert store.mark_running(turn_id).execution_started is True
    store.attach_session(
        turn_id,
        session_id="session_cross_producer",
        created_session=True,
    )
    store.attach_run(turn_id, run_id=run_id)
    assert store.begin_commit(turn_id).commit_allowed is True
    store.mark_completed(
        turn_id,
        commit_marker={
            "schemaVersion": 1,
            "userMessageIndex": 1,
            "assistantMessageIndex": 2,
        },
    )
    completion_id = curator_completion_id(run_id, turn_id)
    assert completion_id is not None
    with schedule.delivery_fence():
        schedule.record_completion(
            completion_id,
            pin=True,
            promote_existing=True,
        )
        store.mark_curator_receipt_recorded(turn_id)
    return turn_id, run_id, completion_id


class _Permissions:
    prompt = None
    operation_checkpoint = None

    def begin_turn(self) -> None:
        pass

    def end_turn(self) -> None:
        pass

    def get_summary(self) -> list[str]:
        return []


class _Tools:
    def get_skills(self) -> list[object]:
        return []

    def get_mcp_servers(self) -> list[object]:
        return []

    def dispose(self) -> None:
        pass


class _DashboardRuntime:
    def __init__(self, workspace: Path, data_dir: Path) -> None:
        self.system_prompt = "cross-producer recovery test"
        self.permissions = _Permissions()
        self.tools = _Tools()
        self.skill_routing = None
        self.pipeline = _pipeline(workspace, data_dir)
        self.maintenance_calls = 0

    def execute(self, messages, _observation, **_kwargs):
        return [
            *messages,
            {"role": "assistant", "content": "recovered"},
        ]

    def maintain_committed_turn(
        self,
        observation,
        *,
        current_turn_id: str,
        current_run_id: str | None,
        completion_already_recorded: bool = False,
    ) -> None:
        self.maintenance_calls += 1
        completion_id = curator_completion_id(
            current_run_id,
            current_turn_id,
        )
        assert completion_id is not None
        self.pipeline.maintain(
            completion_id=completion_id,
            completion_already_recorded=completion_already_recorded,
            agent_depth=0,
            event_sink=observation,
        )

    def dispose(self) -> None:
        self.tools.dispose()


def test_dashboard_turn_releases_full_ring_of_recorded_cli_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Dashboard-only restart must be able to drain CLI-owned pins."""

    monkeypatch.setattr(
        curator_schedule_module,
        "_MAX_RECENT_COMPLETIONS",
        2,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    _configure_session_storage(monkeypatch, data_dir)
    journal = RunJournal(workspace, data_dir=data_dir)
    cli_items = tuple(
        _leave_cli_completion_recorded(
            workspace=workspace,
            journal=journal,
            label=f"cli-{index}",
        )
        for index in range(2)
    )
    schedule = CuratorScheduleStore(workspace)
    before = schedule.snapshot()
    assert before.pending_task_count == 2
    assert set(before.pinned_completion_ids) == {
        completion_id for _run_id_value, completion_id in cli_items
    }
    assert len(journal.scan_pending_curator_completions().release_items) == 2

    runtimes: list[_DashboardRuntime] = []

    def runtime_factory(*, workspace: Path, **_kwargs) -> _DashboardRuntime:
        runtime = _DashboardRuntime(workspace, data_dir)
        runtimes.append(runtime)
        return runtime

    turn_store = ConversationTurnStore(workspace, data_dir=data_dir)
    turn_id = _turn_id("dashboard-current")
    result = ConversationTurnService(
        workspace,
        runtime_factory=runtime_factory,
        journal_factory=lambda resolved: RunJournal(
            resolved,
            data_dir=data_dir,
        ),
        turn_store=turn_store,
    ).turn(
        message="start one dashboard turn",
        session_id=None,
        turn_id=turn_id,
    )

    assert result.assistant == "recovered"
    assert len(runtimes) == 1
    current_completion_id = curator_completion_id(result.run_id, turn_id)
    assert current_completion_id is not None
    after = schedule.snapshot()
    assert after.pending_task_count == before.pending_task_count + 1
    assert after.pinned_completion_ids == ()
    assert current_completion_id in after.recent_completion_ids
    current_record = turn_store.get(turn_id)
    assert current_record is not None
    assert current_record.curator_receipt_recorded is True
    assert current_record.curator_receipt_settled is True
    assert journal.scan_pending_curator_completions().items == ()
    assert journal.scan_pending_curator_completions().release_items == ()


def test_headless_run_releases_full_ring_of_recorded_dashboard_turns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A headless-only restart must be able to drain Dashboard-owned pins."""

    monkeypatch.setattr(
        curator_schedule_module,
        "_MAX_RECENT_COMPLETIONS",
        2,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    store = ConversationTurnStore(workspace, data_dir=data_dir)
    schedule = CuratorScheduleStore(workspace)
    dashboard_items = tuple(
        _leave_dashboard_completion_recorded(
            store=store,
            schedule=schedule,
            label=f"dashboard-{index}",
        )
        for index in range(2)
    )
    before = schedule.snapshot()
    assert before.pending_task_count == 2
    assert set(before.pinned_completion_ids) == {
        completion_id
        for _turn_id_value, _run_id_value, completion_id in dashboard_items
    }

    journal = RunJournal(workspace, data_dir=data_dir)
    pipeline = _pipeline(workspace, data_dir)
    with observe_run(
        workspace=workspace,
        source="headless",
        title="headless current",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        run_id = observation.run_id
        completion_id = observation.curator_completion_id
        assert run_id is not None
        assert completion_id is not None
        assert pipeline.maintain(
            completion_id=completion_id,
            event_sink=observation,
        ) is None
        assert pipeline.maintain(
            completion_id=completion_id,
            event_sink=observation,
        ) is None

    after = schedule.snapshot()
    assert after.pending_task_count == before.pending_task_count + 1
    assert after.pinned_completion_ids == ()
    assert completion_id in after.recent_completion_ids
    for turn_id, _run_id_value, _completion_id in dashboard_items:
        record = store.get(turn_id)
        assert record is not None
        assert record.curator_receipt_recorded is True
        assert record.curator_receipt_settled is True
    scan = journal.scan_pending_curator_completions()
    assert scan.items == ()
    assert scan.release_items == ()


def test_turn_outbox_recovery_is_bounded_by_scan_horizon_not_twelve_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One invocation drains every visible receipt even with tiny pages."""

    from minicode.curator_outbox import reconcile_turn_curator_outbox

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    store = ConversationTurnStore(
        workspace,
        data_dir=data_dir,
        max_records=13,
        scan_limit=13,
    )
    schedule = CuratorScheduleStore(workspace)
    items = tuple(
        _leave_dashboard_completion_recorded(
            store=store,
            schedule=schedule,
            label=f"paged-dashboard-{index}",
        )
        for index in range(13)
    )
    real_scan = store.curator_receipt_recovery_records
    scan_calls = 0

    def one_item_pages(*, limit: int = 1_000):
        nonlocal scan_calls
        assert 1 <= limit <= 1_000
        scan_calls += 1
        return real_scan(limit=1)

    monkeypatch.setattr(
        store,
        "curator_receipt_recovery_records",
        one_item_pages,
    )

    assert reconcile_turn_curator_outbox(store, schedule) is True
    assert scan_calls == 14
    state = schedule.snapshot()
    assert state.pending_task_count == 13
    assert state.pinned_completion_ids == ()
    for turn_id, _run_id_value, _completion_id in items:
        record = store.get(turn_id)
        assert record is not None
        assert record.curator_receipt_recorded is True
        assert record.curator_receipt_settled is True
