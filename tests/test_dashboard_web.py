from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from minicode.gateway import MiniCodeGatewayHandler
from minicode.mcp_observation import mcp_server_key
from minicode.run_journal import RunJournal
from minicode.session import SessionMetadata
from minicode.skills import SkillSummary
from minicode.web.read_model import DashboardReadModel
from tests.node_harness import run_node

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def gateway_port() -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def get(gateway_port: int, path: str) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", gateway_port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def post_bytes(
    gateway_port: int,
    path: str,
    body: bytes,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", gateway_port, timeout=5)
    try:
        connection.request(
            "POST",
            path,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def post_declared_length(
    gateway_port: int,
    path: str,
    content_length: int | str,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", gateway_port, timeout=5)
    try:
        connection.putrequest("POST", path)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(content_length))
        connection.endheaders()
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_dashboard_root_serves_the_shell_with_session_backed_chat_dock(
    gateway_port: int,
) -> None:
    status, headers, body = get(gateway_port, "/")
    html = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert "CodeLoop · Dashboard" in html
    assert "/assets/styles.css" in html
    assert "/assets/cost-format.js" in html
    assert "/assets/app.js" in html
    assert "read-only · loading snapshot" in html
    assert "CodeLoop Session 对话" in html
    assert "发送消息给 CodeLoop" in html
    assert 'id="dock-new"' in html


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/assets/styles.css", "text/css; charset=utf-8"),
        ("/assets/cost-format.js", "text/javascript; charset=utf-8"),
        ("/assets/app.js", "text/javascript; charset=utf-8"),
    ],
)
def test_dashboard_assets_have_explicit_types_and_no_cache(
    gateway_port: int,
    path: str,
    content_type: str,
) -> None:
    status, headers, body = get(gateway_port, path)

    assert status == 200
    assert headers["Content-Type"] == content_type
    assert headers["Cache-Control"] == "no-store"
    assert body


def test_dashboard_master_detail_views_stack_at_standard_desktop_width(
    gateway_port: int,
) -> None:
    status, _, body = get(gateway_port, "/assets/styles.css")
    stylesheet = body.decode("utf-8")

    assert status == 200
    assert "@media (max-width: 1400px)" in stylesheet
    assert (
        ".sessions-master-detail, .runs-master-detail { grid-template-columns: 1fr; }"
        in stylesheet
    )


def test_dashboard_javascript_is_generic_mock_data(gateway_port: int) -> None:
    status, _, body = get(gateway_port, "/assets/app.js")
    javascript = body.decode("utf-8")

    assert status == 200
    assert "mock-workspace · data not connected" in javascript
    assert "/Users/" not in javascript
    assert "fetch('/api/v1/snapshot'" in javascript


def test_cost_formatter_preserves_exact_nano_usd_strings_in_javascript() -> None:
    formatter = ROOT / "minicode/web/static/assets/cost-format.js"
    script = (
        "const { formatNanoUsd } = require(process.argv[1]);"
        "const values = ['0', '1', '300', '530000', '10000000', "
        "'123456789012345678901234'];"
        "console.log(JSON.stringify(values.map(formatNanoUsd)));"
    )

    completed = run_node(script, formatter)

    assert json.loads(completed.stdout) == [
        "$0.000000",
        "$0.000000001",
        "$0.000000300",
        "$0.000530",
        "$0.01",
        "$123,456,789,012,345.678901234",
    ]


def test_cost_formatter_rejects_noncanonical_or_unsafe_values() -> None:
    formatter = ROOT / "minicode/web/static/assets/cost-format.js"
    script = (
        "const { formatNanoUsd } = require(process.argv[1]);"
        "const values = [null, 530000, -1, '1.5', '-1', '', '01', "
        "'100000000000000000000000000000000000000000000000001'];"
        "console.log(JSON.stringify(values.map(formatNanoUsd)));"
    )

    completed = run_node(script, formatter)

    assert json.loads(completed.stdout) == ["—"] * 8


def test_versioned_health_alias_preserves_gateway_health_contract(gateway_port: int) -> None:
    status, headers, body = get(gateway_port, "/api/v1/health")

    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert json.loads(body) == {"ok": True, "service": "minicode-gateway"}


def test_change_feed_route_is_strict_read_only_json_and_no_store(
    gateway_port: int,
) -> None:
    status, headers, body = get(gateway_port, "/api/v1/changes")
    payload = json.loads(body)

    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert payload["schemaVersion"] == 2
    assert payload["mode"] == "read-only"
    assert payload["pollAfterMs"] == 2000
    assert set(payload["resources"]) == {
        "runs",
        "sessions",
        "turns",
        "memory",
        "skills",
        "connections",
        "permissions",
    }


def test_change_feed_route_rejects_all_query_parameters(gateway_port: int) -> None:
    status, _, body = get(gateway_port, "/api/v1/changes?cursor=secret")

    assert status == 400
    assert json.loads(body) == {
        "ok": False,
        "error": {
            "code": "invalid_query",
            "message": "Query parameters are invalid.",
        },
    }


def test_change_feed_route_contains_unexpected_failure_without_source_text() -> None:
    class BrokenChangeFeed:
        def snapshot(self) -> dict[str, object]:
            raise RuntimeError("Bearer secret /Users/private/workspace")

    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.dashboard_change_feed = BrokenChangeFeed()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = get(server.server_address[1], "/api/v1/changes")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 500
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(body) == {
        "ok": False,
        "error": {
            "code": "changes_failed",
            "message": "Change data could not be generated.",
        },
    }


def test_unknown_api_route_returns_structured_json_404(gateway_port: int) -> None:
    status, headers, body = get(gateway_port, "/api/v1/not-real?source=test")
    payload = json.loads(body)

    assert status == 404
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert payload == {
        "ok": False,
        "error": {
            "code": "not_found",
            "message": "API route not found",
            "path": "/api/v1/not-real",
        },
    }


def test_unknown_post_api_route_returns_structured_json_404(gateway_port: int) -> None:
    status, headers, body = post_bytes(gateway_port, "/api/v1/not-real", b"{}")
    payload = json.loads(body)

    assert status == 404
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert payload["error"]["code"] == "not_found"
    assert payload["error"]["path"] == "/api/v1/not-real"


@pytest.mark.parametrize(
    "path",
    [
        "/assets/../gateway.py",
        "/assets/%2e%2e/gateway.py",
        "/assets/%2Fetc/passwd",
        "/assets/%5c..%5cgateway.py",
    ],
)
def test_dashboard_asset_traversal_is_rejected(gateway_port: int, path: str) -> None:
    status, headers, body = get(gateway_port, path)

    assert status in {400, 404}
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert json.loads(body)["ok"] is False


def test_gateway_run_rejects_an_oversized_request_body(gateway_port: int) -> None:
    status, headers, body = post_declared_length(gateway_port, "/run", 1_048_577)

    assert status == 413
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert json.loads(body) == {"ok": False, "error": "request body too large"}


@pytest.mark.parametrize("content_length", ["invalid", -1])
def test_gateway_run_rejects_an_invalid_content_length(
    gateway_port: int,
    content_length: int | str,
) -> None:
    status, headers, body = post_declared_length(gateway_port, "/run", content_length)

    assert status == 400
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert json.loads(body) == {"ok": False, "error": "invalid content length"}


def _post_with_headers(
    gateway_port: int,
    path: str,
    body: bytes,
    headers: dict[str, str],
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", gateway_port, timeout=5)
    try:
        connection.putrequest("POST", path, skip_host="Host" in headers)
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.putheader("Content-Length", str(len(body)))
        connection.endheaders()
        connection.send(body)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_gateway_run_rejects_non_json_content_type(gateway_port: int) -> None:
    """text/plain form posts (CSRF-capable without preflight) must never
    reach the agent."""
    status, headers, body = _post_with_headers(
        gateway_port,
        "/run",
        b'{"prompt": "hello"}',
        {"Content-Type": "text/plain"},
    )

    assert status == 415
    assert json.loads(body)["ok"] is False


def test_gateway_run_rejects_foreign_host_header(gateway_port: int) -> None:
    """DNS-rebinding defence: a non-local Host header is rejected even on a
    loopback bind."""
    status, _headers, body = _post_with_headers(
        gateway_port,
        "/run",
        b'{"prompt": "hello"}',
        {"Content-Type": "application/json", "Host": "evil.example"},
    )

    assert status == 403
    assert json.loads(body)["ok"] is False


def test_gateway_run_rejects_cross_origin_requests(gateway_port: int) -> None:
    status, _headers, body = _post_with_headers(
        gateway_port,
        "/run",
        b'{"prompt": "hello"}',
        {"Content-Type": "application/json", "Origin": "http://evil.example"},
    )

    assert status == 403
    assert json.loads(body)["ok"] is False


def test_gateway_chat_turns_rejects_cross_origin_requests(gateway_port: int) -> None:
    status, _headers, body = _post_with_headers(
        gateway_port,
        "/api/v1/chat/turns",
        b'{"message": "hello"}',
        {"Content-Type": "application/json", "Origin": "http://evil.example"},
    )

    assert status == 403
    assert json.loads(body)["ok"] is False


def test_snapshot_endpoint_returns_the_injected_versioned_read_model(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = DashboardReadModel(
        workspace=workspace,
        data_dir=tmp_path / "home" / ".mini-code",
        session_loader=lambda: [],
        skill_loader=lambda _workspace: [],
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.dashboard_read_model = model
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = get(server.server_address[1], "/api/v1/snapshot")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    payload = json.loads(body)
    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert payload["schemaVersion"] == 1
    assert payload["workspace"]["name"] == "workspace"
    assert payload["overview"]["runs"]["count"] == 0


def test_ops_endpoint_is_read_only_no_store_and_rejects_all_query_parameters(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.dashboard_read_model = DashboardReadModel(
        workspace=workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = get(server.server_address[1], "/api/v1/ops")
        bad_status, bad_headers, bad_body = get(
            server.server_address[1], "/api/v1/ops?workspace=other"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    payload = json.loads(body)
    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert payload["schemaVersion"] == 1
    assert payload["mode"] == "read-only"
    assert payload["coverage"]["cost"] == "unavailable"
    assert payload["cost"]["status"] == "unavailable"
    assert payload["cost"]["value"] is None
    assert payload["cost"]["coverage"]["completedCalls"] == 0
    assert bad_status == 400
    assert bad_headers["Cache-Control"] == "no-store"
    assert json.loads(bad_body) == {
        "ok": False,
        "error": {
            "code": "invalid_query",
            "message": "Query parameters are invalid.",
        },
    }


def test_snapshot_http_response_never_contains_source_secrets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    memory_dir = data_dir / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "memory.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "user-secret",
                        "scope": "user",
                        "content": "Bearer very-secret-token",
                        "category": "password=hidden-value",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "secret-server": {"env": {"API_KEY": "sk-test-secret"}}
                }
            }
        ),
        encoding="utf-8",
    )
    sessions = [
        SessionMetadata(
            session_id="secret-session",
            created_at=1.0,
            updated_at=2.0,
            workspace=str(workspace),
            first_message="Session transcript password=hidden-value",
        )
    ]
    skills = [
        SkillSummary(
            name="secret-skill",
            description="sk-test-secret skill body",
            path=str(tmp_path / "secret" / "SKILL.md"),
            source="project",
        )
    ]
    model = DashboardReadModel(
        workspace=workspace,
        data_dir=data_dir,
        session_loader=lambda: sessions,
        skill_loader=lambda _workspace: skills,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.dashboard_read_model = model
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, body = get(server.server_address[1], "/api/v1/snapshot")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    response_text = body.decode("utf-8")
    for secret in ("sk-test-secret", "very-secret-token", "hidden-value"):
        assert secret not in response_text
    payload = json.loads(response_text)
    assert payload["overview"]["sessions"]["count"] == 1
    assert payload["overview"]["memory"]["totalCount"] == 1
    assert payload["overview"]["skills"]["count"] == 1


def test_connections_http_exposes_current_unavailable_without_loader_and_history(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    data_dir.mkdir(parents=True)
    (data_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "safe-server": {
                        "command": "Authorization=command-secret",
                        "args": ["Bearer args-secret"],
                        "env": {"TOKEN": "sk-env-secret"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(title="MCP HTTP", source="gateway")
    journal.transition(run.id, "running")
    event = journal.append_event(
        run.id,
        "mcp.runtime.observed",
        payload={
            "mcpVersion": 1,
            "serverKey": mcp_server_key(workspace, "safe-server"),
            "transport": "stdio",
            "activity": "tool_request",
            "outcome": "request_succeeded",
            "connectionAttempted": False,
            "protocol": "content-length",
        },
    )
    journal.transition(run.id, "completed")
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.dashboard_read_model = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = get(
            server.server_address[1], "/api/v1/connections"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    payload = json.loads(body)
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert payload["schemaVersion"] == 1
    assert payload["mode"] == "read-only"
    assert payload["summary"]["liveMcpCount"] is None
    assert payload["summary"]["registeredConfiguredMcpCount"] is None
    assert payload["summary"]["activeMcpInstanceCount"] is None
    assert payload["summary"]["observedConfiguredCount"] == 1
    assert payload["mcpCurrent"]["status"] == "unavailable"
    assert payload["mcpCurrent"]["current"] == "unavailable"
    assert payload["mcpCurrent"]["byState"] is None
    assert payload["mcpServers"][0]["current"]["reason"] == "source_unavailable"
    assert payload["mcpRuntime"]["status"] == "stale"
    assert payload["mcpRuntime"]["current"] == "unavailable"
    assert payload["mcpRuntime"]["historical"] == "partial"
    assert payload["mcpRuntime"]["lastObservedAt"] == event.timestamp
    assert payload["coverage"] == {
        "scope": "retained-run-scoped-mcp-observations",
        "historical": "partial",
        "current": "unavailable",
        "runScanLimit": 100,
        "eventScanLimitPerRun": 1000,
        "retainedRuns": 1,
        "scannedRuns": 1,
        "limited": False,
    }
    assert payload["mcpServers"][0]["runtime"]["lastOutcome"] == "request_succeeded"
    assert payload["mcpServers"][0]["runtime"]["connectionAttempted"] is False
    encoded = json.dumps(payload)
    for forbidden in (
        mcp_server_key(workspace, "safe-server"),
        str(workspace),
        "command-secret",
        "args-secret",
        "env-secret",
        '"command"',
        '"args"',
        '"env"',
    ):
        assert forbidden not in encoded


def test_snapshot_endpoint_redacts_unexpected_failure_details() -> None:
    class FailingReadModel:
        def snapshot(self) -> dict[str, object]:
            raise RuntimeError("Bearer very-secret-token")

    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.dashboard_read_model = FailingReadModel()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, body = get(server.server_address[1], "/api/v1/snapshot")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 500
    assert json.loads(body) == {
        "ok": False,
        "error": {
            "code": "snapshot_failed",
            "message": "Dashboard snapshot could not be generated.",
        },
    }
    assert b"very-secret-token" not in body


def test_overview_uses_snapshot_usage_without_legacy_cost_mock_data() -> None:
    javascript = (ROOT / "minicode/web/static/assets/app.js").read_text(
        encoding="utf-8"
    )
    overview = javascript[
        javascript.index("  overview() {") : javascript.index("\n  runs(")
    ]

    assert "fetch('/api/v1/snapshot'" in javascript
    assert "loading" in javascript
    assert "partial" in javascript
    assert "refreshDashboardSnapshot" in javascript
    assert "Retained RunJournal" in overview
    assert "Historical coverage · partial" in overview
    assert "usage.inputTokens" in overview
    assert "usage.outputTokens" in overview
    assert "usage.durationMs" in overview
    assert "costMetricTile(usage.cost)" in overview
    assert "costMetricDetail(usage.cost)" in overview
    assert "snapshot.workspace.id" in overview
    assert "snapshot.workspace.path" not in overview
    assert "persisted observations only" in overview
    assert "DATA.summary" not in overview
    assert "money(s.cost)" not in overview
    assert "runCard(DATA.runs[0]" not in overview


def test_runs_and_ops_frontend_use_canonical_model_observation_stores() -> None:
    javascript = (ROOT / "minicode/web/static/assets/app.js").read_text(
        encoding="utf-8"
    )
    runs_view = javascript[
        javascript.index("  runs(") : javascript.index("\n  sessions(")
    ]
    ops_view = javascript[
        javascript.index("  ops(") : javascript.index("\n  system(")
    ]

    assert "const runsStore" in javascript
    assert "const runDetailStore" in javascript
    assert "const opsStore" in javascript
    assert "fetch(`/api/v1/runs?" in javascript
    assert "fetch(`/api/v1/runs/${encodeURIComponent(runId)}" in javascript
    assert "requestId !== runDetailStore.requestId" in javascript
    assert "runId !== runDetailStore.runId" in javascript
    assert "JSON.stringify(runsStore.filters)" in javascript
    assert "function loadMoreRuns" in javascript
    assert "function loadMoreRunEvents" in javascript
    assert "setRunStatusFilter" in runs_view
    assert "setRunSourceFilter" in runs_view
    assert "Runs 暂时不可用" in javascript
    assert "Context + WorkingMemory observation partial" in runs_view
    assert "historical partial" in runs_view
    assert "SSE 是主要失效通道" in runs_view
    assert "Change Feed 轮询仅在连接不可用时后备" in runs_view
    assert "slice(0, 12)" in javascript
    assert "lifecycle-model-usage-cost-tool-assistant-skill-memory-context · historical partial" in javascript
    assert "function runEventSummary" in javascript
    assert "event.type === 'model.started'" in javascript
    assert "event.type === 'model.completed'" in javascript
    assert "event.type === 'model.failed'" in javascript
    assert "event.type === 'model.costed'" in javascript
    assert "event.type === 'task.outcome'" in javascript
    assert "event.type === 'skill.routed'" in javascript
    assert "event.type === 'skill.loaded'" in javascript
    assert "event.type === 'skill.attributed'" in javascript
    assert "event.type === 'memory.retrieved'" in javascript
    assert "event.type === 'memory.rendered'" in javascript
    assert "event.type === 'context.compacted'" in javascript
    assert "event.type === 'recovery.started'" in javascript
    assert "event.type === 'recovery.completed'" in javascript
    assert "event.type === 'working_memory.observed'" in javascript
    assert "event.type === 'mcp.runtime.observed'" in javascript
    assert "MCP request succeeded" in javascript
    assert "existing connection observed" in javascript
    assert "currently connected" not in javascript
    assert "event.details?.resultType" in javascript
    assert "event.details?.toolCallCount" in javascript
    assert "event.details?.failureKind" in javascript
    assert "event.details?.toolName" in javascript
    assert "event.details?.outcome" in javascript
    assert "event.details?.contentLength" in javascript
    assert "event.details?.qualifiedName" in javascript
    assert "event.details?.contentDigest" in javascript
    assert "event.details?.loadedSkillCount" in javascript
    assert "event.details?.outcomeStatus" in javascript
    assert "event.details?.errorsRecovered" in javascript
    assert "event.details?.strategy" in javascript
    assert "event.details?.trigger" in javascript
    assert "event.details?.messagesBefore" in javascript
    assert "event.details?.protectedTokens" in javascript
    assert "event.details?.contextOperationId" not in javascript
    assert "assistant.completed" in javascript
    assert "Tool input/output is never displayed" in runs_view
    assert "event.payload" not in javascript
    assert "event.details?.prompt" not in javascript
    assert "event.details?.output" not in javascript
    assert "event.details?.usage" in javascript
    assert "event.details?.durationMs" in javascript
    assert "Provider" in javascript
    assert "Estimated" in javascript
    assert "Unavailable" in javascript
    assert "cost" in runs_view
    assert "tokens" in runs_view
    assert "toolCalls" in runs_view
    assert "errors" in runs_view
    assert "unavailable" in runs_view
    assert "DATA.runs" not in runs_view
    assert "DATA.runSteps" not in runs_view
    assert "money(" not in runs_view
    assert "tokens(" not in runs_view
    assert "cancel" not in runs_view.lower()
    assert "retry run" not in runs_view.lower()
    assert "delete" not in runs_view.lower()
    assert "loadRuns(false)" in javascript
    assert "loadOps" in javascript
    assert "refreshOps" in ops_view
    assert "fetch('/api/v1/ops'" in javascript
    assert "requestId !== opsStore.requestId" in javascript
    assert "Retained RunJournal" in ops_view
    assert "historical partial" in ops_view
    assert "data.usage.provider" in ops_view
    assert "data.usage.estimated" in ops_view
    assert "data.usage.combined" in ops_view
    assert "data.duration" in ops_view
    assert "data.cost" in ops_view
    assert "data.costBreakdown" in ops_view
    assert "renderCostBreakdown(data.cost, data.costBreakdown)" in ops_view
    assert "costMetricTile(data.cost)" in ops_view
    assert "Missing Cost · never shown as zero" in runs_view
    assert "SSE 实时失效" in ops_view
    assert "Change Feed 轮询后备" in ops_view
    assert "DATA.runs" not in ops_view
    assert "DATA.events" not in ops_view


def test_frontend_renders_canonical_tool_and_separate_failure_observations() -> None:
    javascript = (ROOT / "minicode/web/static/assets/app.js").read_text(
        encoding="utf-8"
    )
    overview = javascript[
        javascript.index("  overview() {") : javascript.index("\n  runs(")
    ]
    runs_view = javascript[
        javascript.index("  runs(") : javascript.index("\n  sessions(")
    ]
    ops_view = javascript[
        javascript.index("  ops(") : javascript.index("\n  system(")
    ]
    system_view = javascript[
        javascript.index("function renderSystemSummary") : javascript.index(
            "\nconst VIEWS"
        )
    ] + javascript[
        javascript.index("  system(") : javascript.index(
            "\n};", javascript.index("  system(")
        )
    ]

    assert "function toolMetricTile" in javascript
    assert "function failureMetricTile" in javascript
    assert "function renderToolBreakdown" in javascript
    assert "function renderFailureBreakdown" in javascript
    assert "function runToolSummary" in javascript
    assert "function runFailureSummary" in javascript
    assert "toolMetricTile(usage.tools)" in overview
    assert "failureMetricTile(usage.failures)" in overview
    assert "runToolSummary(run.tools)" in javascript
    assert "runFailureSummary(run.failures)" in javascript
    assert "Observed Tool calls" in ops_view
    assert "Tool errors" in ops_view
    assert "Runs with observed failures" in ops_view
    assert "renderToolBreakdown(data.tools, data.toolBreakdown)" in ops_view
    assert "renderFailureBreakdown(data.failures, data.failureBreakdown)" in ops_view
    assert "item.completedCalls" in javascript
    assert "Model attempt failures" in javascript
    assert "Tool input/output is never displayed" in runs_view
    assert "Tool 与 Failure 聚合" in system_view
    assert "totalErrors" not in javascript
    assert "toolDuration" not in javascript
    assert "averageTool" not in javascript
    assert "setInterval(load" not in javascript


def test_cost_frontend_never_reprices_or_coerces_amounts_to_number() -> None:
    javascript = (ROOT / "minicode/web/static/assets/app.js").read_text(
        encoding="utf-8"
    )
    formatter = (ROOT / "minicode/web/static/assets/cost-format.js").read_text(
        encoding="utf-8"
    )

    assert "BigInt(value)" in formatter
    assert "Number(" not in formatter
    assert "parseFloat" not in formatter
    assert "parseInt" not in formatter
    assert "formatNanoUsd(run.cost?.amountNanoUsd)" in javascript
    assert "formatNanoUsd(metric?.value?.amountNanoUsd)" in javascript
    assert "token" not in javascript[javascript.index("function formatNanoUsd") : javascript.index("function formatUsageBuckets")].lower()
    assert "PricingCatalog" not in javascript
    assert "pricePerToken" not in javascript
    assert "tokens *" not in javascript
    assert "setInterval(tickMeta, 1000)" in javascript
    assert "setInterval(load" not in javascript


def test_page_read_routes_forward_bounded_query_parameters() -> None:
    class PageReadModel:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def sessions(self, *, limit=None, cursor=None):
            self.calls.append(("sessions", (limit, cursor)))
            return {
                "schemaVersion": 1,
                "generatedAt": "2026-07-16T12:00:00Z",
                "mode": "read-only",
                "source": {"status": "live", "updatedAt": None, "message": None},
                "items": [],
                "page": {"limit": 2, "hasMore": False, "nextCursor": None},
                "diagnostics": [],
            }

        def runs(self, **kwargs):
            self.calls.append(("runs", kwargs))
            return {
                "schemaVersion": 1,
                "generatedAt": "2026-07-16T12:00:00Z",
                "mode": "read-only",
                "source": {"status": "live", "updatedAt": None, "message": None},
                "coverage": {},
                "summary": {},
                "items": [],
                "page": {"limit": 2, "hasMore": False, "nextCursor": None},
                "filters": {},
                "diagnostics": [],
            }

        def run_detail(self, run_id, *, limit=None, cursor=None):
            self.calls.append(("run_detail", (run_id, limit, cursor)))
            return {
                "schemaVersion": 1,
                "generatedAt": "2026-07-16T12:00:00Z",
                "mode": "read-only",
                "source": {"status": "live", "updatedAt": None, "message": None},
                "coverage": {},
                "run": {"id": run_id},
                "events": [],
                "page": {"limit": 3, "hasMore": False, "nextCursor": None},
                "metrics": {},
                "diagnostics": [],
            }

        def session_detail(self, session_id, *, limit=None, cursor=None):
            self.calls.append(("session_detail", (session_id, limit, cursor)))
            return {
                "schemaVersion": 1,
                "generatedAt": "2026-07-16T12:00:00Z",
                "mode": "read-only",
                "source": {"status": "live", "updatedAt": None, "message": None},
                "session": {"id": session_id},
                "messages": [],
                "page": {"limit": 3, "hasMore": False, "nextCursor": None},
                "diagnostics": [],
            }

        def memory(self, **kwargs):
            self.calls.append(("memory", kwargs))
            return {
                "schemaVersion": 1,
                "generatedAt": "2026-07-16T12:00:00Z",
                "mode": "read-only",
                "source": {"status": "live", "updatedAt": None, "message": None},
                "summary": {},
                "scopes": {},
                "items": [],
                "page": {"limit": 4, "hasMore": False, "nextCursor": None},
                "filters": {},
                "diagnostics": [],
            }

        def skills(self, **kwargs):
            self.calls.append(("skills", kwargs))
            return {
                "schemaVersion": 1,
                "generatedAt": "2026-07-16T12:00:00Z",
                "mode": "read-only",
                "source": {"status": "live", "updatedAt": None, "message": None},
                "summary": {},
                "items": [],
                "page": {"limit": 5, "hasMore": False, "nextCursor": None},
                "filters": {},
                "diagnostics": [],
            }

        def connections(self):
            self.calls.append(("connections", None))
            return {
                "schemaVersion": 1,
                "generatedAt": "2026-07-16T12:00:00Z",
                "mode": "read-only",
                "source": {"status": "stale", "updatedAt": None, "message": None},
                "summary": {},
                "gateway": {},
                "mcpServers": [],
                "diagnostics": [],
            }

        def system(self):
            self.calls.append(("system", None))
            return {
                "schemaVersion": 1,
                "generatedAt": "2026-07-16T12:00:00Z",
                "mode": "read-only",
                "source": {"status": "live", "updatedAt": None, "message": None},
                "application": {},
                "runtime": {},
                "workspace": {},
                "features": {},
                "storage": {},
                "diagnostics": [],
            }

    model = PageReadModel()
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.dashboard_read_model = model
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        responses = [
            get(
                port,
                "/api/v1/runs?status=completed&source=gateway&limit=2&cursor=runs-cursor",
            ),
            get(
                port,
                "/api/v1/runs/run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa?limit=3&cursor=event-cursor",
            ),
            get(port, "/api/v1/sessions?limit=2&cursor=abc"),
            get(port, "/api/v1/sessions/session_01?limit=3&cursor=def"),
            get(
                port,
                "/api/v1/memory?scope=project&tier=short_term&category=testing&limit=4&cursor=ghi",
            ),
            get(
                port,
                "/api/v1/skills?source=project&directory=engineering&limit=5&cursor=jkl",
            ),
            get(port, "/api/v1/connections"),
            get(port, "/api/v1/system"),
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert [response[0] for response in responses] == [200] * 8
    assert all(
        response[1]["Content-Type"] == "application/json; charset=utf-8"
        and response[1]["Cache-Control"] == "no-store"
        for response in responses
    )
    assert model.calls == [
        (
            "runs",
            {
                "status": "completed",
                "source": "gateway",
                "limit": "2",
                "cursor": "runs-cursor",
            },
        ),
        (
            "run_detail",
            ("run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "3", "event-cursor"),
        ),
        ("sessions", ("2", "abc")),
        ("session_detail", ("session_01", "3", "def")),
        (
            "memory",
            {
                "scope": "project",
                "tier": "short_term",
                "category": "testing",
                "limit": "4",
                "cursor": "ghi",
            },
        ),
        (
            "skills",
            {
                "source": "project",
                "directory": "engineering",
                "limit": "5",
                "cursor": "jkl",
            },
        ),
        ("connections", None),
        ("system", None),
    ]


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/api/v1/runs?status=complete", 400, "invalid_status"),
        ("/api/v1/runs?source=web", 400, "invalid_source"),
        ("/api/v1/runs?limit=101", 400, "invalid_limit"),
        ("/api/v1/runs?workspace=/tmp/other", 400, "invalid_query"),
        ("/api/v1/runs?status=failed&status=completed", 400, "invalid_query"),
        ("/api/v1/runs/../secret", 400, "invalid_run_id"),
        (
            "/api/v1/runs/run_ffffffffffffffffffffffffffffffff",
            404,
            "run_not_found",
        ),
        ("/api/v1/sessions?limit=101", 400, "invalid_limit"),
        ("/api/v1/sessions?limit=1&limit=2", 400, "invalid_query"),
        ("/api/v1/sessions?workspace=/tmp/other", 400, "invalid_query"),
        ("/api/v1/sessions/../secret", 400, "invalid_session_id"),
        ("/api/v1/sessions/missing", 404, "session_not_found"),
        ("/api/v1/memory?scope=global", 400, "invalid_scope"),
        ("/api/v1/memory?category=../secret", 400, "invalid_category"),
        ("/api/v1/memory?unknown=value", 400, "invalid_query"),
        ("/api/v1/skills?source=compat", 400, "invalid_source"),
        ("/api/v1/skills?directory=../secret", 400, "invalid_directory"),
        ("/api/v1/skills?workspace=/tmp/other", 400, "invalid_query"),
        ("/api/v1/connections?live=true", 400, "invalid_query"),
        ("/api/v1/system?env=true", 400, "invalid_query"),
    ],
)
def test_page_read_routes_return_structured_request_errors(
    tmp_path: Path, path: str, status: int, code: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.dashboard_read_model = DashboardReadModel(
        workspace, data_dir=tmp_path / "home" / ".mini-code"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response_status, headers, body = get(server.server_address[1], path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    payload = json.loads(body)
    assert response_status == status
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert payload["ok"] is False
    assert payload["error"]["code"] == code


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/api/v1/runs", "runs_failed"),
        (
            "/api/v1/runs/run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "run_failed",
        ),
        ("/api/v1/sessions", "sessions_failed"),
        ("/api/v1/sessions/session_01", "session_failed"),
        ("/api/v1/memory", "memory_failed"),
        ("/api/v1/skills", "skills_failed"),
        ("/api/v1/connections", "connections_failed"),
        ("/api/v1/system", "system_failed"),
        ("/api/v1/ops", "ops_failed"),
    ],
)
def test_page_read_routes_redact_unexpected_failure_details(
    path: str, code: str
) -> None:
    class FailingReadModel:
        def runs(self, **_kwargs):
            raise RuntimeError("Bearer runs-secret")

        def run_detail(self, *_args, **_kwargs):
            raise RuntimeError("password=run-detail-secret")

        def sessions(self, **_kwargs):
            raise RuntimeError("Bearer very-secret-token")

        def session_detail(self, *_args, **_kwargs):
            raise RuntimeError("password=hidden-value")

        def memory(self, **_kwargs):
            raise RuntimeError("sk-test-secret")

        def skills(self, **_kwargs):
            raise RuntimeError("credential=skills-secret")

        def connections(self):
            raise RuntimeError("Authorization=connections-secret")

        def system(self):
            raise RuntimeError("Cookie=system-secret")

        def ops(self):
            raise RuntimeError("Bearer ops-secret")

    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.dashboard_read_model = FailingReadModel()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = get(server.server_address[1], path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 500
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert json.loads(body)["error"]["code"] == code
    for secret in (
        b"runs-secret",
        b"run-detail-secret",
        b"very-secret-token",
        b"hidden-value",
        b"sk-test-secret",
        b"skills-secret",
        b"connections-secret",
        b"system-secret",
    ):
        assert secret not in body


def test_sessions_and_memory_frontend_use_real_independent_read_stores() -> None:
    javascript = (ROOT / "minicode/web/static/assets/app.js").read_text(
        encoding="utf-8"
    )
    sessions_view = javascript[
        javascript.index("  sessions() {") : javascript.index("\n  memory(")
    ]
    memory_view = javascript[
        javascript.index("  memory(") : javascript.index("\n  skills(")
    ]

    assert "const sessionsStore" in javascript
    assert "const sessionDetailStore" in javascript
    assert "const memoryStore" in javascript
    assert "const memoryApprovalStore" in javascript
    assert "fetch('/api/v1/sessions" in javascript
    assert "fetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}" in javascript
    assert "fetch(`/api/v1/memory" in javascript
    assert "requestId !== sessionDetailStore.requestId" in javascript
    assert "sessionId !== sessionDetailStore.sessionId" in javascript
    assert "真实 Session 历史 · read-only" in sessions_view
    assert "DATA.sessions" not in sessions_view
    assert "DATA.memories" not in memory_view
    assert "DATA.memorySnapshot" not in memory_view
    assert "runtimeTraceState('retrieval')" in memory_view
    assert "runtimeTraceState('injection')" in memory_view
    assert "['approvals', '待审批'" in memory_view
    assert "renderMemoryApprovals()" in memory_view
    assert "只有 Project 条目提供严格确认删除；User / Local 无删除入口" in memory_view
    assert "read-write · persistent approval" in javascript
    assert "不会以持久化条目数量替代运行级事实" in memory_view
    assert "const runtimeTraceStore" in javascript
    assert "runtimeTraceStore.detailRequestId" in javascript
    assert "runId !== runtimeTraceStore.selectedRunId" in javascript
    assert "This Run has no observed Memory Retrieval event." in javascript
    assert "Candidates" in javascript
    assert "Controller mode" in javascript
    assert "WorkingMemoryTracker" in memory_view
    assert "process-local" in memory_view
    assert "latest retained process-local snapshot" in memory_view
    assert "not global" in memory_view
    assert "compaction-protection guarantee" in memory_view


def test_sessions_page_and_chat_dock_share_real_session_selection_store() -> None:
    html = (ROOT / "minicode/web/static/index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "minicode/web/static/assets/app.js").read_text(
        encoding="utf-8"
    )
    refresh_body = javascript[
        javascript.index("function refreshSessions()") : javascript.index(
            "\nfunction loadMoreSessions()"
        )
    ]
    storage_writes = [
        line.strip()
        for line in javascript.splitlines()
        if ".setItem(" in line
    ]

    assert "Session 对话 · read-write" in html
    assert "synchronous" in html
    assert "live refresh" in html
    assert "connection-scoped Assistant/Tool stream" in html
    assert "final Session authority" in html
    assert "loopback permission approval" in html
    assert 'id="permission-panel"' in html
    assert "no token streaming" not in html
    assert "发送消息给 CodeLoop" in html
    assert 'id="message"' in html and 'id="message" autocomplete="off"' in html
    assert 'id="chat-submit"' in html
    assert 'id="dock-new"' in html
    assert "模拟会话" not in html
    assert "DATA.sessions" not in javascript
    assert "openMockSession" not in javascript
    assert "模拟界面已收到" not in javascript
    assert "const SESSION_SELECTION_STORAGE_KEY" in javascript
    assert "const ACTIVE_TURN_STORAGE_KEY" in javascript
    assert "const TURN_ID_PATTERN" in javascript
    assert "sessionStorage.getItem" in javascript
    assert "sessionStorage.setItem" in javascript
    assert any("SESSION_SELECTION_STORAGE_KEY" in line for line in storage_writes)
    assert any("ACTIVE_TURN_STORAGE_KEY" in line for line in storage_writes)
    assert storage_writes[-1] == "localStorage.setItem(storageKey, value);"
    assert all(
        sensitive not in line
        for line in storage_writes
        for sensitive in ("draft", "message", "content")
    )
    assert "workspaceId" in javascript
    assert "selectionVersion" in javascript
    assert "reconcileSessionSelection" in javascript
    assert "renderConversationDock" in javascript
    assert "openRunSession" in javascript
    assert "查看 Session" in javascript
    assert "未关联 Session" in javascript
    assert "sessionDetailStore.sessionId = null" not in refresh_body
    assert "const chatStore" in javascript
    assert "requestGeneration" in javascript
    assert "operationGeneration" in javascript
    assert "activeTurnId" in javascript
    assert "recoveryChecked" in javascript
    assert "targetMode" in javascript
    assert "draft" in javascript
    assert "fetch('/api/v1/chat/turns'" in javascript
    assert "crypto.getRandomValues" in javascript
    assert "Math.random" not in javascript
    assert "turnId" in javascript
    assert "checkActiveTurnStatus" in javascript
    assert "reconcileActiveTurnOnce" in javascript
    assert "fetch(`/api/v1/chat/turns/${encodeURIComponent(turnId)}`" in javascript
    assert "resultAvailable" in javascript
    assert "if (chatStore.activeTurnId ||" in javascript
    assert "requestGeneration !== chatStore.requestGeneration" in javascript
    assert "session_conflict" in javascript
    assert "turn_id_conflict" in javascript
    assert "turn_in_progress" in javascript
    assert "turn_interrupted" in javascript
    assert "turn_cancelled" in javascript
    assert "runtime_unavailable" in javascript
    assert "turn_failed" in javascript
    assert "submitChatTurn" in javascript
    assert "cancelActiveTurn" in javascript
    assert "cancellationAccepted" in javascript
    assert "cancel_requested" in javascript
    assert "committing" in javascript
    assert "cancelled" in javascript
    assert "取消请求已记录；当前 Provider 或 Tool 调用可能需要完成后才能停止。" in javascript
    assert "结果正在提交，取消已无法抢占本轮完成。" in javascript
    assert "本轮已取消；不会自动重发。已经发生的 Tool 副作用无法回滚。" in javascript
    assert "已完成，但对应 Session 结果已不可用" in javascript
    assert "fetch(`/api/v1/chat/turns/${encodeURIComponent(turnId)}/cancel`" in javascript
    assert "newConversation" in javascript
    assert "refreshRuns()" in javascript
    assert "refreshDashboardSnapshot()" in javascript
    assert "refreshOps()" in javascript
    assert "fetch('/run" not in javascript
    assert "fetch(\"/run" not in javascript
    assert "setInterval(loadSessions" not in javascript
    assert "setInterval(refreshSessions" not in javascript
    assert "setInterval(submitChatTurn" not in javascript
    assert "setInterval(checkActiveTurnStatus" not in javascript
    assert "setInterval(reconcileActiveTurnOnce" not in javascript
    assert "setTimeout(checkActiveTurnStatus" not in javascript
    assert "setInterval(cancelActiveTurn" not in javascript
    assert "setTimeout(cancelActiveTurn" not in javascript
    assert javascript.count("new EventSource('/api/v1/events')") == 1
    assert "new WebSocket" not in javascript
    html = (ROOT / "minicode/web/static/index.html").read_text(encoding="utf-8")
    assert "synchronous request · SSE live refresh / invalidation · polling fallback" in html
    assert "connection-scoped Assistant/Tool stream" in html
    assert 'id="chat-cancel"' in html


def test_chat_active_turn_storage_contains_only_recovery_identity_fields() -> None:
    javascript = (ROOT / "minicode/web/static/assets/app.js").read_text(
        encoding="utf-8"
    )
    start = javascript.index("function persistActiveTurn")
    end = javascript.index("\nfunction ", start + 10)
    body = javascript[start:end]

    assert "workspaceId" in body
    assert "turnId" in body
    assert "targetSessionId" in body
    assert "version" in body
    for forbidden in ("message", "assistant", "content", "draft"):
        assert forbidden not in body


def test_chat_cancel_requested_and_committing_offer_manual_status_recovery() -> None:
    javascript = (ROOT / "minicode/web/static/assets/app.js").read_text(
        encoding="utf-8"
    )
    feedback_start = javascript.index("function chatFeedback()")
    feedback_end = javascript.index("\nfunction newConversation", feedback_start)
    feedback = javascript[feedback_start:feedback_end]

    assert (
        "['in_progress', 'recovery_error', 'error', 'cancel_requested', "
        "'committing'].includes(chatStore.phase)" in feedback
    )
    assert 'onclick="checkActiveTurnStatus()">检查状态</button>' in feedback
    assert "cancelActiveTurn()" not in feedback


def test_completed_chat_feedback_requires_one_explicit_content_free_action() -> None:
    javascript = (ROOT / "minicode/web/static/assets/app.js").read_text(
        encoding="utf-8"
    )

    assert "feedbackTurnId" in javascript
    assert "feedbackRunId" in javascript
    assert "feedbackSessionId" in javascript
    assert "function chatUserSignal()" in javascript
    assert "async function recordChatFeedback(signal)" in javascript
    assert (
        "fetch(`/api/v1/chat/turns/${encodeURIComponent(turnId)}/feedback`"
        in javascript
    )
    assert "body: JSON.stringify({ signal })" in javascript
    assert "接受结果" in javascript
    assert "需要纠正" in javascript
    assert "拒绝结果" in javascript
    assert "不会把沉默或后续消息当作接受" in javascript
    assert "setTimeout(recordChatFeedback" not in javascript
    assert "setInterval(recordChatFeedback" not in javascript


def test_completed_chat_feedback_ignores_stale_response_and_posts_exact_signal() -> None:
    javascript = (ROOT / "minicode/web/static/assets/app.js").read_text(
        encoding="utf-8"
    )
    functions = javascript[
        javascript.index("function resetChatFeedbackTarget") : javascript.index(
            "\nfunction newConversation"
        )
    ]
    harness = r"""
const assert = require('node:assert/strict');
const TURN_ID_PATTERN = /^turn_[0-9a-f]{32}$/;
const CHAT_STREAM_RUN_ID_PATTERN = /^run_[0-9a-f]{32}$/;
const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const turnA = 'turn_' + 'a'.repeat(32);
const turnB = 'turn_' + 'b'.repeat(32);
const runA = 'run_' + 'a'.repeat(32);
const runB = 'run_' + 'b'.repeat(32);
const chatStore = {
  targetMode: 'existing',
  feedbackTurnId: null,
  feedbackRunId: null,
  feedbackSessionId: null,
  feedbackPhase: 'idle',
  feedbackSignal: null,
  feedbackError: null,
  feedbackGeneration: 0,
};
const sessionDetailStore = { sessionId: 'session_a' };
let renderCalls = 0;
let requests = [];
function renderConversationDock() { renderCalls += 1; }
function esc(value) { return String(value); }
function response(payload, status = 200) {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => payload,
  };
}
function accepted(turnId, runId, signal) {
  return {
    ok: true,
    schemaVersion: 1,
    mode: 'read-write',
    turnId,
    runId,
    signal,
    source: 'explicit_user_action',
    recordedAt: '2026-07-19T10:00:02.000Z',
  };
}

(async () => {
  setCompletedFeedbackTarget({ turnId: turnA, runId: runA, sessionId: 'session_a' });
  let resolveFetch;
  globalThis.fetch = (url, options) => {
    requests.push({ url, options });
    return new Promise((resolve) => { resolveFetch = resolve; });
  };
  const stale = recordChatFeedback('accept');
  sessionDetailStore.sessionId = 'session_b';
  setCompletedFeedbackTarget({ turnId: turnB, runId: runB, sessionId: 'session_b' });
  resolveFetch(response(accepted(turnA, runA, 'accept')));
  await stale;
  assert.equal(chatStore.feedbackTurnId, turnB);
  assert.equal(chatStore.feedbackPhase, 'available');

  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return response(accepted(turnB, runB, 'correct'));
  };
  await recordChatFeedback('correct');
  assert.equal(chatStore.feedbackPhase, 'recorded');
  assert.equal(chatStore.feedbackSignal, 'correct');
  sessionDetailStore.sessionId = 'session_other';
  assert.equal(chatUserSignal(), '');
  const latest = requests.at(-1);
  assert.equal(latest.url, `/api/v1/chat/turns/${turnB}/feedback`);
  assert.deepEqual(JSON.parse(latest.options.body), { signal: 'correct' });
  assert.deepEqual(Object.keys(JSON.parse(latest.options.body)), ['signal']);
  assert.ok(renderCalls >= 3);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    run_node(functions + "\n" + harness)


def test_chat_manual_status_recovery_and_stale_response_behavior() -> None:
    javascript = (ROOT / "minicode/web/static/assets/app.js").read_text(
        encoding="utf-8"
    )
    functions = javascript[
        javascript.index("function validTurnStatus") : javascript.index(
            "\nasync function reconcileActiveTurnOnce"
        )
    ]
    harness = r"""
const assert = require('node:assert/strict');
const TURN_ID_PATTERN = /^turn_[0-9a-f]{32}$/;
const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const turnA = 'turn_' + 'a'.repeat(32);
const turnB = 'turn_' + 'b'.repeat(32);
const baseStatus = {
  ok: true,
  schemaVersion: 1,
  mode: 'read-only',
  sessionId: null,
  created: null,
  runId: null,
  createdAt: '2026-07-19T09:00:00.000Z',
  updatedAt: '2026-07-19T09:00:01.000Z',
  completedAt: null,
  errorCode: null,
  resultAvailable: false,
};
const chatStore = {
  activeTurnId: null,
  activeTargetSessionId: null,
  operationGeneration: 0,
  requestGeneration: 0,
  phase: 'idle',
  error: null,
  draft: 'retained draft',
  targetMode: 'new',
  lastSessionId: null,
  terminalTurnId: null,
  terminalPromise: null,
};
const chatStreamStore = { turnId: null };
const sessionDetailStore = {
  selectionVersion: 0,
  sessionId: null,
  data: null,
};
let renderCalls = 0;
let refreshCalls = 0;
function renderConversationDock() { renderCalls += 1; }
function fixedChatError(code) { return `safe:${code}`; }
function retirePermissionTurn() {}
function clearActiveTurn(turnId = null) {
  if (turnId === null || chatStore.activeTurnId === turnId) {
    chatStore.activeTurnId = null;
    chatStore.activeTargetSessionId = null;
  }
}
function persistSessionSelection() {}
async function refreshSessions() { refreshCalls += 1; }
async function refreshRuns() { refreshCalls += 1; }
async function refreshDashboardSnapshot() { refreshCalls += 1; }
async function refreshOps() { refreshCalls += 1; }
async function loadSessionDetail(sessionId) {
  refreshCalls += 1;
  sessionDetailStore.sessionId = sessionId;
  sessionDetailStore.data = { session: { id: sessionId } };
  return 'loaded';
}
function resetChatStreamState() {}
function setCompletedFeedbackTarget() {}
function response(payload, status = 200) {
  return { status, ok: status >= 200 && status < 300, json: async () => payload };
}

(async () => {
  chatStore.activeTurnId = turnA;
  chatStore.phase = 'cancel_requested';
  globalThis.fetch = async () => response({
    ...baseStatus,
    turnId: turnA,
    status: 'cancelled',
    completedAt: '2026-07-19T09:00:02.000Z',
    errorCode: 'turn_cancelled',
  });
  await checkActiveTurnStatus();
  assert.equal(chatStore.phase, 'cancelled');
  assert.equal(chatStore.activeTurnId, null);
  assert.equal(chatStore.draft, 'retained draft');
  assert.equal(chatStore.requestGeneration, 1);

  chatStore.activeTurnId = turnB;
  chatStore.activeTargetSessionId = null;
  chatStore.phase = 'committing';
  sessionDetailStore.data = null;
  globalThis.fetch = async () => response({
    ...baseStatus,
    turnId: turnB,
    status: 'completed',
    sessionId: 'session_manual',
    created: true,
    completedAt: '2026-07-19T09:00:03.000Z',
    resultAvailable: true,
  });
  await checkActiveTurnStatus();
  assert.equal(chatStore.phase, 'success');
  assert.equal(chatStore.activeTurnId, null);
  assert.equal(sessionDetailStore.sessionId, 'session_manual');
  assert.ok(refreshCalls >= 4);
  assert.equal(chatStore.requestGeneration, 2);

  chatStore.activeTurnId = turnA;
  chatStore.activeTargetSessionId = null;
  chatStore.phase = 'cancel_requested';
  let resolveFetch;
  globalThis.fetch = () => new Promise((resolve) => { resolveFetch = resolve; });
  const stale = checkActiveTurnStatus();
  chatStore.operationGeneration += 1;
  chatStore.activeTurnId = turnB;
  chatStore.phase = 'in_progress';
  sessionDetailStore.sessionId = 'session_newer';
  resolveFetch(response({
    ...baseStatus,
    turnId: turnA,
    status: 'completed',
    sessionId: 'session_stale',
    created: true,
    completedAt: '2026-07-19T09:00:04.000Z',
    resultAvailable: true,
  }));
  await stale;
  assert.equal(chatStore.activeTurnId, turnB);
  assert.equal(chatStore.phase, 'in_progress');
  assert.equal(sessionDetailStore.sessionId, 'session_newer');
  assert.ok(renderCalls > 0);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    run_node(functions + "\n" + harness)


def test_skills_connections_and_system_frontend_use_real_independent_stores() -> None:
    javascript = (ROOT / "minicode/web/static/assets/app.js").read_text(
        encoding="utf-8"
    )
    skills_view = javascript[
        javascript.index("  skills(") : javascript.index("\n  connections(")
    ]
    connections_view = javascript[
        javascript.index("  connections(") : javascript.index("\n  ops(")
    ]
    system_view = javascript[
        javascript.index("function renderSystemSummary") : javascript.index(
            "\nconst VIEWS"
        )
    ] + javascript[
        javascript.index("  system(") : javascript.index(
            "\n};", javascript.index("  system(")
        )
    ]

    assert "const skillsStore" in javascript
    assert "const connectionsStore" in javascript
    assert "const systemStore" in javascript
    assert "fetch(`/api/v1/skills" in javascript
    assert "function assertSkillEvidenceContract" in javascript
    assert "function assertSkillVersionLedgerContract" in javascript
    assert "function skillEvidencePanel" in javascript
    assert "function skillVersionPanel" in javascript
    assert "fetch('/api/v1/connections'" in javascript
    assert "fetch('/api/v1/system'" in javascript
    assert "skillsStore.requestId" in javascript
    assert "JSON.stringify(skillsStore.filters)" in javascript
    assert "Skills 暂时不可用" in skills_view
    assert "正在发现本地 Skills" in skills_view
    assert "loadMoreSkills" in skills_view
    assert "refreshSkills" in skills_view
    assert "runtimeTraceState('skill')" in skills_view
    assert "skillEvidencePanel(data.evidence)" in skills_view
    assert "skillVersionPanel(data.versionLedger)" in skills_view
    assert "promotion locked" in skills_view
    assert "rollback execution locked" in skills_view
    assert "verification and user signals require complete explicit coverage" in skills_view
    assert "task correlation, not causal proof" in skills_view
    assert "This Run has no observed Skill Routing event." in javascript
    assert "selectedTruncated" in javascript
    assert "DATA.skills" not in skills_view
    assert "DATA.gateways" not in connections_view
    assert "DATA.connectors" not in connections_view
    assert "latency" not in connections_view.lower()
    assert "tool count" not in connections_view.lower()
    assert "resource count" not in connections_view.lower()
    assert "configured 不等于 connected" in connections_view
    assert "server.current" in connections_view
    assert "renderMcpCurrentRuntime(server.current)" in connections_view
    assert "renderMcpCurrentCoverage(data)" in connections_view
    assert "Ready in this Gateway process" in javascript
    assert "Starting in this Gateway process" in javascript
    assert "Registered, not started" in javascript
    assert "No active registered client in this Gateway process" in javascript
    assert "This is not an offline claim" in javascript
    assert "Not present in the bounded snapshot; current state unknown" in javascript
    assert "Cross-process · ${esc(coverage.crossProcess)}" in javascript
    assert "Heartbeat · ${coverage.heartbeat === false ? 'false' : 'unavailable'}" in javascript
    assert "Retry" in javascript
    assert "Gateway process snapshot" in javascript
    assert "No global state" in javascript
    assert "No process control" in javascript
    assert "Last observed in retained Runs" in javascript
    assert "historical fact" in javascript
    assert "No retained observation in the scanned window" in javascript
    assert "Current connection status unavailable" in javascript
    assert "Current Gateway process coverage" in javascript
    assert "Historical MCP observation coverage" in javascript
    assert "unmatchedObservedServerCount" in javascript
    assert "assertConnectionsContract(payload)" in javascript
    assert "connections current aggregate contract mismatch" in javascript
    assert "connections server current contract mismatch" in javascript
    assert "summary.liveMcpCount !== current.byState.ready" in javascript
    assert "currentProtocols.includes(processState.protocol)" in javascript
    assert "requestId !== connectionsStore.requestId" in javascript
    assert "connectionsStore.phase = 'empty'" in javascript
    assert "connectionsStore.phase = 'partial'" in javascript
    assert "esc(server.name)" in connections_view
    assert "formatCount(data.summary.registeredConfiguredMcpCount)" in connections_view
    assert "formatCount(data.summary.activeMcpInstanceCount)" in connections_view
    assert "formatCount(data.summary.liveMcpCount)" in connections_view
    assert "registeredConfiguredMcpCount || 0" not in connections_view
    assert "activeMcpInstanceCount || 0" not in connections_view
    assert "liveMcpCount || 0" not in connections_view
    assert "esc(historicalMcpOutcome(runtime.lastOutcome))" in javascript
    assert "currently connected" not in connections_view
    assert "current connected" not in connections_view.lower()
    assert "globally connected" not in connections_view.lower()
    assert "live now" not in connections_view.lower()
    assert "online" not in connections_view.lower()
    assert "healthy" not in connections_view.lower()
    assert "statusPill(runtime.lastOutcome)" not in javascript
    assert "setInterval(loadConnections" not in javascript
    assert javascript.count("new EventSource('/api/v1/events')") == 1
    assert "new WebSocket" not in javascript
    assert "onclick=\"connect" not in connections_view.lower()
    assert "onclick=\"reconnect" not in connections_view.lower()
    assert "onclick=\"start" not in connections_view.lower()
    assert "onclick=\"stop" not in connections_view.lower()
    assert "[object Object]" not in connections_view
    assert "Connections 暂时不可用" in connections_view
    assert "refreshConnections" in connections_view
    assert "DATA." not in system_view
    assert "环境变量" not in system_view
    assert "System 暂时不可用" in system_view
    assert "refreshSystem" in system_view
    assert "data.application" in system_view
    assert "data.runtime" in system_view
    assert "data.features" in system_view
    assert "data.storage" in system_view
    assert "loadSkills(false)" in javascript
    assert "loadConnections()" in javascript
    assert "loadSystem()" in javascript


def test_page_read_http_responses_are_workspace_scoped_and_secret_free(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    other_workspace = tmp_path / "other"
    workspace.mkdir()
    other_workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    sessions_dir = data_dir / "sessions"
    sessions_dir.mkdir(parents=True)
    metadata = {
        "current-session": {
            "session_id": "current-session",
            "created_at": 1,
            "updated_at": 2,
            "first_message": "password=hidden-value",
            "last_message": "Bearer very-secret-token",
            "message_count": 3,
            "workspace": str(workspace),
        },
        "other-session": {
            "session_id": "other-session",
            "created_at": 3,
            "updated_at": 4,
            "first_message": "other-workspace-secret",
            "last_message": "",
            "message_count": 0,
            "workspace": str(other_workspace),
        },
    }
    (data_dir / "sessions_index.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    (sessions_dir / "current-session.json").write_text(
        json.dumps(
            {
                "session_id": "current-session",
                "created_at": 1,
                "updated_at": 2,
                "workspace": str(workspace),
                "messages": [
                    {"role": "system", "content": "system-prompt-secret"},
                    {"role": "user", "content": "api_key=sk-test-secret"},
                    {"role": "assistant", "content": "Cookie: cookie-secret"},
                ],
                "transcript_entries": [{"content": "transcript-secret"}],
                "permissions_summary": {"token": "permission-secret"},
                "skills": [{"content": "skill-body-secret"}],
                "mcp_servers": [{"env": {"TOKEN": "mcp-env-secret"}}],
            }
        ),
        encoding="utf-8",
    )
    memory_dir = workspace / ".mini-code-memory"
    memory_dir.mkdir()
    (memory_dir / "memory.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "project-1",
                        "scope": "project",
                        "category": "security",
                        "tier": "short_term",
                        "content": "Authorization=Bearer page-secret-token",
                        "created_at": 1,
                        "updated_at": 2,
                        "safety_status": "safe",
                        "approval_status": "approved",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    skill_path = workspace / ".mini-code" / "skills" / "safe-skill" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: safe-skill\ndescription: credential=skill-description-secret\n"
        "tools: [read_file]\n---\n\nSkill body sk-skill-body-secret\n",
        encoding="utf-8",
    )
    (data_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "safe-server": {
                        "command": "password=command-secret",
                        "args": ["Bearer mcp-args-secret"],
                        "env": {"API_KEY": "sk-mcp-env-secret"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(
        title="Run password=run-title-secret",
        source="gateway",
        metadata={"origin": "credential=run-metadata-secret"},
    )
    journal.transition(run.id, "running")
    journal.append_event(
        run.id,
        "model.completed",
        payload={
            "summary": "Bearer run-payload-secret",
            "env": {"API_KEY": "sk-run-env-secret"},
        },
    )
    run = journal.transition(run.id, "completed")
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.dashboard_read_model = DashboardReadModel(
        workspace, data_dir=data_dir, run_journal=journal
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        responses = [
            get(port, "/api/v1/sessions"),
            get(port, "/api/v1/sessions/current-session"),
            get(port, "/api/v1/memory"),
            get(port, "/api/v1/skills"),
            get(port, "/api/v1/connections"),
            get(port, "/api/v1/system"),
            get(port, "/api/v1/ops"),
            get(port, "/api/v1/runs"),
            get(port, f"/api/v1/runs/{run.id}"),
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert all(status == 200 for status, _, _ in responses)
    combined = b"\n".join(body for _, _, body in responses).decode("utf-8")
    for secret in (
        "hidden-value",
        "very-secret-token",
        "other-workspace-secret",
        "system-prompt-secret",
        "sk-test-secret",
        "cookie-secret",
        "transcript-secret",
        "permission-secret",
        "skill-body-secret",
        "mcp-env-secret",
        "ops-secret",
        "run-title-secret",
        "run-metadata-secret",
        "run-payload-secret",
        "sk-run-env-secret",
        "page-secret-token",
        "skill-description-secret",
        "skill-body-secret",
        "command-secret",
        "mcp-args-secret",
        "mcp-env-secret",
    ):
        assert secret not in combined
    sessions_payload = json.loads(responses[0][2])
    assert [item["id"] for item in sessions_payload["items"]] == [
        "current-session"
    ]
    detail_payload = json.loads(responses[1][2])
    assert [item["role"] for item in detail_payload["messages"]] == [
        "user",
        "assistant",
    ]
    skills_payload = json.loads(responses[3][2])
    assert [item["name"] for item in skills_payload["items"]] == ["safe-skill"]
    assert "path" not in skills_payload["items"][0]
    connections_payload = json.loads(responses[4][2])
    assert connections_payload["gateway"]["status"] == "live"
    assert connections_payload["mcpServers"][0]["status"] == "configured"
    assert connections_payload["mcpServers"][0]["liveStatus"] == "unavailable"
    system_payload = json.loads(responses[5][2])
    assert system_payload["runtime"]["processMode"] == "gateway"
    assert system_payload["features"]["runs"] == "lifecycle-model-usage-cost-tool-assistant-skill-memory-context"
    assert system_payload["features"]["usage"] == "live"
    ops_payload = json.loads(responses[6][2])
    assert ops_payload["mode"] == "read-only"
    assert ops_payload["cost"]["status"] == "unavailable"
    assert ops_payload["cost"]["value"] is None
    runs_payload = json.loads(responses[7][2])
    assert runs_payload["items"][0]["id"] == run.id
    assert runs_payload["coverage"]["gateway"] == "live"
    assert runs_payload["coverage"]["historical"] == "partial"
    assert runs_payload["coverage"]["scope"] == "lifecycle-model-usage-cost-tool-assistant-skill-memory-context"
    assert runs_payload["coverage"]["model"] == "live"
    assert runs_payload["coverage"]["tool"] == "live"
    assert runs_payload["coverage"]["assistant"] == "live"
    assert runs_payload["coverage"]["usage"] == "live"
    assert runs_payload["coverage"]["memory"] == "live"
    assert runs_payload["coverage"]["skills"] == "live"
    assert runs_payload["coverage"]["context"] == "partial"
    assert runs_payload["coverage"]["workingMemory"] == "partial"
    detail_payload = json.loads(responses[8][2])
    assert detail_payload["run"]["status"] == "completed"
    assert "payload" not in detail_payload["events"][0]
    assert detail_payload["metrics"]["tokens"]["status"] == "unavailable"
