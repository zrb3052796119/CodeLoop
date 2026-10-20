"""Transport-level regression tests for the MCP client upgrade.

Covers Streamable HTTP, layered timeouts, ${VAR} config expansion,
server notifications (tools/list_changed), and protocol negotiation.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import minicode.mcp as mcp_module
from minicode.mcp import (
    HttpMcpClient,
    StdioMcpClient,
    _configured_tool_timeout,
    _expand_env_template,
    create_mcp_backed_tools,
)
from minicode.tooling import ToolContext


class _FakeStreamableHttpHandler(BaseHTTPRequestHandler):
    """Minimal Streamable HTTP MCP endpoint with test hooks on the server."""

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length)
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return
        self.server.requests.append(
            {
                "message": message,
                "session": self.headers.get("Mcp-Session-Id"),
                "authorization": self.headers.get("Authorization"),
            }
        )
        if message.get("id") is None:
            self.send_response(202)
            self.end_headers()
            return
        if (
            self.server.expire_sessions
            and not self.server.expired
            and self.headers.get("Mcp-Session-Id")
        ):
            # Expire the session exactly once; later sessions survive.
            self.server.expired = True
            self.send_response(404)
            self.end_headers()
            return

        method = message.get("method")
        mid = message.get("id")
        if method == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "serverInfo": {"name": "http-fake"},
            }
        elif method == "tools/list":
            result = {"tools": self.server.tools}
        elif method == "tools/call":
            name = message["params"]["name"]
            if name == "boom":
                result = {
                    "content": [{"type": "text", "text": "exploded"}],
                    "isError": True,
                }
            else:
                result = {
                    "content": [
                        {"type": "text", "text": f"echo:{json.dumps(message['params']['arguments'], sort_keys=True)}"}
                    ]
                }
        else:
            self.send_response(400)
            self.end_headers()
            return
        response = {"jsonrpc": "2.0", "id": mid, "result": result}

        if self.server.respond_sse:
            body = f"event: message\ndata: {json.dumps(response)}\n\n".encode()
            content_type = "text/event-stream"
        else:
            body = json.dumps(response).encode()
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if method == "initialize":
            # Every initialize opens a fresh session with its own id.
            self.server.session_counter += 1
            self.send_header("Mcp-Session-Id", f"sess-{self.server.session_counter}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.server.delete_seen = True
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


class _FakeHttpServer:
    def __init__(self, *, respond_sse: bool = False, expire_sessions: bool = False):
        handler = _FakeStreamableHttpHandler
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.requests = []
        self.httpd.respond_sse = respond_sse
        self.httpd.expire_sessions = expire_sessions
        self.httpd.session_counter = 0
        self.httpd.expired = False
        self.httpd.delete_seen = False
        self.httpd.tools = [
            {
                "name": "echo",
                "description": "Echo arguments",
                "inputSchema": {"type": "object"},
            }
        ]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/mcp"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def http_server():
    server = _FakeHttpServer()
    yield server
    server.close()


def _http_config(url: str, **extra) -> dict:
    return {"url": url, **extra}


def test_http_transport_discovers_and_calls_tools(http_server) -> None:
    bundle = create_mcp_backed_tools(
        cwd=".",
        mcp_servers={"remote": _http_config(http_server.url)},
    )
    try:
        summary = next(s for s in bundle["servers"] if s["name"] == "remote")
        assert summary["status"] == "connected"
        assert summary["protocol"] == "streamable-http"

        tool = next(t for t in bundle["tools"] if t.name == "mcp__remote__echo")
        from minicode.auto_mode import PermissionMode
        from minicode.permissions import PermissionManager

        result = tool.run(
            {"text": "hi"},
            ToolContext(cwd=".", permissions=PermissionManager(".", auto_mode=PermissionMode.BYPASS)),
        )
        assert result.ok is True
        assert "echo:" in result.output

        initialize = next(
            r for r in http_server.httpd.requests if r["message"].get("method") == "initialize"
        )
        assert (
            initialize["message"]["params"]["protocolVersion"] == "2025-06-18"
        )
    finally:
        bundle["dispose"]()


def test_sse_stream_without_a_matching_response_names_the_server() -> None:
    client = HttpMcpClient("sse-remote", _http_config("http://127.0.0.1:9/mcp"), ".")
    try:
        with pytest.raises(RuntimeError, match=r"MCP sse-remote: SSE stream ended"):
            client._response_from_sse(
                'data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n',
                99,
            )
    finally:
        client.close()


def test_http_transport_handles_sse_response_mode() -> None:
    server = _FakeHttpServer(respond_sse=True)
    try:
        client = HttpMcpClient("sse-remote", _http_config(server.url), ".")
        try:
            tools = client.list_tools()
            assert [t["name"] for t in tools] == ["echo"]
            result = client.call_tool("echo", {"x": 1})
            assert result.ok is True
            assert "echo:" in result.output
        finally:
            client.close()
    finally:
        server.close()


def test_http_session_header_echoed_and_expiry_recovers() -> None:
    server = _FakeHttpServer(expire_sessions=True)
    try:
        client = HttpMcpClient("expiring", _http_config(server.url), ".")
        try:
            # The first sessioned request after initialize is killed with 404.
            with pytest.raises(RuntimeError, match="session expired"):
                client.list_tools()
            # The next call transparently re-initializes a fresh session.
            tools = client.list_tools()
            assert [t["name"] for t in tools] == ["echo"]
            # The session id was actually echoed on sessioned requests.
            session_headers = [
                r["session"] for r in server.httpd.requests if r["session"]
            ]
            assert session_headers
        finally:
            client.close()
        assert server.httpd.delete_seen
    finally:
        server.close()


def test_http_url_and_headers_expand_env_variables(
    http_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TEST_URL", http_server.url)
    monkeypatch.setenv("MCP_TEST_TOKEN", "secret-token")
    bundle = create_mcp_backed_tools(
        cwd=".",
        mcp_servers={
            "remote": {
                "url": "${MCP_TEST_URL}",
                "headers": {"Authorization": "Bearer ${MCP_TEST_TOKEN}"},
            }
        },
    )
    try:
        tool = next(t for t in bundle["tools"] if t.name == "mcp__remote__echo")
        from minicode.auto_mode import PermissionMode
        from minicode.permissions import PermissionManager

        tool.run(
            {"a": 1},
            ToolContext(cwd=".", permissions=PermissionManager(".", auto_mode=PermissionMode.BYPASS)),
        )
        sent = [
            r["authorization"] for r in http_server.httpd.requests if r["authorization"]
        ]
        assert sent and sent[0] == "Bearer secret-token"
    finally:
        bundle["dispose"]()


def test_env_template_expansion_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_T_SET", "value")
    assert _expand_env_template("${MCP_T_SET}") == "value"
    assert _expand_env_template("x${MCP_T_SET}y") == "xvaluey"
    assert _expand_env_template("${MCP_T_UNSET:-fallback}") == "fallback"
    assert _expand_env_template("${MCP_T_UNSET:-}") == ""
    assert _expand_env_template("plain") == "plain"
    with pytest.raises(RuntimeError, match="MCP_T_UNSET"):
        _expand_env_template("${MCP_T_UNSET}")


def test_stdio_env_expansion_applied_to_command_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_FAKE_MODE", "normal")
    client = StdioMcpClient(
        "fake",
        {
            "command": "python",
            "args": [str(Path(__file__).parent / "fixtures" / "fake_mcp_server.py")],
            "env": {"FAKE_MCP_MODE": "${MCP_FAKE_MODE}"},
            "protocol": "newline-json",
        },
        str(tmp_path),
    )
    try:
        tools = client.list_tools()
        assert [t["name"] for t in tools] == ["echo"]
    finally:
        client.close()


def test_tool_timeout_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _configured_tool_timeout({}) == mcp_module.DEFAULT_TOOL_TIMEOUT_SECONDS
    assert _configured_tool_timeout({"timeout": 120}) == 120.0
    assert _configured_tool_timeout({"timeout": "bad"}) == mcp_module.DEFAULT_TOOL_TIMEOUT_SECONDS
    monkeypatch.setenv("MINI_CODE_MCP_TOOL_TIMEOUT", "300")
    assert _configured_tool_timeout({}) == 300.0
    assert _configured_tool_timeout({"timeout": 45}) == 45.0


def test_tools_list_changed_notification_invalidates_cache(tmp_path: Path) -> None:
    client = StdioMcpClient(
        "fake",
        {
            "command": "python",
            "args": [str(Path(__file__).parent / "fixtures" / "fake_mcp_server.py")],
            "protocol": "newline-json",
        },
        str(tmp_path),
    )
    try:
        client.list_tools()
        assert client._tools_cache is not None

        # A notification (no id) routes through _handle_notification.
        client._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "notifications/tools/list_changed",
            }
        )
        assert client._tools_cache is None

        # Re-listing fetches the fresh tool set from the server again.
        tools = client.list_tools()
        assert [t["name"] for t in tools] == ["echo"]
    finally:
        client.close()


def test_server_logging_notification_surfaces_in_diagnostics(tmp_path: Path) -> None:
    client = StdioMcpClient(
        "fake",
        {
            "command": "python",
            "args": [str(Path(__file__).parent / "fixtures" / "fake_mcp_server.py")],
            "protocol": "newline-json",
        },
        str(tmp_path),
    )
    try:
        client._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {"level": "warning", "data": "index rebuilt"},
            }
        )
        assert any("index rebuilt" in line for line in client.stderr_lines)
    finally:
        client.close()


def test_sse_transport_reports_clear_error(tmp_path: Path) -> None:
    bundle = create_mcp_backed_tools(
        cwd=str(tmp_path),
        mcp_servers={"legacy": {"type": "sse", "url": "https://example.com/sse"}},
    )
    summary = next(s for s in bundle["servers"] if s["name"] == "legacy")
    assert summary["status"] == "error"
    assert "SSE transport is not supported" in summary["error"]
    assert not any(t.name.startswith("mcp__legacy__") for t in bundle["tools"])
    bundle["dispose"]()


def test_http_tool_error_result_flows_to_model(http_server) -> None:
    http_server.httpd.tools.append(
        {"name": "boom", "description": "always fails", "inputSchema": {"type": "object"}}
    )
    bundle = create_mcp_backed_tools(
        cwd=".",
        mcp_servers={"remote": _http_config(http_server.url)},
    )
    try:
        tool = next(t for t in bundle["tools"] if t.name == "mcp__remote__boom")
        from minicode.auto_mode import PermissionMode
        from minicode.permissions import PermissionManager

        result = tool.run(
            {},
            ToolContext(cwd=".", permissions=PermissionManager(".", auto_mode=PermissionMode.BYPASS)),
        )
        # isError=True surfaces as ok=False while keeping the payload, so the
        # model can read the server's failure text and self-correct.
        assert result.ok is False
        assert "exploded" in result.output
    finally:
        bundle["dispose"]()
