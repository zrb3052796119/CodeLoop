from __future__ import annotations

import http.client
import json
import socket
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest

import minicode.session as session_module
from minicode.conversation_deletion import (
    ConversationDeletionAuthority,
    ConversationDeletionError,
)
from minicode.gateway import MiniCodeGatewayHandler
from minicode.session import create_new_session, save_session
from minicode.web.read_model import DashboardReadModel


class _Authority:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.calls: list[tuple[object, ...]] = []
        self.error: str | None = None

    def snapshot(self, target: str) -> dict[str, object]:
        self.calls.append(("snapshot", target))
        if self.error:
            raise ConversationDeletionError(self.error)
        return {
            "schemaVersion": 1,
            "mode": "read-write",
            "kind": self.kind,
            "target": {"id": target},
            "status": "ready",
            "deletionRevision": "delrev_" + "a" * 64,
        }

    def delete(self, target: str, revision: str) -> dict[str, object]:
        self.calls.append(("delete", target, revision))
        if self.error:
            raise ConversationDeletionError(self.error)
        return {
            "schemaVersion": 1,
            "mode": "read-write",
            "kind": self.kind,
            "target": {"id": target},
            "status": "completed",
            "deletionRevision": revision,
        }


@contextmanager
def _gateway(
    tmp_path: Path,
) -> Iterator[tuple[tuple[str, int], _Authority, _Authority]]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.dashboard_read_model = DashboardReadModel(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )
    conversation = _Authority("conversation")
    memory = _Authority("project-memory")
    server.conversation_deletion_authority = conversation
    server.project_memory_deletion_authority = memory
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address, conversation, memory
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _request(
    address: tuple[str, int],
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object], dict[str, str]]:
    connection = http.client.HTTPConnection(*address, timeout=5)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    payload = json.loads(response.read().decode("utf-8"))
    response_headers = {key.casefold(): value for key, value in response.getheaders()}
    connection.close()
    return response.status, payload, response_headers


@pytest.mark.parametrize(
    ("path", "target", "authority_index"),
    [
        ("/api/v1/sessions/session_1/deletion", "session_1", 0),
        ("/api/v1/memory/project/project-1/deletion", "project-1", 1),
    ],
)
def test_deletion_get_and_post_routes_are_strict_no_store_json(
    tmp_path: Path,
    path: str,
    target: str,
    authority_index: int,
) -> None:
    with _gateway(tmp_path) as (address, conversation, memory):
        authorities = (conversation, memory)
        status, preview, headers = _request(address, "GET", path)
        assert status == 200
        assert preview["status"] == "ready"
        assert headers["cache-control"] == "no-store"
        assert headers["content-type"] == "application/json; charset=utf-8"

        body = json.dumps(
            {"deletionRevision": preview["deletionRevision"]}
        ).encode()
        post_status, result, post_headers = _request(
            address,
            "POST",
            path,
            body=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Origin": f"http://127.0.0.1:{address[1]}",
            },
        )

        assert post_status == 200
        assert result["status"] == "completed"
        assert post_headers["cache-control"] == "no-store"
        assert authorities[authority_index].calls == [
            ("snapshot", target),
            ("delete", target, preview["deletionRevision"]),
        ]


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b'{"deletionRevision":"delrev_' + b"a" * 64 + b'","extra":1}', "application/json"),
        (b'{"deletionRevision":"delrev_' + b"a" * 64 + b'","deletionRevision":"delrev_' + b"b" * 64 + b'"}', "application/json"),
        (b'{"deletionRevision":null}', "application/json"),
        (b"{}", "text/plain"),
        (b"[1]", "application/json"),
    ],
)
def test_deletion_post_rejects_non_exact_json_without_calling_authority(
    tmp_path: Path,
    body: bytes,
    content_type: str,
) -> None:
    with _gateway(tmp_path) as (address, conversation, _):
        status, payload, _headers = _request(
            address,
            "POST",
            "/api/v1/sessions/session_1/deletion",
            body=body,
            headers={"Content-Type": content_type},
        )
        assert status == 400
        assert payload["error"]["code"] in {"invalid_request", "invalid_revision"}
        assert conversation.calls == []


def test_deletion_http_rejects_query_origin_oversize_and_maps_safe_errors(
    tmp_path: Path,
) -> None:
    with _gateway(tmp_path) as (address, conversation, _):
        status, payload, _ = _request(
            address,
            "GET",
            "/api/v1/sessions/session_1/deletion?x=1",
        )
        assert (status, payload["error"]["code"]) == (400, "invalid_request")

        revision_body = json.dumps(
            {"deletionRevision": "delrev_" + "a" * 64}
        ).encode()
        status, payload, _ = _request(
            address,
            "POST",
            "/api/v1/sessions/session_1/deletion",
            body=revision_body,
            headers={
                "Content-Type": "application/json",
                "Origin": "https://attacker.example",
            },
        )
        assert (status, payload["error"]["code"]) == (400, "invalid_request")

        status, payload, _ = _request(
            address,
            "POST",
            "/api/v1/sessions/session_1/deletion",
            body=b"x" * 1_025,
            headers={"Content-Type": "application/json"},
        )
        assert (status, payload["error"]["code"]) == (400, "invalid_request")

        conversation.error = "deletion_target_busy"
        status, payload, _ = _request(
            address,
            "GET",
            "/api/v1/sessions/session_1/deletion",
        )
        assert (status, payload["error"]["code"]) == (
            409,
            "deletion_target_busy",
        )
        assert str(tmp_path) not in str(payload)


def test_deletion_invalid_id_and_unknown_api_are_structured(
    tmp_path: Path,
) -> None:
    with _gateway(tmp_path) as (address, _conversation, _memory):
        status, payload, _ = _request(
            address,
            "GET",
            "/api/v1/sessions/%2e%2e/deletion",
        )
        assert (status, payload["error"]["code"]) == (400, "invalid_id")

        status, payload, _ = _request(address, "GET", "/api/v1/deletion/unknown")
        assert (status, payload["error"]["code"]) == (404, "not_found")


@pytest.mark.parametrize(
    ("code", "expected_status"),
    [
        ("invalid_id", 400),
        ("invalid_revision", 400),
        ("deletion_target_not_found", 404),
        ("deletion_revision_stale", 409),
        ("deletion_target_busy", 409),
        ("deletion_write_conflict", 409),
        ("deletion_store_busy", 503),
        ("deletion_unavailable", 503),
        ("deletion_failed", 500),
    ],
)
def test_deletion_http_maps_only_fixed_safe_errors(
    tmp_path: Path,
    code: str,
    expected_status: int,
) -> None:
    with _gateway(tmp_path) as (address, conversation, _):
        conversation.error = code
        status, payload, headers = _request(
            address,
            "GET",
            "/api/v1/sessions/session_1/deletion",
        )

        assert status == expected_status
        assert payload["error"]["code"] == code
        assert set(payload) == {"ok", "error"}
        assert set(payload["error"]) == {"code", "message"}
        assert str(tmp_path) not in str(payload)
        assert headers["cache-control"] == "no-store"


def test_deletion_post_rejects_method_override_without_authority_call(
    tmp_path: Path,
) -> None:
    with _gateway(tmp_path) as (address, conversation, _):
        body = json.dumps(
            {"deletionRevision": "delrev_" + "a" * 64}
        ).encode()
        status, payload, _ = _request(
            address,
            "POST",
            "/api/v1/sessions/session_1/deletion",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-HTTP-Method-Override": "DELETE",
            },
        )

        assert (status, payload["error"]["code"]) == (400, "invalid_request")
        assert conversation.calls == []


def test_lost_post_response_reconciles_via_get_and_idempotent_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr(session_module, "MINI_CODE_DIR", data_dir)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", data_dir / "sessions")
    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "lost response content"}]
    save_session(session, force_full=True)
    real_authority = ConversationDeletionAuthority(workspace, data_dir=data_dir)
    delete_completed = threading.Event()
    allow_response = threading.Event()

    class _PausedResponseAuthority:
        def snapshot(self, target: str) -> dict[str, object]:
            return real_authority.snapshot(target)

        def delete(self, target: str, revision: str) -> dict[str, object]:
            result = real_authority.delete(target, revision)
            delete_completed.set()
            assert allow_response.wait(timeout=5)
            return result

    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.dashboard_read_model = DashboardReadModel(workspace, data_dir=data_dir)
    server.conversation_deletion_authority = _PausedResponseAuthority()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        address = server.server_address
        path = f"/api/v1/sessions/{session.session_id}/deletion"
        status, preview, _ = _request(address, "GET", path)
        assert status == 200
        revision = str(preview["deletionRevision"])
        body = json.dumps({"deletionRevision": revision}).encode("utf-8")
        raw_request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{address[1]}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii") + body
        lost_client = socket.create_connection(address, timeout=5)
        lost_client.sendall(raw_request)
        assert delete_completed.wait(timeout=5)
        lost_client.close()
        allow_response.set()

        get_status, completed, _ = _request(address, "GET", path)
        assert get_status == 200
        assert completed["status"] == "completed"
        retry_status, retried, _ = _request(
            address,
            "POST",
            path,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        assert retry_status == 200
        assert retried["status"] == "already_absent"
    finally:
        allow_response.set()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
