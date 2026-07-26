from __future__ import annotations

from pathlib import Path

import pytest

import minicode.mcp as mcp_module
from minicode.mcp import StdioMcpClient, create_mcp_backed_tools
from minicode.mcp_current_state import McpCurrentStateRegistry
from minicode.mcp_observation import mcp_server_key


def _fake_server_script() -> Path:
    return Path(__file__).parent / "fixtures" / "fake_mcp_server.py"


def _client(
    tmp_path: Path,
    *,
    mode: str = "normal",
    state_registry: object | None = None,
) -> StdioMcpClient:
    return StdioMcpClient(
        "fake",
        {
            "command": "python",
            "args": [str(_fake_server_script())],
            "protocol": "newline-json",
            "env": {"FAKE_MCP_MODE": mode},
        },
        str(tmp_path),
        state_registry=state_registry,
    )


def test_client_registers_ready_fails_on_dead_probe_and_unregisters(
    tmp_path: Path,
) -> None:
    registry = McpCurrentStateRegistry()
    client = _client(tmp_path, state_registry=registry)
    expected_key = mcp_server_key(tmp_path, "fake")

    idle = registry.snapshot().servers
    assert len(idle) == 1
    assert idle[0].server_key == expected_key
    assert idle[0].state == "idle"

    client.start()
    assert registry.snapshot().servers[0].state == "ready"

    assert client.process is not None
    client.process.kill()
    client.process.wait(timeout=5)
    dead = registry.snapshot().servers[0]
    assert dead.state == "failed"
    assert dead.failure_kind == "process_exit"

    client.close()
    assert registry.snapshot().servers == ()


def test_request_failure_with_live_process_remains_ready(tmp_path: Path) -> None:
    registry = McpCurrentStateRegistry()
    client = _client(tmp_path, mode="error_on_call", state_registry=registry)
    client.start()

    with pytest.raises(RuntimeError, match="secret request failed"):
        client.call_tool("echo", {"text": "not projected"})

    state = registry.snapshot().servers[0]
    assert state.state == "ready"
    assert "secret request failed" not in repr(registry.snapshot().to_dict())
    client.close()


def test_failed_initialization_records_safe_failure_then_close_removes_it(
    tmp_path: Path,
) -> None:
    registry = McpCurrentStateRegistry()
    client = StdioMcpClient(
        "missing-secret-name",
        {"command": "definitely-not-an-installed-command", "args": []},
        str(tmp_path),
        state_registry=registry,
    )

    with pytest.raises(RuntimeError):
        client.start()

    state = registry.snapshot().servers[0]
    assert state.state == "failed"
    assert state.failure_kind == "command_not_found"
    assert "missing-secret-name" not in repr(registry.snapshot().to_dict())
    client.close()
    assert registry.snapshot().servers == ()


def test_protocol_fallback_keeps_one_registration_across_internal_cleanup(
    tmp_path: Path,
) -> None:
    registry = McpCurrentStateRegistry()
    client = StdioMcpClient(
        "fake",
        {
            "command": "python",
            "args": [str(_fake_server_script())],
            "env": {"FAKE_MCP_MODE": "normal"},
        },
        str(tmp_path),
        state_registry=registry,
    )

    result = client.call_tool("echo", {"text": "fallback"})

    assert result.output == "echo:fallback"
    [state] = registry.snapshot().servers
    assert state.active_instance_count == 1
    assert state.state == "ready"
    assert state.protocol == "newline-json"
    client.close()
    assert registry.snapshot().servers == ()


def test_dead_process_recovery_reuses_registration_and_returns_ready(
    tmp_path: Path,
) -> None:
    registry = McpCurrentStateRegistry()
    client = _client(tmp_path, state_registry=registry)
    client.start()
    assert client.process is not None
    original_pid = client.process.pid
    client.process.kill()
    client.process.wait(timeout=5)

    result = client.call_tool("echo", {"text": "recovered"})

    assert result.output == "echo:recovered"
    assert client.process is not None and client.process.pid != original_pid
    [state] = registry.snapshot().servers
    assert state.active_instance_count == 1
    assert state.state == "ready"
    client.close()


def test_none_registry_performs_zero_current_state_identity_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_key(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("server key must not be computed")

    monkeypatch.setattr(mcp_module, "mcp_server_key", forbidden_key, raising=False)
    client = _client(tmp_path, state_registry=None)

    result = client.call_tool("echo", {"text": "unchanged"})

    assert result.ok is True
    assert result.output == "echo:unchanged"
    client.close()


class _ObserverExplosion(BaseException):
    pass


class _FailingRegistry:
    def register(self, *_args: object, **_kwargs: object) -> object:
        raise _ObserverExplosion("observer-secret")

    def mark_starting(self, *_args: object, **_kwargs: object) -> bool:
        raise _ObserverExplosion("observer-secret")

    def mark_ready(self, *_args: object, **_kwargs: object) -> bool:
        raise _ObserverExplosion("observer-secret")

    def mark_failed(self, *_args: object, **_kwargs: object) -> bool:
        raise _ObserverExplosion("observer-secret")

    def unregister(self, *_args: object, **_kwargs: object) -> bool:
        raise _ObserverExplosion("observer-secret")


class _FailingTransitionRegistry(_FailingRegistry):
    def __init__(self) -> None:
        self.handle = object()

    def register(self, *_args: object, **_kwargs: object) -> object:
        return self.handle


def test_failing_registry_does_not_change_result_calls_or_cleanup(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, state_registry=_FailingRegistry())

    result = client.call_tool("echo", {"text": "same-result"})

    assert result.ok is True
    assert result.output == "echo:same-result"
    assert client.process is not None
    process = client.process
    client.close()
    assert client.process is None
    assert process.poll() is not None


def test_failing_transition_callbacks_do_not_change_mcp_result(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, state_registry=_FailingTransitionRegistry())

    result = client.call_tool("echo", {"text": "same-result"})

    assert result.output == "echo:same-result"
    client.close()


@pytest.mark.parametrize("control_flow", [KeyboardInterrupt(), SystemExit(7)])
def test_failing_registry_preserves_business_control_flow_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control_flow: BaseException,
) -> None:
    client = _client(tmp_path, state_registry=_FailingRegistry())
    client.start()

    def raise_control_flow(*_args: object, **_kwargs: object) -> object:
        raise control_flow

    monkeypatch.setattr(client, "request", raise_control_flow)
    with pytest.raises(type(control_flow)) as raised:
        client.call_tool("echo", {})
    assert raised.value is control_flow
    client.close()


def test_factory_preserves_eager_discovery_and_registers_each_enabled_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeClient:
        def __init__(
            self,
            server_name: str,
            config: dict[str, object],
            cwd: str,
            *,
            state_registry: object | None = None,
        ) -> None:
            calls.append(f"init:{server_name}:{state_registry is not None}")

        def list_tools(self) -> list[dict[str, object]]:
            calls.append("tools")
            return []

        def list_resources(self) -> list[dict[str, object]]:
            calls.append("resources")
            return []

        def list_prompts(self) -> list[dict[str, object]]:
            calls.append("prompts")
            return []

        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(mcp_module, "StdioMcpClient", FakeClient)
    registry = McpCurrentStateRegistry()

    created = create_mcp_backed_tools(
        cwd=str(tmp_path),
        mcp_servers={"one": {"command": "ignored"}},
        state_registry=registry,
    )

    assert calls == ["init:one:True", "tools", "resources", "prompts"]
    created["dispose"]()
    assert calls[-1] == "close"


def test_real_factory_eager_discovery_is_visible_then_dispose_is_empty(
    tmp_path: Path,
) -> None:
    registry = McpCurrentStateRegistry()

    created = create_mcp_backed_tools(
        cwd=str(tmp_path),
        mcp_servers={
            "fake": {
                "command": "python",
                "args": [str(_fake_server_script())],
                "protocol": "newline-json",
            }
        },
        state_registry=registry,
    )

    assert "mcp__fake__echo" in {tool.name for tool in created["tools"]}
    [state] = registry.snapshot().servers
    assert state.state == "ready"
    assert state.active_instance_count == 1
    created["dispose"]()
    assert registry.snapshot().servers == ()
