"""Content-free RunJournal contract for Gateway permission observations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


PERMISSION_EVENT_VERSION = 1
PERMISSION_EVENT_TYPES = frozenset(
    {"permission.requested", "permission.decided"}
)
PERMISSION_DECISION_KINDS = frozenset(
    {"allowed", "denied", "expired", "cancelled", "closed", "unavailable"}
)
PERMISSION_KINDS = frozenset({"path", "command", "edit", "network"})

_PERMISSION_ID_RE = re.compile(r"^permission_[0-9a-f]{32}$")
_TOOL_OPERATION_ID_RE = re.compile(r"^permissiontool_[0-9a-f]{32}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def normalize_permission_event_payload(
    event_type: str,
    payload: Mapping[str, Any] | None,
) -> dict[str, object] | None:
    """Return an exact safe payload, or ``None`` for any contract violation."""
    if event_type not in PERMISSION_EVENT_TYPES or not isinstance(payload, dict):
        return None
    if event_type == "permission.requested":
        if set(payload) != {
            "permissionVersion",
            "permissionId",
            "kind",
            "toolName",
            "toolOperationId",
            "reviewable",
        }:
            return None
        version = payload.get("permissionVersion")
        permission_id = payload.get("permissionId")
        kind = payload.get("kind")
        tool_name = payload.get("toolName")
        operation_id = payload.get("toolOperationId")
        reviewable = payload.get("reviewable")
        if (
            isinstance(version, bool)
            or version != PERMISSION_EVENT_VERSION
            or not isinstance(permission_id, str)
            or _PERMISSION_ID_RE.fullmatch(permission_id) is None
            or kind not in PERMISSION_KINDS
            or not isinstance(tool_name, str)
            or _TOOL_NAME_RE.fullmatch(tool_name) is None
            or not isinstance(operation_id, str)
            or _TOOL_OPERATION_ID_RE.fullmatch(operation_id) is None
            or not isinstance(reviewable, bool)
        ):
            return None
        return {
            "permissionVersion": PERMISSION_EVENT_VERSION,
            "permissionId": permission_id,
            "kind": kind,
            "toolName": tool_name,
            "toolOperationId": operation_id,
            "reviewable": reviewable,
        }

    if set(payload) != {"permissionVersion", "permissionId", "decisionKind"}:
        return None
    version = payload.get("permissionVersion")
    permission_id = payload.get("permissionId")
    decision_kind = payload.get("decisionKind")
    if (
        isinstance(version, bool)
        or version != PERMISSION_EVENT_VERSION
        or not isinstance(permission_id, str)
        or _PERMISSION_ID_RE.fullmatch(permission_id) is None
        or decision_kind not in PERMISSION_DECISION_KINDS
    ):
        return None
    return {
        "permissionVersion": PERMISSION_EVENT_VERSION,
        "permissionId": permission_id,
        "decisionKind": decision_kind,
    }


__all__ = [
    "PERMISSION_DECISION_KINDS",
    "PERMISSION_EVENT_TYPES",
    "PERMISSION_EVENT_VERSION",
    "PERMISSION_KINDS",
    "normalize_permission_event_payload",
]
