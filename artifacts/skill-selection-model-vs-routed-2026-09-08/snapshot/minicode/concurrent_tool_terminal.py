"""Closed, content-free disposition contract for cancelled parallel tools."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

CONCURRENT_TOOL_TERMINAL_EVENT_TYPE = "tool.concurrent_terminal"
CONCURRENT_TOOL_TERMINAL_VERSION = 1
MAX_CONCURRENT_TOOL_TERMINAL_EVENTS = 1_000

_FIELDS = frozenset(
    {
        "terminalVersion",
        "callId",
        "toolName",
        "status",
        "reason",
        "executionMayContinue",
    }
)
_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "cancelled_before_start",
        "abandoned_running",
    }
)
_CALL_REF_RE = re.compile(r"^callref_[0-9a-f]{32}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _project_call_ref(value: object) -> str:
    digest = hashlib.sha256(
        str(value).encode("utf-8", errors="replace")
    ).hexdigest()
    return "callref_" + digest[:32]


def _project_tool_name(value: object) -> str:
    return (
        value
        if isinstance(value, str) and _TOOL_NAME_RE.fullmatch(value)
        else "unknown"
    )


def project_concurrent_tool_terminal(
    *,
    call_id: object,
    tool_name: object,
    status: object,
) -> dict[str, object]:
    """Project one terminal ownership disposition or raise ``ValueError``."""

    payload = {
        "terminalVersion": CONCURRENT_TOOL_TERMINAL_VERSION,
        "callId": _project_call_ref(call_id),
        "toolName": _project_tool_name(tool_name),
        "status": status,
        "reason": "turn_cancelled",
        "executionMayContinue": status == "abandoned_running",
    }
    normalized = normalize_concurrent_tool_terminal(payload)
    if normalized is None:
        raise ValueError("invalid concurrent tool terminal event")
    return normalized


def normalize_concurrent_tool_terminal(
    payload: object,
) -> dict[str, object] | None:
    """Return a canonical event payload only for the exact closed schema."""

    if not isinstance(payload, Mapping) or set(payload) != _FIELDS:
        return None
    terminal_version = payload.get("terminalVersion")
    call_id = payload.get("callId")
    tool_name = payload.get("toolName")
    status = payload.get("status")
    reason = payload.get("reason")
    execution_may_continue = payload.get("executionMayContinue")
    if (
        type(terminal_version) is not int
        or terminal_version != CONCURRENT_TOOL_TERMINAL_VERSION
        or not isinstance(call_id, str)
        or _CALL_REF_RE.fullmatch(call_id) is None
        or not isinstance(tool_name, str)
        or _TOOL_NAME_RE.fullmatch(tool_name) is None
        or not isinstance(status, str)
        or status not in _STATUSES
        or reason != "turn_cancelled"
        or type(execution_may_continue) is not bool
        or execution_may_continue != (status == "abandoned_running")
    ):
        return None
    return {
        "terminalVersion": CONCURRENT_TOOL_TERMINAL_VERSION,
        "callId": call_id,
        "toolName": tool_name,
        "status": status,
        "reason": "turn_cancelled",
        "executionMayContinue": execution_may_continue,
    }


__all__ = [
    "CONCURRENT_TOOL_TERMINAL_EVENT_TYPE",
    "CONCURRENT_TOOL_TERMINAL_VERSION",
    "MAX_CONCURRENT_TOOL_TERMINAL_EVENTS",
    "normalize_concurrent_tool_terminal",
    "project_concurrent_tool_terminal",
]
