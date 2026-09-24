"""Bounded process-local MCP client current-state observations.

This module owns the closed snapshot contract and synchronization.  It does not
start clients, issue MCP requests, persist data, or know server configuration.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import threading
import uuid

from minicode.mcp_event_contract import (
    MCP_FAILURE_KINDS,
    MCP_PROTOCOLS,
    SERVER_KEY_RE,
)


MCP_CURRENT_STATE_SCHEMA_VERSION = 1
MCP_CURRENT_STATE_VERSION = 1
MCP_CURRENT_STATES = frozenset({"idle", "starting", "ready", "failed"})

DEFAULT_MAX_INSTANCES = 256
DEFAULT_MAX_RESPONSE_SERVERS = 100
DEFAULT_MAX_DIAGNOSTICS = 20
DEFAULT_MAX_SCOPED_SERVER_KEYS = 2_000

_SNAPSHOT_KEYS = frozenset(
    {
        "schemaVersion",
        "stateVersion",
        "scope",
        "current",
        "checkedAt",
        "servers",
        "coverage",
        "diagnostics",
    }
)
_SERVER_KEYS = frozenset(
    {
        "serverKey",
        "state",
        "activeInstanceCount",
        "protocol",
        "failureKind",
        "updatedAt",
    }
)
_COVERAGE_KEYS = frozenset(
    {"scope", "crossProcess", "heartbeat", "limited"}
)
_DIAGNOSTIC_KEYS = frozenset({"code", "count"})
_DIAGNOSTIC_CODES = frozenset(
    {
        "clock_unavailable",
        "diagnostic_budget_exceeded",
        "instance_budget_exceeded",
        "invalid_registration",
        "invalid_transition",
        "probe_failed",
        "response_budget_exceeded",
        "token_factory_unavailable",
    }
)
_STATE_PRECEDENCE = {"idle": 0, "failed": 1, "starting": 2, "ready": 3}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_token() -> str:
    return uuid.uuid4().hex


def _format_timestamp(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    timespec = "microseconds" if normalized.microsecond else "seconds"
    return normalized.isoformat(timespec=timespec).replace("+00:00", "Z")


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _exact_int(value: object, *, minimum: int = 0) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= minimum


@dataclass(frozen=True, slots=True)
class McpCurrentServerState:
    """One redacted aggregate for active instances sharing a server key."""

    server_key: str
    state: str
    active_instance_count: int
    protocol: str | None
    failure_kind: str | None
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "serverKey": self.server_key,
            "state": self.state,
            "activeInstanceCount": self.active_instance_count,
            "protocol": self.protocol,
            "failureKind": self.failure_kind,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class McpCurrentStateDiagnostic:
    """Fixed low-cardinality registry diagnostic."""

    code: str
    count: int

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "count": self.count}


@dataclass(frozen=True, slots=True)
class McpCurrentStateCoverage:
    """Explicit process and completeness boundary for one snapshot."""

    limited: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "scope": "gateway-process",
            "crossProcess": "unavailable",
            "heartbeat": False,
            "limited": self.limited,
        }


@dataclass(frozen=True, slots=True)
class McpCurrentStateSnapshot:
    """Immutable, bounded current-process snapshot."""

    checked_at: str
    servers: tuple[McpCurrentServerState, ...]
    coverage: McpCurrentStateCoverage
    diagnostics: tuple[McpCurrentStateDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a fresh mutable JSON projection without sharing state."""
        return {
            "schemaVersion": MCP_CURRENT_STATE_SCHEMA_VERSION,
            "stateVersion": MCP_CURRENT_STATE_VERSION,
            "scope": "process",
            "current": "process-local",
            "checkedAt": self.checked_at,
            "servers": [server.to_dict() for server in self.servers],
            "coverage": self.coverage.to_dict(),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


class _McpInstanceHandle:
    """Opaque registry ownership handle; its token is never projected."""

    __slots__ = ("_owner", "_token")

    def __init__(self, owner: object, token: str) -> None:
        self._owner = owner
        self._token = token

    def __repr__(self) -> str:
        return "<McpCurrentStateHandle>"


@dataclass(slots=True)
class _InstanceState:
    server_key: str
    state: str
    protocol: str | None
    failure_kind: str | None
    updated_at: datetime
    updated_sequence: int
    probe: Callable[[], bool]
    revision: int = 0


def normalize_mcp_current_state_snapshot(
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    """Return a canonical safe current-state snapshot, or ``None`` if invalid."""
    if not isinstance(payload, Mapping) or set(payload) != _SNAPSHOT_KEYS:
        return None
    schema_version = payload.get("schemaVersion")
    state_version = payload.get("stateVersion")
    if (
        not _exact_int(schema_version)
        or schema_version != MCP_CURRENT_STATE_SCHEMA_VERSION
        or not _exact_int(state_version)
        or state_version != MCP_CURRENT_STATE_VERSION
        or payload.get("scope") != "process"
        or payload.get("current") != "process-local"
        or not _valid_timestamp(payload.get("checkedAt"))
    ):
        return None

    coverage = payload.get("coverage")
    if not isinstance(coverage, Mapping) or set(coverage) != _COVERAGE_KEYS:
        return None
    if (
        coverage.get("scope") != "gateway-process"
        or coverage.get("crossProcess") != "unavailable"
        or coverage.get("heartbeat") is not False
        or not isinstance(coverage.get("limited"), bool)
    ):
        return None

    raw_servers = payload.get("servers")
    if not isinstance(raw_servers, list) or len(raw_servers) > DEFAULT_MAX_RESPONSE_SERVERS:
        return None
    servers: list[dict[str, object]] = []
    previous_key: str | None = None
    for raw_server in raw_servers:
        if not isinstance(raw_server, Mapping) or set(raw_server) != _SERVER_KEYS:
            return None
        server_key = raw_server.get("serverKey")
        state = raw_server.get("state")
        count = raw_server.get("activeInstanceCount")
        protocol = raw_server.get("protocol")
        failure_kind = raw_server.get("failureKind")
        if (
            not isinstance(server_key, str)
            or not SERVER_KEY_RE.fullmatch(server_key)
            or (previous_key is not None and server_key <= previous_key)
            or not isinstance(state, str)
            or state not in MCP_CURRENT_STATES
            or not _exact_int(count, minimum=1)
            or count > DEFAULT_MAX_INSTANCES
            or (
                protocol is not None
                and (
                    not isinstance(protocol, str)
                    or protocol not in MCP_PROTOCOLS
                )
            )
            or (
                failure_kind is not None
                and (
                    not isinstance(failure_kind, str)
                    or failure_kind not in MCP_FAILURE_KINDS
                )
            )
            or not _valid_timestamp(raw_server.get("updatedAt"))
        ):
            return None
        if state == "ready" and (protocol not in MCP_PROTOCOLS or failure_kind is not None):
            return None
        if state in {"idle", "starting"} and (
            protocol is not None or failure_kind is not None
        ):
            return None
        if state == "failed" and failure_kind not in MCP_FAILURE_KINDS:
            return None
        previous_key = server_key
        servers.append(
            {
                "serverKey": server_key,
                "state": state,
                "activeInstanceCount": count,
                "protocol": protocol,
                "failureKind": failure_kind,
                "updatedAt": raw_server["updatedAt"],
            }
        )

    raw_diagnostics = payload.get("diagnostics")
    if (
        not isinstance(raw_diagnostics, list)
        or len(raw_diagnostics) > DEFAULT_MAX_DIAGNOSTICS
    ):
        return None
    diagnostics: list[dict[str, object]] = []
    previous_code: str | None = None
    for raw_diagnostic in raw_diagnostics:
        if (
            not isinstance(raw_diagnostic, Mapping)
            or set(raw_diagnostic) != _DIAGNOSTIC_KEYS
        ):
            return None
        code = raw_diagnostic.get("code")
        count = raw_diagnostic.get("count")
        if (
            not isinstance(code, str)
            or code not in _DIAGNOSTIC_CODES
            or (previous_code is not None and code <= previous_code)
            or not _exact_int(count, minimum=1)
        ):
            return None
        previous_code = code
        diagnostics.append({"code": code, "count": count})

    if coverage["limited"] is not bool(diagnostics):
        return None

    return {
        "schemaVersion": MCP_CURRENT_STATE_SCHEMA_VERSION,
        "stateVersion": MCP_CURRENT_STATE_VERSION,
        "scope": "process",
        "current": "process-local",
        "checkedAt": payload["checkedAt"],
        "servers": servers,
        "coverage": {
            "scope": "gateway-process",
            "crossProcess": "unavailable",
            "heartbeat": False,
            "limited": coverage["limited"],
        },
        "diagnostics": diagnostics,
    }


class McpCurrentStateRegistry:
    """Thread-safe bounded ownership registry for active MCP client instances."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = _utc_now,
        token_factory: Callable[[], str] = _new_token,
        max_instances: int = DEFAULT_MAX_INSTANCES,
        max_response_servers: int = DEFAULT_MAX_RESPONSE_SERVERS,
        max_diagnostics: int = DEFAULT_MAX_DIAGNOSTICS,
    ) -> None:
        for value, name in (
            (max_instances, "max_instances"),
            (max_response_servers, "max_response_servers"),
            (max_diagnostics, "max_diagnostics"),
        ):
            if not _exact_int(value, minimum=1):
                raise ValueError(f"{name} must be a positive integer")
        self._clock = clock
        self._token_factory = token_factory
        self._max_instances = min(max_instances, DEFAULT_MAX_INSTANCES)
        self._max_response_servers = min(
            max_response_servers, DEFAULT_MAX_RESPONSE_SERVERS
        )
        self._max_diagnostics = min(max_diagnostics, DEFAULT_MAX_DIAGNOSTICS)
        self._lock = threading.RLock()
        self._owner = object()
        self._sequence = 0
        self._instances: dict[str, _InstanceState] = {}
        self._handles: dict[str, _McpInstanceHandle] = {}
        self._diagnostics: dict[str, int] = {}

    def _record_diagnostic(self, code: str) -> None:
        with self._lock:
            self._diagnostics[code] = self._diagnostics.get(code, 0) + 1

    def _now(self) -> datetime:
        try:
            value = self._clock()
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValueError("clock must return an aware datetime")
            return value.astimezone(timezone.utc)
        except BaseException:  # observer faults must never escape into MCP work
            self._record_diagnostic("clock_unavailable")
            return _EPOCH

    def _new_unique_token(self) -> str | None:
        for _ in range(4):
            try:
                token = self._token_factory()
            except BaseException:
                self._record_diagnostic("token_factory_unavailable")
                return None
            if not isinstance(token, str) or not token or len(token) > 256:
                self._record_diagnostic("token_factory_unavailable")
                return None
            with self._lock:
                if token not in self._instances:
                    return token
        self._record_diagnostic("token_factory_unavailable")
        return None

    def register(
        self,
        server_key: str,
        *,
        probe: Callable[[], bool],
    ) -> object | None:
        """Register one client without starting it and return an opaque handle."""
        if (
            not isinstance(server_key, str)
            or not SERVER_KEY_RE.fullmatch(server_key)
            or not callable(probe)
        ):
            self._record_diagnostic("invalid_registration")
            return None
        with self._lock:
            if len(self._instances) >= self._max_instances:
                self._record_diagnostic("instance_budget_exceeded")
                return None
        token = self._new_unique_token()
        if token is None:
            return None
        now = self._now()
        with self._lock:
            if len(self._instances) >= self._max_instances or token in self._instances:
                self._record_diagnostic("instance_budget_exceeded")
                return None
            self._sequence += 1
            self._instances[token] = _InstanceState(
                server_key=server_key,
                state="idle",
                protocol=None,
                failure_kind=None,
                updated_at=now,
                updated_sequence=self._sequence,
                probe=probe,
            )
            handle = _McpInstanceHandle(self._owner, token)
            self._handles[token] = handle
        return handle

    def _handle_token(self, handle: object) -> str | None:
        if (
            not isinstance(handle, _McpInstanceHandle)
            or handle._owner is not self._owner
        ):
            return None
        with self._lock:
            if self._handles.get(handle._token) is not handle:
                return None
            return handle._token

    def _transition(
        self,
        handle: object,
        *,
        state: str,
        protocol: str | None,
        failure_kind: str | None,
    ) -> bool:
        token = self._handle_token(handle)
        if token is None:
            self._record_diagnostic("invalid_transition")
            return False
        now = self._now()
        with self._lock:
            instance = self._instances.get(token)
            if instance is None:
                return False
            instance.state = state
            instance.protocol = protocol
            instance.failure_kind = failure_kind
            instance.updated_at = now
            self._sequence += 1
            instance.updated_sequence = self._sequence
            instance.revision += 1
            return True

    def mark_starting(self, handle: object) -> bool:
        return self._transition(
            handle,
            state="starting",
            protocol=None,
            failure_kind=None,
        )

    def mark_ready(self, handle: object, *, protocol: str) -> bool:
        if not isinstance(protocol, str) or protocol not in MCP_PROTOCOLS:
            self._record_diagnostic("invalid_transition")
            return False
        return self._transition(
            handle,
            state="ready",
            protocol=protocol,
            failure_kind=None,
        )

    def mark_failed(
        self,
        handle: object,
        *,
        failure_kind: str,
        protocol: str | None = None,
    ) -> bool:
        if (
            not isinstance(failure_kind, str)
            or failure_kind not in MCP_FAILURE_KINDS
            or (
                protocol is not None
                and (
                    not isinstance(protocol, str)
                    or protocol not in MCP_PROTOCOLS
                )
            )
        ):
            self._record_diagnostic("invalid_transition")
            return False
        return self._transition(
            handle,
            state="failed",
            protocol=protocol,
            failure_kind=failure_kind,
        )

    def unregister(self, handle: object) -> bool:
        token = self._handle_token(handle)
        if token is None:
            self._record_diagnostic("invalid_transition")
            return False
        with self._lock:
            removed = self._instances.pop(token, None) is not None
            if removed:
                self._handles.pop(token, None)
            return removed

    def snapshot(self) -> McpCurrentStateSnapshot:
        """Return the backward-compatible unscoped process snapshot."""
        return self._snapshot(None)

    def snapshot_for(
        self,
        server_keys: Collection[str],
    ) -> McpCurrentStateSnapshot:
        """Return a snapshot selected before probes by bounded opaque keys."""
        if isinstance(server_keys, (str, bytes)) or not isinstance(
            server_keys,
            Collection,
        ):
            raise TypeError("server_keys must be a bounded collection")
        if len(server_keys) > DEFAULT_MAX_SCOPED_SERVER_KEYS:
            raise ValueError("server_keys exceeds the scoped input budget")
        selected_keys: set[str] = set()
        for server_key in server_keys:
            if (
                not isinstance(server_key, str)
                or not SERVER_KEY_RE.fullmatch(server_key)
            ):
                raise ValueError("server_keys contains an invalid server key")
            selected_keys.add(server_key)
        return self._snapshot(frozenset(selected_keys))

    def _snapshot(
        self,
        server_keys: frozenset[str] | None,
    ) -> McpCurrentStateSnapshot:
        """Probe selected ready instances outside the lock and aggregate them."""
        scoped = server_keys is not None
        request_diagnostics: dict[str, int] = {}
        response_limited = False
        if scoped:
            sorted_keys = sorted(server_keys)
            response_limited = len(sorted_keys) > self._max_response_servers
            visible_keys = frozenset(sorted_keys[: self._max_response_servers])
            if response_limited:
                request_diagnostics["response_budget_exceeded"] = 1
            try:
                checked_at = self._clock()
                if not isinstance(checked_at, datetime) or checked_at.tzinfo is None:
                    raise ValueError("clock must return an aware datetime")
                checked_at = checked_at.astimezone(timezone.utc)
            except BaseException:
                checked_at = _EPOCH
        else:
            visible_keys = None
            checked_at = self._now()
        with self._lock:
            probes = tuple(
                (token, instance.revision, instance.probe)
                for token, instance in self._instances.items()
                if instance.state == "ready"
                and (
                    visible_keys is None
                    or instance.server_key in visible_keys
                )
            )

        probe_results: list[tuple[str, int, str | None]] = []
        for token, revision, probe in probes:
            try:
                alive = probe()
                if alive is True:
                    probe_results.append((token, revision, None))
                else:
                    probe_results.append((token, revision, "process_exit"))
            except BaseException:
                if scoped:
                    request_diagnostics["probe_failed"] = (
                        request_diagnostics.get("probe_failed", 0) + 1
                    )
                else:
                    self._record_diagnostic("probe_failed")
                probe_results.append((token, revision, "other"))

        with self._lock:
            for token, revision, failure_kind in probe_results:
                if failure_kind is None:
                    continue
                instance = self._instances.get(token)
                if (
                    instance is not None
                    and instance.revision == revision
                    and instance.state == "ready"
                ):
                    instance.state = "failed"
                    instance.failure_kind = failure_kind
                    instance.updated_at = checked_at
                    self._sequence += 1
                    instance.updated_sequence = self._sequence
                    instance.revision += 1

            grouped: dict[str, list[tuple[str, _InstanceState]]] = {}
            for token, instance in self._instances.items():
                if (
                    visible_keys is not None
                    and instance.server_key not in visible_keys
                ):
                    continue
                grouped.setdefault(instance.server_key, []).append((token, instance))

            projected: list[McpCurrentServerState] = []
            for server_key in sorted(grouped):
                instances = grouped[server_key]
                winner_token, winner = max(
                    instances,
                    key=lambda item: (
                        _STATE_PRECEDENCE[item[1].state],
                        item[1].updated_at,
                        item[1].updated_sequence,
                    ),
                )
                del winner_token
                projected.append(
                    McpCurrentServerState(
                        server_key=server_key,
                        state=winner.state,
                        active_instance_count=len(instances),
                        protocol=winner.protocol,
                        failure_kind=winner.failure_kind,
                        updated_at=_format_timestamp(winner.updated_at),
                    )
                )

            if not scoped:
                response_limited = len(projected) > self._max_response_servers
                if response_limited:
                    self._record_diagnostic("response_budget_exceeded")
                    projected = projected[: self._max_response_servers]

            diagnostics_items = sorted(
                request_diagnostics.items()
                if scoped
                else self._diagnostics.items()
            )
            diagnostics_limited = len(diagnostics_items) > self._max_diagnostics
            if diagnostics_limited:
                diagnostics_items = diagnostics_items[: self._max_diagnostics]
                if self._max_diagnostics:
                    diagnostics_items[-1] = (
                        "diagnostic_budget_exceeded",
                        len(request_diagnostics if scoped else self._diagnostics)
                        - self._max_diagnostics
                        + 1,
                    )
                    diagnostics_items.sort()
            diagnostics = tuple(
                McpCurrentStateDiagnostic(code=code, count=count)
                for code, count in diagnostics_items
            )
            limited = bool(diagnostics) or response_limited or diagnostics_limited

        return McpCurrentStateSnapshot(
            checked_at=_format_timestamp(checked_at),
            servers=tuple(projected),
            coverage=McpCurrentStateCoverage(limited=limited),
            diagnostics=diagnostics,
        )


__all__ = [
    "DEFAULT_MAX_DIAGNOSTICS",
    "DEFAULT_MAX_INSTANCES",
    "DEFAULT_MAX_RESPONSE_SERVERS",
    "MCP_CURRENT_STATES",
    "MCP_CURRENT_STATE_SCHEMA_VERSION",
    "MCP_CURRENT_STATE_VERSION",
    "McpCurrentServerState",
    "McpCurrentStateCoverage",
    "McpCurrentStateDiagnostic",
    "McpCurrentStateRegistry",
    "McpCurrentStateSnapshot",
    "normalize_mcp_current_state_snapshot",
]
