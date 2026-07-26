"""Safe, run-scoped MCP runtime observation projection.

This module owns the stable server key, low-cardinality failure classifier,
and closed payload projection for MCP runtime facts.  It never stores raw MCP
configuration, command lines, request/response payloads, stderr, or exception
text.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from minicode.mcp_event_contract import (
    MCP_ACTIVITY,
    MCP_FAILURE_KINDS,
    MCP_OUTCOMES,
    MCP_PROTOCOLS,
    MCP_RUNTIME_EVENT,
    MCP_TRANSPORT,
    MCP_VERSION,
    SERVER_KEY_RE,
    normalize_mcp_runtime_payload,
)
from minicode.run_events import AgentEventSink, emit_event_safely
from minicode.run_journal import stable_workspace_id


def mcp_server_key(workspace: str | Path, server_name: str) -> str:
    """Return the deterministic, workspace-scoped, non-reversible server key."""
    workspace_identity = stable_workspace_id(workspace)
    digest = hashlib.sha256(
        f"{workspace_identity}\0{server_name}".encode("utf-8")
    ).hexdigest()[:32]
    return f"mcpsrv_{digest}"


def classify_mcp_failure(error: BaseException | None) -> str:
    """Classify an MCP failure without returning original exception text."""
    if error is None:
        return "other"
    if isinstance(error, TimeoutError):
        return "timeout"
    error_type = type(error).__name__.casefold()
    if "timeout" in error_type:
        return "timeout"
    text = str(error).casefold()
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "command not found" in text or "no such file" in text or "not in the allowed list" in text:
        return "command_not_found"
    if "process exited" in text or "closed before completing" in text:
        return "process_exit"
    if "json" in text or "protocol" in text or "content-length" in text:
        return "protocol_error"
    if text.startswith("mcp ") or "tools/call" in text or "request" in text:
        return "request_error"
    return "other"


def project_mcp_runtime_observation(
    *,
    workspace: str | Path,
    server_name: str,
    outcome: str,
    connection_attempted: bool,
    protocol: str | None = None,
    failure: BaseException | str | None = None,
) -> dict[str, object]:
    """Project one terminal MCP tool-request fact into the closed safe schema."""
    if outcome not in MCP_OUTCOMES:
        raise ValueError("invalid MCP outcome")
    if not isinstance(connection_attempted, bool):
        raise ValueError("invalid MCP connectionAttempted")
    payload: dict[str, object] = {
        "mcpVersion": MCP_VERSION,
        "serverKey": mcp_server_key(workspace, server_name),
        "transport": MCP_TRANSPORT,
        "activity": MCP_ACTIVITY,
        "outcome": outcome,
        "connectionAttempted": connection_attempted,
    }
    if protocol is not None:
        if protocol not in MCP_PROTOCOLS:
            raise ValueError("invalid MCP protocol")
        payload["protocol"] = protocol
    if outcome != "request_succeeded":
        if isinstance(failure, str):
            failure_kind = failure if failure in MCP_FAILURE_KINDS else "other"
        else:
            failure_kind = classify_mcp_failure(failure)
        payload["failureKind"] = failure_kind
    elif failure is not None:
        raise ValueError("successful MCP observation cannot include failure")
    normalized = normalize_mcp_runtime_payload(payload)
    if normalized is None:
        raise ValueError("invalid MCP runtime payload")
    return normalized


def emit_mcp_runtime_observation_safely(
    sink: AgentEventSink | None,
    *,
    step: int | None,
    workspace: str | Path,
    server_name: str,
    outcome: str,
    connection_attempted: bool,
    protocol: str | None = None,
    failure: BaseException | str | None = None,
) -> None:
    """Emit one MCP runtime fact without allowing observation failure into MCP."""
    if sink is None:
        return
    try:
        payload = project_mcp_runtime_observation(
            workspace=workspace,
            server_name=server_name,
            outcome=outcome,
            connection_attempted=connection_attempted,
            protocol=protocol,
            failure=failure,
        )
    except Exception:  # noqa: BLE001 - observation is optional and best effort
        return
    emit_event_safely(sink, MCP_RUNTIME_EVENT, step=step, payload=payload)


__all__ = [
    "MCP_ACTIVITY",
    "MCP_FAILURE_KINDS",
    "MCP_OUTCOMES",
    "MCP_PROTOCOLS",
    "MCP_RUNTIME_EVENT",
    "MCP_TRANSPORT",
    "MCP_VERSION",
    "SERVER_KEY_RE",
    "classify_mcp_failure",
    "emit_mcp_runtime_observation_safely",
    "mcp_server_key",
    "project_mcp_runtime_observation",
]
