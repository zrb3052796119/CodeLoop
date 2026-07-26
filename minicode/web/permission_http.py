"""Strict loopback HTTP adapter for the process-local permission authority."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol
from urllib.parse import urlsplit

from minicode.permission_approval import (
    PERMISSION_ID_RE,
    PermissionApprovalBroker,
    PermissionApprovalError,
    TURN_ID_RE,
    is_loopback_gateway_host,
)


PERMISSION_DECISION_MAX_REQUEST_BODY_BYTES = 1_024
_DECISION_PATH_RE = re.compile(
    r"^/api/v1/permissions/(permission_[0-9a-f]{32})/decision$"
)

_ERROR_STATUS = {
    "invalid_request": 400,
    "permission_not_found": 404,
    "permission_turn_mismatch": 409,
    "permission_already_decided": 409,
    "permission_expired": 409,
    "permission_cancelled": 409,
    "permission_unavailable": 503,
    "permission_not_reviewable": 409,
}
_ERROR_MESSAGES = {
    "invalid_request": "Permission request is invalid.",
    "permission_not_found": "Permission request was not found.",
    "permission_turn_mismatch": "Permission request does not belong to this Turn.",
    "permission_already_decided": "Permission request already has a different decision.",
    "permission_expired": "Permission request has expired.",
    "permission_cancelled": "Permission request was cancelled.",
    "permission_unavailable": "Permission approval is unavailable.",
    "permission_not_reviewable": "Permission request cannot be safely approved.",
}


class _PermissionHandler(Protocol):
    path: str
    headers: Any
    rfile: Any
    server: Any

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None: ...


class _InvalidPermissionRequest(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _InvalidPermissionRequest("duplicate JSON field")
        value[key] = item
    return value


def _broker(handler: _PermissionHandler) -> PermissionApprovalBroker:
    broker = getattr(handler.server, "permission_approval_broker", None)
    if not isinstance(broker, PermissionApprovalBroker):
        raise PermissionApprovalError("permission_unavailable")
    return broker


def _send_error(handler: _PermissionHandler, code: str, *, status: int | None = None) -> None:
    safe_code = code if code in _ERROR_STATUS else "permission_unavailable"
    handler._send_json(
        {
            "ok": False,
            "error": {
                "code": safe_code,
                "message": _ERROR_MESSAGES[safe_code],
            },
        },
        status=status or _ERROR_STATUS[safe_code],
    )


def _content_length(handler: _PermissionHandler) -> int:
    values = handler.headers.get_all("Content-Length", [])
    if len(values) != 1 or not re.fullmatch(r"[0-9]+", values[0] or ""):
        raise _InvalidPermissionRequest("invalid content length")
    length = int(values[0])
    if length <= 0 or length > PERMISSION_DECISION_MAX_REQUEST_BODY_BYTES:
        raise _InvalidPermissionRequest("invalid content length")
    return length


def _content_type(handler: _PermissionHandler) -> None:
    raw = handler.headers.get("Content-Type")
    if not isinstance(raw, str) or handler.headers.get_content_type() != "application/json":
        raise _InvalidPermissionRequest("invalid content type")
    charset = handler.headers.get_content_charset()
    if charset is not None and charset.lower().replace("_", "-") != "utf-8":
        raise _InvalidPermissionRequest("invalid charset")


def _origin(handler: _PermissionHandler) -> None:
    values = handler.headers.get_all("Origin", [])
    if not values:
        return
    if len(values) != 1 or len(values[0]) > 512:
        raise _InvalidPermissionRequest("invalid origin")
    host_values = handler.headers.get_all("Host", [])
    if len(host_values) != 1:
        raise _InvalidPermissionRequest("invalid host")
    origin = urlsplit(values[0])
    host = urlsplit("//" + host_values[0])
    try:
        origin_port = origin.port
        host_port = host.port
    except ValueError as error:
        raise _InvalidPermissionRequest("invalid origin") from error
    if (
        origin.scheme != "http"
        or origin.username is not None
        or origin.password is not None
        or origin.path not in {"", "/"}
        or origin.query
        or origin.fragment
        or not isinstance(origin.hostname, str)
        or not isinstance(host.hostname, str)
        or origin.hostname.casefold() != host.hostname.casefold()
        or origin_port != host_port
        or not is_loopback_gateway_host(origin.hostname)
    ):
        raise _InvalidPermissionRequest("cross-origin request")


def serve_permission_pending(handler: _PermissionHandler) -> None:
    if "?" in handler.path:
        _send_error(handler, "invalid_request")
        return
    try:
        handler._send_json(_broker(handler).snapshot())
    except PermissionApprovalError as error:
        _send_error(handler, error.code)
    except BaseException:  # noqa: BLE001 - never expose authority internals
        _send_error(handler, "permission_unavailable")


def serve_permission_decision(handler: _PermissionHandler, path: str) -> None:
    try:
        if "?" in handler.path:
            raise _InvalidPermissionRequest("query is not allowed")
        match = _DECISION_PATH_RE.fullmatch(path)
        if match is None or PERMISSION_ID_RE.fullmatch(match.group(1)) is None:
            raise _InvalidPermissionRequest("invalid path")
        _content_type(handler)
        _origin(handler)
        raw = handler.rfile.read(_content_length(handler))
        data = json.loads(
            raw.decode("utf-8", errors="strict"), object_pairs_hook=_strict_object
        )
        if not isinstance(data, dict) or set(data) != {"turnId", "decision"}:
            raise _InvalidPermissionRequest("invalid fields")
        turn_id = data.get("turnId")
        decision = data.get("decision")
        if (
            not isinstance(turn_id, str)
            or TURN_ID_RE.fullmatch(turn_id) is None
            or decision not in {"allow_once", "deny_once"}
        ):
            raise _InvalidPermissionRequest("invalid decision")
        result = _broker(handler).decide(
            permission_id=match.group(1),
            turn_id=turn_id,
            decision=decision,
        )
        handler._send_json(
            {
                "schemaVersion": 1,
                "mode": "read-write",
                "permissionId": result.permission_id,
                "turnId": result.turn_id,
                "status": result.status,
                "decision": result.decision,
                "decisionAccepted": result.decision_accepted,
                "updatedAt": result.updated_at,
            }
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _InvalidPermissionRequest,
    ):
        _send_error(handler, "invalid_request")
    except PermissionApprovalError as error:
        _send_error(handler, error.code)
    except BaseException:  # noqa: BLE001 - never expose authority internals
        _send_error(handler, "permission_unavailable")


def is_permission_decision_path(path: str) -> bool:
    return _DECISION_PATH_RE.fullmatch(path) is not None


__all__ = [
    "PERMISSION_DECISION_MAX_REQUEST_BODY_BYTES",
    "is_permission_decision_path",
    "serve_permission_decision",
    "serve_permission_pending",
]
