from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

import minicode.working_memory as working_memory_module
import minicode.agent_loop as agent_loop_module
from minicode.agent_loop import run_agent_turn
from minicode.tooling import ToolRegistry
from minicode.types import AgentStep
from minicode.working_memory import (
    WorkingMemoryEntry,
    WorkingMemorySnapshot,
    WorkingMemoryTracker,
    get_working_memory,
)
from minicode.run_events import project_working_memory_event


def test_snapshot_excludes_expired_entries_without_mutating_tracker() -> None:
    tracker = WorkingMemoryTracker(max_entries=4, max_tokens=1_000)
    active = WorkingMemoryEntry(
        content="active decision",
        entry_type="key_decision",
        created_at=10.0,
        expires_at=30.0,
    )
    expired = WorkingMemoryEntry(
        content="expired secret",
        entry_type="error_context",
        created_at=5.0,
        expires_at=15.0,
    )
    tracker._entries = [active, expired]
    original_entries = list(tracker._entries)

    assert tracker.snapshot(now=20.0) == WorkingMemorySnapshot(
        entries=1,
        max_entries=4,
        protected_tokens=active.token_count(),
        max_tokens=1_000,
    )
    assert tracker._entries == original_entries
    assert tracker._entries[0] is active
    assert tracker._entries[1] is expired


def test_working_memory_projector_exposes_only_process_local_counts() -> None:
    snapshot = WorkingMemorySnapshot(
        entries=3,
        max_entries=15,
        protected_tokens=240,
        max_tokens=4_000,
    )

    assert project_working_memory_event(snapshot) == {
        "workingMemoryVersion": 1,
        "action": "protected",
        "scope": "process",
        "entries": 3,
        "maxEntries": 15,
        "protectedTokens": 240,
        "maxTokens": 4_000,
    }


def test_snapshot_is_deterministic_and_never_cleans_or_reorders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = WorkingMemoryTracker(max_entries=3, max_tokens=500)
    first = WorkingMemoryEntry("first", "active_task", expires_at=None)
    second = WorkingMemoryEntry("second", "key_decision", expires_at=99.0)
    tracker._entries = [first, second]
    original = list(tracker._entries)
    monkeypatch.setattr(
        tracker,
        "clear_expired",
        lambda: pytest.fail("snapshot must not clear expired entries"),
    )

    first_snapshot = tracker.snapshot(now=50.0)
    second_snapshot = tracker.snapshot(now=50.0)

    assert first_snapshot == second_snapshot
    assert tracker._entries == original
    assert [id(entry) for entry in tracker._entries] == [
        id(entry) for entry in original
    ]
    with pytest.raises(FrozenInstanceError):
        first_snapshot.entries = 2  # type: ignore[misc]


def test_snapshot_token_estimation_failure_does_not_mutate_tracker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = WorkingMemoryTracker()
    entry = WorkingMemoryEntry("secret body", "active_task")
    tracker._entries = [entry]
    monkeypatch.setattr(
        working_memory_module,
        "estimate_tokens",
        lambda _content: (_ for _ in ()).throw(RuntimeError("estimate failed")),
    )

    with pytest.raises(RuntimeError, match="estimate failed"):
        tracker.snapshot(now=1.0)

    assert tracker._entries == [entry]


@pytest.mark.parametrize(
    "snapshot",
    [
        WorkingMemorySnapshot(True, 15, 1, 4_000),
        WorkingMemorySnapshot(-1, 15, 1, 4_000),
        WorkingMemorySnapshot(16, 15, 1, 4_000),
        WorkingMemorySnapshot(1, 15, True, 4_000),
        WorkingMemorySnapshot(1, 15, -1, 4_000),
        WorkingMemorySnapshot(1, 15, 4_001, 4_000),
    ],
)
def test_working_memory_projector_rejects_invalid_counts(
    snapshot: WorkingMemorySnapshot,
) -> None:
    with pytest.raises(ValueError):
        project_working_memory_event(snapshot)


def test_working_memory_singleton_is_shared_only_within_this_process() -> None:
    assert get_working_memory() is get_working_memory()


def test_agent_emits_snapshot_only_after_real_protection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    events: list[tuple[str, object]] = []

    class Model:
        def next(self, _messages, on_stream_chunk=None):
            return AgentStep(type="assistant", content="final secret body")

    class Sink:
        def emit(self, event_type, *, step=None, payload=None):
            events.append((event_type, payload))

    class Tracker:
        def snapshot(self):
            calls.append("snapshot")
            return WorkingMemorySnapshot(1, 15, 4, 4_000)

    def protect_context(**_kwargs):
        calls.append("protect")
        return SimpleNamespace()

    monkeypatch.setattr(agent_loop_module, "protect_context", protect_context)
    monkeypatch.setattr(agent_loop_module, "get_working_memory", lambda: Tracker())

    result = run_agent_turn(
        model=Model(),
        tools=ToolRegistry([]),
        messages=[{"role": "user", "content": "go"}],
        cwd=".",
        enable_work_chain=False,
        event_sink=Sink(),
    )

    assert result[-1] == {"role": "assistant", "content": "final secret body"}
    assert calls == ["protect", "snapshot"]
    assert [event_type for event_type, _payload in events] == [
        "model.started",
        "model.completed",
        "model.costed",
        "working_memory.observed",
        "task.outcome",
    ]
    assert events[-2][1] == {
        "workingMemoryVersion": 1,
        "action": "protected",
        "scope": "process",
        "entries": 1,
        "maxEntries": 15,
        "protectedTokens": 4,
        "maxTokens": 4_000,
    }
    assert "final secret body" not in str(events)


def test_agent_without_sink_does_no_working_memory_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Model:
        def next(self, _messages, on_stream_chunk=None):
            return AgentStep(type="assistant", content="done")

    monkeypatch.setattr(
        agent_loop_module,
        "get_working_memory",
        lambda: pytest.fail("no sink must not obtain or snapshot the tracker"),
    )

    result = run_agent_turn(
        model=Model(),
        tools=ToolRegistry([]),
        messages=[{"role": "user", "content": "go"}],
        cwd=".",
        enable_work_chain=False,
        event_sink=None,
    )

    assert result[-1] == {"role": "assistant", "content": "done"}


def test_snapshot_and_sink_failures_do_not_change_assistant_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Model:
        def next(self, _messages, on_stream_chunk=None):
            return AgentStep(type="assistant", content="same")

    class SnapshotFailure:
        def snapshot(self):
            raise RuntimeError("password=snapshot-secret")

    class SinkFailure:
        def emit(self, *_args, **_kwargs):
            raise RuntimeError("password=sink-secret")

    monkeypatch.setattr(
        agent_loop_module, "get_working_memory", lambda: SnapshotFailure()
    )
    first = run_agent_turn(
        model=Model(),
        tools=ToolRegistry([]),
        messages=[{"role": "user", "content": "go"}],
        cwd=".",
        enable_work_chain=False,
        event_sink=SinkFailure(),
    )

    monkeypatch.setattr(
        agent_loop_module,
        "get_working_memory",
        lambda: SimpleNamespace(
            snapshot=lambda: WorkingMemorySnapshot(1, 15, 1, 4_000)
        ),
    )
    second = run_agent_turn(
        model=Model(),
        tools=ToolRegistry([]),
        messages=[{"role": "user", "content": "go"}],
        cwd=".",
        enable_work_chain=False,
        event_sink=SinkFailure(),
    )

    assert first[-1] == second[-1] == {"role": "assistant", "content": "same"}


def test_protect_context_failure_preserves_same_exception_and_emits_no_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("protect failed")
    events: list[str] = []

    class Model:
        def next(self, _messages, on_stream_chunk=None):
            return AgentStep(type="assistant", content="done")

    class Sink:
        def emit(self, event_type, *, step=None, payload=None):
            events.append(event_type)

    def fail_protection(**_kwargs):
        raise failure

    monkeypatch.setattr(agent_loop_module, "protect_context", fail_protection)
    monkeypatch.setattr(
        agent_loop_module,
        "get_working_memory",
        lambda: pytest.fail("failed protection must not be snapshotted"),
    )

    with pytest.raises(RuntimeError) as raised:
        run_agent_turn(
            model=Model(),
            tools=ToolRegistry([]),
            messages=[{"role": "user", "content": "go"}],
            cwd=".",
            enable_work_chain=False,
            event_sink=Sink(),
        )

    assert raised.value is failure
    assert "working_memory.observed" not in events


def test_two_runs_in_one_process_observe_the_same_tracker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = WorkingMemoryTracker(max_entries=15, max_tokens=4_000)

    class Model:
        def next(self, _messages, on_stream_chunk=None):
            return AgentStep(type="assistant", content="decision")

    class Sink:
        def __init__(self) -> None:
            self.payloads: list[object] = []

        def emit(self, event_type, *, step=None, payload=None):
            if event_type == "working_memory.observed":
                self.payloads.append(payload)

    monkeypatch.setattr(
        agent_loop_module,
        "protect_context",
        lambda **kwargs: tracker.add(
            kwargs["content"], kwargs["entry_type"], kwargs["ttl_seconds"]
        ),
    )
    monkeypatch.setattr(agent_loop_module, "get_working_memory", lambda: tracker)
    sinks = [Sink(), Sink()]

    for sink in sinks:
        run_agent_turn(
            model=Model(),
            tools=ToolRegistry([]),
            messages=[{"role": "user", "content": "go"}],
            cwd=".",
            enable_work_chain=False,
            event_sink=sink,
        )

    assert [sink.payloads[0]["entries"] for sink in sinks] == [1, 2]
    assert all(sink.payloads[0]["scope"] == "process" for sink in sinks)
