from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from minicode.mcp import StdioMcpClient, create_mcp_backed_tools
from minicode.mcp_observation import mcp_server_key, project_mcp_runtime_observation
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.tooling import ToolContext, ToolResult


def _fake_server_script() -> Path:
    return Path(__file__).parent / "fixtures" / "fake_mcp_server.py"


def _client(tmp_path: Path, *, mode: str = "normal", command: str = "python") -> StdioMcpClient:
    return StdioMcpClient(
        "fake",
        {
            "command": command,
            "args": [str(_fake_server_script())],
            "protocol": "newline-json",
            "env": {"FAKE_MCP_MODE": mode},
        },
        str(tmp_path),
    )


class RecordingSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[tuple[str, int | None, dict[str, object]]] = []

    def emit(self, event_type: str, *, step: int | None = None, payload: dict[str, object] | None = None) -> None:
        if self.fail:
            raise RuntimeError("sink exploded")
        self.events.append((event_type, step, dict(payload or {})))


def _mcp_events(sink: RecordingSink) -> list[dict[str, object]]:
    return [payload for event_type, _step, payload in sink.events if event_type == "mcp.runtime.observed"]


def test_sink_none_preserves_mcp_result_and_does_no_projection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path)
    calls = 0

    def fail_projection(*_args: Any, **_kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        raise AssertionError("projection must not run without a sink")

    monkeypatch.setattr("minicode.mcp_observation.project_mcp_runtime_observation", fail_projection)

    result = client.call_tool("echo", {"text": "hi"})

    assert result == ToolResult(ok=True, output="echo:hi")
    assert calls == 0
    client.close()


def test_started_client_success_records_reused_connection(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.start()
    sink = RecordingSink()

    result = client.call_tool("echo", {"text": "hi"}, event_sink=sink, step=7, workspace=str(tmp_path))

    assert result.ok is True
    [event] = _mcp_events(sink)
    assert event == {
        "mcpVersion": 1,
        "serverKey": mcp_server_key(str(tmp_path), "fake"),
        "transport": "stdio",
        "activity": "tool_request",
        "outcome": "request_succeeded",
        "connectionAttempted": False,
        "protocol": "newline-json",
    }
    assert sink.events[0][1] == 7
    client.close()


def test_unstarted_client_success_records_connection_attempt(tmp_path: Path) -> None:
    sink = RecordingSink()
    client = _client(tmp_path)

    result = client.call_tool("echo", {"text": "hi"}, event_sink=sink, step=3, workspace=str(tmp_path))

    assert result.ok is True
    [event] = _mcp_events(sink)
    assert event["outcome"] == "request_succeeded"
    assert event["connectionAttempted"] is True
    assert event["protocol"] == "newline-json"
    client.close()


def test_command_not_found_records_connection_failure_without_command(tmp_path: Path) -> None:
    sink = RecordingSink()
    missing_command = tmp_path / f"missing-minicode-mcp-{uuid.uuid4().hex}"
    client = _client(tmp_path, command=str(missing_command))

    with pytest.raises(RuntimeError):
        client.call_tool("echo", {"text": "hi"}, event_sink=sink, workspace=str(tmp_path))

    [event] = _mcp_events(sink)
    assert event["outcome"] == "connection_failed"
    assert event["connectionAttempted"] is True
    assert event["failureKind"] == "command_not_found"
    encoded = json.dumps(event, sort_keys=True)
    assert missing_command.name not in encoded
    assert str(tmp_path) not in encoded


def test_initialize_timeout_records_connection_timeout(tmp_path: Path) -> None:
    sink = RecordingSink()
    client = _client(tmp_path, mode="hang_initialize")

    with pytest.raises(RuntimeError):
        client.call_tool("echo", {"text": "hi"}, event_sink=sink, workspace=str(tmp_path))

    [event] = _mcp_events(sink)
    assert event["outcome"] == "connection_failed"
    assert event["failureKind"] == "timeout"
    client.close()


def test_started_client_request_failure_records_request_failed(tmp_path: Path) -> None:
    sink = RecordingSink()
    client = _client(tmp_path, mode="error_on_call")
    client.start()

    with pytest.raises(RuntimeError):
        client.call_tool("echo", {"text": "secret-input"}, event_sink=sink, workspace=str(tmp_path))

    [event] = _mcp_events(sink)
    assert event["outcome"] == "request_failed"
    assert event["connectionAttempted"] is False
    assert event["failureKind"] == "request_error"
    encoded = json.dumps(event, sort_keys=True)
    assert "secret" not in encoded
    client.close()


def test_dead_process_reconnect_records_connection_attempt(tmp_path: Path) -> None:
    sink = RecordingSink()
    client = _client(tmp_path)
    client.start()
    assert client.process is not None
    client.process.kill()
    client.process.wait(timeout=5)

    result = client.call_tool("echo", {"text": "again"}, event_sink=sink, workspace=str(tmp_path))

    assert result.ok is True
    [event] = _mcp_events(sink)
    assert event["outcome"] == "request_succeeded"
    assert event["connectionAttempted"] is True
    client.close()


def test_protocol_fallback_records_one_terminal_event_with_final_protocol(tmp_path: Path) -> None:
    sink = RecordingSink()
    client = StdioMcpClient(
        "fake",
        {
            "command": "python",
            "args": [str(_fake_server_script())],
            "env": {"FAKE_MCP_MODE": "normal"},
        },
        str(tmp_path),
    )

    result = client.call_tool("echo", {"text": "hi"}, event_sink=sink, workspace=str(tmp_path))

    assert result.ok is True
    events = _mcp_events(sink)
    assert len(events) == 1
    assert events[0]["outcome"] == "request_succeeded"
    assert events[0]["protocol"] == "newline-json"
    client.close()


def test_sink_failure_does_not_change_mcp_result(tmp_path: Path) -> None:
    client = _client(tmp_path)
    sink = RecordingSink(fail=True)

    result = client.call_tool("echo", {"text": "hi"}, event_sink=sink, workspace=str(tmp_path))

    assert result == ToolResult(ok=True, output="echo:hi")
    client.close()


def test_keyboard_interrupt_and_system_exit_are_not_reclassified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path)
    sink = RecordingSink()

    def raise_keyboard() -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr(client, "_ensure_started", raise_keyboard)
    with pytest.raises(KeyboardInterrupt):
        client.call_tool("echo", {}, event_sink=sink, workspace=str(tmp_path))
    assert _mcp_events(sink) == []

    def raise_system_exit() -> None:
        raise SystemExit(2)

    monkeypatch.setattr(client, "_ensure_started", raise_system_exit)
    with pytest.raises(SystemExit):
        client.call_tool("echo", {}, event_sink=sink, workspace=str(tmp_path))
    assert _mcp_events(sink) == []


def test_wrapper_uses_per_invocation_sink_and_does_not_store_it(tmp_path: Path) -> None:
    bundle = create_mcp_backed_tools(
        cwd=str(tmp_path),
        mcp_servers={
            "fake": {
                "command": "python",
                "args": [str(_fake_server_script())],
                "protocol": "newline-json",
            }
        },
    )
    tool = next(tool for tool in bundle["tools"] if tool.name == "mcp__fake__echo")
    sink_a = RecordingSink()
    sink_b = RecordingSink()

    assert tool.run({"text": "a"}, ToolContext(cwd=str(tmp_path), _event_sink=sink_a, _step=1)).ok is True
    assert tool.run({"text": "b"}, ToolContext(cwd=str(tmp_path), _event_sink=sink_b, _step=2)).ok is True
    assert tool.run({"text": "outside"}, ToolContext(cwd=str(tmp_path))).ok is True

    assert len(_mcp_events(sink_a)) == 1
    assert len(_mcp_events(sink_b)) == 1
    assert sink_a.events[0][1] == 1
    assert sink_b.events[0][1] == 2
    bundle["dispose"]()


def test_tool_started_runtime_observed_tool_finished_order(tmp_path: Path) -> None:
    journal = RunJournal(tmp_path, data_dir=tmp_path / "runs")
    bundle = create_mcp_backed_tools(
        cwd=str(tmp_path),
        mcp_servers={
            "fake": {
                "command": "python",
                "args": [str(_fake_server_script())],
                "protocol": "newline-json",
            }
        },
    )
    tool = next(tool for tool in bundle["tools"] if tool.name == "mcp__fake__echo")

    with observe_run(
        workspace=tmp_path,
        source="headless",
        title="MCP runtime order",
        journal_factory=lambda _workspace, _session_id=None: journal,
    ) as observation:
        observation.tool_started("mcp__fake__echo")
        result = tool.run({"text": "hi"}, ToolContext(cwd=str(tmp_path), _event_sink=observation, _step=4))
        observation.tool_finished("mcp__fake__echo", is_error=not result.ok)
        run_id = observation._lifecycle._run_id

    types = [event.type for event in journal.list_events(run_id, limit=20).items]
    assert types[:2] == ["run.queued", "run.started"]
    assert types.index("tool.started") < types.index("mcp.runtime.observed") < types.index("tool.finished")
    assert types[-1] == "run.completed"
    bundle["dispose"]()


def test_projector_rejects_untrusted_payload_shapes(tmp_path: Path) -> None:
    assert project_mcp_runtime_observation(
        workspace=str(tmp_path),
        server_name="fake",
        outcome="request_succeeded",
        connection_attempted=True,
        protocol="newline-json",
    )["serverKey"].startswith("mcpsrv_")
    with pytest.raises(ValueError):
        project_mcp_runtime_observation(
            workspace=str(tmp_path),
            server_name="fake",
            outcome="request_succeeded",
            connection_attempted=True,
            failure="other",
        )
