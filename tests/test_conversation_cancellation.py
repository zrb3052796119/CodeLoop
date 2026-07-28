from __future__ import annotations

import threading
from pathlib import Path

import pytest

from minicode.conversation import (
    ConversationFeedbackConflict,
    ConversationRuntimeUnavailable,
    ConversationTurnCancelled,
    ConversationTurnService,
)
from minicode.conversation_turn_store import ConversationTurnStore
from minicode.run_journal import RunJournal
from minicode.session import create_new_session, load_session


TURN_ID = "turn_" + "a" * 32


class Permissions:
    def begin_turn(self) -> None:
        pass

    def end_turn(self) -> None:
        pass

    def get_summary(self) -> list[str]:
        return []


class Tools:
    def get_skills(self) -> list[object]:
        return []

    def get_mcp_servers(self) -> list[object]:
        return []

    def dispose(self) -> None:
        pass


class Runtime:
    system_prompt = "safe system"
    skill_routing = None

    def __init__(
        self,
        *,
        started: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.permissions = Permissions()
        self.tools = Tools()
        self.started = started
        self.release = release
        self.execute_calls = 0

    def execute(self, messages, _observation, *, cancellation_token=None):
        self.execute_calls += 1
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            assert self.release.wait(5)
        if cancellation_token is not None:
            cancellation_token.raise_if_requested()
        return [*messages, {"role": "assistant", "content": "safe reply"}]

    def dispose(self) -> None:
        pass


class BlockingFailingRuntime(Runtime):
    def execute(self, messages, _observation, *, cancellation_token=None):
        self.execute_calls += 1
        assert self.started is not None
        assert self.release is not None
        self.started.set()
        assert self.release.wait(5)
        raise RuntimeError("Bearer model-tool-fixture-secret /private/execution")


class RuntimeFactory:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.calls = 0

    def __call__(self, **_kwargs):
        self.calls += 1
        return self.runtime


class BlockingFailingRuntimeFactory:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, **_kwargs):
        self.calls += 1
        self.started.set()
        assert self.release.wait(5)
        raise RuntimeError("Bearer runtime-fixture-secret /private/runtime")


class CommitGateStore(ConversationTurnStore):
    def __init__(self, *args, gate_before: bool, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.gate_before = gate_before
        self.reached = threading.Event()
        self.proceed = threading.Event()

    def begin_commit(self, turn_id):
        if self.gate_before:
            self.reached.set()
            assert self.proceed.wait(5)
            return super().begin_commit(turn_id)
        decision = super().begin_commit(turn_id)
        self.reached.set()
        assert self.proceed.wait(5)
        return decision


class StartGateStore(ConversationTurnStore):
    """Deterministically pause after claim but before the running transition."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.reached = threading.Event()
        self.proceed = threading.Event()

    def mark_running(self, turn_id):
        self.reached.set()
        assert self.proceed.wait(5)
        return super().mark_running(turn_id)


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr("minicode.session.MINI_CODE_DIR", data_dir)
    monkeypatch.setattr("minicode.session.SESSIONS_DIR", data_dir / "sessions")
    return workspace, data_dir


def _thread_turn(
    service: ConversationTurnService,
    *,
    message: str = "safe",
    session_id: str | None = None,
    turn_id: str = TURN_ID,
):
    outcome: dict[str, object] = {}

    def target() -> None:
        try:
            outcome["result"] = service.turn(
                message=message,
                session_id=session_id,
                turn_id=turn_id,
            )
        except BaseException as error:
            outcome["error"] = error

    worker = threading.Thread(target=target)
    worker.start()
    return worker, outcome


def _service(workspace, data_dir, factory, *, store=None, creator=create_new_session):
    return ConversationTurnService(
        workspace,
        runtime_factory=factory,
        session_creator=creator,
        turn_store=store,
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )


def test_completed_turn_records_one_explicit_run_user_signal(isolated) -> None:
    workspace, data_dir = isolated
    service = _service(workspace, data_dir, RuntimeFactory(Runtime()))
    turn = service.turn(
        message="safe",
        session_id=None,
        turn_id=TURN_ID,
    )

    first = service.record_feedback(TURN_ID, "correct")
    repeated = service.record_feedback(TURN_ID, "correct")

    assert first == repeated
    assert first.turn_id == TURN_ID
    assert first.run_id == turn.run_id
    assert first.signal == "correct"
    assert first.source == "explicit_user_action"
    assert (
        RunJournal(workspace, data_dir=data_dir)
        .get_user_signal(turn.run_id)
        .signal
        == "correct"
    )
    with pytest.raises(ConversationFeedbackConflict):
        service.record_feedback(TURN_ID, "accept")


class RenderingRuntime(Runtime):
    """A fake Runtime that also binds rendered Memory IDs to the Run, the
    way the real AgentTurnRuntime does via emit_memory_result_safely."""

    def __init__(self, entry_ids: list[str]) -> None:
        super().__init__()
        self.entry_ids = entry_ids

    def execute(self, messages, observation, *, cancellation_token=None):
        observation.record_rendered_memory_ids(self.entry_ids)
        return super().execute(messages, observation, cancellation_token=cancellation_token)


def test_accepted_feedback_applies_corroborated_memory_feedback_once(
    isolated,
) -> None:
    from minicode.memory import MemoryManager, MemoryScope

    workspace, data_dir = isolated
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Use pytest fixtures for auth tests",
        tags=["test"],
    )
    assert entry is not None

    service = _service(
        workspace, data_dir, RuntimeFactory(RenderingRuntime([entry.id]))
    )
    service.turn(message="safe", session_id=None, turn_id=TURN_ID)

    service.record_feedback(TURN_ID, "accept")
    service.record_feedback(TURN_ID, "accept")  # idempotent replay

    reloaded = MemoryManager(project_root=workspace)
    updated = reloaded.memories[MemoryScope.PROJECT]._id_index[entry.id]
    assert updated.corroborated_success_count == 1
    assert updated.corroborated_failure_count == 0


def test_rejected_feedback_corroborates_negatively(isolated) -> None:
    from minicode.memory import MemoryManager, MemoryScope

    workspace, data_dir = isolated
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Use pytest fixtures for auth tests",
        tags=["test"],
    )
    assert entry is not None

    service = _service(
        workspace, data_dir, RuntimeFactory(RenderingRuntime([entry.id]))
    )
    service.turn(message="safe", session_id=None, turn_id=TURN_ID)

    service.record_feedback(TURN_ID, "reject")

    reloaded = MemoryManager(project_root=workspace)
    updated = reloaded.memories[MemoryScope.PROJECT]._id_index[entry.id]
    assert updated.corroborated_success_count == 0
    assert updated.corroborated_failure_count == 1


def test_feedback_without_rendered_memory_ids_leaves_memory_untouched(
    isolated,
) -> None:
    from minicode.memory import MemoryManager, MemoryScope

    workspace, data_dir = isolated
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Use pytest fixtures for auth tests",
        tags=["test"],
    )
    assert entry is not None

    service = _service(workspace, data_dir, RuntimeFactory(Runtime()))
    service.turn(message="safe", session_id=None, turn_id=TURN_ID)

    service.record_feedback(TURN_ID, "accept")

    reloaded = MemoryManager(project_root=workspace)
    updated = reloaded.memories[MemoryScope.PROJECT]._id_index[entry.id]
    assert updated.corroborated_success_count == 0
    assert updated.corroborated_failure_count == 0


def test_accepted_cancel_wins_before_running_without_runtime_or_session_side_effects(
    isolated,
) -> None:
    workspace, data_dir = isolated
    store = StartGateStore(
        workspace,
        data_dir=data_dir,
        owner_id="1" * 32,
    )
    factory = RuntimeFactory(Runtime())
    creator_calls: list[str] = []

    def tracked_creator(path: str):
        creator_calls.append(path)
        return create_new_session(path)

    service = _service(
        workspace,
        data_dir,
        factory,
        store=store,
        creator=tracked_creator,
    )
    worker, outcome = _thread_turn(service)

    assert store.reached.wait(5)
    cancellation = service.cancel(TURN_ID)
    assert cancellation.cancellation_accepted is True
    assert cancellation.status == "cancel_requested"
    assert store.get(TURN_ID).status == "cancel_requested"
    store.proceed.set()
    worker.join(5)

    assert not worker.is_alive()
    assert factory.calls == 0
    assert creator_calls == []
    assert not (data_dir / "sessions").exists()
    assert (
        type(outcome.get("error")),
        store.get(TURN_ID).status,
    ) == (ConversationTurnCancelled, "cancelled")


def test_persisted_cancel_wins_over_simultaneous_runtime_construction_failure(
    isolated,
) -> None:
    workspace, data_dir = isolated
    factory = BlockingFailingRuntimeFactory()
    store = ConversationTurnStore(
        workspace,
        data_dir=data_dir,
        owner_id="1" * 32,
    )
    service = _service(workspace, data_dir, factory, store=store)
    worker, outcome = _thread_turn(service)

    assert factory.started.wait(5)
    cancellation = service.cancel(TURN_ID)
    assert cancellation.cancellation_accepted is True
    assert cancellation.status == "cancel_requested"
    factory.release.set()
    worker.join(5)

    assert not worker.is_alive()
    assert (
        type(outcome.get("error")),
        store.get(TURN_ID).status,
    ) == (ConversationTurnCancelled, "cancelled")
    assert factory.calls == 1
    assert not list((data_dir / "sessions").glob("*.json"))
    runs = RunJournal(workspace, data_dir=data_dir).list_runs(limit=10).items
    assert all(run.status != "completed" for run in runs)
    serialized_runs = str([run.to_dict() for run in runs])
    assert "runtime-fixture-secret" not in serialized_runs
    assert "/private/runtime" not in serialized_runs


def test_runtime_construction_failure_without_cancel_remains_failed(
    isolated,
) -> None:
    workspace, data_dir = isolated
    factory = BlockingFailingRuntimeFactory()
    store = ConversationTurnStore(
        workspace,
        data_dir=data_dir,
        owner_id="1" * 32,
    )
    service = _service(workspace, data_dir, factory, store=store)
    worker, outcome = _thread_turn(service)

    assert factory.started.wait(5)
    factory.release.set()
    worker.join(5)

    assert not worker.is_alive()
    assert isinstance(outcome.get("error"), ConversationRuntimeUnavailable)
    assert store.get(TURN_ID).status == "failed"
    assert store.get(TURN_ID).error_code == "runtime_unavailable"
    assert not list((data_dir / "sessions").glob("*.json"))


def test_persisted_cancel_wins_over_simultaneous_session_creation_failure(
    isolated,
) -> None:
    workspace, data_dir = isolated
    creator_started = threading.Event()
    creator_release = threading.Event()

    def failing_creator(_path: str):
        creator_started.set()
        assert creator_release.wait(5)
        raise OSError("Bearer session-fixture-secret /private/session")

    factory = RuntimeFactory(Runtime())
    store = ConversationTurnStore(
        workspace,
        data_dir=data_dir,
        owner_id="1" * 32,
    )
    service = _service(
        workspace,
        data_dir,
        factory,
        store=store,
        creator=failing_creator,
    )
    worker, outcome = _thread_turn(service)

    assert creator_started.wait(5)
    assert service.cancel(TURN_ID).cancellation_accepted is True
    creator_release.set()
    worker.join(5)

    assert not worker.is_alive()
    assert isinstance(outcome.get("error"), ConversationTurnCancelled)
    assert store.get(TURN_ID).status == "cancelled"
    assert factory.calls == 0
    assert not (data_dir / "sessions").exists()
    serialized = store.record_path(TURN_ID).read_text(encoding="utf-8")
    assert "session-fixture-secret" not in serialized
    assert "/private/session" not in serialized


def test_accepted_turn_cancelled_before_session_creation_never_builds_runtime(
    isolated,
) -> None:
    workspace, data_dir = isolated
    creator_started = threading.Event()
    creator_release = threading.Event()

    def blocking_creator(path: str):
        creator_started.set()
        assert creator_release.wait(5)
        return create_new_session(path)

    factory = RuntimeFactory(Runtime())
    service = _service(
        workspace,
        data_dir,
        factory,
        creator=blocking_creator,
    )
    worker, outcome = _thread_turn(service)

    assert creator_started.wait(5)
    cancellation = service.cancel(TURN_ID)
    assert cancellation.cancellation_accepted is True
    assert cancellation.status == "cancel_requested"
    creator_release.set()
    worker.join(5)

    assert not worker.is_alive()
    assert isinstance(outcome.get("error"), ConversationTurnCancelled)
    assert factory.calls == 0
    assert not (data_dir / "sessions").exists()


def test_agent_in_flight_cancel_stops_without_session_commit_and_interrupts_run(
    isolated,
) -> None:
    workspace, data_dir = isolated
    started = threading.Event()
    release = threading.Event()
    runtime = Runtime(started=started, release=release)
    service = _service(workspace, data_dir, RuntimeFactory(runtime))
    worker, outcome = _thread_turn(service)

    assert started.wait(5)
    cancellation = service.cancel(TURN_ID)
    assert cancellation.cancellation_accepted is True
    assert service.status(TURN_ID).status == "cancel_requested"
    release.set()
    worker.join(5)

    assert isinstance(outcome.get("error"), ConversationTurnCancelled)
    assert service.status(TURN_ID).status == "cancelled"
    assert not list((data_dir / "sessions").glob("*.json"))
    runs = RunJournal(workspace, data_dir=data_dir).list_runs(limit=10).items
    assert len(runs) == 1
    assert runs[0].status == "interrupted"
    events = RunJournal(workspace, data_dir=data_dir).list_events(runs[0].id).items
    assert events[-1].type == "run.interrupted"
    assert events[-1].payload == {"summary": "execution_cancelled"}


def test_persisted_cancel_wins_over_simultaneous_model_or_tool_failure(
    isolated,
) -> None:
    workspace, data_dir = isolated
    started = threading.Event()
    release = threading.Event()
    runtime = BlockingFailingRuntime(started=started, release=release)
    store = ConversationTurnStore(
        workspace,
        data_dir=data_dir,
        owner_id="1" * 32,
    )
    service = _service(
        workspace,
        data_dir,
        RuntimeFactory(runtime),
        store=store,
    )
    worker, outcome = _thread_turn(service)

    assert started.wait(5)
    assert service.cancel(TURN_ID).cancellation_accepted is True
    release.set()
    worker.join(5)

    assert not worker.is_alive()
    assert isinstance(outcome.get("error"), ConversationTurnCancelled)
    assert store.get(TURN_ID).status == "cancelled"
    assert not list((data_dir / "sessions").glob("*.json"))
    runs = RunJournal(workspace, data_dir=data_dir).list_runs(limit=10).items
    assert len(runs) == 1
    assert runs[0].status == "interrupted"
    events = RunJournal(workspace, data_dir=data_dir).list_events(runs[0].id).items
    assert events[-1].payload == {"summary": "execution_cancelled"}
    serialized = str([event.to_dict() for event in events])
    assert "model-tool-fixture-secret" not in serialized
    assert "/private/execution" not in serialized


def test_cancel_wins_commit_gate_and_keeps_new_session_invisible(isolated) -> None:
    workspace, data_dir = isolated
    store = CommitGateStore(
        workspace,
        data_dir=data_dir,
        owner_id="1" * 32,
        gate_before=True,
    )
    runtime = Runtime()
    service = _service(workspace, data_dir, RuntimeFactory(runtime), store=store)
    worker, outcome = _thread_turn(service)

    assert store.reached.wait(5)
    cancellation = service.cancel(TURN_ID)
    assert cancellation.cancellation_accepted is True
    store.proceed.set()
    worker.join(5)

    assert isinstance(outcome.get("error"), ConversationTurnCancelled)
    assert service.status(TURN_ID).status == "cancelled"
    assert not list((data_dir / "sessions").glob("*.json"))
    with pytest.raises(ConversationTurnCancelled):
        service.turn(message="safe", session_id=None, turn_id=TURN_ID)
    assert runtime.execute_calls == 1


def test_commit_wins_gate_and_late_cancel_cannot_overturn_completion(isolated) -> None:
    workspace, data_dir = isolated
    store = CommitGateStore(
        workspace,
        data_dir=data_dir,
        owner_id="1" * 32,
        gate_before=False,
    )
    service = _service(workspace, data_dir, RuntimeFactory(Runtime()), store=store)
    worker, outcome = _thread_turn(service)

    assert store.reached.wait(5)
    cancellation = service.cancel(TURN_ID)
    assert cancellation.cancellation_accepted is False
    assert cancellation.status == "committing"
    store.proceed.set()
    worker.join(5)

    assert "error" not in outcome
    result = outcome["result"]
    assert result.assistant == "safe reply"
    assert service.status(TURN_ID).status == "completed"
    assert service.cancel(TURN_ID).cancellation_accepted is False
    session = load_session(result.session_id)
    assert session is not None
    assert session.messages[-1] == {"role": "assistant", "content": "safe reply"}


def test_cancelled_continued_turn_keeps_session_bytes_messages_history_and_marker(
    isolated,
) -> None:
    workspace, data_dir = isolated
    seed = _service(workspace, data_dir, RuntimeFactory(Runtime())).turn(
        message="seed",
        session_id=None,
        turn_id="turn_" + "b" * 32,
    )
    session_path = data_dir / "sessions" / f"{seed.session_id}.json"
    before = session_path.read_bytes()
    before_session = load_session(seed.session_id)
    assert before_session is not None
    started = threading.Event()
    release = threading.Event()
    service = _service(
        workspace,
        data_dir,
        RuntimeFactory(Runtime(started=started, release=release)),
    )
    worker, outcome = _thread_turn(
        service,
        message="cancel continuation",
        session_id=seed.session_id,
    )

    assert started.wait(5)
    assert service.cancel(TURN_ID).cancellation_accepted is True
    release.set()
    worker.join(5)

    assert isinstance(outcome.get("error"), ConversationTurnCancelled)
    assert session_path.read_bytes() == before
    after_session = load_session(seed.session_id)
    assert after_session is not None
    assert after_session.messages == before_session.messages
    assert after_session.history == before_session.history
    assert after_session.turn_commits == before_session.turn_commits


def test_journal_failure_does_not_change_cancelled_turn_or_session_fact(
    isolated,
) -> None:
    workspace, data_dir = isolated
    started = threading.Event()
    release = threading.Event()
    runtime = Runtime(started=started, release=release)
    service = ConversationTurnService(
        workspace,
        runtime_factory=RuntimeFactory(runtime),
        journal_factory=lambda _resolved: (_ for _ in ()).throw(
            OSError("Bearer journal-secret")
        ),
    )
    worker, outcome = _thread_turn(service)

    assert started.wait(5)
    assert service.cancel(TURN_ID).cancellation_accepted is True
    release.set()
    worker.join(5)

    assert isinstance(outcome.get("error"), ConversationTurnCancelled)
    assert service.status(TURN_ID).status == "cancelled"
    assert not list((data_dir / "sessions").glob("*.json"))
