from __future__ import annotations

import http.client
import json
import threading
import time
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from minicode.gateway import MiniCodeGatewayHandler
from minicode.permission_approval import PermissionApprovalBroker
from minicode.permission_approval import is_loopback_gateway_host
from minicode.permissions import PermissionManager
from minicode.tooling import ToolContext
from minicode.tools.http_utils import http_request_tool
from minicode.tools.write_file import write_file_tool
from minicode.turn_cancellation import TurnCancellationToken


@pytest.fixture
def permission_server(tmp_path: Path) -> Iterator[tuple[int, PermissionApprovalBroker, Path]]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.permission_approval_broker = broker
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], broker, workspace
    finally:
        broker.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), json.loads(response.read())
    finally:
        connection.close()


def _pending(port: int) -> tuple[int, dict[str, str], dict[str, object]]:
    deadline = time.monotonic() + 1
    response = _request(port, "GET", "/api/v1/permissions/pending")
    while time.monotonic() < deadline and not response[2].get("items"):
        time.sleep(0.005)
        response = _request(port, "GET", "/api/v1/permissions/pending")
    return response


def test_gateway_pending_and_decision_wake_real_tool(permission_server) -> None:
    port, broker, workspace = permission_server
    turn_id = "turn_" + "4" * 32
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id="run_" + "5" * 32,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    outcome: dict[str, object] = {}

    def write() -> None:
        session.tool_started("write_file")
        try:
            outcome["result"] = write_file_tool.run(
                {"path": "http.txt", "content": "allowed"},
                ToolContext(cwd=str(workspace), permissions=manager),
            )
        finally:
            session.tool_finished("write_file")

    worker = threading.Thread(target=write)
    worker.start()
    status, headers, pending = _pending(port)
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    item = pending["items"][0]

    status, _, response = _request(
        port,
        "POST",
        f"/api/v1/permissions/{item['permissionId']}/decision",
        body=json.dumps(
            {"turnId": turn_id, "decision": "allow_once"}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert status == 200
    assert response["decisionAccepted"] is True
    assert "Access-Control-Allow-Origin" not in _
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert (workspace / "http.txt").read_text(encoding="utf-8") == "allowed"


def test_gateway_network_pending_is_content_free_and_allow_once_sends_once(
    permission_server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, broker, workspace = permission_server
    turn_id = "turn_" + "6" * 32
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    network_calls: list[str] = []
    outcome: dict[str, object] = {}

    class Response:
        status = 200
        headers = {"Content-Type": "application/json", "Content-Length": "11"}

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= 64 * 1024
            return b'{"ok":true}'

    def open_once(request: object, *, timeout: float) -> Response:
        assert timeout > 0
        network_calls.append(request.full_url)
        return Response()

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect",
        open_once,
    )

    def request() -> None:
        session.tool_started("http_request")
        try:
            outcome["result"] = http_request_tool.run(
                http_request_tool.validator(
                    {
                        "url": "https://93.184.216.34/mutate?fixture-secret=hidden",
                        "method": "POST",
                        "headers": {"Authorization": "fixture-secret"},
                        "body": '{"fixture-secret":true}',
                        "timeout": 2,
                    }
                ),
                ToolContext(cwd=str(workspace), permissions=manager),
            )
        finally:
            session.tool_finished("http_request")

    worker = threading.Thread(target=request)
    worker.start()
    status, headers, pending = _pending(port)
    serialized = json.dumps(pending)
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    item = pending["items"][0]
    assert item["toolName"] == "http_request"
    assert item["kind"] == "network"
    assert item["reviewable"] is True
    assert item["choices"] == ["allow_once", "deny_once"]
    assert item["review"] == {
        "reviewVersion": 1,
        "method": "POST",
        "scheme": "https",
        "hostname": "93.184.216.34",
        "port": 443,
        "pathSummary": "/mutate",
        "hasBody": True,
        "hasSensitiveHeaders": True,
        "requestFingerprint": item["review"]["requestFingerprint"],
    }
    assert "fixture-secret" not in serialized
    assert str(workspace) not in serialized
    decision_status, _, decision = _request(
        port,
        "POST",
        f"/api/v1/permissions/{item['permissionId']}/decision",
        body=json.dumps(
            {"turnId": turn_id, "decision": "allow_once"}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert decision_status == 200
    assert decision["status"] == "allowed"
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert outcome["result"].ok is True
    assert len(network_calls) == 1
    session.close()


@pytest.mark.parametrize(
    ("hostname", "path_summary"),
    [
        ("api.public.example", "/mutate?fixture-secret=hidden"),
        ("192.0.2.1", "/mutate"),
    ],
)
def test_gateway_unsafe_or_incomplete_network_review_is_deny_only(
    permission_server,
    hostname: str,
    path_summary: str,
) -> None:
    port, broker, _workspace = permission_server
    turn_id = "turn_" + "9" * 32
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    outcome: dict[str, object] = {}

    def prompt() -> None:
        session.tool_started("http_request")
        try:
            outcome["result"] = session.prompt(
                {
                    "schemaVersion": 1,
                    "kind": "network",
                    "review": {
                        "reviewVersion": 1,
                        "method": "POST",
                        "scheme": "https",
                        "hostname": hostname,
                        "port": 443,
                        "pathSummary": path_summary,
                        "hasBody": True,
                        "hasSensitiveHeaders": False,
                        "requestFingerprint": "networkreq_" + "a" * 64,
                    },
                }
            )
        finally:
            session.tool_finished("http_request")

    worker = threading.Thread(target=prompt)
    worker.start()
    status, _, pending = _pending(port)
    assert status == 200
    item = pending["items"][0]
    assert item["kind"] == "network"
    assert item["reviewable"] is False
    assert item["review"] == {}
    assert item["choices"] == ["deny_once"]
    serialized = json.dumps(pending)
    assert "fixture-secret" not in serialized
    path = f"/api/v1/permissions/{item['permissionId']}/decision"
    allow_status, _, allow_payload = _request(
        port,
        "POST",
        path,
        body=json.dumps(
            {"turnId": turn_id, "decision": "allow_once"}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert allow_status == 409
    assert allow_payload["error"]["code"] == "permission_not_reviewable"
    deny_status, _, deny_payload = _request(
        port,
        "POST",
        path,
        body=json.dumps(
            {"turnId": turn_id, "decision": "deny_once"}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert deny_status == 200
    assert deny_payload["status"] == "denied"
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert outcome["result"]["decision"] == "deny_operation"
    session.close()


def test_permission_routes_fail_closed_without_broker(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, body = _request(
            server.server_address[1], "GET", "/api/v1/permissions/pending"
        )
        assert status == 503
        assert body["error"]["code"] == "permission_unavailable"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _start_pending_prompt(
    broker: PermissionApprovalBroker,
    *,
    turn_id: str,
) -> tuple[threading.Thread, dict[str, object]]:
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    outcome: dict[str, object] = {}

    def prompt() -> None:
        session.tool_started("write_file")
        try:
            outcome["result"] = session.prompt(
                {
                    "schemaVersion": 1,
                    "kind": "edit",
                    "review": {
                        "targetPath": str((broker.workspace / "strict.txt").resolve()),
                        "diffPreview": "--- a/strict.txt\n+++ b/strict.txt",
                    },
                }
            )
        finally:
            session.tool_finished("write_file")

    thread = threading.Thread(target=prompt)
    thread.start()
    return thread, outcome


def test_permission_decision_http_is_strict_same_origin_and_idempotent(
    permission_server,
) -> None:
    port, broker, _ = permission_server
    turn_id = "turn_" + "e" * 32
    worker, _outcome = _start_pending_prompt(broker, turn_id=turn_id)
    item = _pending(port)[2]["items"][0]
    path = f"/api/v1/permissions/{item['permissionId']}/decision"
    body = json.dumps({"turnId": turn_id, "decision": "allow_once"}).encode()

    status, headers, first = _request(
        port,
        "POST",
        path,
        body=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Origin": f"http://127.0.0.1:{port}",
        },
    )
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in headers
    assert first == {
        "schemaVersion": 1,
        "mode": "read-write",
        "permissionId": item["permissionId"],
        "turnId": turn_id,
        "status": "allowed",
        "decision": "allow_once",
        "decisionAccepted": True,
        "updatedAt": first["updatedAt"],
    }
    retry_status, _, retry = _request(
        port,
        "POST",
        path,
        body=body,
        headers={"Content-Type": "application/json"},
    )
    assert retry_status == 200
    assert retry["decisionAccepted"] is False
    assert retry["updatedAt"] == first["updatedAt"]
    worker.join(timeout=1)


@pytest.mark.parametrize(
    ("path_suffix", "body", "headers", "expected_status"),
    [
        ("?extra=1", b'{"turnId":"turn_x","decision":"allow_once"}', {"Content-Type": "application/json"}, 400),
        ("", b'{}', {}, 400),
        ("", b'{"turnId":"turn_x","decision":"allow_once"}', {"Content-Type": "text/plain"}, 400),
        ("", b'{"turnId":"turn_x","decision":"allow_once"}', {"Content-Type": "application/json; charset=latin-1"}, 400),
        ("", b'{"turnId":"turn_x","decision":"allow_once","extra":1}', {"Content-Type": "application/json"}, 400),
        ("", b'{"turnId":"turn_x","turnId":"turn_y","decision":"allow_once"}', {"Content-Type": "application/json"}, 400),
        ("", b'{"turnId":true,"decision":"allow_once"}', {"Content-Type": "application/json"}, 400),
        ("", b'{"turnId":"turn_x","decision":true}', {"Content-Type": "application/json"}, 400),
    ],
)
def test_permission_decision_rejects_malformed_transport(
    permission_server,
    path_suffix: str,
    body: bytes,
    headers: dict[str, str],
    expected_status: int,
) -> None:
    port, _broker, _workspace = permission_server
    permission_id = "permission_" + "1" * 32
    status, response_headers, payload = _request(
        port,
        "POST",
        f"/api/v1/permissions/{permission_id}/decision{path_suffix}",
        body=body,
        headers=headers,
    )
    assert status == expected_status
    assert response_headers["Cache-Control"] == "no-store"
    assert payload["error"]["code"] == "invalid_request"


def test_permission_http_rejects_cross_origin_turn_mismatch_and_opposite_retry(
    permission_server,
) -> None:
    port, broker, _ = permission_server
    turn_id = "turn_" + "f" * 32
    worker, _outcome = _start_pending_prompt(broker, turn_id=turn_id)
    item = _pending(port)[2]["items"][0]
    path = f"/api/v1/permissions/{item['permissionId']}/decision"

    status, _, payload = _request(
        port,
        "POST",
        path,
        body=json.dumps({"turnId": turn_id, "decision": "allow_once"}).encode(),
        headers={
            "Content-Type": "application/json",
            "Origin": "https://attacker.example",
        },
    )
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"

    mismatch_status, _, mismatch = _request(
        port,
        "POST",
        path,
        body=json.dumps(
            {"turnId": "turn_" + "0" * 32, "decision": "allow_once"}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert mismatch_status == 409
    assert mismatch["error"]["code"] == "permission_turn_mismatch"

    deny_status, _, deny = _request(
        port,
        "POST",
        path,
        body=json.dumps({"turnId": turn_id, "decision": "deny_once"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert deny_status == 200
    assert deny["status"] == "denied"
    opposite_status, _, opposite = _request(
        port,
        "POST",
        path,
        body=json.dumps({"turnId": turn_id, "decision": "allow_once"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert opposite_status == 409
    assert opposite["error"]["code"] == "permission_already_decided"
    worker.join(timeout=1)


def test_permission_http_body_limit_query_and_unknown_routes(permission_server) -> None:
    port, _broker, _workspace = permission_server
    status, _, pending_error = _request(
        port, "GET", "/api/v1/permissions/pending?cursor=1"
    )
    assert status == 400
    assert pending_error["error"]["code"] == "invalid_request"

    status, _, oversized = _request(
        port,
        "POST",
        "/api/v1/permissions/permission_" + "2" * 32 + "/decision",
        body=b"{" + b" " * 1_024 + b"}",
        headers={"Content-Type": "application/json"},
    )
    assert status == 400
    assert oversized["error"]["code"] == "invalid_request"

    unknown_status, _, unknown = _request(
        port, "POST", "/api/v1/permissions/not-a-permission/decision", body=b"{}"
    )
    assert unknown_status == 404
    assert unknown["error"]["code"] == "not_found"


def test_loopback_host_gate_is_fail_closed() -> None:
    assert is_loopback_gateway_host("127.0.0.1") is True
    assert is_loopback_gateway_host("::1") is True
    assert is_loopback_gateway_host("localhost") is True
    assert is_loopback_gateway_host("0.0.0.0") is False
    assert is_loopback_gateway_host("example.invalid") is False


def test_fallback_http_change_feed_observes_the_same_permission_broker(
    permission_server,
) -> None:
    port, broker, workspace = permission_server
    before_status, _, before = _request(port, "GET", "/api/v1/changes")
    assert before_status == 200
    assert before["schemaVersion"] == 2
    assert before["resources"]["permissions"]["status"] == "live"
    before_revision = before["resources"]["permissions"]["revision"]

    turn_id = "turn_" + "8" * 32
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    worker = threading.Thread(
        target=lambda: manager.ensure_edit(
            str(workspace / "fallback.txt"),
            "--- a/fallback.txt\n+++ b/fallback.txt\n@@ -0,0 +1 @@\n+safe",
        )
    )
    worker.start()
    item = None
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        pending_status, _, pending = _pending(port)
        assert pending_status == 200
        if pending["items"]:
            item = pending["items"][0]
            break
        time.sleep(0.002)
    assert item is not None
    requested_status, _, requested = _request(port, "GET", "/api/v1/changes")
    assert requested_status == 200
    requested_revision = requested["resources"]["permissions"]["revision"]
    assert requested_revision != before_revision

    decision_status, _, decision = _request(
        port,
        "POST",
        f"/api/v1/permissions/{item['permissionId']}/decision",
        body=json.dumps({"turnId": turn_id, "decision": "allow_once"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert decision_status == 200
    assert decision["status"] == "allowed"
    worker.join(timeout=1)
    assert not worker.is_alive()
    decided_status, _, decided = _request(port, "GET", "/api/v1/changes")
    assert decided_status == 200
    assert decided["resources"]["permissions"]["revision"] != requested_revision
    session.close()


def test_pending_http_redacts_sensitive_command_and_local_path_and_refuses_allow(
    permission_server,
) -> None:
    port, broker, workspace = permission_server
    outside = workspace.parent / "outside"
    outside.mkdir()
    turn_id = "turn_" + "7" * 32
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(workspace), prompt=session.prompt)
    outcome: dict[str, object] = {}
    sensitive_marker = "http-sensitive-marker"

    def check() -> None:
        session.tool_started("run_command")
        try:
            manager.ensure_command(
                "tool",
                [
                    "--password",
                    sensitive_marker,
                    f"--output={outside / 'result.txt'}",
                ],
                str(workspace),
                force_prompt_reason="Command review requested.",
            )
        except BaseException as error:  # noqa: BLE001 - expected deny result
            outcome["error"] = error
        finally:
            session.tool_finished("run_command")

    worker = threading.Thread(target=check)
    worker.start()
    item: dict[str, object] | None = None
    try:
        status, headers, payload = _pending(port)
        serialized = json.dumps(payload, ensure_ascii=False)
        assert status == 200
        assert headers["Cache-Control"] == "no-store"
        assert "Access-Control-Allow-Origin" not in headers
        item = payload["items"][0]
        assert item["reviewable"] is False
        assert item["choices"] == ["deny_once"]
        assert sensitive_marker not in serialized
        assert str(outside) not in serialized
        path = f"/api/v1/permissions/{item['permissionId']}/decision"
        allow_status, _, allow_payload = _request(
            port,
            "POST",
            path,
            body=json.dumps(
                {"turnId": turn_id, "decision": "allow_once"}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert allow_status == 409
        assert allow_payload["error"]["code"] == "permission_not_reviewable"
        deny_status, _, deny_payload = _request(
            port,
            "POST",
            path,
            body=json.dumps(
                {"turnId": turn_id, "decision": "deny_once"}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert deny_status == 200
        assert deny_payload["status"] == "denied"
        assert sensitive_marker not in json.dumps(deny_payload)
    finally:
        if item is not None:
            try:
                broker.decide(
                    permission_id=str(item["permissionId"]),
                    turn_id=turn_id,
                    decision="deny_once",
                )
            except Exception:
                pass
        worker.join(timeout=1)
    assert not worker.is_alive()
    assert isinstance(outcome.get("error"), RuntimeError)
