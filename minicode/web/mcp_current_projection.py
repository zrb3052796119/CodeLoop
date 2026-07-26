"""Safe read-side projection of process-local MCP current state.

This module owns the association boundary between configured server names and
the non-reversible server keys exposed by the Gateway registry snapshot.  It
does not read configuration, journals, HTTP state, or MCP processes itself.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from minicode.mcp_current_state import normalize_mcp_current_state_snapshot
from minicode.mcp_observation import mcp_server_key


McpCurrentStateLoader = Callable[[frozenset[str]], Mapping[str, object]]

_LIVE_MESSAGE = (
    "Current MCP client state is limited to this Gateway process and this snapshot."
)
_UNAVAILABLE_MESSAGE = (
    "Current MCP client state is unavailable for this Dashboard read model."
)
_ERROR_MESSAGE = "Current MCP client state could not be read safely."
_STATES = ("idle", "starting", "ready", "failed")
_MAX_CONFIGURED_SERVERS = 2_000


@dataclass(frozen=True, slots=True)
class McpCurrentProjectionDiagnostic:
    """One fixed, low-cardinality projection diagnostic."""

    code: str
    count: int

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "count": self.count}


@dataclass(frozen=True, slots=True)
class McpCurrentServerProjection:
    """Current-state fields for one configured server, without its raw key."""

    status: str
    state: str | None
    active_instance_count: int | None
    protocol: str | None
    failure_kind: str | None
    updated_at: str | None
    reason: str | None

    @property
    def live_status(self) -> str:
        if self.status == "live" and self.state in _STATES:
            return self.state
        if self.status == "error":
            return "error"
        return "unavailable"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "state": self.state,
            "activeInstanceCount": self.active_instance_count,
            "protocol": self.protocol,
            "failureKind": self.failure_kind,
            "updatedAt": self.updated_at,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class McpCurrentProjection:
    """Frozen, bounded projection for one request-time registry snapshot."""

    status: str
    current: str
    state_version: int | None
    checked_at: str | None
    by_state: tuple[tuple[str, int], ...] | None
    configured_set: str
    limited: bool
    diagnostics: tuple[McpCurrentProjectionDiagnostic, ...]
    message: str
    servers: tuple[McpCurrentServerProjection, ...]
    registered_configured_mcp_count: int | None
    active_mcp_instance_count: int | None
    live_mcp_count: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "current": self.current,
            "stateVersion": self.state_version,
            "checkedAt": self.checked_at,
            "byState": (
                dict(self.by_state) if self.by_state is not None else None
            ),
            "coverage": {
                "scope": "gateway-process",
                "crossProcess": "unavailable",
                "heartbeat": False,
                "association": "configured-current-workspace-only",
                "configuredSet": self.configured_set,
                "unmatched": "suppressed",
                "limited": self.limited,
            },
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "message": self.message,
        }


def _missing_server(*, status: str, reason: str) -> McpCurrentServerProjection:
    return McpCurrentServerProjection(
        status=status,
        state=None,
        active_instance_count=None,
        protocol=None,
        failure_kind=None,
        updated_at=None,
        reason=reason,
    )


def _non_live_projection(
    configured_count: int,
    *,
    configured_set: str,
    status: str,
    diagnostic_code: str | None,
) -> McpCurrentProjection:
    reason = "source_error" if status == "error" else "source_unavailable"
    server_status = "error" if status == "error" else "unavailable"
    diagnostics = (
        (McpCurrentProjectionDiagnostic(diagnostic_code, 1),)
        if diagnostic_code is not None
        else ()
    )
    return McpCurrentProjection(
        status=status,
        current="unavailable",
        state_version=None,
        checked_at=None,
        by_state=None,
        configured_set=configured_set,
        limited=True,
        diagnostics=diagnostics,
        message=_ERROR_MESSAGE if status == "error" else _UNAVAILABLE_MESSAGE,
        servers=tuple(
            _missing_server(status=server_status, reason=reason)
            for _ in range(configured_count)
        ),
        registered_configured_mcp_count=None,
        active_mcp_instance_count=None,
        live_mcp_count=None,
    )


def project_current_mcp_state(
    workspace: str | Path,
    configured_server_names: Iterable[str],
    snapshot_loader: McpCurrentStateLoader | None,
    *,
    configured_set_complete: bool = True,
) -> McpCurrentProjection:
    """Associate one loader snapshot with the current configured server set.

    The loader is called at most once.  Unmatched snapshot keys never contribute
    fields, counts, or diagnostics to the returned projection.
    """

    bounded_names = tuple(
        islice(configured_server_names, _MAX_CONFIGURED_SERVERS + 1)
    )
    input_limited = len(bounded_names) > _MAX_CONFIGURED_SERVERS
    configured_names = bounded_names[:_MAX_CONFIGURED_SERVERS]
    configured_keys = tuple(
        mcp_server_key(workspace, name) for name in configured_names
    )
    configured_set_complete = configured_set_complete and not input_limited
    configured_set = "complete" if configured_set_complete else "partial"
    if snapshot_loader is None:
        return _non_live_projection(
            len(configured_names),
            configured_set=configured_set,
            status="unavailable",
            diagnostic_code=None,
        )
    try:
        raw_snapshot = snapshot_loader(frozenset(configured_keys))
    except BaseException:  # observation failures must not break Dashboard reads
        return _non_live_projection(
            len(configured_names),
            configured_set=configured_set,
            status="error",
            diagnostic_code="mcp_current_source_failed",
        )
    if not isinstance(raw_snapshot, Mapping):
        normalized = None
    else:
        normalized = normalize_mcp_current_state_snapshot(raw_snapshot)
    if normalized is None:
        return _non_live_projection(
            len(configured_names),
            configured_set=configured_set,
            status="error",
            diagnostic_code="mcp_current_snapshot_invalid",
        )

    by_key = {
        str(item["serverKey"]): item
        for item in normalized["servers"]
        if isinstance(item, Mapping)
    }
    limited = bool(normalized["coverage"]["limited"])
    servers: list[McpCurrentServerProjection] = []
    matched: list[Mapping[str, object]] = []
    for server_key in configured_keys:
        item = by_key.get(server_key)
        if item is None:
            servers.append(
                _missing_server(
                    status="unavailable",
                    reason="snapshot_limited" if limited else "not_registered",
                )
            )
            continue
        matched.append(item)
        servers.append(
            McpCurrentServerProjection(
                status="live",
                state=str(item["state"]),
                active_instance_count=int(item["activeInstanceCount"]),
                protocol=(
                    str(item["protocol"])
                    if item["protocol"] is not None
                    else None
                ),
                failure_kind=(
                    str(item["failureKind"])
                    if item["failureKind"] is not None
                    else None
                ),
                updated_at=str(item["updatedAt"]),
                reason=None,
            )
        )

    exact = configured_set_complete and not limited
    state_counts = {
        state: sum(item["state"] == state for item in matched)
        for state in _STATES
    }
    diagnostics = tuple(
        McpCurrentProjectionDiagnostic(
            code=str(item["code"]),
            count=int(item["count"]),
        )
        for item in normalized["diagnostics"]
    )
    return McpCurrentProjection(
        status="live",
        current="process-local",
        state_version=int(normalized["stateVersion"]),
        checked_at=str(normalized["checkedAt"]),
        by_state=(
            tuple((state, state_counts[state]) for state in _STATES)
            if exact
            else None
        ),
        configured_set=configured_set,
        limited=limited,
        diagnostics=diagnostics,
        message=_LIVE_MESSAGE,
        servers=tuple(servers),
        registered_configured_mcp_count=len(matched) if exact else None,
        active_mcp_instance_count=(
            sum(int(item["activeInstanceCount"]) for item in matched)
            if exact
            else None
        ),
        live_mcp_count=state_counts["ready"] if exact else None,
    )


__all__ = [
    "McpCurrentProjection",
    "McpCurrentProjectionDiagnostic",
    "McpCurrentServerProjection",
    "McpCurrentStateLoader",
    "project_current_mcp_state",
]
