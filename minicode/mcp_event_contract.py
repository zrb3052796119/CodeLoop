"""Closed contract for safe MCP runtime observation payloads."""

from __future__ import annotations

from collections.abc import Mapping
import re

MCP_RUNTIME_EVENT = "mcp.runtime.observed"
MCP_VERSION = 1
MCP_TRANSPORT = "stdio"
MCP_ACTIVITY = "tool_request"
MCP_OUTCOMES = frozenset({"request_succeeded", "connection_failed", "request_failed"})
MCP_FAILURE_KINDS = frozenset(
    {"timeout", "command_not_found", "process_exit", "protocol_error", "request_error", "other"}
)
MCP_PROTOCOLS = frozenset({"content-length", "newline-json"})
SERVER_KEY_RE = re.compile(r"^mcpsrv_[0-9a-f]{32}$")
MCP_ALLOWED_KEYS = frozenset(
    {
        "mcpVersion",
        "serverKey",
        "transport",
        "activity",
        "outcome",
        "connectionAttempted",
        "protocol",
        "failureKind",
    }
)
MCP_REQUIRED_KEYS = frozenset(
    {
        "mcpVersion",
        "serverKey",
        "transport",
        "activity",
        "outcome",
        "connectionAttempted",
    }
)


def _is_exact_bool(value: object) -> bool:
    return isinstance(value, bool)


def normalize_mcp_runtime_payload(
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    """Return a canonical safe MCP runtime payload or None if invalid."""
    if not isinstance(payload, Mapping):
        return None
    keys = set(payload.keys())
    if keys - MCP_ALLOWED_KEYS:
        return None
    if not MCP_REQUIRED_KEYS <= keys:
        return None
    version = payload.get("mcpVersion")
    if isinstance(version, bool) or not isinstance(version, int) or version != MCP_VERSION:
        return None
    server_key = payload.get("serverKey")
    if not isinstance(server_key, str) or not SERVER_KEY_RE.fullmatch(server_key):
        return None
    transport = payload.get("transport")
    if transport != MCP_TRANSPORT:
        return None
    activity = payload.get("activity")
    if activity != MCP_ACTIVITY:
        return None
    outcome = payload.get("outcome")
    if outcome not in MCP_OUTCOMES:
        return None
    connection_attempted = payload.get("connectionAttempted")
    if not _is_exact_bool(connection_attempted):
        return None
    protocol = payload.get("protocol")
    if protocol is not None and protocol not in MCP_PROTOCOLS:
        return None
    failure_kind = payload.get("failureKind")
    if outcome == "request_succeeded":
        if "failureKind" in keys:
            return None
    else:
        if failure_kind not in MCP_FAILURE_KINDS:
            return None
    normalized: dict[str, object] = {
        "mcpVersion": MCP_VERSION,
        "serverKey": server_key,
        "transport": MCP_TRANSPORT,
        "activity": MCP_ACTIVITY,
        "outcome": outcome,
        "connectionAttempted": connection_attempted,
    }
    if protocol is not None:
        normalized["protocol"] = protocol
    if outcome != "request_succeeded":
        normalized["failureKind"] = failure_kind
    return normalized


__all__ = [
    "MCP_ACTIVITY",
    "MCP_ALLOWED_KEYS",
    "MCP_FAILURE_KINDS",
    "MCP_OUTCOMES",
    "MCP_PROTOCOLS",
    "MCP_REQUIRED_KEYS",
    "MCP_RUNTIME_EVENT",
    "MCP_TRANSPORT",
    "MCP_VERSION",
    "SERVER_KEY_RE",
    "normalize_mcp_runtime_payload",
]
