"""Strict loopback HTTP adapter for persistent Memory approval."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Protocol
from urllib.parse import urlsplit

from minicode.memory_approval import (
    MEMORY_ID_RE,
    MEMORY_REVIEW_REVISION_RE,
    MemoryApprovalAuthority,
    MemoryApprovalError,
)
from minicode.permission_approval import is_loopback_gateway_host


MEMORY_DECISION_MAX_REQUEST_BODY_BYTES = 1_024
_DECISION_PATH_RE = re.compile(
    r"^/api/v1/memory/approvals/([A-Za-z0-9][A-Za-z0-9._-]{0,159})/decision$"
)

_ERROR_STATUS = {
    "invalid_request": 400,
    "invalid_memory_id": 400,
    "invalid_decision": 400,
    "invalid_review_revision": 400,
    "memory_approval_not_found": 404,
    "memory_review_stale": 409,
    "memory_already_decided": 409,
    "memory_not_reviewable": 409,
    "memory_write_conflict": 409,
    "memory_store_busy": 423,
    "memory_approval_failed": 500,
    "memory_approval_unavailable": 503,
}
_ERROR_MESSAGES = {
    "invalid_request": "Memory approval request is invalid.",
    "invalid_memory_id": "Memory ID is invalid.",
    "invalid_decision": "Memory decision is invalid.",
    "invalid_review_revision": "Memory review revision is invalid.",
    "memory_approval_not_found": "Memory approval was not found.",
    "memory_review_stale": "Memory review is stale.",
    "memory_already_decided": "Memory approval already has a terminal decision.",
    "memory_not_reviewable": "Memory cannot be safely approved.",
    "memory_write_conflict": "Memory changed during the decision.",
    "memory_store_busy": "Memory store is temporarily busy.",
    "memory_approval_failed": "Memory approval failed.",
    "memory_approval_unavailable": "Memory approval is unavailable.",
}


class _MemoryHandler(Protocol):
    path: str
    headers: Any
    rfile: Any
    server: Any

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None: ...
    def _dashboard_read_model(self) -> Any: ...


class _InvalidRequest(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidRequest("duplicate JSON field")
        result[key] = value
    return result


def _send_error(handler: _MemoryHandler, code: str, *, status: int | None = None) -> None:
    safe_code = code if code in _ERROR_STATUS else "memory_approval_failed"
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


def _accepts_json(handler: _MemoryHandler) -> None:
    values = handler.headers.get_all("Accept", [])
    if not values:
        return
    if len(values) != 1 or len(values[0]) > 1_024:
        raise _InvalidRequest("invalid Accept")
    accepts = False
    for item in values[0].split(","):
        parts = [part.strip().casefold() for part in item.split(";")]
        media_type = parts[0]
        if not media_type:
            raise _InvalidRequest("invalid Accept")
        quality = 1.0
        quality_seen = False
        valid = True
        for parameter in parts[1:]:
            if not parameter.startswith("q=") or quality_seen:
                valid = False
                break
            quality_seen = True
            try:
                quality = float(parameter.removeprefix("q="))
            except ValueError:
                valid = False
                break
            if not math.isfinite(quality) or not 0 <= quality <= 1:
                valid = False
                break
        if not valid:
            raise _InvalidRequest("invalid Accept")
        if valid and media_type in {"application/json", "*/*"} and quality > 0:
            accepts = True
    if not accepts:
        raise _InvalidRequest("JSON is not accepted")


def _content_length(handler: _MemoryHandler) -> int:
    values = handler.headers.get_all("Content-Length", [])
    if len(values) != 1 or re.fullmatch(r"[0-9]+", values[0] or "") is None:
        raise _InvalidRequest("invalid content length")
    length = int(values[0])
    if length <= 0 or length > MEMORY_DECISION_MAX_REQUEST_BODY_BYTES:
        raise _InvalidRequest("invalid content length")
    return length


def _content_type(handler: _MemoryHandler) -> None:
    values = handler.headers.get_all("Content-Type", [])
    if len(values) != 1 or handler.headers.get_content_type() != "application/json":
        raise _InvalidRequest("invalid content type")
    charset = handler.headers.get_content_charset()
    if charset is not None and charset.lower().replace("_", "-") != "utf-8":
        raise _InvalidRequest("invalid charset")
    params = handler.headers.get_params(header="Content-Type") or []
    if any(key.casefold() != "charset" for key, _ in params[1:]):
        raise _InvalidRequest("invalid content type parameter")


def _origin(handler: _MemoryHandler) -> None:
    values = handler.headers.get_all("Origin", [])
    if not values:
        return
    host_values = handler.headers.get_all("Host", [])
    if len(values) != 1 or len(values[0]) > 512 or len(host_values) != 1:
        raise _InvalidRequest("invalid origin")
    origin = urlsplit(values[0])
    host = urlsplit("//" + host_values[0])
    try:
        origin_port = origin.port
        host_port = host.port
    except ValueError as error:
        raise _InvalidRequest("invalid origin") from error
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
        raise _InvalidRequest("cross-origin request")


def _authority(handler: _MemoryHandler) -> MemoryApprovalAuthority:
    address = getattr(handler.server, "server_address", ("", 0))
    host = address[0] if isinstance(address, tuple) and address else ""
    if not isinstance(host, str) or not is_loopback_gateway_host(host):
        raise MemoryApprovalError("memory_approval_unavailable")
    authority = getattr(handler.server, "memory_approval_authority", None)
    if isinstance(authority, MemoryApprovalAuthority):
        return authority
    read_model = handler._dashboard_read_model()
    authority = MemoryApprovalAuthority(read_model.workspace)
    setattr(handler.server, "memory_approval_authority", authority)
    return authority


def serve_memory_approval_pending(handler: _MemoryHandler) -> None:
    try:
        if "?" in handler.path:
            raise _InvalidRequest("query is not allowed")
        _accepts_json(handler)
        handler._send_json(_authority(handler).snapshot())
    except _InvalidRequest:
        _send_error(handler, "invalid_request")
    except MemoryApprovalError as error:
        _send_error(handler, error.code)
    except BaseException:  # noqa: BLE001 - never expose authority internals
        _send_error(handler, "memory_approval_failed")


def serve_memory_approval_decision(handler: _MemoryHandler, path: str) -> None:
    try:
        if "?" in handler.path:
            raise _InvalidRequest("query is not allowed")
        match = _DECISION_PATH_RE.fullmatch(path)
        if match is None or MEMORY_ID_RE.fullmatch(match.group(1)) is None:
            raise MemoryApprovalError("invalid_memory_id")
        _accepts_json(handler)
        _content_type(handler)
        _origin(handler)
        raw = handler.rfile.read(_content_length(handler))
        data = json.loads(
            raw.decode("utf-8", errors="strict"), object_pairs_hook=_strict_object
        )
        if not isinstance(data, dict) or set(data) != {"decision", "reviewRevision"}:
            raise _InvalidRequest("invalid fields")
        decision = data.get("decision")
        review_revision = data.get("reviewRevision")
        if not isinstance(decision, str) or decision not in {"approve", "reject"}:
            raise MemoryApprovalError("invalid_decision")
        if (
            not isinstance(review_revision, str)
            or MEMORY_REVIEW_REVISION_RE.fullmatch(review_revision) is None
        ):
            raise MemoryApprovalError("invalid_review_revision")
        result = _authority(handler).decide(
            memory_id=match.group(1),
            decision=decision,
            review_revision=review_revision,
        )
        handler._send_json(
            {
                "schemaVersion": 1,
                "generatedAt": result.updated_at,
                "mode": "read-write",
                "memoryId": result.memory_id,
                "status": result.status,
                "decision": result.decision,
                "decisionAccepted": result.decision_accepted,
                "updatedAt": result.updated_at,
            }
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _InvalidRequest):
        _send_error(handler, "invalid_request")
    except MemoryApprovalError as error:
        _send_error(handler, error.code)
    except BaseException:  # noqa: BLE001 - never expose authority internals
        _send_error(handler, "memory_approval_failed")


def is_memory_approval_decision_path(path: str) -> bool:
    return _DECISION_PATH_RE.fullmatch(path) is not None


__all__ = [
    "MEMORY_DECISION_MAX_REQUEST_BODY_BYTES",
    "is_memory_approval_decision_path",
    "serve_memory_approval_decision",
    "serve_memory_approval_pending",
]
