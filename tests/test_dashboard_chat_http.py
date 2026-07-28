from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from pathlib import Path

import pytest

from minicode.conversation import (
    ConversationFeedbackConflict,
    ConversationFeedbackUnavailable,
    ConversationRuntimeUnavailable,
    ConversationSessionBusy,
    ConversationSessionConflict,
    ConversationSessionNotFound,
    ConversationTurnFailed,
    ConversationTurnCancelled,
    ConversationTurnIdConflict,
    ConversationTurnInProgress,
    ConversationTurnInterrupted,
    ConversationTurnNotFound,
)
from minicode.gateway import MiniCodeGatewayHandler
from minicode.conversation import ConversationTurnService
from minicode.conversation_turn_store import ConversationTurnStore
from minicode.run_journal import RunJournal
from minicode.web.read_model import DashboardReadModel


class RecordingChatService:
    def __init__(self, outcome=None) -> None:
        self.calls: list[dict[str, object]] = []
        self.outcome = outcome or SimpleNamespace(
            turn_id="turn_" + "b" * 32,
            session_id="session_01",
            created=True,
            assistant="real assistant",
            updated_at="2026-07-19T10:00:00.000Z",
            run_id="run_" + "a" * 32,
        )
        self.status_outcome = SimpleNamespace(
            turn_id="turn_" + "b" * 32,
            status="completed",
            session_id="session_01",
            created_session=True,
            run_id="run_" + "a" * 32,
            created_at="2026-07-19T09:59:00.000Z",
            updated_at="2026-07-19T10:00:00.000Z",
            completed_at="2026-07-19T10:00:00.000Z",
            error_code=None,
            result_available=True,
        )
        self.cancel_outcome = SimpleNamespace(
            turn_id="turn_" + "b" * 32,
            status="cancel_requested",
            cancellation_accepted=True,
            session_id="session_01",
            run_id="run_" + "a" * 32,
            updated_at="2026-07-19T10:00:01.000Z",
        )
        self.feedback_outcome = SimpleNamespace(
            turn_id="turn_" + "b" * 32,
            run_id="run_" + "a" * 32,
            signal="accept",
            source="explicit_user_action",
            recorded_at="2026-07-19T10:00:02.000Z",
        )

    def turn(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    def status(self, turn_id):
        self.calls.append({"status_turn_id": turn_id})
        if isinstance(self.status_outcome, BaseException):
            raise self.status_outcome
        return self.status_outcome

    def cancel(self, turn_id):
        self.calls.append({"cancel_turn_id": turn_id})
        if isinstance(self.cancel_outcome, BaseException):
            raise self.cancel_outcome
        return self.cancel_outcome

    def record_feedback(self, turn_id, signal):
        self.calls.append(
            {"feedback_turn_id": turn_id, "signal": signal}
        )
        if isinstance(self.feedback_outcome, BaseException):
            raise self.feedback_outcome
        return self.feedback_outcome


class AcceptedStartGateStore(ConversationTurnStore):
    """Pause the request thread between accepted claim and running transition."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.reached = threading.Event()
        self.proceed = threading.Event()

    def mark_running(self, turn_id):
        self.reached.set()
        assert self.proceed.wait(5)
        return super().mark_running(turn_id)


@pytest.fixture
def chat_server() -> Iterator[tuple[int, RecordingChatService]]:
    service = RecordingChatService()
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.conversation_turn_service = service
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], service
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def post(
    port: int,
    body: bytes,
    *,
    path: str = "/api/v1/chat/turns",
    content_type: str | None = "application/json",
) -> tuple[int, dict[str, str], dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {} if content_type is None else {"Content-Type": content_type}
    try:
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), json.loads(response.read())
    finally:
        connection.close()


def post_declared(
    port: int,
    *,
    content_length: str | None,
    body: bytes = b"",
    path: str = "/api/v1/chat/turns",
) -> tuple[int, dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.putrequest("POST", path)
        connection.putheader("Content-Type", "application/json")
        if content_length is not None:
            connection.putheader("Content-Length", content_length)
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def get(port: int, path: str) -> tuple[int, dict[str, str], dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), json.loads(response.read())
    finally:
        connection.close()


def test_chat_http_success_has_versioned_safe_contract(chat_server) -> None:
    port, service = chat_server

    status, headers, payload = post(
        port,
        json.dumps(
            {
                "message": " hello ",
                "sessionId": None,
                "turnId": "turn_" + "b" * 32,
            }
        ).encode(),
    )

    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert payload == {
        "ok": True,
        "schemaVersion": 1,
        "mode": "read-write",
        "turnId": "turn_" + "b" * 32,
        "sessionId": "session_01",
        "created": True,
        "assistant": {"role": "assistant", "content": "real assistant"},
        "updatedAt": "2026-07-19T10:00:00.000Z",
        "runId": "run_" + "a" * 32,
    }
    assert service.calls == [
        {
            "message": "hello",
            "session_id": None,
            "turn_id": "turn_" + "b" * 32,
        }
    ]


def test_chat_http_omitted_turn_id_is_securely_generated_and_returned(
    chat_server,
) -> None:
    port, service = chat_server
    service.outcome.turn_id = "turn_" + "c" * 32

    status, _, payload = post(port, b'{"message":"compatible"}')

    assert status == 200
    assert payload["turnId"] == "turn_" + "c" * 32
    assert service.calls == [
        {"message": "compatible", "session_id": None, "turn_id": None}
    ]


@pytest.mark.parametrize(
    ("body", "path", "content_type"),
    [
        (b"", "/api/v1/chat/turns", "application/json"),
        (b"{", "/api/v1/chat/turns", "application/json"),
        (b'\xff', "/api/v1/chat/turns", "application/json"),
        (b'{"message":""}', "/api/v1/chat/turns", "application/json"),
        (b'{"message":true}', "/api/v1/chat/turns", "application/json"),
        (b'{"message":"ok","sessionId":"../foreign"}', "/api/v1/chat/turns", "application/json"),
        (b'{"message":"one","message":"two"}', "/api/v1/chat/turns", "application/json"),
        (b'{"message":"ok","workspace":"/tmp/other"}', "/api/v1/chat/turns", "application/json"),
        (b'{"message":"ok","turnId":true}', "/api/v1/chat/turns", "application/json"),
        (b'{"message":"ok","turnId":1}', "/api/v1/chat/turns", "application/json"),
        (b'{"message":"ok","turnId":"turn_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}', "/api/v1/chat/turns", "application/json"),
        (b'{"message":"ok","turnId":"../turn_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}', "/api/v1/chat/turns", "application/json"),
        (b'{"message":"ok","turnId":" turn_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}', "/api/v1/chat/turns", "application/json"),
        (b'{"message":"ok","turnId":"turn_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","turnId":"turn_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}', "/api/v1/chat/turns", "application/json"),
        (b'{"message":"ok"}', "/api/v1/chat/turns?retry=1", "application/json"),
        (b'{"message":"ok"}', "/api/v1/chat/turns", "text/plain"),
        (b'{"message":"ok"}', "/api/v1/chat/turns", None),
        (b'{"message":"ok"}', "/api/v1/chat/turns", "application/json; charset=utf-16"),
    ],
)
def test_chat_http_rejects_invalid_input_before_service(
    chat_server,
    body: bytes,
    path: str,
    content_type: str | None,
) -> None:
    port, service = chat_server

    status, _, payload = post(
        port,
        body,
        path=path,
        content_type=content_type,
    )

    assert status == 400
    assert payload == {
        "ok": False,
        "error": {
            "code": "invalid_request",
            "message": "Chat request is invalid.",
        },
    }
    assert service.calls == []


@pytest.mark.parametrize("content_length", [None, "invalid", "-1", "65537"])
def test_chat_http_rejects_missing_invalid_or_oversized_content_length(
    chat_server,
    content_length: str | None,
) -> None:
    port, service = chat_server

    status, payload = post_declared(port, content_length=content_length)

    assert status == 400
    assert payload["error"]["code"] == "invalid_request"
    assert service.calls == []


@pytest.mark.parametrize(
    ("error", "status", "code", "message"),
    [
        (ConversationSessionNotFound(), 404, "session_not_found", "Session was not found."),
        (ConversationSessionConflict(), 409, "session_conflict", "Session changed before this turn could be saved."),
        (ConversationSessionBusy(), 503, "session_busy", "Session storage is busy."),
        (ConversationRuntimeUnavailable(), 503, "runtime_unavailable", "Chat runtime is unavailable."),
        (ConversationTurnFailed(), 500, "turn_failed", "Chat turn failed."),
        (ConversationTurnIdConflict(), 409, "turn_id_conflict", "Turn ID belongs to a different request."),
        (ConversationTurnInProgress(), 409, "turn_in_progress", "Chat turn is already in progress."),
        (ConversationTurnInterrupted(), 409, "turn_interrupted", "Chat turn was interrupted and will not be retried."),
        (ConversationTurnCancelled(), 409, "turn_cancelled", "Chat turn was cancelled and will not be retried."),
        (RuntimeError("Bearer provider-secret /private/path"), 500, "turn_failed", "Chat turn failed."),
    ],
)
def test_chat_http_maps_only_fixed_safe_errors(
    chat_server,
    error: BaseException,
    status: int,
    code: str,
    message: str,
) -> None:
    port, service = chat_server
    service.outcome = error

    actual_status, _, payload = post(
        port,
        b'{"message":"safe","turnId":"turn_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}',
    )

    assert actual_status == status
    assert payload == {
        "ok": False,
        "turnId": "turn_" + "b" * 32,
        "error": {"code": code, "message": message},
    }
    assert "provider-secret" not in json.dumps(payload)
    assert "/private/path" not in json.dumps(payload)


def test_chat_turn_status_get_is_strict_versioned_and_allowlisted(chat_server) -> None:
    port, service = chat_server

    status, headers, payload = get(
        port, "/api/v1/chat/turns/turn_" + "b" * 32
    )

    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert payload == {
        "ok": True,
        "schemaVersion": 1,
        "mode": "read-only",
        "turnId": "turn_" + "b" * 32,
        "status": "completed",
        "sessionId": "session_01",
        "created": True,
        "runId": "run_" + "a" * 32,
        "createdAt": "2026-07-19T09:59:00.000Z",
        "updatedAt": "2026-07-19T10:00:00.000Z",
        "completedAt": "2026-07-19T10:00:00.000Z",
        "errorCode": None,
        "resultAvailable": True,
    }
    assert service.calls == [{"status_turn_id": "turn_" + "b" * 32}]
    serialized = json.dumps(payload)
    for forbidden in ("fingerprint", "owner", "/private", "assistant"):
        assert forbidden not in serialized.lower()


@pytest.mark.parametrize("turn_status", ["cancel_requested", "committing", "cancelled"])
def test_chat_turn_status_supports_cancellation_states(
    chat_server,
    turn_status: str,
) -> None:
    port, service = chat_server
    service.status_outcome.status = turn_status
    service.status_outcome.error_code = (
        "turn_cancelled" if turn_status == "cancelled" else None
    )
    service.status_outcome.result_available = False

    status, _, payload = get(port, "/api/v1/chat/turns/turn_" + "b" * 32)

    assert status == 200
    assert payload["status"] == turn_status
    assert payload["resultAvailable"] is False


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/chat/turns/turn_" + "A" * 32,
        "/api/v1/chat/turns/../escape",
        "/api/v1/chat/turns/turn_" + "a" * 32 + "?retry=1",
        "/api/v1/chat/turns/turn_" + "a" * 32 + "/extra",
    ],
)
def test_chat_turn_status_rejects_invalid_path_before_service(
    chat_server, path: str
) -> None:
    port, service = chat_server

    status, _, payload = get(port, path)

    assert status == 400
    assert payload["error"]["code"] == "invalid_request"
    assert service.calls == []


def test_chat_turn_status_missing_and_foreign_are_identical(chat_server) -> None:
    port, service = chat_server
    service.status_outcome = ConversationTurnNotFound()

    status, _, payload = get(
        port, "/api/v1/chat/turns/turn_" + "b" * 32
    )

    assert status == 404
    assert payload == {
        "ok": False,
        "turnId": "turn_" + "b" * 32,
        "error": {"code": "turn_not_found", "message": "Turn was not found."},
    }


def test_chat_turn_cancel_is_strict_idempotent_and_allowlisted(chat_server) -> None:
    port, service = chat_server

    status, headers, payload = post(
        port,
        b"{}",
        path="/api/v1/chat/turns/turn_" + "b" * 32 + "/cancel",
    )

    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert payload == {
        "ok": True,
        "schemaVersion": 1,
        "mode": "read-write",
        "turnId": "turn_" + "b" * 32,
        "status": "cancel_requested",
        "cancellationAccepted": True,
        "sessionId": "session_01",
        "runId": "run_" + "a" * 32,
        "updatedAt": "2026-07-19T10:00:01.000Z",
    }
    assert service.calls == [{"cancel_turn_id": "turn_" + "b" * 32}]
    assert all(
        forbidden not in json.dumps(payload).lower()
        for forbidden in ("fingerprint", "owner", "reason", "token")
    )


def test_chat_turn_feedback_is_explicit_versioned_and_content_free(
    chat_server,
) -> None:
    port, service = chat_server

    status, headers, payload = post(
        port,
        b'{"signal":"accept"}',
        path="/api/v1/chat/turns/turn_" + "b" * 32 + "/feedback",
    )

    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert payload == {
        "ok": True,
        "schemaVersion": 1,
        "mode": "read-write",
        "turnId": "turn_" + "b" * 32,
        "runId": "run_" + "a" * 32,
        "signal": "accept",
        "source": "explicit_user_action",
        "recordedAt": "2026-07-19T10:00:02.000Z",
    }
    assert service.calls == [
        {
            "feedback_turn_id": "turn_" + "b" * 32,
            "signal": "accept",
        }
    ]
    serialized = json.dumps(payload).lower()
    assert "message" not in serialized
    assert "reason" not in serialized


@pytest.mark.parametrize(
    ("body", "path", "content_type"),
    [
        (b"", "/api/v1/chat/turns/turn_" + "b" * 32 + "/feedback", "application/json"),
        (b"{}", "/api/v1/chat/turns/turn_" + "b" * 32 + "/feedback", "application/json"),
        (b'{"signal":"yes"}', "/api/v1/chat/turns/turn_" + "b" * 32 + "/feedback", "application/json"),
        (b'{"signal":"accept","reason":"secret"}', "/api/v1/chat/turns/turn_" + "b" * 32 + "/feedback", "application/json"),
        (b'{"signal":"accept","signal":"reject"}', "/api/v1/chat/turns/turn_" + "b" * 32 + "/feedback", "application/json"),
        (b'{"signal":"accept"}', "/api/v1/chat/turns/turn_" + "B" * 32 + "/feedback", "application/json"),
        (b'{"signal":"accept"}', "/api/v1/chat/turns/turn_" + "b" * 32 + "/feedback?retry=1", "application/json"),
        (b'{"signal":"accept"}', "/api/v1/chat/turns/turn_" + "b" * 32 + "/feedback", "text/plain"),
    ],
)
def test_chat_turn_feedback_rejects_implicit_or_invalid_signal(
    chat_server,
    body: bytes,
    path: str,
    content_type: str,
) -> None:
    port, service = chat_server

    status, _, payload = post(port, body, path=path, content_type=content_type)

    assert status == 400
    assert payload["error"]["code"] == "invalid_request"
    assert service.calls == []


@pytest.mark.parametrize(
    ("outcome", "expected_status", "code"),
    [
        (ConversationTurnNotFound(), 404, "turn_not_found"),
        (ConversationFeedbackConflict(), 409, "feedback_conflict"),
        (ConversationFeedbackUnavailable(), 409, "feedback_unavailable"),
        (RuntimeError("Bearer fixture-secret"), 500, "turn_failed"),
    ],
)
def test_chat_turn_feedback_maps_only_fixed_errors(
    chat_server,
    outcome,
    expected_status: int,
    code: str,
) -> None:
    port, service = chat_server
    service.feedback_outcome = outcome

    status, _, payload = post(
        port,
        b'{"signal":"reject"}',
        path="/api/v1/chat/turns/turn_" + "b" * 32 + "/feedback",
    )

    assert status == expected_status
    assert payload["error"]["code"] == code
    assert "fixture-secret" not in json.dumps(payload)


def test_http_accepted_boundary_cancel_returns_turn_cancelled_and_persists_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr("minicode.session.MINI_CODE_DIR", data_dir)
    monkeypatch.setattr("minicode.session.SESSIONS_DIR", data_dir / "sessions")
    store = AcceptedStartGateStore(
        workspace,
        data_dir=data_dir,
        owner_id="1" * 32,
    )
    runtime_calls: list[str] = []

    def forbidden_runtime(**_kwargs):
        runtime_calls.append("called")
        raise AssertionError("runtime must not be constructed")

    service = ConversationTurnService(
        workspace,
        runtime_factory=forbidden_runtime,
        turn_store=store,
        observation_enabled=False,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.conversation_turn_service = service
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    original: dict[str, object] = {}
    turn_id = "turn_" + "d" * 32

    def submit_original() -> None:
        original["response"] = post(
            server.server_address[1],
            json.dumps({"message": "accepted race", "turnId": turn_id}).encode(),
        )

    request_thread = threading.Thread(target=submit_original)
    request_thread.start()
    try:
        assert store.reached.wait(5)
        cancel_status, _, cancel_payload = post(
            server.server_address[1],
            b"{}",
            path=f"/api/v1/chat/turns/{turn_id}/cancel",
        )
        assert cancel_status == 200
        assert cancel_payload["status"] == "cancel_requested"
        assert cancel_payload["cancellationAccepted"] is True
        store.proceed.set()
        request_thread.join(5)
        assert not request_thread.is_alive()
        original_status, _, original_payload = original["response"]
        status_code, _, status_payload = get(
            server.server_address[1],
            f"/api/v1/chat/turns/{turn_id}",
        )
    finally:
        store.proceed.set()
        request_thread.join(5)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    assert original_status == 409
    assert original_payload == {
        "ok": False,
        "turnId": turn_id,
        "error": {
            "code": "turn_cancelled",
            "message": "Chat turn was cancelled and will not be retried.",
        },
    }
    assert status_code == 200
    assert status_payload["status"] == "cancelled"
    assert status_payload["errorCode"] == "turn_cancelled"
    assert store.get(turn_id).status == "cancelled"
    assert runtime_calls == []
    assert not (data_dir / "sessions").exists()
    serialized = json.dumps([cancel_payload, original_payload, status_payload])
    for forbidden in ("fingerprint", "owner", "fixture-secret", "/private"):
        assert forbidden not in serialized.lower()


@pytest.mark.parametrize(
    ("body", "path", "content_type"),
    [
        (b"", "/api/v1/chat/turns/turn_" + "b" * 32 + "/cancel", "application/json"),
        (b"null", "/api/v1/chat/turns/turn_" + "b" * 32 + "/cancel", "application/json"),
        (b"[]", "/api/v1/chat/turns/turn_" + "b" * 32 + "/cancel", "application/json"),
        (b'{"reason":"secret"}', "/api/v1/chat/turns/turn_" + "b" * 32 + "/cancel", "application/json"),
        (b"{", "/api/v1/chat/turns/turn_" + "b" * 32 + "/cancel", "application/json"),
        (b"\xff", "/api/v1/chat/turns/turn_" + "b" * 32 + "/cancel", "application/json"),
        (b"{}", "/api/v1/chat/turns/turn_" + "B" * 32 + "/cancel", "application/json"),
        (b"{}", "/api/v1/chat/turns/turn_" + "b" * 32 + "/cancel?force=true", "application/json"),
        (b"{}", "/api/v1/chat/turns/turn_" + "b" * 32 + "/cancel", "text/plain"),
        (b"{}", "/api/v1/chat/turns/turn_" + "b" * 32 + "/cancel", "application/json; charset=utf-16"),
    ],
)
def test_chat_turn_cancel_rejects_invalid_request_before_service(
    chat_server,
    body: bytes,
    path: str,
    content_type: str,
) -> None:
    port, service = chat_server

    status, _, payload = post(port, body, path=path, content_type=content_type)

    assert status == 400
    assert payload == {
        "ok": False,
        "error": {"code": "invalid_request", "message": "Chat request is invalid."},
    }
    assert service.calls == []


@pytest.mark.parametrize("content_length", [None, "invalid", "-1", "1025"])
def test_chat_turn_cancel_rejects_invalid_or_oversized_content_length(
    chat_server,
    content_length: str | None,
) -> None:
    port, service = chat_server

    status, payload = post_declared(
        port,
        content_length=content_length,
        body=b"{}",
        path="/api/v1/chat/turns/turn_" + "b" * 32 + "/cancel",
    )

    assert status == 400
    assert payload["error"]["code"] == "invalid_request"
    assert service.calls == []


@pytest.mark.parametrize(
    ("outcome", "expected_status", "code", "message"),
    [
        (ConversationTurnNotFound(), 404, "turn_not_found", "Turn was not found."),
        (RuntimeError("Bearer fixture-secret"), 500, "turn_failed", "Chat turn cancellation failed."),
    ],
)
def test_chat_turn_cancel_maps_only_fixed_errors(
    chat_server,
    outcome,
    expected_status: int,
    code: str,
    message: str,
) -> None:
    port, service = chat_server
    service.cancel_outcome = outcome

    status, _, payload = post(
        port,
        b"{}",
        path="/api/v1/chat/turns/turn_" + "b" * 32 + "/cancel",
    )

    assert status == expected_status
    assert payload == {
        "ok": False,
        "turnId": "turn_" + "b" * 32,
        "error": {"code": code, "message": message},
    }
    assert "fixture-secret" not in json.dumps(payload)


def test_chat_http_new_continue_read_apis_and_restart_share_durable_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr("minicode.session.MINI_CODE_DIR", data_dir)
    monkeypatch.setattr("minicode.session.SESSIONS_DIR", data_dir / "sessions")

    class Permissions:
        def begin_turn(self):
            pass

        def end_turn(self):
            pass

        def get_summary(self):
            return []

    class Tools:
        def get_skills(self):
            return []

        def get_mcp_servers(self):
            return []

        def dispose(self):
            pass

    execute_calls = []

    class Runtime:
        system_prompt = "system"
        permissions = Permissions()
        tools = Tools()
        skill_routing = None

        def execute(self, messages, _observation):
            prompt = messages[-1]["content"]
            execute_calls.append(prompt)
            return [*messages, {"role": "assistant", "content": f"reply:{prompt}"}]

        def dispose(self):
            pass

    def make_server():
        read_model = DashboardReadModel(
            workspace,
            data_dir=data_dir,
            run_journal=RunJournal(workspace, data_dir=data_dir),
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
        server.dashboard_read_model = read_model
        server.conversation_turn_service = ConversationTurnService(
            workspace,
            runtime_factory=lambda **_kwargs: Runtime(),
            journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    server, thread = make_server()
    try:
        first_status, _, first = post(
            server.server_address[1],
            b'{"message":"first","turnId":"turn_11111111111111111111111111111111"}',
        )
        second_status, _, second = post(
            server.server_address[1],
            json.dumps(
                {
                    "message": "second",
                    "sessionId": first["sessionId"],
                    "turnId": "turn_22222222222222222222222222222222",
                }
            ).encode(),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert first_status == second_status == 200
    assert first["created"] is True and second["created"] is False
    assert first["sessionId"] == second["sessionId"]

    restarted, restarted_thread = make_server()
    try:
        port = restarted.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("GET", "/api/v1/sessions")
            sessions = json.loads(connection.getresponse().read())
            connection.request(
                "GET", f"/api/v1/sessions/{first['sessionId']}?limit=50"
            )
            detail = json.loads(connection.getresponse().read())
            connection.request("GET", "/api/v1/runs?limit=20")
            runs = json.loads(connection.getresponse().read())
            connection.request("GET", "/api/v1/snapshot")
            snapshot = json.loads(connection.getresponse().read())
            connection.request(
                "GET",
                "/api/v1/chat/turns/turn_11111111111111111111111111111111",
            )
            turn_status = json.loads(connection.getresponse().read())
        finally:
            connection.close()
        duplicate_status, _, duplicate = post(
            port,
            b'{"message":"first","turnId":"turn_11111111111111111111111111111111"}',
        )
    finally:
        restarted.shutdown()
        restarted.server_close()
        restarted_thread.join(timeout=5)

    assert len(sessions["items"]) == 1
    assert [item["content"] for item in detail["messages"]] == [
        "first",
        "reply:first",
        "second",
        "reply:second",
    ]
    assert len(runs["items"]) == 2
    assert {item["sessionId"] for item in runs["items"]} == {first["sessionId"]}
    assert snapshot["overview"]["sessions"]["count"] == 1
    assert turn_status["status"] == "completed"
    assert turn_status["resultAvailable"] is True
    assert duplicate_status == 200
    assert duplicate["assistant"]["content"] == "reply:first"
    assert execute_calls == ["first", "second"]
