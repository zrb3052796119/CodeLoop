"""Process-local cooperative cancellation for one durable Dashboard Turn.

The token deliberately carries no message, reason, Session content, or Provider
state.  Durable cancellation authority remains in ``ConversationTurnStore``;
this module only gives already-running Python code a thread-safe checkpoint.
"""

from __future__ import annotations

import re
import threading


_TURN_ID_RE = re.compile(r"turn_[0-9a-f]{32}")


class TurnCancellationRequested(RuntimeError):
    """Internal control-flow signal that must not cross the HTTP boundary."""

    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        super().__init__("turn cancellation requested")


class TurnCancellationToken:
    """Small thread-safe cancellation flag for exactly one live Turn."""

    __slots__ = ("_event", "turn_id")

    def __init__(self, turn_id: str) -> None:
        if not isinstance(turn_id, str) or _TURN_ID_RE.fullmatch(turn_id) is None:
            raise ValueError("invalid turn id")
        self.turn_id = turn_id
        self._event = threading.Event()

    def is_requested(self) -> bool:
        return self._event.is_set()

    def request(self) -> bool:
        first_request = not self._event.is_set()
        self._event.set()
        return first_request

    def raise_if_requested(self) -> None:
        if self._event.is_set():
            raise TurnCancellationRequested(self.turn_id)


class TurnCancellationRegistry:
    """Bounded-by-live-Turn token registry owned by one Turn Store instance."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: dict[str, TurnCancellationToken] = {}

    def acquire(self, turn_id: str, *, requested: bool = False) -> TurnCancellationToken:
        with self._lock:
            token = self._tokens.get(turn_id)
            if token is None:
                token = TurnCancellationToken(turn_id)
                self._tokens[turn_id] = token
            if requested:
                token.request()
            return token

    def request(self, turn_id: str) -> bool:
        with self._lock:
            token = self._tokens.get(turn_id)
            return token.request() if token is not None else False

    def release(self, turn_id: str) -> None:
        with self._lock:
            self._tokens.pop(turn_id, None)


def raise_if_cancelled(token: TurnCancellationToken | None) -> None:
    """Checkpoint helper whose ``None`` path is an exact no-op."""
    if token is not None:
        token.raise_if_requested()


__all__ = [
    "TurnCancellationRegistry",
    "TurnCancellationRequested",
    "TurnCancellationToken",
    "raise_if_cancelled",
]
