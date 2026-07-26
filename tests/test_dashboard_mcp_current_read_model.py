from __future__ import annotations

import json
from pathlib import Path

from minicode.mcp_observation import mcp_server_key
from minicode.run_journal import RunJournal
from minicode.web.read_model import DashboardReadModel


CHECKED_AT = "2026-07-18T09:30:00Z"


def _write_config(
    path: Path,
    names: list[str],
    *,
    disabled: set[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    name: {
                        "command": "python",
                        "enabled": name not in (disabled or set()),
                    }
                    for name in names
                }
            }
        ),
        encoding="utf-8",
    )


def _snapshot(
    workspace: Path,
    servers: list[tuple[str, str, int, str | None, str | None]],
    *,
    limited: bool = False,
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "stateVersion": 1,
        "scope": "process",
        "current": "process-local",
        "checkedAt": CHECKED_AT,
        "servers": sorted(
            [
                {
                    "serverKey": mcp_server_key(workspace, name),
                    "state": state,
                    "activeInstanceCount": count,
                    "protocol": protocol,
                    "failureKind": failure_kind,
                    "updatedAt": CHECKED_AT,
                }
                for name, state, count, protocol, failure_kind in servers
            ],
            key=lambda item: item["serverKey"],
        ),
        "coverage": {
            "scope": "gateway-process",
            "crossProcess": "unavailable",
            "heartbeat": False,
            "limited": limited,
        },
        "diagnostics": (
            [{"code": "response_budget_exceeded", "count": 1}]
            if limited
            else []
        ),
    }


def test_configured_mcp_server_keys_are_workspace_scoped_and_opaque(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    _write_config(data_dir / "mcp.json", ["shared", "user-only"])
    _write_config(workspace / ".mcp.json", ["shared", "project-only"])

    keys = DashboardReadModel(
        workspace,
        data_dir=data_dir,
    ).configured_mcp_server_keys()

    assert keys == frozenset(
        {
            mcp_server_key(workspace, "shared"),
            mcp_server_key(workspace, "user-only"),
            mcp_server_key(workspace, "project-only"),
        }
    )
    assert all(str(workspace) not in key for key in keys)
    assert all(
        name not in key
        for name in ("shared", "user-only", "project-only")
        for key in keys
    )


def test_connections_combines_live_current_config_and_independent_history(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    names = ["idle", "starting", "ready", "failed", "missing", "disabled"]
    _write_config(data_dir / "mcp.json", names, disabled={"disabled"})
    loader_calls = 0

    def load(_server_keys: frozenset[str]) -> dict[str, object]:
        nonlocal loader_calls
        loader_calls += 1
        return _snapshot(
            workspace,
            [
                ("idle", "idle", 1, None, None),
                ("starting", "starting", 2, None, None),
                ("ready", "ready", 3, "newline-json", None),
                ("failed", "failed", 1, None, "timeout"),
                ("disabled", "ready", 1, "content-length", None),
                ("hidden-source-name", "ready", 99, "newline-json", None),
            ],
        )

    payload = DashboardReadModel(
        workspace,
        data_dir=data_dir,
        mcp_current_state_loader=load,
    ).connections()

    assert loader_calls == 1
    assert payload["schemaVersion"] == 1
    assert payload["mode"] == "read-only"
    assert payload["source"]["status"] == "live"
    assert payload["source"]["updatedAt"] == CHECKED_AT
    assert payload["summary"] == {
        "gatewayStatus": "live",
        "configuredMcpCount": 6,
        "registeredConfiguredMcpCount": 5,
        "activeMcpInstanceCount": 8,
        "liveMcpCount": 2,
        "complete": True,
        "observedConfiguredCount": 0,
        "unobservedConfiguredCount": 6,
        "unmatchedObservedServerCount": 0,
    }
    assert payload["mcpCurrent"]["status"] == "live"
    assert payload["mcpCurrent"]["byState"] == {
        "idle": 1,
        "starting": 1,
        "ready": 2,
        "failed": 1,
    }
    assert payload["mcpCurrent"]["coverage"] == {
        "scope": "gateway-process",
        "crossProcess": "unavailable",
        "heartbeat": False,
        "association": "configured-current-workspace-only",
        "configuredSet": "complete",
        "unmatched": "suppressed",
        "limited": False,
    }
    by_name = {item["name"]: item for item in payload["mcpServers"]}
    assert by_name["ready"]["liveStatus"] == "ready"
    assert by_name["ready"]["current"] == {
        "status": "live",
        "state": "ready",
        "activeInstanceCount": 3,
        "protocol": "newline-json",
        "failureKind": None,
        "updatedAt": CHECKED_AT,
        "reason": None,
    }
    assert by_name["failed"]["current"]["failureKind"] == "timeout"
    assert by_name["missing"]["current"]["reason"] == "not_registered"
    assert by_name["disabled"]["status"] == "disabled"
    assert by_name["disabled"]["liveStatus"] == "ready"
    encoded = json.dumps(payload)
    assert "hidden-source-name" not in encoded
    assert mcp_server_key(workspace, "hidden-source-name") not in encoded
    assert "99" not in encoded


def test_current_and_historical_truth_can_disagree_without_overwriting_each_other(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    _write_config(data_dir / "mcp.json", ["current-ready", "current-failed"])
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(title="Retained MCP", source="gateway")
    journal.transition(run.id, "running")
    for name, outcome, failure_kind in (
        ("current-ready", "connection_failed", "timeout"),
        ("current-failed", "request_succeeded", None),
    ):
        payload: dict[str, object] = {
            "mcpVersion": 1,
            "serverKey": mcp_server_key(workspace, name),
            "transport": "stdio",
            "activity": "tool_request",
            "outcome": outcome,
            "connectionAttempted": True,
        }
        if failure_kind is not None:
            payload["failureKind"] = failure_kind
        else:
            payload["protocol"] = "newline-json"
        journal.append_event(run.id, "mcp.runtime.observed", payload=payload)
    journal.transition(run.id, "completed")

    response = DashboardReadModel(
        workspace,
        data_dir=data_dir,
        run_journal=journal,
        mcp_current_state_loader=lambda _server_keys: _snapshot(
            workspace,
            [
                ("current-ready", "ready", 1, "content-length", None),
                ("current-failed", "failed", 1, None, "process_exit"),
            ],
        ),
    ).connections()

    by_name = {item["name"]: item for item in response["mcpServers"]}
    assert by_name["current-ready"]["liveStatus"] == "ready"
    assert by_name["current-ready"]["runtime"]["lastOutcome"] == "connection_failed"
    assert by_name["current-failed"]["liveStatus"] == "failed"
    assert by_name["current-failed"]["runtime"]["lastOutcome"] == "request_succeeded"


def test_limited_current_snapshot_keeps_matched_card_and_nulls_all_current_counts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    _write_config(data_dir / "mcp.json", ["ready", "missing"])

    response = DashboardReadModel(
        workspace,
        data_dir=data_dir,
        mcp_current_state_loader=lambda _server_keys: _snapshot(
            workspace,
            [("ready", "ready", 1, "newline-json", None)],
            limited=True,
        ),
    ).connections()

    assert response["source"]["status"] == "live"
    assert response["summary"]["registeredConfiguredMcpCount"] is None
    assert response["summary"]["activeMcpInstanceCount"] is None
    assert response["summary"]["liveMcpCount"] is None
    assert response["mcpCurrent"]["byState"] is None
    assert response["mcpCurrent"]["coverage"]["limited"] is True
    assert {item["name"]: item for item in response["mcpServers"]}["ready"][
        "liveStatus"
    ] == "ready"
    assert {item["name"]: item for item in response["mcpServers"]}["missing"][
        "current"
    ]["reason"] == "snapshot_limited"


def test_partial_config_keeps_live_matches_but_current_aggregates_are_unknown(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    data_dir.mkdir(parents=True)
    (data_dir / "mcp.json").write_text(
        '{"mcpServers": Authorization=user-secret',
        encoding="utf-8",
    )
    _write_config(workspace / ".mcp.json", ["project-ready"])

    response = DashboardReadModel(
        workspace,
        data_dir=data_dir,
        mcp_current_state_loader=lambda _server_keys: _snapshot(
            workspace,
            [("project-ready", "ready", 1, "newline-json", None)],
        ),
    ).connections()

    assert response["source"]["status"] == "error"
    assert response["mcpCurrent"]["status"] == "live"
    assert response["mcpCurrent"]["coverage"]["configuredSet"] == "partial"
    assert response["summary"]["registeredConfiguredMcpCount"] is None
    assert response["summary"]["activeMcpInstanceCount"] is None
    assert response["summary"]["liveMcpCount"] is None
    assert response["mcpServers"][0]["liveStatus"] == "ready"
    assert "user-secret" not in json.dumps(response)


def test_current_source_error_isolated_from_config_and_historical_data(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    _write_config(data_dir / "mcp.json", ["configured"])

    def fail(_server_keys: frozenset[str]) -> dict[str, object]:
        raise OSError("Bearer loader-secret")

    response = DashboardReadModel(
        workspace,
        data_dir=data_dir,
        mcp_current_state_loader=fail,
    ).connections()

    assert response["source"]["status"] == "error"
    assert response["mcpCurrent"]["status"] == "error"
    assert response["mcpRuntime"]["status"] == "unavailable"
    assert response["mcpServers"][0]["status"] == "configured"
    assert response["mcpServers"][0]["liveStatus"] == "error"
    assert response["mcpServers"][0]["current"]["reason"] == "source_error"
    assert "loader-secret" not in json.dumps(response)


def test_historical_source_error_keeps_live_current_and_config_cards(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    _write_config(data_dir / "mcp.json", ["ready"])

    class FailingJournal:
        def list_runs(self, *, limit: int) -> object:
            del limit
            raise OSError("Authorization=history-secret")

    response = DashboardReadModel(
        workspace,
        data_dir=data_dir,
        run_journal=FailingJournal(),  # type: ignore[arg-type]
        mcp_current_state_loader=lambda _server_keys: _snapshot(
            workspace,
            [("ready", "ready", 1, "newline-json", None)],
        ),
    ).connections()

    assert response["source"]["status"] == "error"
    assert response["mcpCurrent"]["status"] == "live"
    assert response["mcpRuntime"]["status"] == "error"
    assert response["mcpServers"][0]["status"] == "configured"
    assert response["mcpServers"][0]["liveStatus"] == "ready"
    assert response["mcpServers"][0]["runtime"]["status"] == "error"
    assert "history-secret" not in json.dumps(response)


def test_fail_once_loader_recovers_on_the_next_manual_read(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    _write_config(data_dir / "mcp.json", ["ready"])
    calls = 0

    def load(_server_keys: frozenset[str]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary-secret")
        return _snapshot(
            workspace,
            [("ready", "ready", 1, "newline-json", None)],
        )

    model = DashboardReadModel(
        workspace,
        data_dir=data_dir,
        mcp_current_state_loader=load,
    )

    first = model.connections()
    second = model.connections()

    assert first["mcpCurrent"]["status"] == "error"
    assert first["mcpServers"][0]["current"]["reason"] == "source_error"
    assert second["mcpCurrent"]["status"] == "live"
    assert second["mcpServers"][0]["liveStatus"] == "ready"
    assert calls == 2
    assert "temporary-secret" not in json.dumps(first)


def test_non_connections_pages_never_call_scoped_loader(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    calls: list[frozenset[str]] = []

    def load(server_keys: frozenset[str]) -> dict[str, object]:
        calls.append(server_keys)
        return _snapshot(workspace, [])

    model = DashboardReadModel(
        workspace,
        data_dir=data_dir,
        session_loader=lambda: [],
        skill_loader=lambda _workspace: [],
        mcp_current_state_loader=load,
    )

    model.snapshot()
    model.runs()
    model.sessions()
    model.memory()
    model.skills()
    model.ops()
    model.system()
    assert calls == []

    model.connections()
    assert calls == [frozenset()]
    model.connections()
    assert calls == [frozenset(), frozenset()]
