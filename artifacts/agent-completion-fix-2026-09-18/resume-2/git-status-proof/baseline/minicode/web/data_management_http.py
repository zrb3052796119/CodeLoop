"""Strict loopback HTTP adapter for Dashboard deletion authorities."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Protocol
from urllib.parse import urlsplit

from minicode.conversation_deletion import (
    DELETION_REVISION_RE,
    SESSION_ID_RE,
    ConversationDeletionAuthority,
    ConversationDeletionError,
)
from minicode.memory_approval import MEMORY_ID_RE
from minicode.permission_approval import is_loopback_gateway_host
from minicode.project_memory_deletion import (
    ProjectMemoryDeletionAuthority,
    ProjectMemoryDeletionError,
)


DELETION_MAX_REQUEST_BODY_BYTES = 1_024
_CONVERSATION_PATH_RE = re.compile(r"^/api/v1/sessions/([^/]+)/deletion$")
_MEMORY_PATH_RE = re.compile(r"^/api/v1/memory/project/([^/]+)/deletion$")

_ERROR_STATUS = {
    "invalid_request": 400,
    "invalid_id": 400,
    "invalid_revision": 400,
    "deletion_target_not_found": 404,
    "deletion_revision_stale": 409,
    "deletion_target_busy": 409,
    "deletion_write_conflict": 409,
    "deletion_store_busy": 503,
    "deletion_unavailable": 503,
    "deletion_failed": 500,
}
_ERROR_MESSAGES = {
    "invalid_request": "Deletion request is invalid.",
    "invalid_id": "Deletion target ID is invalid.",
    "invalid_revision": "Deletion revision is invalid.",
    "deletion_target_not_found": "Deletion target was not found.",
    "deletion_revision_stale": "Deletion preview is stale.",
    "deletion_target_busy": "Deletion target is still active.",
    "deletion_write_conflict": "Deletion target changed during deletion.",
    "deletion_store_busy": "Deletion store is temporarily busy.",
    "deletion_unavailable": "Deletion is temporarily unavailable.",
    "deletion_failed": "Deletion failed.",
}


class _DeletionHandler(Protocol):
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


def _send_error(handler: _DeletionHandler, code: str) -> None:
    safe_code = code if code in _ERROR_STATUS else "deletion_failed"
    handler._send_json(
        {
            "ok": False,
            "error": {
                "code": safe_code,
                "message": _ERROR_MESSAGES[safe_code],
            },
        },
        status=_ERROR_STATUS[safe_code],
    )


def _accepts_json(handler: _DeletionHandler) -> None:
    values = handler.headers.get_all("Accept", [])
    if not values:
        return
    if len(values) != 1 or len(values[0]) > 1_024:
        raise _InvalidRequest("invalid Accept")
    accepted = False
    for item in values[0].split(","):
        parts = [part.strip().casefold() for part in item.split(";")]
        media_type = parts[0]
        quality = 1.0
        quality_seen = False
        if not media_type:
            raise _InvalidRequest("invalid Accept")
        for parameter in parts[1:]:
            if not parameter.startswith("q=") or quality_seen:
                raise _InvalidRequest("invalid Accept")
            quality_seen = True
            try:
                quality = float(parameter.removeprefix("q="))
            except ValueError as error:
                raise _InvalidRequest("invalid Accept") from error
            if not math.isfinite(quality) or not 0 <= quality <= 1:
                raise _InvalidRequest("invalid Accept")
        if media_type in {"application/json", "*/*"} and quality > 0:
            accepted = True
    if not accepted:
        raise _InvalidRequest("JSON is not accepted")


def _content_length(handler: _DeletionHandler) -> int:
    values = handler.headers.get_all("Content-Length", [])
    if len(values) != 1 or re.fullmatch(r"[0-9]+", values[0] or "") is None:
        raise _InvalidRequest("invalid content length")
    length = int(values[0])
    if length <= 0 or length > DELETION_MAX_REQUEST_BODY_BYTES:
        raise _InvalidRequest("invalid content length")
    return length


def _content_type(handler: _DeletionHandler) -> None:
    values = handler.headers.get_all("Content-Type", [])
    if len(values) != 1 or handler.headers.get_content_type() != "application/json":
        raise _InvalidRequest("invalid content type")
    charset = handler.headers.get_content_charset()
    if charset is not None and charset.lower().replace("_", "-") != "utf-8":
        raise _InvalidRequest("invalid charset")
    params = handler.headers.get_params(header="Content-Type") or []
    if any(key.casefold() != "charset" for key, _ in params[1:]):
        raise _InvalidRequest("invalid content type parameter")


def _origin(handler: _DeletionHandler) -> None:
    values = handler.headers.get_all("Origin", [])
    if not values:
        return
    hosts = handler.headers.get_all("Host", [])
    if len(values) != 1 or len(values[0]) > 512 or len(hosts) != 1:
        raise _InvalidRequest("invalid origin")
    origin = urlsplit(values[0])
    host = urlsplit("//" + hosts[0])
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


def _route(path: str) -> tuple[str, str]:
    conversation = _CONVERSATION_PATH_RE.fullmatch(path)
    if conversation is not None:
        target = conversation.group(1)
        if SESSION_ID_RE.fullmatch(target) is None:
            raise ConversationDeletionError("invalid_id")
        return "conversation", target
    memory = _MEMORY_PATH_RE.fullmatch(path)
    if memory is not None:
        target = memory.group(1)
        if MEMORY_ID_RE.fullmatch(target) is None:
            raise ProjectMemoryDeletionError("invalid_id")
        return "project-memory", target
    raise _InvalidRequest("invalid deletion path")


def _loopback(handler: _DeletionHandler) -> None:
    address = getattr(handler.server, "server_address", ("", 0))
    host = address[0] if isinstance(address, tuple) and address else ""
    if not isinstance(host, str) or not is_loopback_gateway_host(host):
        raise ProjectMemoryDeletionError("deletion_unavailable")


def _authority(handler: _DeletionHandler, kind: str) -> Any:
    _loopback(handler)
    read_model = handler._dashboard_read_model()
    if kind == "conversation":
        authority = getattr(handler.server, "conversation_deletion_authority", None)
        if authority is None:
            authority = ConversationDeletionAuthority(
                read_model.workspace,
                data_dir=read_model.data_dir,
            )
            setattr(handler.server, "conversation_deletion_authority", authority)
        return authority
    authority = getattr(handler.server, "project_memory_deletion_authority", None)
    if authority is None:
        authority = ProjectMemoryDeletionAuthority(
            read_model.workspace,
            data_dir=read_model.data_dir,
        )
        setattr(handler.server, "project_memory_deletion_authority", authority)
    return authority


def serve_deletion_preview(handler: _DeletionHandler, path: str) -> None:
    try:
        if "?" in handler.path:
            raise _InvalidRequest("query is not allowed")
        _accepts_json(handler)
        kind, target = _route(path)
        handler._send_json(_authority(handler, kind).snapshot(target))
    except _InvalidRequest:
        _send_error(handler, "invalid_request")
    except (ConversationDeletionError, ProjectMemoryDeletionError) as error:
        _send_error(handler, error.code)
    except BaseException:  # noqa: BLE001 - never expose authority internals
        _send_error(handler, "deletion_failed")


def serve_deletion_request(handler: _DeletionHandler, path: str) -> None:
    try:
        if "?" in handler.path:
            raise _InvalidRequest("query is not allowed")
        if any(
            handler.headers.get_all(name, [])
            for name in (
                "X-HTTP-Method-Override",
                "X-Method-Override",
                "X-HTTP-Method",
            )
        ):
            raise _InvalidRequest("method override is not allowed")
        _accepts_json(handler)
        _content_type(handler)
        _origin(handler)
        kind, target = _route(path)
        raw = handler.rfile.read(_content_length(handler))
        data = json.loads(
            raw.decode("utf-8", errors="strict"), object_pairs_hook=_strict_object
        )
        if not isinstance(data, dict) or set(data) != {"deletionRevision"}:
            raise _InvalidRequest("invalid fields")
        revision = data.get("deletionRevision")
        if not isinstance(revision, str) or DELETION_REVISION_RE.fullmatch(revision) is None:
            raise ConversationDeletionError("invalid_revision")
        handler._send_json(_authority(handler, kind).delete(target, revision))
    except (UnicodeDecodeError, json.JSONDecodeError, _InvalidRequest):
        _send_error(handler, "invalid_request")
    except (ConversationDeletionError, ProjectMemoryDeletionError) as error:
        _send_error(handler, error.code)
    except BaseException:  # noqa: BLE001 - never expose authority internals
        _send_error(handler, "deletion_failed")


def is_deletion_path(path: str) -> bool:
    return bool(
        _CONVERSATION_PATH_RE.fullmatch(path)
        or _MEMORY_PATH_RE.fullmatch(path)
        or (
            path.startswith("/api/v1/sessions/") and path.endswith("/deletion")
        )
        or (
            path.startswith("/api/v1/memory/project/")
            and path.endswith("/deletion")
        )
    )


__all__ = [
    "DELETION_MAX_REQUEST_BODY_BYTES",
    "is_deletion_path",
    "serve_deletion_preview",
    "serve_deletion_request",
]
