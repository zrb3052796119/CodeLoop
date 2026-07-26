from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest

import minicode.memory as memory_mod
from minicode.gateway import MiniCodeGatewayHandler
from minicode.memory import MemoryApprovalPolicy, MemoryManager, MemoryScope
from minicode.memory_approval import MemoryApprovalAuthority
from minicode.web.read_model import DashboardReadModel


@contextmanager
def _gateway(workspace: Path) -> Iterator[tuple[str, int]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.dashboard_read_model = DashboardReadModel(workspace)
    server.memory_approval_authority = MemoryApprovalAuthority(workspace)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _request(
    address: tuple[str, int],
    method: str,
    path: str,
    body: bytes | None = None,
    headers_override: dict[str, str] | None = None,
):
    status, _, payload = _request_full(
        address,
        method,
        path,
        body=body,
        headers_override=headers_override,
    )
    return status, payload


def _request_full(
    address: tuple[str, int],
    method: str,
    path: str,
    body: bytes | None = None,
    headers_override: dict[str, str] | None = None,
):
    connection = http.client.HTTPConnection(*address, timeout=5)
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    headers.update(headers_override or {})
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    response_headers = dict(response.getheaders())
    payload = json.loads(response.read().decode("utf-8"))
    connection.close()
    return response.status, response_headers, payload


def test_real_gateway_exposes_versioned_memory_approval_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with _gateway(workspace) as address:
        status, pending = _request(
            address,
            "GET",
            "/api/v1/memory/approvals/pending",
        )
        post_status, decision = _request(
            address,
            "POST",
            "/api/v1/memory/approvals/project-missing/decision",
            json.dumps(
                {
                    "decision": "approve",
                    "reviewRevision": "memoryreviewrev_" + "a" * 64,
                }
            ).encode("utf-8"),
        )

    assert status == 200
    assert pending["schemaVersion"] == 1
    assert post_status == 404
    assert decision["error"]["code"] == "memory_approval_not_found"


def test_real_gateway_approves_exact_review_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "note",
        "Use exact review fencing",
        source="reflection",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    with _gateway(workspace) as address:
        status, pending = _request(address, "GET", "/api/v1/memory/approvals/pending")
        item = pending["items"][0]
        accepted_status, accepted = _request(
            address,
            "POST",
            f"/api/v1/memory/approvals/{entry.id}/decision",
            json.dumps(
                {"decision": "approve", "reviewRevision": item["reviewRevision"]}
            ).encode("utf-8"),
        )
        retry_status, retry = _request(
            address,
            "POST",
            f"/api/v1/memory/approvals/{entry.id}/decision",
            json.dumps(
                {"decision": "approve", "reviewRevision": item["reviewRevision"]}
            ).encode("utf-8"),
        )

    assert status == 200
    assert accepted_status == 200
    assert accepted["decisionAccepted"] is True
    assert accepted["status"] == "approved"
    assert retry_status == 200
    assert retry["decisionAccepted"] is False


def test_real_gateway_returns_409_for_stale_review_without_mutating_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "note",
        "Review the first Gateway content",
        source="reflection",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    with _gateway(workspace) as address:
        _, pending = _request(address, "GET", "/api/v1/memory/approvals/pending")
        stale_revision = pending["items"][0]["reviewRevision"]
        changed = "Review the changed Gateway content"
        assert manager.update_entry(MemoryScope.PROJECT, entry.id, changed)
        status, payload = _request(
            address,
            "POST",
            f"/api/v1/memory/approvals/{entry.id}/decision",
            json.dumps(
                {"decision": "reject", "reviewRevision": stale_revision}
            ).encode("utf-8"),
        )

    assert status == 409
    assert payload["error"]["code"] == "memory_review_stale"
    current = MemoryManager(project_root=workspace).memories[
        MemoryScope.PROJECT
    ]._id_index[entry.id]
    assert current.content == changed
    assert current.approval_status == "pending"


@pytest.mark.parametrize(
    ("path", "body", "headers", "code"),
    [
        (
            "/api/v1/memory/approvals/project-x/decision?workspace=/tmp/other",
            b'{"decision":"reject","reviewRevision":"memoryreviewrev_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
            {},
            "invalid_request",
        ),
        (
            "/api/v1/memory/approvals/%2e%2e%2fproject-x/decision",
            b'{"decision":"reject","reviewRevision":"memoryreviewrev_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
            {},
            "invalid_memory_id",
        ),
        (
            "/api/v1/memory/approvals/project-x/decision",
            b'{"decision":"reject","decision":"approve","reviewRevision":"memoryreviewrev_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
            {},
            "invalid_request",
        ),
        (
            "/api/v1/memory/approvals/project-x/decision",
            b'{"decision":"reject","reviewRevision":"memoryreviewrev_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","extra":true}',
            {},
            "invalid_request",
        ),
        (
            "/api/v1/memory/approvals/project-x/decision",
            b"{}",
            {"Content-Type": "text/plain"},
            "invalid_request",
        ),
        (
            "/api/v1/memory/approvals/project-x/decision",
            b"{}",
            {"Accept": "text/html"},
            "invalid_request",
        ),
    ],
)
def test_memory_approval_http_rejects_ambiguous_or_unsafe_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    body: bytes,
    headers: dict[str, str],
    code: str,
) -> None:
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with _gateway(workspace) as address:
        status, payload = _request(
            address, "POST", path, body, headers_override=headers
        )

    assert status == 400
    assert payload == {
        "ok": False,
        "error": {
            "code": code,
            "message": payload["error"]["message"],
        },
    }
    assert str(tmp_path) not in json.dumps(payload)


@pytest.mark.parametrize(
    ("body", "headers"),
    [
        (b"{" + b" " * 1_024 + b"}", {}),
        (b"{}", {"Content-Type": "application/json; charset=latin-1"}),
        (b"{}", {"Content-Type": "application/json; profile=strict"}),
        (b"{}", {"Accept": "application/json;q=0"}),
        (b"{}", {"Accept": "application/json;profile=x,*/*"}),
        (
            b'{"decision":true,"reviewRevision":"memoryreviewrev_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
            {},
        ),
        (
            b'{"decision":"reject","reviewRevision":true}',
            {},
        ),
    ],
)
def test_memory_approval_http_enforces_body_mime_accept_and_types(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    headers: dict[str, str],
) -> None:
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with _gateway(workspace) as address:
        status, payload = _request(
            address,
            "POST",
            "/api/v1/memory/approvals/project-x/decision",
            body,
            headers_override=headers,
        )

    assert status == 400
    assert payload["error"]["code"] in {
        "invalid_request",
        "invalid_decision",
        "invalid_review_revision",
    }


def test_memory_approval_http_is_same_origin_no_store_and_has_no_cors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "note",
        "Approve through a same-origin memory contract",
        source="reflection",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    with _gateway(workspace) as address:
        get_status, get_headers, pending = _request_full(
            address,
            "GET",
            "/api/v1/memory/approvals/pending",
        )
        item = pending["items"][0]
        body = json.dumps(
            {"decision": "approve", "reviewRevision": item["reviewRevision"]}
        ).encode("utf-8")
        blocked_status, blocked = _request(
            address,
            "POST",
            f"/api/v1/memory/approvals/{entry.id}/decision",
            body,
            headers_override={"Origin": "https://attacker.example"},
        )
        accepted_status, accepted_headers, accepted = _request_full(
            address,
            "POST",
            f"/api/v1/memory/approvals/{entry.id}/decision",
            body,
            headers_override={"Origin": f"http://{address[0]}:{address[1]}"},
        )

    assert get_status == 200
    assert get_headers["Cache-Control"] == "no-store"
    assert get_headers["Content-Type"] == "application/json; charset=utf-8"
    assert "Access-Control-Allow-Origin" not in get_headers
    assert blocked_status == 400
    assert blocked["error"]["code"] == "invalid_request"
    assert accepted_status == 200
    assert accepted["decisionAccepted"] is True
    assert accepted_headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in accepted_headers


@pytest.mark.parametrize(
    "encoded_id",
    ["%2e%2e", "%2fetc", "%5csecret", "project-x%2fother"],
)
def test_memory_approval_http_rejects_encoded_path_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    encoded_id: str,
) -> None:
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    body = json.dumps(
        {
            "decision": "reject",
            "reviewRevision": "memoryreviewrev_" + "a" * 64,
        }
    ).encode("utf-8")
    with _gateway(workspace) as address:
        status, payload = _request(
            address,
            "POST",
            f"/api/v1/memory/approvals/{encoded_id}/decision",
            body,
        )

    assert status == 400
    assert payload["error"]["code"] == "invalid_memory_id"


def test_unknown_memory_approval_api_route_is_structured_404(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with _gateway(workspace) as address:
        status, payload = _request(
            address,
            "POST",
            "/api/v1/memory/approvals/not-a-route",
            b"{}",
        )

    assert status == 404
    assert payload["error"]["code"] == "not_found"
