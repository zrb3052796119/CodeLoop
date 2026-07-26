"""Connection-scoped NDJSON presentation for one synchronous Chat turn."""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from minicode.conversation_turn_store import validate_turn_id


CHAT_STREAM_CONTENT_TYPE = "application/x-ndjson; charset=utf-8"
CHAT_STREAM_MAX_FRAME_BYTES = 4 * 1024
CHAT_STREAM_ASSISTANT_BUDGET_BYTES = 128 * 1024
CHAT_STREAM_TOOL_EVENT_BUDGET = 512
_SAFE_TOOL_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


class ChatStreamWriter:
    """Serialize bounded NDJSON frames without becoming conversation truth."""

    def __init__(
        self,
        turn_id: str,
        write_bytes: Callable[[bytes], None],
        *,
        assistant_budget_bytes: int = CHAT_STREAM_ASSISTANT_BUDGET_BYTES,
        tool_event_budget: int = CHAT_STREAM_TOOL_EVENT_BUDGET,
    ) -> None:
        if (
            isinstance(assistant_budget_bytes, bool)
            or not isinstance(assistant_budget_bytes, int)
            or not 1 <= assistant_budget_bytes <= 1024 * 1024
        ):
            raise ValueError("assistant stream budget is invalid")
        if (
            isinstance(tool_event_budget, bool)
            or not isinstance(tool_event_budget, int)
            or not 1 <= tool_event_budget <= 4096
        ):
            raise ValueError("tool stream budget is invalid")
        self._turn_id = validate_turn_id(turn_id)
        self._write_bytes = write_bytes
        self._lock = threading.RLock()
        self._sequence = 0
        self._detached = False
        self._assistant_budget_bytes = assistant_budget_bytes
        self._assistant_emitted_bytes = 0
        self._assistant_truncated = False
        self._tool_event_budget = tool_event_budget
        self._tool_events_emitted = 0
        self._tools_truncated = False
        self._pending_tools: dict[str, deque[str]] = defaultdict(deque)

    @property
    def detached(self) -> bool:
        with self._lock:
            return self._detached

    def _encode(
        self,
        event_type: str,
        fields: dict[str, Any] | None = None,
    ) -> bytes:
        payload = {
            "schemaVersion": 1,
            "type": event_type,
            "turnId": self._turn_id,
            "sequence": self._sequence,
            **(fields or {}),
        }
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8", errors="strict")

    def _emit(
        self,
        event_type: str,
        fields: dict[str, Any] | None = None,
    ) -> bool:
        with self._lock:
            if self._detached:
                return False
            try:
                encoded = self._encode(event_type, fields)
                if len(encoded) > CHAT_STREAM_MAX_FRAME_BYTES:
                    raise ValueError("chat stream frame exceeds byte limit")
                self._write_bytes(encoded)
            except BaseException:  # noqa: BLE001 - presentation must detach safely
                self._detached = True
                return False
            self._sequence += 1
            return True

    def ready(self) -> None:
        self._emit("chat.stream.ready")

    def assistant_delta(self, text: str) -> None:
        if not isinstance(text, str) or not text or self._assistant_truncated:
            return
        try:
            text.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            return
        with self._lock:
            budget_left = (
                self._assistant_budget_bytes - self._assistant_emitted_bytes
            )
            low = 0
            high = len(text)
            allowed_chars = 0
            while low <= high:
                middle = (low + high) // 2
                size = len(text[:middle].encode("utf-8", errors="strict"))
                if size <= budget_left:
                    allowed_chars = middle
                    low = middle + 1
                else:
                    high = middle - 1
            remaining = text[:allowed_chars]
            self._assistant_emitted_bytes += len(
                remaining.encode("utf-8", errors="strict")
            )
            while remaining and not self._detached:
                low = 1
                high = len(remaining)
                best = 0
                while low <= high:
                    middle = (low + high) // 2
                    try:
                        size = len(
                            self._encode(
                                "chat.assistant.delta",
                                {"text": remaining[:middle]},
                            )
                        )
                    except (TypeError, ValueError, UnicodeEncodeError):
                        size = CHAT_STREAM_MAX_FRAME_BYTES + 1
                    if size <= CHAT_STREAM_MAX_FRAME_BYTES:
                        best = middle
                        low = middle + 1
                    else:
                        high = middle - 1
                if best == 0:
                    return
                if not self._emit(
                    "chat.assistant.delta",
                    {"text": remaining[:best]},
                ):
                    return
                remaining = remaining[best:]
            if allowed_chars < len(text) and not self._detached:
                self._assistant_truncated = True
                self._emit(
                    "chat.stream.truncated",
                    {"category": "assistant"},
                )

    @staticmethod
    def _safe_tool_name(tool_name: object) -> str:
        return (
            tool_name
            if isinstance(tool_name, str) and _SAFE_TOOL_NAME.fullmatch(tool_name)
            else "unknown"
        )

    def _tool_budget_available(self) -> bool:
        if self._tools_truncated:
            return False
        if self._tool_events_emitted < self._tool_event_budget:
            return True
        self._tools_truncated = True
        self._emit("chat.stream.truncated", {"category": "tools"})
        return False

    def tool_started(self, tool_name: str) -> None:
        with self._lock:
            if self._detached or not self._tool_budget_available():
                return
            safe_name = self._safe_tool_name(tool_name)
            stream_id = f"toolstream_{uuid.uuid4().hex}"
            if self._emit(
                "chat.tool.started",
                {"toolStreamId": stream_id, "toolName": safe_name},
            ):
                self._pending_tools[safe_name].append(stream_id)
                self._tool_events_emitted += 1

    def tool_finished(self, tool_name: str, *, is_error: bool) -> None:
        with self._lock:
            if self._detached or not self._tool_budget_available():
                return
            safe_name = self._safe_tool_name(tool_name)
            pending = self._pending_tools.get(safe_name)
            stream_id = pending[0] if pending else None
            fields: dict[str, Any] = {
                "toolName": safe_name,
                "outcome": "error" if is_error is True else "success",
                "paired": stream_id is not None,
            }
            if stream_id is not None:
                fields["toolStreamId"] = stream_id
            if self._emit("chat.tool.finished", fields):
                self._tool_events_emitted += 1
                if pending:
                    pending.popleft()
                    if not pending:
                        self._pending_tools.pop(safe_name, None)

    def completed(self, result: Any) -> None:
        self._emit(
            "chat.turn.completed",
            {
                "status": "completed",
                "sessionId": result.session_id,
                "created": result.created,
                "updatedAt": result.updated_at,
                "runId": result.run_id,
            },
        )

    def error(self, code: str) -> None:
        self._emit("chat.turn.error", {"code": code})


__all__ = [
    "CHAT_STREAM_CONTENT_TYPE",
    "CHAT_STREAM_ASSISTANT_BUDGET_BYTES",
    "CHAT_STREAM_MAX_FRAME_BYTES",
    "CHAT_STREAM_TOOL_EVENT_BUDGET",
    "ChatStreamWriter",
]
