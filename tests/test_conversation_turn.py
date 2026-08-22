from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import multiprocessing
import threading

import pytest

from minicode.conversation import (
    ConversationRuntimeUnavailable,
    ConversationSessionBusy,
    ConversationSessionConflict,
    ConversationSessionNotFound,
    ConversationTurnFailed,
    ConversationTurnService,
)
from minicode.run_journal import RunJournal
from minicode.session import load_session


def _configure_storage(data_dir: str) -> None:
    from minicode import session as session_module

    root = Path(data_dir)
    session_module.MINI_CODE_DIR = root
    session_module.SESSIONS_DIR = root / "sessions"


def _advance_session(data_dir: str, session_id: str) -> None:
    _configure_storage(data_dir)
    from minicode.session import load_session, save_session

    session = load_session(session_id)
    assert session is not None
    session.messages.extend(
        [
            {"role": "user", "content": "winning request"},
            {"role": "assistant", "content": "winning reply"},
        ]
    )
    session.history.append("winning request")
    save_session(session)


def _hold_store_lock(data_dir: str, ready, release) -> None:
    from minicode.session_store import session_store_transaction

    with session_store_transaction(data_dir):
        ready.set()
        assert release.wait(10)


class FakePermissions:
    def __init__(self) -> None:
        self.begin_calls = 0
        self.end_calls = 0

    def begin_turn(self) -> None:
        self.begin_calls += 1

    def end_turn(self) -> None:
        self.end_calls += 1

    def get_summary(self) -> list[str]:
        return ["safe permission summary"]


class FakeTools:
    def __init__(self) -> None:
        self.dispose_calls = 0

    def get_skills(self) -> list[dict[str, object]]:
        return [{"name": "safe-skill"}]

    def get_mcp_servers(self) -> list[dict[str, object]]:
        return [{"name": "safe-mcp"}]

    def dispose(self) -> None:
        self.dispose_calls += 1


class FakeRuntime:
    def __init__(self, replies: list[str | BaseException | None]) -> None:
        self.system_prompt = "safe current system prompt"
        self.permissions = FakePermissions()
        self.tools = FakeTools()
        self.skill_routing = None
        self._replies = replies
        self.received: list[list[dict[str, object]]] = []

    def execute(self, messages, observation):
        snapshot = [dict(message) for message in messages]
        self.received.append(snapshot)
        reply = self._replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if reply is None:
            return list(messages)
        return [*messages, {"role": "assistant", "content": reply}]

    def dispose(self) -> None:
        self.tools.dispose()


class FakeRuntimeFactory:
    def __init__(self, replies: list[str | BaseException | None]) -> None:
        self.replies = replies
        self.instances: list[FakeRuntime] = []

    def __call__(self, *, workspace: Path, prompt: str, **_kwargs) -> FakeRuntime:
        assert workspace.is_absolute()
        assert prompt
        runtime = FakeRuntime(self.replies)
        self.instances.append(runtime)
        return runtime


@pytest.fixture
def isolated_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr("minicode.session.MINI_CODE_DIR", data_dir)
    monkeypatch.setattr("minicode.session.SESSIONS_DIR", data_dir / "sessions")
    return workspace, data_dir


def test_new_and_continued_turn_commit_one_session_and_one_linked_run_each(
    isolated_sessions,
) -> None:
    workspace, data_dir = isolated_sessions
    factory = FakeRuntimeFactory(["first real reply", "second real reply"])
    service = ConversationTurnService(
        workspace,
        runtime_factory=factory,
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )

    first = service.turn(message="first request", session_id=None)
    second = service.turn(message="second request", session_id=first.session_id)

    assert first.created is True
    assert second.created is False
    assert second.session_id == first.session_id
    assert first.assistant == "first real reply"
    assert second.assistant == "second real reply"
    assert first.run_id is not None and second.run_id is not None
    assert first.run_id != second.run_id

    reloaded = load_session(first.session_id)
    assert reloaded is not None
    visible = [
        (message["role"], message["content"])
        for message in reloaded.messages
        if message.get("role") in {"user", "assistant"}
    ]
    assert visible == [
        ("user", "first request"),
        ("assistant", "first real reply"),
        ("user", "second request"),
        ("assistant", "second real reply"),
    ]
    assert reloaded.history == ["first request", "second request"]
    assert reloaded.metadata.message_count == len(reloaded.messages)
    assert len(factory.instances) == 2
    assert [message["content"] for message in factory.instances[1].received[0]] == [
        "safe current system prompt",
        "first request",
        "first real reply",
        "second request",
    ]
    assert all(runtime.permissions.begin_calls == 1 for runtime in factory.instances)
    assert all(runtime.permissions.end_calls == 1 for runtime in factory.instances)
    assert all(runtime.tools.dispose_calls == 1 for runtime in factory.instances)

    runs = RunJournal(workspace, data_dir=data_dir).list_runs(limit=100).items
    assert len(runs) == 2
    assert {run.source for run in runs} == {"gateway"}
    assert {run.session_id for run in runs} == {first.session_id}
    assert {run.status for run in runs} == {"completed"}
    for run in runs:
        events = RunJournal(workspace, data_dir=data_dir).list_events(run.id).items
        assert [event.type for event in events] == [
            "run.queued",
            "run.started",
            "assistant.completed",
            "run.completed",
        ]


def test_existing_legacy_messages_get_a_safe_system_prompt_without_assuming_index_zero(
    isolated_sessions,
) -> None:
    workspace, data_dir = isolated_sessions
    factory = FakeRuntimeFactory(["seed reply", "continued"])
    service = ConversationTurnService(
        workspace,
        runtime_factory=factory,
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )
    first = service.turn(message="seed", session_id=None)
    session = load_session(first.session_id)
    assert session is not None
    session.messages = [
        {"role": "user", "content": "legacy user first"},
        {"role": "system", "content": "stale later system"},
        {"role": "assistant", "content": "legacy answer"},
    ]
    from minicode.session import save_session

    save_session(session, force_full=True)

    service.turn(message="continue legacy", session_id=session.session_id)

    received = factory.instances[-1].received[0]
    assert received[0] == {"role": "user", "content": "legacy user first"}
    assert received[1] == {
        "role": "system",
        "content": "safe current system prompt",
    }
    assert {
        key: received[-1][key]
        for key in ("role", "content")
    } == {"role": "user", "content": "continue legacy"}


def test_agent_failure_commits_truthful_user_only_state_and_marks_run_failed(
    isolated_sessions,
) -> None:
    workspace, data_dir = isolated_sessions
    factory = FakeRuntimeFactory([RuntimeError("Bearer provider-secret")])
    service = ConversationTurnService(
        workspace,
        runtime_factory=factory,
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )

    with pytest.raises(ConversationTurnFailed) as raised:
        service.turn(message="durable user request", session_id=None)

    assert raised.value.code == "turn_failed"
    sessions = list((data_dir / "sessions").glob("*.json"))
    assert len(sessions) == 1
    session = load_session(sessions[0].stem)
    assert session is not None
    assert [message["role"] for message in session.messages] == ["system", "user"]
    assert session.messages[-1]["content"] == "durable user request"
    assert not any(message.get("role") == "assistant" for message in session.messages)
    runtime = factory.instances[0]
    assert runtime.permissions.end_calls == 1
    assert runtime.tools.dispose_calls == 1
    run = RunJournal(workspace, data_dir=data_dir).list_runs().items[0]
    assert run.status == "failed"
    assert "provider-secret" not in str(
        [event.to_dict() for event in RunJournal(workspace, data_dir=data_dir).list_events(run.id).items]
    )


def test_normal_return_without_new_assistant_is_a_truthful_failure(
    isolated_sessions,
) -> None:
    workspace, data_dir = isolated_sessions
    factory = FakeRuntimeFactory([None])
    service = ConversationTurnService(
        workspace,
        runtime_factory=factory,
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )

    with pytest.raises(ConversationTurnFailed):
        service.turn(message="no fake response", session_id=None)

    session_path = next((data_dir / "sessions").glob("*.json"))
    session = load_session(session_path.stem)
    assert session is not None
    assert [message["role"] for message in session.messages] == ["system", "user"]
    assert "(no response)" not in str(session.messages)
    run = RunJournal(workspace, data_dir=data_dir).list_runs().items[0]
    assert run.status == "failed"


def test_journal_failure_keeps_turn_and_commit_successful_with_null_run_id(
    isolated_sessions,
) -> None:
    workspace, _data_dir = isolated_sessions
    factory = FakeRuntimeFactory(["still committed"])
    service = ConversationTurnService(
        workspace,
        runtime_factory=factory,
        journal_factory=lambda _resolved: (_ for _ in ()).throw(
            OSError("password=journal-secret")
        ),
    )

    result = service.turn(message="journal independent", session_id=None)

    assert result.assistant == "still committed"
    assert result.run_id is None


@pytest.mark.parametrize(
    ("storage_error", "public_error"),
    [
        (
            __import__("minicode.session", fromlist=["SessionWriteConflictError"]).SessionWriteConflictError(
                "secret conflict"
            ),
            ConversationSessionConflict,
        ),
        (
            __import__("minicode.session", fromlist=["SessionStoreBusyError"]).SessionStoreBusyError(
                "secret busy"
            ),
            ConversationSessionBusy,
        ),
    ],
)
def test_commit_conflict_and_busy_are_fixed_and_never_rerun_agent(
    isolated_sessions,
    storage_error: Exception,
    public_error: type[Exception],
) -> None:
    workspace, _data_dir = isolated_sessions
    factory = FakeRuntimeFactory(["computed once"])

    def fail_save(_session) -> None:
        raise storage_error

    service = ConversationTurnService(
        workspace,
        runtime_factory=factory,
        session_saver=fail_save,
        observation_enabled=False,
    )

    with pytest.raises(public_error):
        service.turn(message="single execution", session_id=None)

    assert len(factory.instances) == 1
    assert len(factory.instances[0].received) == 1
    assert factory.instances[0].tools.dispose_calls == 1


def test_missing_and_foreign_sessions_are_indistinguishable_before_runtime(
    isolated_sessions,
) -> None:
    workspace, _data_dir = isolated_sessions
    foreign = SimpleNamespace(workspace=str(workspace.parent / "foreign"))
    factory = FakeRuntimeFactory(["must not run"])

    for loaded in (None, foreign):
        service = ConversationTurnService(
            workspace,
            runtime_factory=factory,
            session_loader=lambda _session_id, value=loaded: value,
            observation_enabled=False,
        )
        with pytest.raises(ConversationSessionNotFound) as raised:
            service.turn(message="isolated", session_id="session_01")
        assert raised.value.code == "session_not_found"

    assert factory.instances == []


def test_real_cross_process_stale_conflict_preserves_winner_without_rerun(
    isolated_sessions,
) -> None:
    workspace, data_dir = isolated_sessions
    seed_factory = FakeRuntimeFactory(["seed reply"])
    seed_service = ConversationTurnService(
        workspace,
        runtime_factory=seed_factory,
        observation_enabled=False,
    )
    seeded = seed_service.turn(message="seed request", session_id=None)

    agent_ready = threading.Event()
    agent_release = threading.Event()

    class BarrierRuntime(FakeRuntime):
        def execute(self, messages, observation):
            self.received.append([dict(message) for message in messages])
            agent_ready.set()
            assert agent_release.wait(10)
            return [*messages, {"role": "assistant", "content": "stale reply"}]

    runtime = BarrierRuntime([])
    service = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: runtime,
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )
    outcome: list[BaseException | None] = []

    def run_stale_turn() -> None:
        try:
            service.turn(message="stale request", session_id=seeded.session_id)
        except BaseException as error:
            outcome.append(error)
        else:
            outcome.append(None)

    worker = threading.Thread(target=run_stale_turn)
    worker.start()
    assert agent_ready.wait(5)
    process = multiprocessing.get_context("spawn").Process(
        target=_advance_session,
        args=(str(data_dir), seeded.session_id),
    )
    process.start()
    process.join(10)
    assert process.exitcode == 0
    agent_release.set()
    worker.join(10)
    assert not worker.is_alive()

    assert len(outcome) == 1
    assert isinstance(outcome[0], ConversationSessionConflict)
    assert len(runtime.received) == 1
    reloaded = load_session(seeded.session_id)
    assert reloaded is not None
    visible = [
        message["content"]
        for message in reloaded.messages
        if message.get("role") in {"user", "assistant"}
    ]
    assert visible == [
        "seed request",
        "seed reply",
        "winning request",
        "winning reply",
    ]
    run = RunJournal(workspace, data_dir=data_dir).list_runs().items[0]
    assert run.status == "failed"


def test_real_process_lock_busy_keeps_session_bytes_unchanged(
    isolated_sessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, data_dir = isolated_sessions
    seed = ConversationTurnService(
        workspace,
        runtime_factory=FakeRuntimeFactory(["seed reply"]),
        observation_enabled=False,
    ).turn(message="seed", session_id=None)
    tracked = [
        data_dir / "sessions" / f"{seed.session_id}.json",
        data_dir / "sessions_index.json",
    ]
    before = {path: path.read_bytes() for path in tracked}
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_store_lock,
        args=(str(data_dir), ready, release),
    )
    holder.start()
    assert ready.wait(5)
    monkeypatch.setattr(
        "minicode.session_store.SESSION_STORE_LOCK_TIMEOUT_SECONDS", 0.02
    )
    runtime = FakeRuntime(["computed but not committed"])
    service = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: runtime,
        observation_enabled=False,
    )
    try:
        with pytest.raises(ConversationSessionBusy):
            service.turn(message="busy request", session_id=seed.session_id)
    finally:
        release.set()
        holder.join(10)

    assert holder.exitcode == 0
    assert len(runtime.received) == 1
    assert {path: path.read_bytes() for path in tracked} == before


def test_runtime_initialization_and_unclassified_commit_failures_are_fixed(
    isolated_sessions,
) -> None:
    workspace, _data_dir = isolated_sessions

    def unavailable_runtime(**_kwargs):
        raise RuntimeError("api_key=runtime-secret /private/runtime")

    with pytest.raises(ConversationRuntimeUnavailable) as unavailable:
        ConversationTurnService(
            workspace,
            runtime_factory=unavailable_runtime,
            observation_enabled=False,
        ).turn(message="runtime", session_id=None)
    assert unavailable.value.code == "runtime_unavailable"

    runtime = FakeRuntime(["computed"])

    def broken_save(_session) -> None:
        raise OSError("Bearer commit-secret /private/session")

    with pytest.raises(ConversationTurnFailed) as failed:
        ConversationTurnService(
            workspace,
            runtime_factory=lambda **_kwargs: runtime,
            session_saver=broken_save,
            observation_enabled=False,
        ).turn(message="commit", session_id=None)
    assert failed.value.code == "turn_failed"
    assert runtime.tools.dispose_calls == 1
