from __future__ import annotations

import http.client
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from minicode.gateway import MiniCodeGatewayHandler
import minicode.gateway as gateway_module
from minicode.mcp_current_state import McpCurrentStateRegistry
from minicode.mcp_observation import mcp_server_key
from minicode.tooling import ToolContext, ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.task import task_tool
from minicode.web import DashboardReadModel


def _post(port: int, prompt: str) -> tuple[int, dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        body = json.dumps({"prompt": prompt}).encode("utf-8")
        connection.request(
            "POST",
            "/run",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def _get(port: int, path: str) -> tuple[int, dict[str, object] | None]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        if response.headers.get_content_type() == "application/json":
            return response.status, json.loads(body)
        return response.status, None
    finally:
        connection.close()


def test_tool_registry_injects_current_state_dependency_only_when_present() -> None:
    seen: list[object | None] = []
    tool = ToolDefinition(
        name="inspect",
        description="inspect composition",
        input_schema={"type": "object"},
        validator=lambda value: value,
        run=lambda _value, context: (
            seen.append(context._mcp_current_state_registry)
            or ToolResult(ok=True, output="ok")
        ),
    )
    state_registry = McpCurrentStateRegistry()
    observed = ToolRegistry(
        [tool],
        mcp_current_state_registry=state_registry,
    )
    default = ToolRegistry([tool])

    assert observed.execute("inspect", {}, ToolContext()).ok is True
    assert default.execute("inspect", {}, ToolContext()).ok is True
    assert seen == [state_registry, None]
    assert "McpCurrentStateRegistry" not in repr(
        ToolContext(_mcp_current_state_registry=state_registry)
    )


def test_run_gateway_owns_exactly_one_registry_for_server_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scoped_key = mcp_server_key("/workspace", "configured")
    created_registries: list[object] = []
    created_servers: list[object] = []
    captured_loaders: list[object] = []
    read_model = SimpleNamespace(workspace=Path("/workspace"))

    class FakeRegistry:
        def __init__(self) -> None:
            created_registries.append(self)

        def snapshot_for(self, server_keys: frozenset[str]) -> object:
            assert server_keys == frozenset({scoped_key})
            registry = self

            class Snapshot:
                def to_dict(self) -> dict[str, object]:
                    return {"registry": registry}

            return Snapshot()

    class FakeServer:
        def __init__(self, address: tuple[str, int], handler: object) -> None:
            self.address = address
            self.handler = handler
            self.serve_calls = 0
            self.close_calls = 0
            created_servers.append(self)

        def serve_forever(self) -> None:
            self.serve_calls += 1

        def server_close(self) -> None:
            self.close_calls += 1

    monkeypatch.setattr(
        "minicode.mcp_current_state.McpCurrentStateRegistry",
        FakeRegistry,
    )
    monkeypatch.setattr(gateway_module, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(
        gateway_module.DashboardReadModel,
        "from_environment",
        lambda **kwargs: (
            captured_loaders.append(kwargs["mcp_current_state_loader"])
            or read_model
        ),
    )
    monkeypatch.setenv("MINI_CODE_GATEWAY_HOST", "127.0.0.1")
    monkeypatch.setenv("MINI_CODE_GATEWAY_PORT", "8765")

    gateway_module.run_gateway()

    assert len(created_registries) == 1
    assert len(created_servers) == 1
    server = created_servers[0]
    assert server.address == ("127.0.0.1", 8765)
    assert server.mcp_current_state_registry is created_registries[0]
    assert server.dashboard_read_model is read_model
    assert server.conversation_turn_service.workspace == Path("/workspace")
    assert (
        server.conversation_turn_service._mcp_current_state_registry
        is created_registries[0]
    )
    assert len(captured_loaders) == 1
    assert captured_loaders[0](frozenset({scoped_key})) == {
        "registry": created_registries[0]
    }
    assert server.serve_calls == 1
    assert server.close_calls == 1


def test_task_registry_inherits_dependency_and_disposes_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_registry = McpCurrentStateRegistry()
    captured: list[object | None] = []
    dispose_calls = 0

    class FullTools:
        def list(self) -> list[ToolDefinition]:
            return []

        def dispose(self) -> None:
            nonlocal dispose_calls
            dispose_calls += 1

    def create_tools(
        _cwd: str,
        runtime: dict[str, object] | None = None,
        *,
        mcp_current_state_registry: object | None = None,
    ) -> FullTools:
        assert runtime is not None
        captured.append(mcp_current_state_registry)
        return FullTools()

    monkeypatch.setattr("minicode.tools.create_default_tool_registry", create_tools)
    monkeypatch.setattr(
        "minicode.model_registry.create_model_adapter",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        "minicode.permissions.PermissionManager",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "minicode.tools.task.run_agent_turn",
        lambda **_kwargs: [{"role": "assistant", "content": "nested result"}],
    )

    result = task_tool.run(
        {"description": "inspect", "prompt": "inspect", "agent_type": "explore"},
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _mcp_current_state_registry=state_registry,
        ),
    )

    assert result.ok is True
    assert captured == [state_registry]
    assert dispose_calls == 1


def test_standalone_task_keeps_factory_signature_and_disposes_on_setup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispose_calls = 0

    class FullTools:
        def list(self) -> list[ToolDefinition]:
            return []

        def dispose(self) -> None:
            nonlocal dispose_calls
            dispose_calls += 1

    def create_tools(
        _cwd: str,
        runtime: dict[str, object] | None = None,
    ) -> FullTools:
        assert runtime is not None
        return FullTools()

    def fail_model(**_kwargs: object) -> object:
        raise RuntimeError("model setup failed")

    monkeypatch.setattr("minicode.tools.create_default_tool_registry", create_tools)
    monkeypatch.setattr(
        "minicode.model_registry.create_model_adapter",
        fail_model,
    )

    with pytest.raises(RuntimeError, match="model setup failed"):
        task_tool.run(
            {"description": "inspect", "prompt": "inspect", "agent_type": "explore"},
            ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
        )

    assert dispose_calls == 1


def test_gateway_concurrent_runs_share_registry_and_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = McpCurrentStateRegistry()
    key = mcp_server_key(tmp_path, "same-server")
    both_registered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = 0

    def fake_headless(
        prompt: str,
        *,
        run_source: str,
        mcp_current_state_registry: McpCurrentStateRegistry,
    ) -> str:
        nonlocal active
        assert run_source == "gateway"
        assert mcp_current_state_registry is registry
        handle = registry.register(key, probe=lambda: True)
        assert handle is not None
        registry.mark_ready(handle, protocol="newline-json")
        try:
            with lock:
                active += 1
                if active == 2:
                    both_registered.set()
            assert release.wait(timeout=5)
            return f"done:{prompt}"
        finally:
            registry.unregister(handle)

    monkeypatch.setattr("minicode.headless.run_headless", fake_headless)
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"same-server": {"command": "python"}}}),
        encoding="utf-8",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.mcp_current_state_registry = registry
    server.dashboard_read_model = DashboardReadModel(
        tmp_path,
        mcp_current_state_loader=lambda server_keys: (
            registry.snapshot_for(server_keys).to_dict()
        ),
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    responses: list[tuple[int, dict[str, object]]] = []
    requests = [
        threading.Thread(
            target=lambda prompt=prompt: responses.append(
                _post(server.server_address[1], prompt)
            )
        )
        for prompt in ("one", "two")
    ]
    try:
        for request in requests:
            request.start()
        assert both_registered.wait(timeout=5)
        current = registry.snapshot().servers
        assert len(current) == 1
        assert current[0].state == "ready"
        assert current[0].active_instance_count == 2
        status, connections = _get(
            server.server_address[1],
            "/api/v1/connections",
        )
        assert status == 200
        assert connections is not None
        assert connections["summary"]["registeredConfiguredMcpCount"] == 1
        assert connections["summary"]["activeMcpInstanceCount"] == 2
        assert connections["summary"]["liveMcpCount"] == 1
        assert connections["mcpServers"][0]["liveStatus"] == "ready"
        release.set()
        for request in requests:
            request.join(timeout=5)
        assert sorted(response[0] for response in responses) == [200, 200]
        assert registry.snapshot().servers == ()
        status, connections = _get(
            server.server_address[1],
            "/api/v1/connections",
        )
        assert status == 200
        assert connections is not None
        assert connections["summary"]["registeredConfiguredMcpCount"] == 0
        assert connections["summary"]["activeMcpInstanceCount"] == 0
        assert connections["summary"]["liveMcpCount"] == 0
        assert connections["mcpServers"][0]["current"]["reason"] == "not_registered"
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


def test_fallback_dashboard_read_model_does_not_consume_server_registry(
    tmp_path: Path,
) -> None:
    class ForbiddenSnapshotRegistry:
        def snapshot_for(self, _server_keys: frozenset[str]) -> object:
            raise AssertionError("fallback read model must not discover server state")

    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.mcp_current_state_registry = ForbiddenSnapshotRegistry()
    server.dashboard_read_model = DashboardReadModel(tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root_status, _ = _get(server.server_address[1], "/")
        connections_status, connections = _get(
            server.server_address[1],
            "/api/v1/connections",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert root_status == 200
    assert connections_status == 200
    assert connections is not None
    assert connections["summary"]["liveMcpCount"] is None
    assert connections["summary"]["registeredConfiguredMcpCount"] is None
    assert connections["summary"]["activeMcpInstanceCount"] is None
    assert connections["mcpCurrent"]["status"] == "unavailable"
    assert connections["mcpRuntime"]["current"] == "unavailable"
    assert connections["coverage"]["current"] == "unavailable"


def test_change_feed_scopes_registry_before_probing_other_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"configured": {"command": "python"}}}
        ),
        encoding="utf-8",
    )
    registry = McpCurrentStateRegistry()
    unmatched_probe_calls = 0

    def unmatched_probe() -> bool:
        nonlocal unmatched_probe_calls
        unmatched_probe_calls += 1
        raise RuntimeError("Authorization=other-workspace-secret")

    handle = registry.register(
        mcp_server_key(tmp_path / "other-workspace", "configured"),
        probe=unmatched_probe,
    )
    assert handle is not None
    assert registry.mark_ready(handle, protocol="newline-json") is True

    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.mcp_current_state_registry = registry
    server.dashboard_read_model = DashboardReadModel(workspace)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _get(server.server_address[1], "/api/v1/changes")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 200
    assert payload is not None
    assert payload["resources"]["connections"]["status"] == "live"
    assert unmatched_probe_calls == 0
    assert "other-workspace-secret" not in json.dumps(payload)


def test_root_does_not_call_loader_and_each_connections_get_calls_it_once(
    tmp_path: Path,
) -> None:
    registry = McpCurrentStateRegistry()
    calls = 0

    def load(server_keys: frozenset[str]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return registry.snapshot_for(server_keys).to_dict()

    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.mcp_current_state_registry = registry
    server.dashboard_read_model = DashboardReadModel(
        tmp_path,
        mcp_current_state_loader=load,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert _get(server.server_address[1], "/")[0] == 200
        assert _get(server.server_address[1], "/api/v1/health")[0] == 200
        assert _get(server.server_address[1], "/api/v1/system")[0] == 200
        assert calls == 0
        assert _get(server.server_address[1], "/api/v1/connections")[0] == 200
        assert calls == 1
        assert _get(server.server_address[1], "/api/v1/connections")[0] == 200
        assert calls == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
