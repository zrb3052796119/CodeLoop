from __future__ import annotations

import json
import hashlib
import multiprocessing
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import minicode.curator_schedule as curator_schedule_module
import minicode.cybernetic_supervisor as supervisor_module
import minicode.memory as memory_module
from minicode.agent_loop import run_agent_turn
from minicode.curator_schedule import (
    MAX_CURATOR_INTERVAL_TASKS,
    CuratorClaimLost,
    CuratorScheduleCorrupt,
    CuratorScheduleError,
    CuratorScheduleStore,
    curator_completion_id,
)
from minicode.memory import MemoryEntry, MemoryManager, MemoryScope
from minicode.memory_curator_agent import CuratorReport
from minicode.memory_pipeline import MemoryPipeline
from minicode.memory_store import MemoryStoreBusy, MemoryStoreCoordinator
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.turn_cancellation import TurnCancellationToken
from minicode.types import AgentStep


def _claim_worker(workspace: str, run_id: str, output: object) -> None:
    store = CuratorScheduleStore(workspace)
    store.record_completion(_completion(run_id))
    with store.execution_fence():
        decision = store.claim_due(interval=3, now=100.0)
    output.put(
        (
            bool(decision.claim),
            decision.claim.owner_id if decision.claim is not None else None,
        )
    )


def _fork_lock_probe(workspace: str, output: object) -> None:
    started = time.monotonic()
    try:
        CuratorScheduleStore(
            workspace,
            lock_timeout=0.05,
        ).record_completion(_completion("fork-child"))
    except BaseException as error:
        output.put((type(error).__name__, time.monotonic() - started))
    else:
        output.put(("entered", time.monotonic() - started))


def _run_id(label: str) -> str:
    return "run_" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:32]


def _turn_id(label: str) -> str:
    return "turn_" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:32]


def _completion(label: str, turn_label: str | None = None) -> str:
    result = curator_completion_id(
        _run_id(label),
        _turn_id(turn_label) if turn_label is not None else None,
    )
    assert result is not None
    return result


def _claim_due(
    store: CuratorScheduleStore,
    *,
    interval: int = 1,
    force: bool = False,
    now: float | int | None = None,
):
    with store.execution_fence():
        return store.claim_due(interval=interval, force=force, now=now)


def _record_and_claim(
    store: CuratorScheduleStore,
    completion_id: str,
    *,
    interval: int,
    now: float | int | None = None,
):
    store.record_completion(completion_id)
    return _claim_due(store, interval=interval, now=now)


def _finish_claim(
    store: CuratorScheduleStore,
    claim_id: str,
    *,
    outcome: str,
    now: float | int | None = None,
):
    with store.execution_fence():
        return store.finish_claim(claim_id, outcome=outcome, now=now)


def _renew_claim(
    store: CuratorScheduleStore,
    claim_id: str,
    *,
    now: float | int | None = None,
    allow_expired: bool = False,
):
    with store.execution_fence():
        return store.renew_claim(
            claim_id,
            now=now,
            allow_expired=allow_expired,
        )


class _CancelAfterCheckpoints:
    """Deterministic token that requests cancellation during curator work."""

    def __init__(self, *, stop_after: int, turn_id: str) -> None:
        self.stop_after = stop_after
        self.turn_id = turn_id
        self.checkpoints = 0

    def raise_if_requested(self) -> None:
        self.checkpoints += 1
        if self.checkpoints >= self.stop_after:
            raise TurnCancellationRequested(self.turn_id)


def _fresh_pipeline(
    workspace: Path,
    reports: list[CuratorReport],
    *,
    interval: int = 3,
) -> MemoryPipeline:
    pipeline = MemoryPipeline()
    pipeline.initialize(workspace_path=str(workspace))
    pipeline._curator._run_interval = interval

    def run_cycle(**_kwargs: object) -> CuratorReport:
        report = CuratorReport()
        reports.append(report)
        return report

    pipeline._curator.run_cycle = run_cycle
    return pipeline


def test_completion_receipt_is_stable_content_free_and_strict() -> None:
    first = curator_completion_id(_run_id("run-1"), _turn_id("turn-1"))
    assert first is not None
    assert len(first) == 64
    assert first == curator_completion_id(_run_id("run-1"), _turn_id("turn-1"))
    assert first != curator_completion_id(_run_id("run-1"), _turn_id("turn-2"))
    assert _run_id("run-1") not in first
    assert curator_completion_id(1) is None
    assert curator_completion_id(_run_id("run-1"), "bad\nturn") is None


def test_interval_matches_the_bounded_replay_window(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = CuratorScheduleStore(workspace)

    accepted = _record_and_claim(
        store,
        _completion("interval-boundary"),
        interval=MAX_CURATOR_INTERVAL_TASKS,
    )
    assert accepted.state.pending_task_count == 1
    with pytest.raises(ValueError):
        _record_and_claim(
            store,
            _completion("interval-overflow"),
            interval=MAX_CURATOR_INTERVAL_TASKS + 1,
        )
    with pytest.raises(ValueError):
        from minicode.memory_curator_agent import MemoryCuratorAgent

        MemoryCuratorAgent(
            run_interval_tasks=MAX_CURATOR_INTERVAL_TASKS + 1
        )


def test_fresh_pipeline_instances_trigger_once_at_durable_threshold(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reports: list[CuratorReport] = []
    results = []

    for index in range(10):
        pipeline = _fresh_pipeline(workspace, reports, interval=10)
        results.append(
            pipeline.maintain(
                completion_id=_completion(f"run-{index}"),
            )
        )

    assert results[:9] == [None] * 9
    assert results[9] is not None
    assert len(reports) == 1
    state = CuratorScheduleStore(workspace).snapshot()
    assert state.pending_task_count == 0
    assert state.completed_cycle_count == 1
    assert state.active_claim is None


def test_explicit_outbox_delivery_proof_survives_recent_id_eviction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        curator_schedule_module,
        "_MAX_RECENT_COMPLETIONS",
        3,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    schedule = CuratorScheduleStore(workspace)
    delivered = _completion("dashboard-delivered")
    schedule.record_completion(delivered)
    for index in range(3):
        schedule.record_completion(_completion(f"eviction-filler-{index}"))

    before = schedule.snapshot()
    assert delivered not in before.recent_completion_ids
    assert before.pending_task_count == 4

    pipeline = _fresh_pipeline(workspace, [], interval=10)
    assert (
        pipeline.maintain(
            completion_id=delivered,
            completion_already_recorded=True,
            record_only=True,
        )
        is None
    )
    assert CuratorScheduleStore(workspace).snapshot() == before

    # An external-delivery proof changes only the delivery step. It must not
    # bypass the root-task identity contract that guards maintenance.
    assert (
        pipeline.maintain(
            completion_id=delivered,
            completion_already_recorded=True,
            record_only=True,
            agent_depth=1,
        )
        is None
    )
    assert (
        pipeline.maintain(
            completion_id="not-a-completion-id",
            completion_already_recorded=True,
            record_only=True,
        )
        is None
    )
    assert (
        pipeline.maintain(
            completion_already_recorded=True,
            record_only=True,
        )
        is None
    )
    assert CuratorScheduleStore(workspace).snapshot() == before


def test_real_agent_turn_chain_reaches_tenth_task_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    workspace.mkdir()
    data_dir.mkdir()
    monkeypatch.setattr(memory_module, "MINI_CODE_DIR", data_dir)
    monkeypatch.setattr(
        supervisor_module,
        "SUPERVISOR_STATE_PATH",
        data_dir / "cybernetic_supervisor.json",
    )
    manager = MemoryManager(project_root=workspace, data_root=data_dir)

    class FinalModel:
        model_id = "test-model"

        @staticmethod
        def next(_messages: object, on_stream_chunk: object = None) -> AgentStep:
            return AgentStep(type="assistant", content="done")

    run_ids: list[str] = []
    for index in range(10):
        with observe_run(
            workspace=workspace,
            source="headless",
            title=f"curator task {index}",
            journal_factory=lambda resolved: RunJournal(
                resolved,
                data_dir=data_dir,
            ),
        ) as observation:
            assert observation.run_id is not None
            run_ids.append(observation.run_id)
            result = run_agent_turn(
                model=FinalModel(),
                tools=ToolRegistry([]),
                messages=[
                    {"role": "system", "content": "SYSTEM"},
                    {
                        "role": "user",
                        "content": f"task {index}",
                        # Embedded CLI callers may carry a conversation Turn
                        # identity, but the CLI Run outbox remains run-bound.
                        "_conversation_turn_id": _turn_id(f"embedded-{index}"),
                    },
                ],
                cwd=str(workspace),
                memory_manager=manager,
                event_sink=observation,
                runtime={"reflectionSynthesizerMode": "rule"},
            )
            observation.assistant_completed(
                content_present=True,
                content_length=len(result[-1].get("content", "")),
            )

    state = CuratorScheduleStore(workspace).snapshot()
    assert state.pending_task_count == 0
    assert state.completed_cycle_count == 1
    assert len(state.recent_completion_ids) == 10
    assert set(state.recent_completion_ids) == {
        curator_completion_id(run_id) for run_id in run_ids
    }
    assert RunJournal(
        workspace,
        data_dir=data_dir,
    ).pending_curator_completions() == ()


def test_cli_run_outbox_replays_a_scheduler_write_failure_on_next_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    workspace.mkdir()
    data_dir.mkdir()
    monkeypatch.setattr(memory_module, "MINI_CODE_DIR", data_dir)
    reports: list[CuratorReport] = []
    first_pipeline = _fresh_pipeline(workspace, reports, interval=10)
    journal_factory = lambda resolved: RunJournal(resolved, data_dir=data_dir)

    with observe_run(
        workspace=workspace,
        source="headless",
        title="scheduler unavailable",
        journal_factory=journal_factory,
    ) as first_observation:
        first_run_id = first_observation.run_id
        assert first_run_id is not None
        schedule = first_pipeline._curator_schedule
        real_record = schedule.record_completion

        def fail_record(_completion_id: str) -> object:
            raise MemoryStoreBusy("synthetic scheduler contention")

        schedule.record_completion = fail_record
        assert first_pipeline.maintain(
            completion_id=curator_completion_id(first_run_id),
            event_sink=first_observation,
        ) is None
        schedule.record_completion = real_record

    journal = RunJournal(workspace, data_dir=data_dir)
    first_completion = curator_completion_id(first_run_id)
    assert first_completion is not None
    assert journal.pending_curator_completions() == (
        (first_run_id, first_completion),
    )

    second_pipeline = _fresh_pipeline(workspace, reports, interval=10)
    with observe_run(
        workspace=workspace,
        source="headless",
        title="scheduler recovered",
        journal_factory=journal_factory,
    ) as second_observation:
        second_run_id = second_observation.run_id
        assert second_run_id is not None
        assert second_pipeline.maintain(
            completion_id=curator_completion_id(second_run_id),
            event_sink=second_observation,
        ) is None

    assert journal.pending_curator_completions() == ()
    state = CuratorScheduleStore(workspace).snapshot()
    assert state.pending_task_count == 2
    assert set(state.recent_completion_ids) == {
        first_completion,
        curator_completion_id(second_run_id),
    }


def test_old_ack_failure_enqueues_current_run_before_deferring_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    workspace.mkdir()
    data_dir.mkdir()
    monkeypatch.setattr(memory_module, "MINI_CODE_DIR", data_dir)
    journal = RunJournal(workspace, data_dir=data_dir)

    old = journal.create_run(title="old pending", source="headless")
    old_completion = curator_completion_id(old.id)
    assert old_completion is not None
    journal.transition(old.id, "running")
    assert journal.prepare_curator_completion(old.id, old_completion)
    journal.transition(old.id, "completed")

    real_ack = journal.acknowledge_curator_completion
    failed_once = False

    def fail_old_ack_once(run_id: str, completion_id: str) -> bool:
        nonlocal failed_once
        if run_id == old.id and not failed_once:
            failed_once = True
            return False
        return real_ack(run_id, completion_id)

    journal.acknowledge_curator_completion = fail_old_ack_once
    first_pipeline = _fresh_pipeline(workspace, [], interval=10)
    with observe_run(
        workspace=workspace,
        source="headless",
        title="current durable enqueue",
        journal_factory=lambda _resolved: journal,
    ) as observation:
        current_run_id = observation.run_id
        assert current_run_id is not None
        current_completion = curator_completion_id(current_run_id)
        assert current_completion is not None
        assert first_pipeline.maintain(
            completion_id=current_completion,
            event_sink=observation,
        ) is None

    assert set(journal.pending_curator_completions()) == {
        (old.id, old_completion),
        (current_run_id, current_completion),
    }
    runs_root = (
        data_dir
        / "dashboard"
        / "workspaces"
        / journal.workspace_id
        / "runs"
    )
    old_receipt = json.loads(
        (runs_root / old.id / "curator_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    current_receipt = json.loads(
        (runs_root / current_run_id / "curator_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert old_receipt["state"] == "delivering"
    assert current_receipt["state"] == "pending"
    first_state = CuratorScheduleStore(workspace).snapshot()
    assert first_state.recent_completion_ids == (old_completion,)
    assert current_completion not in first_state.recent_completion_ids

    journal.acknowledge_curator_completion = real_ack
    second_pipeline = _fresh_pipeline(workspace, [], interval=10)
    with observe_run(
        workspace=workspace,
        source="headless",
        title="recover every pending receipt",
        journal_factory=lambda _resolved: journal,
    ) as observation:
        next_run_id = observation.run_id
        assert next_run_id is not None
        next_completion = curator_completion_id(next_run_id)
        assert next_completion is not None
        assert second_pipeline.maintain(
            completion_id=next_completion,
            event_sink=observation,
        ) is None

    assert journal.pending_curator_completions() == ()
    recovered = CuratorScheduleStore(workspace).snapshot()
    assert recovered.pending_task_count == 3
    assert set(recovered.recent_completion_ids) == {
        old_completion,
        current_completion,
        next_completion,
    }


def test_finalized_workflow_records_completion_without_running_curator_inline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    workspace.mkdir()
    data_dir.mkdir()
    monkeypatch.setattr(memory_module, "MINI_CODE_DIR", data_dir)
    monkeypatch.setattr(
        supervisor_module,
        "SUPERVISOR_STATE_PATH",
        data_dir / "cybernetic_supervisor.json",
    )
    manager = MemoryManager(project_root=workspace, data_root=data_dir)
    schedule = CuratorScheduleStore(workspace)
    for index in range(9):
        schedule.record_completion(_completion(f"seed-{index}"))
    authority_output = "committed workflow authority bytes"

    class WorkflowModel:
        model_id = "test-model"
        calls = 0

        def next(self, _messages: object, on_stream_chunk: object = None) -> AgentStep:
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("authority delivery must not call the model again")
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "workflow-final",
                        "toolName": "read_file",
                        "input": {},
                    }
                ],
            )

    tools = ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="finalized workflow fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _input, _context: ToolResult(
                    ok=True,
                    output=authority_output,
                    _must_deliver=True,
                ),
            )
        ]
    )
    journal_factory = lambda resolved: RunJournal(resolved, data_dir=data_dir)

    with observe_run(
        workspace=workspace,
        source="headless",
        title="finalized workflow",
        journal_factory=journal_factory,
    ) as observation:
        result = run_agent_turn(
            model=WorkflowModel(),
            tools=tools,
            messages=[
                {"role": "system", "content": "SYSTEM"},
                {"role": "user", "content": "commit workflow"},
            ],
            cwd=str(workspace),
            memory_manager=manager,
            event_sink=observation,
            runtime={"reflectionSynthesizerMode": "rule"},
        )
        workflow_run_id = observation.run_id

    assert result[-1]["role"] == "tool_result"
    assert result[-1]["content"] == authority_output
    assert workflow_run_id is not None
    state = schedule.snapshot()
    assert state.pending_task_count == 10
    assert state.completed_cycle_count == 0
    assert state.active_claim is None
    assert RunJournal(
        workspace,
        data_dir=data_dir,
    ).pending_curator_completions() == ()


def test_completion_replay_is_byte_stable_and_does_not_retrigger(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = CuratorScheduleStore(workspace)
    receipt = _completion("run-1")
    first = _record_and_claim(store, receipt, interval=3, now=100)
    before = store.path.read_bytes()

    replay_store = CuratorScheduleStore(workspace)
    replay_state = replay_store.record_completion(receipt)
    replay = _claim_due(replay_store, interval=3, now=200)

    assert first.claim is None
    assert replay.claim is None
    assert replay.replayed_completion is False
    assert replay.reason == "below_threshold"
    assert replay_state == replay.state
    assert replay.state.pending_task_count == 1
    assert store.path.read_bytes() == before


def test_claim_mutation_requires_workspace_execution_fence(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = CuratorScheduleStore(workspace)
    store.record_completion(_completion("fenced-claim"))

    with pytest.raises(
        CuratorScheduleError,
        match="requires the workspace execution fence",
    ):
        store.claim_due(interval=1)

    unclaimed = store.snapshot()
    assert unclaimed.pending_task_count == 1
    assert unclaimed.active_claim is None
    with store.execution_fence():
        claimed = store.claim_due(interval=1)
    assert claimed.claim is not None


def test_pipeline_does_not_advance_current_receipt_after_replay_ack_gap(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reports: list[CuratorReport] = []
    pipeline = _fresh_pipeline(workspace, reports, interval=1)

    class ReplayBlockedSink:
        prepare_calls = 0
        deferred_ids: list[str] = []

        def replay_pending_curator_completions(
            self,
            _deliver,
            *,
            defer_completion_id: str | None = None,
            release=None,
        ):
            if defer_completion_id is not None:
                self.deferred_ids.append(defer_completion_id)
            return False

        def prepare_curator_completion(self, _completion_id: str) -> bool:
            self.prepare_calls += 1
            return True

    sink = ReplayBlockedSink()
    assert pipeline.maintain(
        completion_id=_completion("blocked-by-old-outbox"),
        event_sink=sink,
    ) is None

    assert sink.prepare_calls == 1
    assert sink.deferred_ids == [_completion("blocked-by-old-outbox")]
    state = CuratorScheduleStore(workspace).snapshot()
    assert state.pending_task_count == 0
    assert state.active_claim is None
    assert reports == []


def test_pipeline_stops_cycle_when_current_run_outbox_ack_is_uncertain(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reports: list[CuratorReport] = []
    pipeline = _fresh_pipeline(workspace, reports, interval=1)

    class AckBlockedSink:
        @staticmethod
        def replay_pending_curator_completions(
            deliver,
            *,
            defer_completion_id: str | None = None,
            release=None,
        ):
            assert defer_completion_id is not None
            deliver(defer_completion_id)
            return False

        @staticmethod
        def prepare_curator_completion(_completion_id: str) -> bool:
            return True

    completion_id = _completion("current-outbox-ack-gap")
    assert pipeline.maintain(
        completion_id=completion_id,
        event_sink=AckBlockedSink(),
    ) is None

    state = CuratorScheduleStore(workspace).snapshot()
    assert state.pending_task_count == 1
    assert state.recent_completion_ids == (completion_id,)
    assert state.active_claim is None
    assert reports == []


@pytest.mark.parametrize("replay_result", [None, 0, "yes"])
def test_pipeline_requires_explicit_true_outbox_replay_proof(
    tmp_path: Path,
    replay_result: object,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    pipeline = _fresh_pipeline(workspace, [], interval=10)

    class AmbiguousReplaySink:
        @staticmethod
        def prepare_curator_completion(_completion_id: str) -> bool:
            return True

        @staticmethod
        def replay_pending_curator_completions(
            _deliver,
            *,
            defer_completion_id: str | None = None,
            release=None,
        ):
            assert defer_completion_id is not None
            return replay_result

    assert pipeline.maintain(
        completion_id=_completion(f"ambiguous-{replay_result!r}"),
        event_sink=AmbiguousReplaySink(),
    ) is None
    assert CuratorScheduleStore(workspace).snapshot().pending_task_count == 0


def test_prepare_failure_still_attempts_final_scheduler_delivery(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    pipeline = _fresh_pipeline(workspace, [], interval=10)

    class BrokenOptionalOutboxSink:
        @staticmethod
        def prepare_curator_completion(_completion_id: str) -> bool:
            return False

        @staticmethod
        def replay_pending_curator_completions(_deliver) -> bool:
            return True

    completion_id = _completion("prepare-failed-scheduler-succeeds")
    assert pipeline.maintain(
        completion_id=completion_id,
        event_sink=BrokenOptionalOutboxSink(),
    ) is None
    state = CuratorScheduleStore(workspace).snapshot()
    assert state.pending_task_count == 1
    assert state.recent_completion_ids == (completion_id,)


def test_pipeline_defers_claim_when_called_inside_memory_transaction(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "home" / ".mini-code"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace, data_root=data_root)
    reports: list[CuratorReport] = []
    pipeline = _fresh_pipeline(workspace, reports, interval=1)
    pipeline._memory = manager
    completion_id = _completion("inverted-lock-order")

    result = manager.coordinated_write(
        tuple(MemoryScope),
        lambda: pipeline.maintain(completion_id=completion_id),
    )

    assert result is None
    state = CuratorScheduleStore(workspace).snapshot()
    assert state.pending_task_count == 1
    assert state.recent_completion_ids == (completion_id,)
    assert state.active_claim is None
    assert reports == []


@pytest.mark.parametrize("legacy_version", [1, 2])
def test_legacy_schedule_is_read_without_rewrite_and_first_mutation_upgrades_v3(
    tmp_path: Path,
    legacy_version: int,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = CuratorScheduleStore(workspace)
    first_receipt = _completion("legacy-v1")
    store.record_completion(first_receipt)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 3
    payload["schemaVersion"] = legacy_version
    payload.pop("pinnedCompletionIds")
    store.path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    legacy_bytes = store.path.read_bytes()

    restarted = CuratorScheduleStore(workspace)
    snapshot = restarted.snapshot()
    assert snapshot.pending_task_count == 1
    assert snapshot.recent_completion_ids == (first_receipt,)
    assert restarted.path.read_bytes() == legacy_bytes

    # Even an idempotent replay is a current-version mutation boundary.
    restarted.record_completion(first_receipt)
    upgraded = json.loads(restarted.path.read_text(encoding="utf-8"))
    assert upgraded["schemaVersion"] == 3
    assert upgraded["pendingTaskCount"] == 1
    assert upgraded["recentCompletionIds"] == [first_receipt]
    assert upgraded["pinnedCompletionIds"] == []


def test_record_only_is_replay_safe_and_never_creates_an_orphan_claim(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = CuratorScheduleStore(workspace)
    receipts = [_completion(f"record-only-{index}") for index in range(3)]

    for receipt in receipts:
        state = store.record_completion(receipt)

    assert state.pending_task_count == 3
    assert state.active_claim is None
    before = store.path.read_bytes()
    assert store.record_completion(receipts[-1]) == state
    assert store.path.read_bytes() == before

    claimed = _claim_due(store, interval=3)
    assert claimed.replayed_completion is False
    assert claimed.reason == "claimed"
    assert claimed.claim is not None
    assert claimed.claim.consume_task_count == 3


def test_root_identity_is_required_and_force_does_not_record_a_task(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reports: list[CuratorReport] = []
    pipeline = _fresh_pipeline(workspace, reports)

    assert pipeline.maintain() is None
    assert pipeline.maintain(
        completion_id=_completion("subagent-run"),
        agent_depth=1,
    ) is None
    assert not (workspace / ".mini-code-memory" / "curator_schedule.json").exists()

    assert pipeline.maintain(force=True) is not None
    state = CuratorScheduleStore(workspace).snapshot()
    assert state.pending_task_count == 0
    assert state.completed_cycle_count == 1
    assert len(reports) == 1


def test_active_claim_serializes_concurrent_completions_and_preserves_overflow(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    seed = CuratorScheduleStore(workspace)
    for index in range(2):
        assert _record_and_claim(
            seed,
            _completion(f"seed-{index}"),
            interval=3,
            now=90 + index,
        ).claim is None

    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    workers = [
        context.Process(
            target=_claim_worker,
            args=(str(workspace), f"race-{index}", output),
        )
        for index in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)
        assert worker.exitcode == 0

    results = [output.get(timeout=2) for _ in workers]
    assert sorted(claimed for claimed, _owner in results) == [False, True]
    owner_id = next(
        owner
        for claimed, owner in results
        if claimed and owner is not None
    )
    state = CuratorScheduleStore(workspace).snapshot()
    assert state.pending_task_count == 4
    assert state.active_claim is not None
    assert state.active_claim.owner_id == owner_id

    completed = _finish_claim(
        CuratorScheduleStore(workspace, owner_id=owner_id),
        state.active_claim.claim_id,
        outcome="completed",
        now=101,
    )
    assert completed.pending_task_count == 1
    assert completed.completed_cycle_count == 1


@pytest.mark.skipif(os.name != "posix", reason="POSIX fork regression")
def test_forked_child_cannot_inherit_nested_scheduler_lock_authority(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = CuratorScheduleStore(workspace)
    store.snapshot()
    context = multiprocessing.get_context("fork")
    output = context.Queue()

    with store._coordinator.transaction():
        worker = context.Process(
            target=_fork_lock_probe,
            args=(str(workspace), output),
        )
        worker.start()
        worker.join(timeout=2)
        assert worker.exitcode == 0
        result, elapsed = output.get(timeout=1)
        assert result == MemoryStoreBusy.__name__
        assert elapsed >= 0.04

    assert store.snapshot().pending_task_count == 0


@pytest.mark.skipif(os.name != "posix", reason="POSIX fork regression")
def test_forked_child_does_not_clean_up_an_inherited_transaction_fd(
    tmp_path: Path,
) -> None:
    root = tmp_path / "locks"
    probe = tmp_path / "child-probe"
    coordinator = MemoryStoreCoordinator(root)
    read_fd, write_fd = os.pipe()
    pid = -1
    probe_fd = -1

    try:
        try:
            with coordinator.transaction():
                pid = os.fork()
                if pid == 0:
                    os.close(read_fd)
                    probe_fd = os.open(
                        probe,
                        os.O_RDWR | os.O_CREAT,
                        0o600,
                    )
        except BaseException as error:
            if pid == 0:
                os.write(write_fd, f"error:{type(error).__name__}".encode())
                os._exit(2)
            raise

        if pid == 0:
            try:
                os.fstat(probe_fd)
                os.write(write_fd, b"open")
            except BaseException as error:
                os.write(write_fd, f"error:{type(error).__name__}".encode())
                os._exit(3)
            finally:
                try:
                    os.close(probe_fd)
                except OSError:
                    pass
            os._exit(0)

        os.close(write_fd)
        write_fd = -1
        payload = os.read(read_fd, 128)
        waited_pid, status = os.waitpid(pid, 0)
        assert waited_pid == pid
        assert os.waitstatus_to_exitcode(status) == 0
        assert payload == b"open"
    finally:
        for descriptor in (read_fd, write_fd):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def test_failed_and_cancelled_cycles_keep_debt_and_apply_bounded_cooldown(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = CuratorScheduleStore(workspace)
    decision = None
    for index in range(3):
        decision = _record_and_claim(
            store,
            _completion(f"run-{index}"),
            interval=3,
            now=100 + index,
        )
    assert decision is not None and decision.claim is not None

    failed = _finish_claim(
        store,
        decision.claim.claim_id,
        outcome="failed",
        now=103,
    )
    assert failed.pending_task_count == 3
    assert failed.consecutive_failures == 1
    assert failed.retry_not_before_ms == 108_000

    cooling = _record_and_claim(
        store,
        _completion("run-cooling"),
        interval=3,
        now=107,
    )
    assert cooling.claim is None
    assert cooling.reason == "retry_cooldown"
    retry = _record_and_claim(
        store,
        _completion("run-retry"),
        interval=3,
        now=108,
    )
    assert retry.claim is not None
    cancelled = _finish_claim(
        store,
        retry.claim.claim_id,
        outcome="cancelled",
        now=109,
    )
    assert cancelled.pending_task_count == 5
    assert cancelled.consecutive_failures == 1
    assert cancelled.retry_not_before_ms == 114_000


def test_expired_lease_has_one_takeover_and_does_not_lose_pending_tasks(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = CuratorScheduleStore(workspace, lease_seconds=1)
    decision = None
    for index in range(3):
        decision = _record_and_claim(
            store,
            _completion(f"run-{index}"),
            interval=3,
            now=100,
        )
    assert decision is not None and decision.claim is not None

    takeover_store = CuratorScheduleStore(
        workspace,
        lease_seconds=1,
    )
    takeover = _record_and_claim(
        takeover_store,
        _completion("run-after-crash"),
        interval=3,
        now=101,
    )

    assert takeover.claim is not None
    assert takeover.claim.claim_id != decision.claim.claim_id
    assert takeover.state.pending_task_count == 4
    assert takeover.state.consecutive_failures == 1


def test_matching_owner_can_renew_claim_but_stale_owner_is_fenced(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    owner = "a" * 32
    store = CuratorScheduleStore(
        workspace,
        lease_seconds=1,
        owner_id=owner,
    )
    first = _record_and_claim(
        store,
        _completion("renew-first"),
        interval=1,
        now=100,
    )
    assert first.claim is not None

    renewed = _renew_claim(store, first.claim.claim_id, now=100.5)
    assert renewed is not None
    assert renewed.claim_id == first.claim.claim_id
    assert renewed.claimed_at_ms == 100_500
    assert renewed.lease_until_ms == 101_500
    stale_store = CuratorScheduleStore(
        workspace,
        lease_seconds=1,
        owner_id="b" * 32,
    )
    assert _renew_claim(stale_store, renewed.claim_id, now=100.75) is None

    takeover_store = CuratorScheduleStore(
        workspace,
        lease_seconds=1,
        owner_id="b" * 32,
    )
    takeover = _record_and_claim(
        takeover_store,
        _completion("renew-takeover"),
        interval=1,
        now=101.5,
    )
    assert takeover.claim is not None
    assert takeover.claim.claim_id != renewed.claim_id
    assert _renew_claim(store, renewed.claim_id, now=101.6) is None


@pytest.mark.parametrize(
    "shared_data_root",
    [True, False],
    ids=["same-data-root", "different-data-roots"],
)
def test_workspace_execution_fence_prevents_successor_takeover_during_curator_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shared_data_root: bool,
) -> None:
    old_data_dir = tmp_path / "old-home" / ".mini-code"
    new_data_dir = (
        old_data_dir
        if shared_data_root
        else tmp_path / "new-home" / ".mini-code"
    )
    monkeypatch.setattr(memory_module, "MINI_CODE_DIR", old_data_dir)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    old_manager = MemoryManager(
        project_root=workspace,
        data_root=old_data_dir,
    )
    new_manager = MemoryManager(
        project_root=workspace,
        data_root=new_data_dir,
    )
    old_pipeline = MemoryPipeline(old_manager)
    new_pipeline = MemoryPipeline(new_manager)
    old_pipeline.initialize(workspace_path=str(workspace))
    new_pipeline.initialize(workspace_path=str(workspace))
    old_pipeline._curator._run_interval = 1
    new_pipeline._curator._run_interval = 1
    old_pipeline._curator_schedule = CuratorScheduleStore(
        workspace,
        lease_seconds=1,
        owner_id="c" * 32,
    )
    new_schedule = CuratorScheduleStore(
        workspace,
        lease_seconds=1,
        owner_id="d" * 32,
    )
    new_pipeline._curator_schedule = new_schedule
    old_started = threading.Event()
    new_fence_attempted = threading.Event()
    release_old = threading.Event()
    old_results: list[object] = []
    new_results: list[object] = []

    def blocked_collect(report: CuratorReport) -> CuratorReport:
        old_started.set()
        assert release_old.wait(5)
        return report

    old_pipeline._curator._collect_stats = blocked_collect
    original_new_execution_fence = new_schedule.execution_fence

    def observe_new_execution_fence():
        new_fence_attempted.set()
        return original_new_execution_fence()

    new_schedule.execution_fence = observe_new_execution_fence
    old_worker = threading.Thread(
        target=lambda: old_results.append(
            old_pipeline.maintain(
                completion_id=_completion("stale-old"),
            )
        )
    )
    old_worker.start()
    assert old_started.wait(2)
    old_claim = CuratorScheduleStore(workspace).snapshot().active_claim
    assert old_claim is not None
    time.sleep(1.05)

    new_worker = threading.Thread(
        target=lambda: new_results.append(
            new_pipeline.maintain(
                completion_id=_completion("stale-new"),
            )
        )
    )
    new_worker.start()
    deadline = time.monotonic() + 2
    observed = None
    while time.monotonic() < deadline:
        state = CuratorScheduleStore(workspace).snapshot()
        observed = state.active_claim
        if state.pending_task_count == 2:
            break
        time.sleep(0.01)
    if not shared_data_root:
        assert new_fence_attempted.wait(2)
        assert new_results == []
    assert observed == old_claim
    assert CuratorScheduleStore(workspace).snapshot().pending_task_count == 2
    release_old.set()
    old_worker.join(timeout=5)
    new_worker.join(timeout=5)

    assert not old_worker.is_alive()
    assert not new_worker.is_alive()
    assert len(old_results) == 1
    assert old_results[0]["status"] == "completed"
    assert len(new_results) == 1
    assert new_results[0]["status"] == "completed"
    state = CuratorScheduleStore(workspace).snapshot()
    assert state.pending_task_count == 0
    assert state.completed_cycle_count == 2
    assert state.active_claim is None


def test_expired_lease_can_be_reclaimed_by_the_same_completion_receipt(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = CuratorScheduleStore(workspace, lease_seconds=1)
    last_receipt = ""
    decision = None
    for index in range(3):
        last_receipt = _completion(f"run-{index}")
        decision = _record_and_claim(
            store,
            last_receipt,
            interval=3,
            now=100,
        )
    assert decision is not None and decision.claim is not None
    old_claim_id = decision.claim.claim_id

    before_expiry_store = CuratorScheduleStore(
        workspace,
        lease_seconds=1,
    )
    before_expiry_store.record_completion(last_receipt)
    before_expiry = _claim_due(
        before_expiry_store,
        interval=3,
        now=100.5,
    )
    assert before_expiry.claim is None
    assert before_expiry.reason == "claim_active"
    assert before_expiry.replayed_completion is False

    reclaimed_store = CuratorScheduleStore(workspace, lease_seconds=1)
    reclaimed_store.record_completion(last_receipt)
    reclaimed = _claim_due(reclaimed_store, interval=3, now=101)
    assert reclaimed.claim is not None
    assert reclaimed.reason == "claimed"
    assert reclaimed.replayed_completion is False
    assert reclaimed.state.pending_task_count == 3
    assert reclaimed.claim.claim_id != old_claim_id

    with pytest.raises(CuratorClaimLost):
        _finish_claim(
            reclaimed_store,
            old_claim_id,
            outcome="completed",
            now=101.5,
        )
    stale_finish = reclaimed_store.snapshot()
    assert stale_finish.active_claim == reclaimed.claim
    assert stale_finish.pending_task_count == 3


def test_expired_claim_clear_is_durable_when_interval_increases(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = CuratorScheduleStore(workspace, lease_seconds=1)
    last_receipt = ""
    for index in range(3):
        last_receipt = _completion(f"run-{index}")
        decision = _record_and_claim(
            store,
            last_receipt,
            interval=3,
            now=100,
        )
    assert decision.claim is not None

    replay_store = CuratorScheduleStore(
        workspace,
        lease_seconds=1,
    )
    replay_store.record_completion(last_receipt)
    replay = _claim_due(replay_store, interval=4, now=101)

    assert replay.claim is None
    assert replay.reason == "below_threshold"
    assert replay.replayed_completion is False
    assert replay.state.active_claim is None
    assert CuratorScheduleStore(workspace).snapshot().active_claim is None


@pytest.mark.parametrize("payload", [b"not-json", b"[]", b'{"schemaVersion":true}'])
def test_corrupt_schedule_fails_closed_without_overwrite(
    tmp_path: Path,
    payload: bytes,
) -> None:
    workspace = tmp_path / "workspace"
    state_root = workspace / ".mini-code-memory"
    state_root.mkdir(parents=True)
    state_path = state_root / "curator_schedule.json"
    state_path.write_bytes(payload)

    with pytest.raises(CuratorScheduleCorrupt):
        CuratorScheduleStore(workspace).record_completion(
            _completion("run-1")
        )

    assert state_path.read_bytes() == payload


@pytest.mark.parametrize("corruption", ["oversized-lease", "future-retry"])
def test_schedule_rejects_temporal_state_that_can_freeze_maintenance(
    tmp_path: Path,
    corruption: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = CuratorScheduleStore(workspace)
    receipt = _completion("temporal-corruption")
    decision = _record_and_claim(store, receipt, interval=1)
    assert decision.claim is not None
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    if corruption == "oversized-lease":
        claim = payload["activeClaim"]
        claim["leaseUntilMs"] = claim["claimedAtMs"] + (24 * 60 * 60 * 1000) + 1
    else:
        payload["activeClaim"] = None
        payload["retryNotBeforeMs"] = 99_999_999_999_999
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CuratorScheduleCorrupt):
        CuratorScheduleStore(workspace).snapshot()


def test_symlink_and_fifo_schedule_authorities_fail_closed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    state_root = workspace / ".mini-code-memory"
    state_root.mkdir(parents=True)
    state_path = state_root / "curator_schedule.json"
    victim = tmp_path / "victim.json"
    victim.write_text("unchanged", encoding="utf-8")
    state_path.symlink_to(victim)
    store = CuratorScheduleStore(workspace)

    with pytest.raises(CuratorScheduleCorrupt):
        store.record_completion(_completion("run-symlink"))
    assert victim.read_text(encoding="utf-8") == "unchanged"

    state_path.unlink()
    if hasattr(os, "mkfifo"):
        os.mkfifo(state_path)
        with pytest.raises(CuratorScheduleCorrupt):
            store.record_completion(_completion("run-fifo"))


def test_symlinked_schedule_root_is_rejected_before_any_external_write(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / ".mini-code-memory").symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(CuratorScheduleCorrupt):
        CuratorScheduleStore(workspace).record_completion(
            _completion("run-root-symlink")
        )

    assert list(outside.iterdir()) == []


def test_pipeline_failed_report_and_cancellation_do_not_ack_debt(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    seed = CuratorScheduleStore(workspace)
    for index in range(2):
        seed.record_completion(_completion(f"seed-{index}"))

    failed_pipeline = MemoryPipeline()
    failed_pipeline.initialize(workspace_path=str(workspace))
    failed_pipeline._curator._run_interval = 3
    failed_pipeline._curator.run_cycle = MagicMock(
        return_value=CuratorReport(status="failed")
    )
    result = failed_pipeline.maintain(
        completion_id=_completion("failed-run")
    )
    assert result is not None and result["status"] == "failed"
    failed_state = CuratorScheduleStore(workspace).snapshot()
    assert failed_state.pending_task_count == 3
    assert failed_state.active_claim is None

    # Clear the durable failure cooldown using the store's explicit timestamp,
    # then exercise Pipeline cancellation on a fresh workspace.
    cancelled_workspace = tmp_path / "cancelled"
    cancelled_workspace.mkdir()
    cancelled_seed = CuratorScheduleStore(cancelled_workspace)
    for index in range(2):
        cancelled_seed.record_completion(_completion(f"cancel-seed-{index}"))
    cancelled_pipeline = MemoryPipeline()
    cancelled_pipeline.initialize(workspace_path=str(cancelled_workspace))
    cancelled_pipeline._curator._run_interval = 3

    def cancel(**_kwargs: object) -> CuratorReport:
        raise TurnCancellationRequested("cancelled")

    cancelled_pipeline._curator.run_cycle = cancel
    with pytest.raises(TurnCancellationRequested):
        cancelled_pipeline.maintain(
            completion_id=_completion("cancelled-run")
        )
    cancelled_state = CuratorScheduleStore(cancelled_workspace).snapshot()
    assert cancelled_state.pending_task_count == 3
    assert cancelled_state.active_claim is None
    assert cancelled_state.consecutive_failures == 0
    assert cancelled_state.retry_not_before_ms > 0


def test_real_requested_cancellation_and_expired_deadline_preserve_debt(
    tmp_path: Path,
) -> None:
    cancelled_workspace = tmp_path / "cancelled-real"
    cancelled_workspace.mkdir()
    cancelled_seed = CuratorScheduleStore(cancelled_workspace)
    for index in range(9):
        cancelled_seed.record_completion(
            _completion(f"cancelled-real-seed-{index}")
        )
    cancelled_pipeline = MemoryPipeline()
    cancelled_pipeline.initialize(workspace_path=str(cancelled_workspace))
    token = TurnCancellationToken(_turn_id("cancelled-real"))
    token.request()

    with pytest.raises(TurnCancellationRequested):
        cancelled_pipeline.maintain(
            completion_id=_completion("cancelled-real-run"),
            cancellation_token=token,
        )
    cancelled_state = CuratorScheduleStore(cancelled_workspace).snapshot()
    assert cancelled_state.pending_task_count == 10
    assert cancelled_state.completed_cycle_count == 0
    assert cancelled_state.active_claim is None

    deadline_workspace = tmp_path / "deadline-real"
    deadline_workspace.mkdir()
    deadline_seed = CuratorScheduleStore(deadline_workspace)
    for index in range(9):
        deadline_seed.record_completion(
            _completion(f"deadline-real-seed-{index}")
        )
    deadline_pipeline = MemoryPipeline()
    deadline_pipeline.initialize(workspace_path=str(deadline_workspace))

    assert deadline_pipeline.maintain(
        completion_id=_completion("deadline-real-run"),
        deadline_monotonic=0.0,
    ) is None
    deadline_state = CuratorScheduleStore(deadline_workspace).snapshot()
    assert deadline_state.pending_task_count == 10
    assert deadline_state.completed_cycle_count == 0
    assert deadline_state.active_claim is None
    assert deadline_state.consecutive_failures == 1


def test_curator_cancellation_during_large_scan_preserves_schedule_debt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        memory_module,
        "MINI_CODE_DIR",
        tmp_path / "home" / ".mini-code",
    )
    workspace = tmp_path / "cancelled-mid-scan"
    workspace.mkdir()
    seed = CuratorScheduleStore(workspace)
    for index in range(9):
        seed.record_completion(
            _completion(f"cancelled-mid-scan-seed-{index}")
        )

    manager = MemoryManager(project_root=workspace)
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(workspace))
    memory_file = manager.memories[MemoryScope.PROJECT]
    memory_file.entries = [
        MemoryEntry(
            id=f"mid-scan-{index:04d}",
            scope=MemoryScope.PROJECT,
            category="testing",
            content=f"distinct curator cancellation probe {index}",
            domains=["testing"],
        )
        for index in range(300)
    ]
    memory_file._rebuild_indices()
    token = _CancelAfterCheckpoints(
        stop_after=6,
        turn_id=_turn_id("cancelled-mid-scan"),
    )

    with pytest.raises(TurnCancellationRequested):
        pipeline.maintain(
            completion_id=_completion("cancelled-mid-scan-run"),
            cancellation_token=token,
        )

    assert token.checkpoints == 6
    state = CuratorScheduleStore(workspace).snapshot()
    assert state.pending_task_count == 10
    assert state.completed_cycle_count == 0
    assert state.active_claim is None
    assert state.consecutive_failures == 0
    assert state.retry_not_before_ms > 0


def test_finalize_write_failure_leaves_live_lease_as_duplicate_fence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = CuratorScheduleStore(workspace)
    decision = None
    for index in range(3):
        decision = _record_and_claim(
            store,
            _completion(f"run-{index}"),
            interval=3,
        )
    assert decision is not None and decision.claim is not None

    def fail_write(_state: object) -> None:
        raise OSError("synthetic finalize fault")

    monkeypatch.setattr(store, "_write_locked", fail_write)
    with pytest.raises(OSError):
        _finish_claim(
            store,
            decision.claim.claim_id,
            outcome="completed",
        )

    next_store = CuratorScheduleStore(workspace)
    next_store.record_completion(
        _completion("next-run"),
    )
    next_decision = _claim_due(next_store, interval=3)
    assert next_decision.claim is None
    assert next_decision.reason == "claim_active"
    assert next_decision.state.pending_task_count == 4


def test_persisted_schema_is_content_free_and_bounded(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_text = "secret task prompt that must never be persisted"
    receipt = _completion("run-id", "turn-id")
    store = CuratorScheduleStore(workspace)
    store.record_completion(receipt)

    raw = store.path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert task_text not in raw
    assert "run-id" not in raw
    assert "turn-id" not in raw
    assert set(payload) == {
        "activeClaim",
        "completedCycleCount",
        "consecutiveFailures",
        "generation",
        "lastAttemptAtMs",
        "lastCompletedAtMs",
        "pendingTaskCount",
        "pinnedCompletionIds",
        "recentCompletionIds",
        "retryNotBeforeMs",
        "schemaVersion",
    }
    assert store.path.stat().st_size < 64 * 1024


def test_cross_producer_pressure_never_evicts_a_pinned_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        curator_schedule_module,
        "_MAX_RECENT_COMPLETIONS",
        3,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    producer = CuratorScheduleStore(workspace)
    pinned = _completion("pinned-producer")
    with producer.delivery_fence():
        producer.record_completion(pinned, pin=True)

    other_producer = CuratorScheduleStore(workspace)
    traffic = [_completion(f"other-producer-{index}") for index in range(4)]
    for completion_id in traffic:
        other_producer.record_completion(completion_id)

    state = producer.snapshot()
    assert state.pending_task_count == 5
    assert state.recent_completion_ids == (pinned, *traffic[-2:])
    assert state.pinned_completion_ids == (pinned,)


def test_all_pinned_ring_rejects_a_new_completion_without_partial_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        curator_schedule_module,
        "_MAX_RECENT_COMPLETIONS",
        3,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    producer = CuratorScheduleStore(workspace)
    pinned = [_completion(f"all-pinned-{index}") for index in range(3)]
    with producer.delivery_fence():
        for completion_id in pinned:
            producer.record_completion(completion_id, pin=True)

    before = producer.path.read_bytes()
    before_state = producer.snapshot()
    with pytest.raises(
        CuratorScheduleError,
        match="deduplication capacity is pinned",
    ):
        CuratorScheduleStore(workspace).record_completion(
            _completion("must-not-be-counted")
        )

    assert producer.path.read_bytes() == before
    assert producer.snapshot() == before_state
    assert before_state.pending_task_count == 3
    assert before_state.recent_completion_ids == tuple(pinned)
    assert before_state.pinned_completion_ids == tuple(pinned)


def test_legacy_unpinned_receipt_can_be_promoted_without_recounting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        curator_schedule_module,
        "_MAX_RECENT_COMPLETIONS",
        3,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    legacy_receipt = _completion("legacy-unpinned")
    store = CuratorScheduleStore(workspace)
    store.record_completion(legacy_receipt)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["schemaVersion"] = 2
    payload.pop("pinnedCompletionIds")
    store.path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    restarted = CuratorScheduleStore(workspace)
    with restarted.delivery_fence():
        promoted = restarted.record_completion(
            legacy_receipt,
            pin=True,
            promote_existing=True,
        )
        replayed = restarted.record_completion(
            legacy_receipt,
            pin=True,
            promote_existing=True,
        )

    assert promoted.pending_task_count == 1
    assert promoted.recent_completion_ids == (legacy_receipt,)
    assert promoted.pinned_completion_ids == (legacy_receipt,)
    assert replayed == promoted
    upgraded = json.loads(store.path.read_text(encoding="utf-8"))
    assert upgraded["schemaVersion"] == 3
    assert upgraded["pendingTaskCount"] == 1
    assert upgraded["pinnedCompletionIds"] == [legacy_receipt]


def test_release_reconciliation_settles_an_evicted_receipt_without_recounting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        curator_schedule_module,
        "_MAX_RECENT_COMPLETIONS",
        3,
    )
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    workspace.mkdir()
    data_dir.mkdir()
    journal = RunJournal(workspace, data_dir=data_dir)
    old = journal.create_run(title="release gap", source="headless")
    old_completion = curator_completion_id(old.id)
    assert old_completion is not None
    journal.transition(old.id, "running")
    assert journal.prepare_curator_completion(old.id, old_completion)
    journal.transition(old.id, "completed")
    assert journal.begin_curator_completion_delivery(old.id, old_completion)

    schedule = CuratorScheduleStore(workspace)
    with schedule.delivery_fence():
        schedule.record_completion(old_completion, pin=True)
        assert journal.acknowledge_curator_completion(
            old.id,
            old_completion,
        )
        schedule.acknowledge_completion(old_completion)
    assert journal.scan_pending_curator_completions().release_items == (
        (old.id, old_completion),
    )

    fillers = [_completion(f"release-gap-{index}") for index in range(3)]
    for completion_id in fillers:
        CuratorScheduleStore(workspace).record_completion(completion_id)
    before_reconcile = schedule.snapshot()
    assert before_reconcile.pending_task_count == 4
    assert old_completion not in before_reconcile.recent_completion_ids
    assert before_reconcile.pinned_completion_ids == ()

    with observe_run(
        workspace=workspace,
        source="headless",
        title="release reconciler",
        journal_factory=lambda _resolved: journal,
    ) as observation, schedule.delivery_fence():
        assert observation.replay_pending_curator_completions(
            lambda completion_id: schedule.record_completion(
                completion_id,
                pin=True,
                promote_existing=True,
            ),
            release=schedule.acknowledge_completion,
        ) is True

    after_reconcile = schedule.snapshot()
    assert after_reconcile.pending_task_count == 4
    assert after_reconcile.recent_completion_ids == (
        fillers[0],
        fillers[1],
        fillers[2],
    )
    assert journal.scan_pending_curator_completions().release_items == ()
    receipt = json.loads(
        (
            data_dir
            / "dashboard"
            / "workspaces"
            / journal.workspace_id
            / "runs"
            / old.id
            / "curator_receipt.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["state"] == "settled"
