from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from minicode.mcp_current_state import McpCurrentStateRegistry
from minicode.mcp_observation import mcp_server_key
from minicode.web.mcp_current_projection import project_current_mcp_state


CHECKED_AT = "2026-07-18T08:00:00Z"


def _snapshot(
    workspace: Path,
    servers: list[tuple[str, str, int, str | None, str | None]],
    *,
    limited: bool = False,
) -> dict[str, object]:
    diagnostics = (
        [{"code": "response_budget_exceeded", "count": 1}]
        if limited
        else []
    )
    return {
        "schemaVersion": 1,
        "stateVersion": 1,
        "scope": "process",
        "current": "process-local",
        "checkedAt": CHECKED_AT,
        "servers": sorted(
            (
                {
                    "serverKey": mcp_server_key(workspace, name),
                    "state": state,
                    "activeInstanceCount": count,
                    "protocol": protocol,
                    "failureKind": failure_kind,
                    "updatedAt": CHECKED_AT,
                }
                for name, state, count, protocol, failure_kind in servers
            ),
            key=lambda item: item["serverKey"],
        ),
        "coverage": {
            "scope": "gateway-process",
            "crossProcess": "unavailable",
            "heartbeat": False,
            "limited": limited,
        },
        "diagnostics": diagnostics,
    }


def test_missing_loader_returns_bounded_unavailable_projection(tmp_path: Path) -> None:
    projection = project_current_mcp_state(
        tmp_path,
        ["configured"],
        None,
    )

    assert projection.to_dict() == {
        "status": "unavailable",
        "current": "unavailable",
        "stateVersion": None,
        "checkedAt": None,
        "byState": None,
        "coverage": {
            "scope": "gateway-process",
            "crossProcess": "unavailable",
            "heartbeat": False,
            "association": "configured-current-workspace-only",
            "configuredSet": "complete",
            "unmatched": "suppressed",
            "limited": True,
        },
        "diagnostics": [],
        "message": "Current MCP client state is unavailable for this Dashboard read model.",
    }
    assert projection.registered_configured_mcp_count is None
    assert projection.active_mcp_instance_count is None
    assert projection.live_mcp_count is None
    assert projection.servers[0].to_dict() == {
        "status": "unavailable",
        "state": None,
        "activeInstanceCount": None,
        "protocol": None,
        "failureKind": None,
        "updatedAt": None,
        "reason": "source_unavailable",
    }


def test_live_projection_associates_only_current_workspace_configured_servers(
    tmp_path: Path,
) -> None:
    configured = ["idle", "starting", "ready", "failed", "not-registered"]
    snapshot = _snapshot(
        tmp_path,
        [
            ("idle", "idle", 1, None, None),
            ("starting", "starting", 2, None, None),
            ("ready", "ready", 3, "newline-json", None),
            ("failed", "failed", 1, None, "timeout"),
            ("unmatched-secret-name", "ready", 7, "content-length", None),
        ],
    )
    calls = 0

    def load(_server_keys: frozenset[str]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return snapshot

    projection = project_current_mcp_state(tmp_path, configured, load)

    assert calls == 1
    assert projection.to_dict() == {
        "status": "live",
        "current": "process-local",
        "stateVersion": 1,
        "checkedAt": CHECKED_AT,
        "byState": {"idle": 1, "starting": 1, "ready": 1, "failed": 1},
        "coverage": {
            "scope": "gateway-process",
            "crossProcess": "unavailable",
            "heartbeat": False,
            "association": "configured-current-workspace-only",
            "configuredSet": "complete",
            "unmatched": "suppressed",
            "limited": False,
        },
        "diagnostics": [],
        "message": "Current MCP client state is limited to this Gateway process and this snapshot.",
    }
    assert projection.registered_configured_mcp_count == 4
    assert projection.active_mcp_instance_count == 7
    assert projection.live_mcp_count == 1
    assert [server.state for server in projection.servers] == [
        "idle",
        "starting",
        "ready",
        "failed",
        None,
    ]
    assert projection.servers[-1].reason == "not_registered"
    encoded = repr(projection.to_dict()) + repr(
        [server.to_dict() for server in projection.servers]
    )
    assert "unmatched-secret-name" not in encoded
    assert mcp_server_key(tmp_path, "unmatched-secret-name") not in encoded
    assert "activeInstanceCount" not in repr(projection.to_dict())


def test_valid_empty_snapshot_produces_exact_zero_counts(tmp_path: Path) -> None:
    projection = project_current_mcp_state(
        tmp_path,
        ["configured"],
        lambda _server_keys: _snapshot(tmp_path, []),
    )

    assert projection.registered_configured_mcp_count == 0
    assert projection.active_mcp_instance_count == 0
    assert projection.live_mcp_count == 0
    assert projection.to_dict()["byState"] == {
        "idle": 0,
        "starting": 0,
        "ready": 0,
        "failed": 0,
    }
    assert projection.servers[0].reason == "not_registered"


def test_limited_snapshot_preserves_matched_cards_but_nulls_aggregates(
    tmp_path: Path,
) -> None:
    projection = project_current_mcp_state(
        tmp_path,
        ["ready", "missing"],
        lambda _server_keys: _snapshot(
            tmp_path,
            [("ready", "ready", 2, "content-length", None)],
            limited=True,
        ),
    )

    assert projection.status == "live"
    assert projection.registered_configured_mcp_count is None
    assert projection.active_mcp_instance_count is None
    assert projection.live_mcp_count is None
    assert projection.to_dict()["byState"] is None
    assert projection.to_dict()["diagnostics"] == [
        {"code": "response_budget_exceeded", "count": 1}
    ]
    assert projection.servers[0].state == "ready"
    assert projection.servers[1].reason == "snapshot_limited"


def test_partial_config_set_preserves_matches_but_nulls_aggregates(
    tmp_path: Path,
) -> None:
    projection = project_current_mcp_state(
        tmp_path,
        ["ready", "missing"],
        lambda _server_keys: _snapshot(
            tmp_path,
            [("ready", "ready", 1, "newline-json", None)],
        ),
        configured_set_complete=False,
    )

    assert projection.to_dict()["coverage"]["configuredSet"] == "partial"
    assert projection.to_dict()["byState"] is None
    assert projection.registered_configured_mcp_count is None
    assert projection.active_mcp_instance_count is None
    assert projection.live_mcp_count is None
    assert projection.servers[0].state == "ready"
    assert projection.servers[1].reason == "not_registered"


@pytest.mark.parametrize("failure", ["loader", "schema"])
def test_loader_and_schema_errors_are_fixed_safe_error_projections(
    tmp_path: Path,
    failure: str,
) -> None:
    def load(_server_keys: frozenset[str]) -> dict[str, object]:
        if failure == "loader":
            raise RuntimeError("Authorization=projection-secret")
        return {"schemaVersion": 1, "secret": "projection-secret"}

    projection = project_current_mcp_state(tmp_path, ["configured"], load)
    encoded = repr(projection.to_dict()) + repr(projection.servers[0].to_dict())

    assert projection.status == "error"
    assert projection.current == "unavailable"
    assert projection.to_dict()["byState"] is None
    assert projection.servers[0].status == "error"
    assert projection.servers[0].reason == "source_error"
    assert "projection-secret" not in encoded
    expected_code = (
        "mcp_current_source_failed"
        if failure == "loader"
        else "mcp_current_snapshot_invalid"
    )
    assert projection.to_dict()["diagnostics"] == [
        {"code": expected_code, "count": 1}
    ]


def test_projection_and_nested_records_are_frozen(tmp_path: Path) -> None:
    projection = project_current_mcp_state(
        tmp_path,
        ["ready"],
        lambda _server_keys: _snapshot(
            tmp_path,
            [("ready", "ready", 1, "newline-json", None)],
        ),
    )

    assert isinstance(projection.servers, tuple)
    assert isinstance(projection.diagnostics, tuple)
    with pytest.raises(FrozenInstanceError):
        projection.status = "error"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        projection.servers[0].state = "failed"  # type: ignore[misc]


def test_projection_applies_a_hard_configured_input_budget(tmp_path: Path) -> None:
    projection = project_current_mcp_state(
        tmp_path,
        (f"server-{index}" for index in range(10_000)),
        lambda _server_keys: _snapshot(tmp_path, []),
    )

    assert len(projection.servers) == 2_000
    assert projection.to_dict()["coverage"]["configuredSet"] == "partial"
    assert projection.registered_configured_mcp_count is None
    assert projection.active_mcp_instance_count is None
    assert projection.live_mcp_count is None


def test_unmatched_probe_failure_does_not_change_workspace_projection(
    tmp_path: Path,
) -> None:
    registry = McpCurrentStateRegistry(token_factory=lambda: "instance-other")
    other_key = mcp_server_key(tmp_path / "other-workspace", "configured")
    probe_calls = 0

    def unmatched_probe() -> bool:
        nonlocal probe_calls
        probe_calls += 1
        raise RuntimeError("Authorization=other-workspace-secret")

    handle = registry.register(other_key, probe=unmatched_probe)
    assert handle is not None
    assert registry.mark_ready(handle, protocol="newline-json") is True

    projection = project_current_mcp_state(
        tmp_path,
        ["configured-missing"],
        lambda keys: registry.snapshot_for(keys).to_dict(),
    )

    assert probe_calls == 0
    assert projection.diagnostics == ()
    assert projection.limited is False
    assert projection.registered_configured_mcp_count == 0
    assert projection.active_mcp_instance_count == 0
    assert projection.live_mcp_count == 0
    assert projection.to_dict()["byState"] == {
        "idle": 0,
        "starting": 0,
        "ready": 0,
        "failed": 0,
    }
    assert projection.servers[0].reason == "not_registered"


def test_unmatched_response_budget_does_not_limit_scoped_snapshot(
    tmp_path: Path,
) -> None:
    registry = McpCurrentStateRegistry(
        token_factory=iter(f"token-{index}" for index in range(5)).__next__,
        max_response_servers=1,
    )
    selected = registry.register(
        mcp_server_key(tmp_path, "visible"),
        probe=lambda: True,
    )
    assert selected is not None
    assert registry.mark_ready(selected, protocol="newline-json") is True
    for index in range(3):
        handle = registry.register(
            mcp_server_key(tmp_path / "other", f"server-{index}"),
            probe=lambda: True,
        )
        assert handle is not None
        assert registry.mark_ready(handle, protocol="content-length") is True
    assert registry.snapshot().coverage.limited is True

    projection = project_current_mcp_state(
        tmp_path,
        ["visible"],
        lambda keys: registry.snapshot_for(keys).to_dict(),
    )

    assert projection.limited is False
    assert projection.diagnostics == ()
    assert projection.registered_configured_mcp_count == 1
    assert projection.active_mcp_instance_count == 1
    assert projection.live_mcp_count == 1
    assert projection.servers[0].state == "ready"


def test_scoped_loader_called_once_with_exact_bounded_key_set(
    tmp_path: Path,
) -> None:
    calls: list[frozenset[str]] = []

    def load(keys: frozenset[str]) -> dict[str, object]:
        calls.append(keys)
        return _snapshot(tmp_path, [])

    projection = project_current_mcp_state(
        tmp_path,
        (f"server-{index}" for index in range(10_000)),
        load,
    )

    expected = frozenset(
        mcp_server_key(tmp_path, f"server-{index}")
        for index in range(2_000)
    )
    assert calls == [expected]
    assert len(projection.servers) == 2_000
    assert projection.configured_set == "partial"


def test_same_server_name_in_two_workspaces_does_not_cross_associate(
    tmp_path: Path,
) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    registry = McpCurrentStateRegistry(
        token_factory=iter(("instance-a", "instance-b")).__next__,
    )
    probe_calls = {"a": 0, "b": 0}

    def probe(workspace: str) -> bool:
        probe_calls[workspace] += 1
        return True

    handle_a = registry.register(
        mcp_server_key(workspace_a, "shared"),
        probe=lambda: probe("a"),
    )
    handle_b = registry.register(
        mcp_server_key(workspace_b, "shared"),
        probe=lambda: probe("b"),
    )
    assert handle_a is not None and handle_b is not None
    assert registry.mark_ready(handle_a, protocol="newline-json") is True
    assert registry.mark_ready(handle_b, protocol="content-length") is True

    projection = project_current_mcp_state(
        workspace_a,
        ["shared"],
        lambda keys: registry.snapshot_for(keys).to_dict(),
    )

    assert projection.servers[0].state == "ready"
    assert projection.servers[0].protocol == "newline-json"
    assert probe_calls == {"a": 1, "b": 0}
