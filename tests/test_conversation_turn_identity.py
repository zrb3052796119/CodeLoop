from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from minicode.conversation import (
    ConversationTurnCancelled,
    ConversationTurnFailed,
    ConversationTurnIdConflict,
    ConversationTurnInProgress,
    ConversationTurnInterrupted,
    ConversationTurnNotFound,
    ConversationTurnService,
)
from minicode.conversation_turn_store import (
    ConversationTurnStore,
    request_fingerprint,
)
from minicode.session import load_session
from minicode.web.read_model import DashboardReadModel


TURN_ID = "turn_" + "a" * 32


class Permissions:
    def begin_turn(self) -> None:
        pass

    def end_turn(self) -> None:
        pass

    def get_summary(self) -> list[str]:
        return []


class Tools:
    def get_skills(self) -> list[dict[str, object]]:
        return []

    def get_mcp_servers(self) -> list[dict[str, object]]:
        return []

    def dispose(self) -> None:
        pass


class Runtime:
    system_prompt = "safe system"
    permissions = Permissions()
    tools = Tools()
    skill_routing = None

    def __init__(self, reply: str = "real reply") -> None:
        self.reply = reply
        self.calls = 0

    def execute(self, messages, _observation):
        self.calls += 1
        return [*messages, {"role": "assistant", "content": self.reply}]

    def dispose(self) -> None:
        pass


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr("minicode.session.MINI_CODE_DIR", data_dir)
    monkeypatch.setattr("minicode.session.SESSIONS_DIR", data_dir / "sessions")
    return workspace, data_dir


def _store(workspace: Path, data_dir: Path, owner: str) -> ConversationTurnStore:
    return ConversationTurnStore(workspace, data_dir=data_dir, owner_id=owner)


def test_completed_duplicate_returns_authoritative_session_result_without_agent(
    isolated,
) -> None:
    workspace, data_dir = isolated
    runtimes: list[Runtime] = []

    def factory(**_kwargs):
        runtime = Runtime()
        runtimes.append(runtime)
        return runtime

    service = ConversationTurnService(
        workspace,
        runtime_factory=factory,
        observation_enabled=False,
        turn_store=_store(workspace, data_dir, "1" * 32),
    )

    first = service.turn(message="one durable request", session_id=None, turn_id=TURN_ID)
    duplicate = service.turn(message="one durable request", session_id=None, turn_id=TURN_ID)

    assert duplicate == first
    assert first.turn_id == TURN_ID
    assert len(runtimes) == 1
    assert runtimes[0].calls == 1
    session = load_session(first.session_id)
    assert session is not None
    assert len(session.turn_commits) == 1
    marker = session.turn_commits[0]
    assert marker["turnId"] == TURN_ID
    assert session.messages[marker["userMessageIndex"]]["role"] == "user"
    assert session.messages[marker["assistantMessageIndex"]] == {
        "role": "assistant",
        "content": "real reply",
    }


def test_same_turn_id_with_different_message_or_session_conflicts_before_agent(
    isolated,
) -> None:
    workspace, data_dir = isolated
    runtimes: list[Runtime] = []
    service = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: runtimes.append(Runtime()) or runtimes[-1],
        observation_enabled=False,
        turn_store=_store(workspace, data_dir, "1" * 32),
    )
    result = service.turn(message="original", session_id=None, turn_id=TURN_ID)

    with pytest.raises(ConversationTurnIdConflict):
        service.turn(message="different", session_id=None, turn_id=TURN_ID)
    with pytest.raises(ConversationTurnIdConflict):
        service.turn(
            message="original",
            session_id=result.session_id,
            turn_id=TURN_ID,
        )
    assert len(runtimes) == 1


def test_two_concurrent_duplicate_requests_execute_one_agent(isolated) -> None:
    workspace, data_dir = isolated
    entered = threading.Event()
    release = threading.Event()
    runtime = Runtime()

    def execute(messages, _observation):
        runtime.calls += 1
        entered.set()
        assert release.wait(5)
        return [*messages, {"role": "assistant", "content": "once"}]

    runtime.execute = execute
    service = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: runtime,
        observation_enabled=False,
        turn_store=_store(workspace, data_dir, "1" * 32),
    )
    outcomes: list[object] = []

    def call() -> None:
        try:
            outcomes.append(
                service.turn(message="same", session_id=None, turn_id=TURN_ID)
            )
        except BaseException as error:
            outcomes.append(error)

    first = threading.Thread(target=call)
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=call)
    second.start()
    second.join(5)
    assert not second.is_alive()
    release.set()
    first.join(5)

    assert runtime.calls == 1
    assert len(outcomes) == 2
    assert sum(isinstance(item, ConversationTurnInProgress) for item in outcomes) == 1


def test_failed_terminal_duplicate_never_executes_again(isolated) -> None:
    workspace, data_dir = isolated
    runtime = Runtime()

    def execute(_messages, _observation):
        runtime.calls += 1
        raise RuntimeError("Bearer provider-secret")

    runtime.execute = execute
    service = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: runtime,
        observation_enabled=False,
        turn_store=_store(workspace, data_dir, "1" * 32),
    )

    for _ in range(2):
        with pytest.raises(ConversationTurnFailed):
            service.turn(message="fail once", session_id=None, turn_id=TURN_ID)
    assert runtime.calls == 1
    assert service.status(TURN_ID).status == "failed"


def test_restart_recovers_running_without_marker_as_interrupted_and_never_runs(
    isolated,
) -> None:
    workspace, data_dir = isolated
    first_store = _store(workspace, data_dir, "1" * 32)
    fingerprint = request_fingerprint(
        workspace_id=first_store.workspace_id,
        session_id=None,
        message="uncertain",
    )
    first_store.claim(turn_id=TURN_ID, fingerprint=fingerprint)
    first_store.mark_running(TURN_ID)
    runtime = Runtime("must not execute")
    restarted = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: runtime,
        observation_enabled=False,
        turn_store=_store(workspace, data_dir, "2" * 32),
    )

    status = restarted.status(TURN_ID)
    assert status.status == "interrupted"
    assert status.result_available is False
    with pytest.raises(ConversationTurnInterrupted):
        restarted.turn(message="uncertain", session_id=None, turn_id=TURN_ID)
    assert runtime.calls == 0


def test_restart_reconciles_cancel_requested_without_marker_as_cancelled(
    isolated,
) -> None:
    workspace, data_dir = isolated
    first_store = _store(workspace, data_dir, "1" * 32)
    fingerprint = request_fingerprint(
        workspace_id=first_store.workspace_id,
        session_id=None,
        message="cancelled restart",
    )
    first_store.claim(turn_id=TURN_ID, fingerprint=fingerprint)
    first_store.mark_running(TURN_ID)
    first_store.request_cancel(TURN_ID)
    first_store.release_claim(TURN_ID)
    runtime = Runtime("must not execute")
    restarted = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: runtime,
        observation_enabled=False,
        turn_store=_store(workspace, data_dir, "2" * 32),
    )

    assert restarted.status(TURN_ID).status == "cancelled"
    with pytest.raises(ConversationTurnCancelled):
        restarted.turn(
            message="cancelled restart",
            session_id=None,
            turn_id=TURN_ID,
        )
    assert runtime.calls == 0


def test_restart_reconciles_committing_without_marker_as_interrupted(
    isolated,
) -> None:
    workspace, data_dir = isolated
    first_store = _store(workspace, data_dir, "1" * 32)
    fingerprint = request_fingerprint(
        workspace_id=first_store.workspace_id,
        session_id=None,
        message="commit uncertainty",
    )
    first_store.claim(turn_id=TURN_ID, fingerprint=fingerprint)
    first_store.mark_running(TURN_ID)
    first_store.attach_session(
        TURN_ID,
        session_id="session_uncertain",
        created_session=True,
    )
    assert first_store.begin_commit(TURN_ID).commit_allowed is True
    first_store.release_claim(TURN_ID)
    restarted = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: Runtime("must not execute"),
        observation_enabled=False,
        turn_store=_store(workspace, data_dir, "2" * 32),
    )

    status = restarted.status(TURN_ID)
    assert status.status == "interrupted"
    assert status.result_available is False


def test_session_commit_before_turn_completion_reconciles_after_restart(
    isolated,
) -> None:
    workspace, data_dir = isolated
    store = _store(workspace, data_dir, "1" * 32)
    real_complete = store.mark_completed

    def lose_completion(*_args, **_kwargs):
        raise OSError("Turn Store unavailable /private/secret")

    store.mark_completed = lose_completion  # type: ignore[method-assign]
    runtime = Runtime("committed exactly once")
    service = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: runtime,
        observation_enabled=False,
        turn_store=store,
    )
    with pytest.raises(ConversationTurnFailed):
        service.turn(message="crash window", session_id=None, turn_id=TURN_ID)
    assert runtime.calls == 1
    store.mark_completed = real_complete  # type: ignore[method-assign]

    forbidden = Runtime("must not run")
    restarted = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: forbidden,
        observation_enabled=False,
        turn_store=_store(workspace, data_dir, "2" * 32),
    )
    status = restarted.status(TURN_ID)
    assert status.status == "completed"
    assert status.result_available is True
    recovered = restarted.turn(
        message="crash window", session_id=None, turn_id=TURN_ID
    )
    assert recovered.assistant == "committed exactly once"
    assert forbidden.calls == 0
    session = load_session(recovered.session_id)
    assert session is not None
    visible = [
        item["content"]
        for item in session.messages
        if item.get("role") in {"user", "assistant"}
    ]
    assert visible == ["crash window", "committed exactly once"]


def test_restart_marker_is_completed_even_if_record_says_cancel_requested(
    isolated,
) -> None:
    workspace, data_dir = isolated
    store = _store(workspace, data_dir, "1" * 32)

    def lose_completion(*_args, **_kwargs):
        raise OSError("completion checkpoint unavailable")

    store.mark_completed = lose_completion  # type: ignore[method-assign]
    with pytest.raises(ConversationTurnFailed):
        ConversationTurnService(
            workspace,
            runtime_factory=lambda **_kwargs: Runtime("committed before cancel"),
            observation_enabled=False,
            turn_store=store,
        ).turn(message="marker wins", session_id=None, turn_id=TURN_ID)
    path = store.record_path(TURN_ID)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["status"] = "cancel_requested"
    path.write_text(json.dumps(raw), encoding="utf-8")

    restarted = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: Runtime("must not execute"),
        observation_enabled=False,
        turn_store=_store(workspace, data_dir, "2" * 32),
    )

    status = restarted.status(TURN_ID)
    assert status.status == "completed"
    assert status.result_available is True


def test_status_is_workspace_isolated(isolated, tmp_path: Path) -> None:
    workspace, data_dir = isolated
    service = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: Runtime(),
        observation_enabled=False,
        turn_store=_store(workspace, data_dir, "1" * 32),
    )
    service.turn(message="local", session_id=None, turn_id=TURN_ID)
    foreign_workspace = tmp_path / "foreign"
    foreign_workspace.mkdir()
    foreign = ConversationTurnService(
        foreign_workspace,
        runtime_factory=lambda **_kwargs: Runtime(),
        observation_enabled=False,
        turn_store=_store(foreign_workspace, data_dir, "2" * 32),
    )

    with pytest.raises(ConversationTurnNotFound):
        foreign.status(TURN_ID)


def test_internal_commit_marker_never_appears_in_sessions_api_projection(
    isolated,
) -> None:
    workspace, data_dir = isolated
    result = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: Runtime("public assistant"),
        observation_enabled=False,
        turn_store=_store(workspace, data_dir, "1" * 32),
    ).turn(message="public user", session_id=None, turn_id=TURN_ID)

    payload = DashboardReadModel(workspace, data_dir=data_dir).session_detail(
        result.session_id
    )
    serialized = json.dumps(payload)
    assert [item["content"] for item in payload["messages"]] == [
        "public user",
        "public assistant",
    ]
    assert TURN_ID not in serialized
    assert "turn_commits" not in serialized
    assert "userMessageIndex" not in serialized


def test_turn_store_claim_failure_is_necessary_and_prevents_agent_execution(
    isolated,
) -> None:
    workspace, data_dir = isolated
    store = _store(workspace, data_dir, "1" * 32)
    runtime = Runtime("must not run")

    def unavailable(**_kwargs):
        raise OSError("Turn Store /private/secret unavailable")

    store.claim = unavailable  # type: ignore[method-assign]
    service = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: runtime,
        observation_enabled=False,
        turn_store=store,
    )

    with pytest.raises(ConversationTurnFailed) as raised:
        service.turn(message="safe", session_id=None, turn_id=TURN_ID)
    assert raised.value.code == "turn_failed"
    assert runtime.calls == 0


def test_agent_executed_but_session_not_committed_becomes_interrupted_without_replay(
    isolated,
) -> None:
    workspace, data_dir = isolated
    runtime = Runtime("computed but never committed")

    def process_exit(_session) -> None:
        raise SystemExit("simulated exit before Session replace")

    service = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: runtime,
        session_saver=process_exit,
        observation_enabled=False,
        turn_store=_store(workspace, data_dir, "1" * 32),
    )

    with pytest.raises(SystemExit):
        service.turn(message="uncertain model result", session_id=None, turn_id=TURN_ID)
    assert runtime.calls == 1
    assert service.status(TURN_ID).status == "interrupted"
    with pytest.raises(ConversationTurnInterrupted):
        service.turn(message="uncertain model result", session_id=None, turn_id=TURN_ID)
    assert runtime.calls == 1
    assert not (data_dir / "sessions").exists()
