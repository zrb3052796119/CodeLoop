"""Strict standard-library HTTP boundary for synchronous Dashboard Chat turns."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Protocol

from minicode.conversation import (
    ConversationRuntimeUnavailable,
    ConversationSessionBusy,
    ConversationSessionConflict,
    ConversationSessionNotFound,
    ConversationTurnFailed,
    ConversationTurnCancelled,
    ConversationTurnIdConflict,
    ConversationTurnInProgress,
    ConversationTurnInterrupted,
    ConversationTurnNotFound,
)
from minicode.conversation_turn_store import TURN_ID_PATTERN, TURN_STATUSES


CHAT_MAX_REQUEST_BODY_BYTES = 65_536
CHAT_MAX_MESSAGE_CHARS = 32_000
CHAT_CANCEL_MAX_REQUEST_BODY_BYTES = 1_024
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_ALLOWED_FIELDS = frozenset({"message", "sessionId", "turnId"})
_INVALID_REQUEST = {
    "ok": False,
    "error": {
        "code": "invalid_request",
        "message": "Chat request is invalid.",
    },
}


class _ChatHandler(Protocol):
    path: str
    headers: Any
    rfile: Any
    wfile: Any
    connection: Any
    close_connection: bool

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None: ...

    def _conversation_turn_service(self) -> Any: ...

    def send_response(self, code: int, message: str | None = None) -> None: ...

    def send_header(self, keyword: str, value: str) -> None: ...

    def end_headers(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ChatTurnRequest:
    message: str
    session_id: str | None
    turn_id: str | None


class _InvalidChatRequest(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidChatRequest("duplicate JSON field")
        result[key] = value
    return result


def _content_length(
    handler: _ChatHandler,
    *,
    maximum: int = CHAT_MAX_REQUEST_BODY_BYTES,
) -> int:
    values = handler.headers.get_all("Content-Length", [])
    if len(values) != 1:
        raise _InvalidChatRequest("invalid content length")
    value = values[0]
    if not isinstance(value, str) or not value.isdecimal():
        raise _InvalidChatRequest("invalid content length")
    length = int(value)
    if length <= 0 or length > maximum:
        raise _InvalidChatRequest("invalid content length")
    return length


def _content_type(handler: _ChatHandler) -> None:
    raw = handler.headers.get("Content-Type")
    if not isinstance(raw, str) or handler.headers.get_content_type() != "application/json":
        raise _InvalidChatRequest("invalid content type")
    charset = handler.headers.get_content_charset()
    if charset is not None and charset.lower().replace("_", "-") != "utf-8":
        raise _InvalidChatRequest("invalid charset")


def parse_chat_turn_request(handler: _ChatHandler) -> ChatTurnRequest:
    """Validate the complete transport/body contract before runtime construction."""
    if "?" in handler.path:
        raise _InvalidChatRequest("query is not allowed")
    _content_type(handler)
    length = _content_length(handler)
    try:
        raw = handler.rfile.read(length)
        text = raw.decode("utf-8", errors="strict")
        data = json.loads(text, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, _InvalidChatRequest) as error:
        raise _InvalidChatRequest("invalid JSON") from error
    if not isinstance(data, dict) or not set(data).issubset(_ALLOWED_FIELDS):
        raise _InvalidChatRequest("invalid fields")
    message = data.get("message")
    if not isinstance(message, str):
        raise _InvalidChatRequest("invalid message")
    message = message.strip()
    if not message or len(message) > CHAT_MAX_MESSAGE_CHARS or "\x00" in message:
        raise _InvalidChatRequest("invalid message")
    session_id = data.get("sessionId")
    if session_id is not None and (
        not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id)
    ):
        raise _InvalidChatRequest("invalid session id")
    turn_id = data.get("turnId")
    if turn_id is not None and (
        not isinstance(turn_id, str) or TURN_ID_PATTERN.fullmatch(turn_id) is None
    ):
        raise _InvalidChatRequest("invalid turn id")
    return ChatTurnRequest(
        message=message,
        session_id=session_id,
        turn_id=turn_id,
    )


def parse_chat_cancel_request(handler: _ChatHandler, path: str) -> str:
    """Validate an exact cancellation route and its empty JSON object body."""
    match = re.fullmatch(
        r"/api/v1/chat/turns/(turn_[0-9a-f]{32})/cancel",
        path,
    )
    if "?" in handler.path or match is None:
        raise _InvalidChatRequest("invalid cancel path")
    _content_type(handler)
    length = _content_length(
        handler,
        maximum=CHAT_CANCEL_MAX_REQUEST_BODY_BYTES,
    )
    try:
        raw = handler.rfile.read(length)
        data = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _InvalidChatRequest) as error:
        raise _InvalidChatRequest("invalid JSON") from error
    if not isinstance(data, dict) or data:
        raise _InvalidChatRequest("cancel body must be empty")
    return match.group(1)


def _send_error(
    handler: _ChatHandler,
    *,
    status: int,
    code: str,
    message: str,
    turn_id: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {"code": code, "message": message},
    }
    if turn_id is not None:
        payload["turnId"] = turn_id
    handler._send_json(
        payload,
        status=status,
    )


def _accepts_ndjson(handler: _ChatHandler) -> bool:
    values = handler.headers.get_all("Accept", [])
    if not values:
        return False
    rendered = ",".join(values)
    if len(rendered) > 1_024:
        return False
    for item in rendered.split(","):
        parts = [part.strip().lower() for part in item.split(";")]
        if parts[0] != "application/x-ndjson":
            continue
        quality = 1.0
        quality_seen = False
        valid = True
        for parameter in parts[1:]:
            if not parameter.startswith("q="):
                continue
            if quality_seen:
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
        if valid and quality > 0:
            return True
    return False


def _stream_error_code(error: BaseException) -> str:
    mappings = (
        (ConversationSessionNotFound, "session_not_found"),
        (ConversationSessionConflict, "session_conflict"),
        (ConversationSessionBusy, "session_busy"),
        (ConversationRuntimeUnavailable, "runtime_unavailable"),
        (ConversationTurnIdConflict, "turn_id_conflict"),
        (ConversationTurnInProgress, "turn_in_progress"),
        (ConversationTurnInterrupted, "turn_interrupted"),
        (ConversationTurnCancelled, "turn_cancelled"),
    )
    for error_type, code in mappings:
        if isinstance(error, error_type):
            return code
    return "turn_failed"


def _serve_chat_turn_stream(
    handler: _ChatHandler,
    request: ChatTurnRequest,
) -> None:
    from minicode.web.chat_stream import CHAT_STREAM_CONTENT_TYPE, ChatStreamWriter

    if request.turn_id is None:
        handler._send_json(_INVALID_REQUEST, status=400)
        return
    handler.send_response(200)
    handler.send_header("Content-Type", CHAT_STREAM_CONTENT_TYPE)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Accel-Buffering", "no")
    handler.send_header("Connection", "close")
    handler.end_headers()
    write_timeout = getattr(handler.server, "chat_stream_write_timeout", 5.0)
    if (
        isinstance(write_timeout, bool)
        or not isinstance(write_timeout, (int, float))
        or not 0.1 <= float(write_timeout) <= 30.0
    ):
        write_timeout = 5.0
    try:
        handler.connection.settimeout(float(write_timeout))
    except (AttributeError, OSError):
        pass

    def write_frame(frame: bytes) -> None:
        handler.wfile.write(frame)
        handler.wfile.flush()

    stream = ChatStreamWriter(request.turn_id, write_frame)
    stream.ready()
    try:
        result = handler._conversation_turn_service().turn(
            message=request.message,
            session_id=request.session_id,
            turn_id=request.turn_id,
            presentation=stream,
        )
    except BaseException as error:  # noqa: BLE001 - headers already committed
        stream.error(_stream_error_code(error))
    else:
        stream.completed(result)
    finally:
        handler.close_connection = True


def serve_chat_turn(handler: _ChatHandler) -> None:
    """Serve one synchronous Chat turn using only fixed safe response contracts."""
    try:
        request = parse_chat_turn_request(handler)
    except _InvalidChatRequest:
        handler._send_json(_INVALID_REQUEST, status=400)
        return

    if _accepts_ndjson(handler):
        _serve_chat_turn_stream(handler, request)
        return

    try:
        result = handler._conversation_turn_service().turn(
            message=request.message,
            session_id=request.session_id,
            turn_id=request.turn_id,
        )
    except ConversationSessionNotFound:
        _send_error(
            handler,
            status=404,
            code="session_not_found",
            message="Session was not found.",
            turn_id=request.turn_id,
        )
        return
    except ConversationSessionConflict:
        _send_error(
            handler,
            status=409,
            code="session_conflict",
            message="Session changed before this turn could be saved.",
            turn_id=request.turn_id,
        )
        return
    except ConversationSessionBusy:
        _send_error(
            handler,
            status=503,
            code="session_busy",
            message="Session storage is busy.",
            turn_id=request.turn_id,
        )
        return
    except ConversationRuntimeUnavailable:
        _send_error(
            handler,
            status=503,
            code="runtime_unavailable",
            message="Chat runtime is unavailable.",
            turn_id=request.turn_id,
        )
        return
    except ConversationTurnIdConflict:
        _send_error(
            handler,
            status=409,
            code="turn_id_conflict",
            message="Turn ID belongs to a different request.",
            turn_id=request.turn_id,
        )
        return
    except ConversationTurnInProgress:
        _send_error(
            handler,
            status=409,
            code="turn_in_progress",
            message="Chat turn is already in progress.",
            turn_id=request.turn_id,
        )
        return
    except ConversationTurnInterrupted:
        _send_error(
            handler,
            status=409,
            code="turn_interrupted",
            message="Chat turn was interrupted and will not be retried.",
            turn_id=request.turn_id,
        )
        return
    except ConversationTurnCancelled:
        _send_error(
            handler,
            status=409,
            code="turn_cancelled",
            message="Chat turn was cancelled and will not be retried.",
            turn_id=request.turn_id,
        )
        return
    except Exception:  # noqa: BLE001 - ConversationTurnFailed included; let process-exit signals propagate
        _send_error(
            handler,
            status=500,
            code="turn_failed",
            message="Chat turn failed.",
            turn_id=request.turn_id,
        )
        return

    handler._send_json(
        {
            "ok": True,
            "schemaVersion": 1,
            "mode": "read-write",
            "turnId": result.turn_id,
            "sessionId": result.session_id,
            "created": result.created,
            "assistant": {"role": "assistant", "content": result.assistant},
            "updatedAt": result.updated_at,
            "runId": result.run_id,
        }
    )


def serve_chat_turn_cancel(handler: _ChatHandler, path: str) -> None:
    """Serve one strict idempotent cooperative cancellation request."""
    try:
        turn_id = parse_chat_cancel_request(handler, path)
    except _InvalidChatRequest:
        handler._send_json(_INVALID_REQUEST, status=400)
        return
    try:
        result = handler._conversation_turn_service().cancel(turn_id)
    except ConversationTurnNotFound:
        _send_error(
            handler,
            status=404,
            code="turn_not_found",
            message="Turn was not found.",
            turn_id=turn_id,
        )
        return
    except Exception:  # noqa: BLE001 - ConversationTurnFailed included; let process-exit signals propagate
        _send_error(
            handler,
            status=500,
            code="turn_failed",
            message="Chat turn cancellation failed.",
            turn_id=turn_id,
        )
        return
    if result.status not in TURN_STATUSES:
        _send_error(
            handler,
            status=500,
            code="turn_failed",
            message="Chat turn cancellation failed.",
            turn_id=turn_id,
        )
        return
    handler._send_json(
        {
            "ok": True,
            "schemaVersion": 1,
            "mode": "read-write",
            "turnId": result.turn_id,
            "status": result.status,
            "cancellationAccepted": result.cancellation_accepted,
            "sessionId": result.session_id,
            "runId": result.run_id,
            "updatedAt": result.updated_at,
        }
    )


def serve_chat_turn_status(handler: _ChatHandler, path: str) -> None:
    """Serve one allowlisted workspace-scoped durable Turn status."""
    prefix = "/api/v1/chat/turns/"
    turn_id = path.removeprefix(prefix) if path.startswith(prefix) else ""
    if (
        "?" in handler.path
        or TURN_ID_PATTERN.fullmatch(turn_id) is None
    ):
        handler._send_json(_INVALID_REQUEST, status=400)
        return
    try:
        result = handler._conversation_turn_service().status(turn_id)
    except ConversationTurnNotFound:
        _send_error(
            handler,
            status=404,
            code="turn_not_found",
            message="Turn was not found.",
            turn_id=turn_id,
        )
        return
    except Exception:  # noqa: BLE001 - ConversationTurnFailed included; let process-exit signals propagate
        _send_error(
            handler,
            status=500,
            code="turn_failed",
            message="Turn status is unavailable.",
            turn_id=turn_id,
        )
        return
    handler._send_json(
        {
            "ok": True,
            "schemaVersion": 1,
            "mode": "read-only",
            "turnId": result.turn_id,
            "status": result.status,
            "sessionId": result.session_id,
            "created": result.created_session,
            "runId": result.run_id,
            "createdAt": result.created_at,
            "updatedAt": result.updated_at,
            "completedAt": result.completed_at,
            "errorCode": result.error_code,
            "resultAvailable": result.result_available,
        }
    )


__all__ = [
    "CHAT_MAX_MESSAGE_CHARS",
    "CHAT_MAX_REQUEST_BODY_BYTES",
    "CHAT_CANCEL_MAX_REQUEST_BODY_BYTES",
    "ChatTurnRequest",
    "parse_chat_turn_request",
    "parse_chat_cancel_request",
    "serve_chat_turn",
    "serve_chat_turn_cancel",
    "serve_chat_turn_status",
]
