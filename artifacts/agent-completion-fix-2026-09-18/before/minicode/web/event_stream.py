"""Bounded process-local SSE invalidation transport for the Dashboard.

The stream samples one existing :class:`DashboardChangeFeed` and publishes only
opaque resource invalidations.  Persisted Dashboard data remains authoritative
and is never copied into this transport.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from minicode.web.change_feed import DashboardChangeFeed


RESOURCE_NAMES = (
    "runs",
    "sessions",
    "turns",
    "memory",
    "skills",
    "connections",
    "permissions",
)
RESOURCE_STATUSES = frozenset({"live", "partial", "unavailable", "error"})
HEARTBEAT_FRAME = b": heartbeat\n\n"
_REVISION_RE = re.compile(r"rev_[0-9a-f]{64}")
_EVENT_ID_RE = re.compile(r"evt_([0-9a-f]{32})_([0-9a-f]{16})")
_MAX_EVENT_ID_CHARS = 64
_MAX_EVENT_BYTES = 4 * 1024


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_time(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("clock must return an aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


@dataclass(frozen=True, slots=True)
class _StoredEvent:
    sequence: int
    frame: bytes


class InvalidEventCursor(ValueError):
    """Raised before subscription when Last-Event-ID is syntactically invalid."""


class EventStreamBusy(RuntimeError):
    """Raised before subscription when the bounded client budget is full."""


class DashboardEventSubscription:
    """A bounded cursor over one shared Dashboard event ring."""

    def __init__(
        self,
        stream: DashboardEventStream,
        *,
        cursor: int,
        initial_frames: tuple[bytes, ...],
    ) -> None:
        self._stream = stream
        self._cursor = cursor
        self._initial_frames = deque(initial_frames)
        self._closed = False

    def next_batch(
        self,
        *,
        timeout: float | None = None,
        limit: int = 32,
    ) -> tuple[bytes, ...]:
        """Return replay/live frames or one content-free heartbeat comment."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 64:
            raise ValueError("batch limit must be between 1 and 64")
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout < 0
        ):
            raise ValueError("timeout must be a non-negative number")
        return self._stream._next_batch(self, timeout=timeout, limit=limit)

    def close(self) -> None:
        """Release this subscription. Repeated close calls are harmless."""
        self._stream._close_subscription(self)


class DashboardEventStream:
    """Own one sampler, one bounded event ring, and all SSE subscriptions."""

    def __init__(
        self,
        change_feed: DashboardChangeFeed,
        *,
        clock: Callable[[], datetime] = _utc_now,
        ring_size: int = 256,
        max_subscribers: int = 8,
        heartbeat_seconds: float = 15.0,
        sampler_wait: Callable[[threading.Event, float], bool] | None = None,
    ) -> None:
        if isinstance(ring_size, bool) or not isinstance(ring_size, int):
            raise ValueError("ring_size must be an integer")
        if not 1 <= ring_size <= 1_024:
            raise ValueError("ring_size must be between 1 and 1024")
        if isinstance(max_subscribers, bool) or not isinstance(max_subscribers, int):
            raise ValueError("max_subscribers must be an integer")
        if not 1 <= max_subscribers <= 64:
            raise ValueError("max_subscribers must be between 1 and 64")
        if isinstance(heartbeat_seconds, bool) or not isinstance(
            heartbeat_seconds, (int, float)
        ):
            raise ValueError("heartbeat_seconds must be a number")
        if not 0.1 <= float(heartbeat_seconds) <= 60.0:
            raise ValueError("heartbeat_seconds must be between 0.1 and 60")

        self._change_feed = change_feed
        self._clock = clock
        self._ring: deque[_StoredEvent] = deque(maxlen=ring_size)
        self._max_subscribers = max_subscribers
        self._heartbeat_seconds = float(heartbeat_seconds)
        self._sampler_wait = sampler_wait or (
            lambda stop, seconds: stop.wait(seconds)
        )
        self._epoch = uuid.uuid4().hex
        self._stream_id = f"stream_{self._epoch}"
        self._sequence = 0
        self._baseline: dict[str, tuple[str, str]] | None = None
        self._condition = threading.Condition(threading.RLock())
        self._stop = threading.Event()
        self._first_sample_done = threading.Event()
        self._sampler: threading.Thread | None = None
        self._started = False
        self._closed = False
        self._subscriptions: set[DashboardEventSubscription] = set()

    def start(self) -> None:
        """Start the sole sampler thread exactly once."""
        with self._condition:
            if self._closed:
                return
            if not self._started:
                self._started = True
                sampler = threading.Thread(
                    target=self._sample_loop,
                    name="minicode-dashboard-event-sampler",
                    daemon=True,
                )
                self._sampler = sampler
                sampler.start()
        self._first_sample_done.wait(timeout=5)

    def subscribe(
        self, last_event_id: str | None = None
    ) -> DashboardEventSubscription:
        """Open one bounded subscriber at the current stream position."""
        with self._condition:
            if self._closed:
                raise RuntimeError("event stream is closed")
            if len(self._subscriptions) >= self._max_subscribers:
                raise EventStreamBusy("event stream subscriber limit reached")
            if last_event_id is None:
                cursor = self._sequence
                initial_frames = (self._ready_frame(cursor),)
            else:
                epoch, cursor = self._parse_cursor(last_event_id)
                if epoch != self._epoch:
                    cursor = self._sequence
                    initial_frames = (self._reset_frame("stream_restarted"),)
                elif cursor > self._sequence or self._cursor_is_too_old(cursor):
                    cursor = self._sequence
                    initial_frames = (self._reset_frame("replay_unavailable"),)
                else:
                    initial_frames = ()
            subscription = DashboardEventSubscription(
                self,
                cursor=cursor,
                initial_frames=initial_frames,
            )
            self._subscriptions.add(subscription)
            return subscription

    @staticmethod
    def _parse_cursor(value: str) -> tuple[str, int]:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > _MAX_EVENT_ID_CHARS
        ):
            raise InvalidEventCursor("event cursor is invalid")
        match = _EVENT_ID_RE.fullmatch(value)
        if match is None:
            raise InvalidEventCursor("event cursor is invalid")
        return match.group(1), int(match.group(2), 16)

    def _cursor_is_too_old(self, cursor: int) -> bool:
        return bool(self._ring) and cursor < self._ring[0].sequence - 1

    def _ready_frame(self, sequence: int) -> bytes:
        return self._event_frame(
            "stream.ready",
            sequence,
            {
                "schemaVersion": 2,
                "type": "stream.ready",
                "streamId": self._stream_id,
                "generatedAt": _iso_time(self._clock()),
                "retryMs": 2_000,
            },
            retry_ms=2_000,
        )

    def _reset_frame(self, reason: str) -> bytes:
        if reason not in {"stream_restarted", "replay_unavailable"}:
            raise ValueError("invalid stream reset reason")
        return self._event_frame(
            "stream.reset",
            self._sequence,
            {
                "schemaVersion": 2,
                "type": "stream.reset",
                "generatedAt": _iso_time(self._clock()),
                "reason": reason,
                "resources": list(RESOURCE_NAMES),
            },
            retry_ms=2_000,
        )

    def close(self) -> None:
        """Stop sampling and immediately wake every waiting subscriber."""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            for subscription in self._subscriptions:
                subscription._closed = True
                subscription._initial_frames.clear()
            self._subscriptions.clear()
            self._condition.notify_all()
            sampler = self._sampler
        if sampler is not None and sampler is not threading.current_thread():
            sampler.join(timeout=5)

    def _sample_loop(self) -> None:
        while not self._stop.is_set():
            poll_seconds = 2.0
            try:
                snapshot = self._change_feed.snapshot()
                resources, poll_seconds = self._safe_snapshot(snapshot)
                with self._condition:
                    if self._closed:
                        return
                    if self._baseline is None:
                        self._baseline = resources
                    else:
                        changed = [
                            name
                            for name in RESOURCE_NAMES
                            if self._baseline[name] != resources[name]
                        ]
                        self._baseline = resources
                        if changed:
                            self._append_changed(resources, changed)
            except Exception:
                # Sampling failures are transport-local and never become event data.
                pass
            finally:
                self._first_sample_done.set()
            if self._sampler_wait(self._stop, poll_seconds):
                return

    @staticmethod
    def _safe_snapshot(
        snapshot: Mapping[str, Any],
    ) -> tuple[dict[str, tuple[str, str]], float]:
        if not isinstance(snapshot, Mapping):
            raise ValueError("invalid change snapshot")
        schema_version = snapshot.get("schemaVersion")
        if isinstance(schema_version, bool) or schema_version != 2:
            raise ValueError("invalid change schema")
        raw_resources = snapshot.get("resources")
        if (
            not isinstance(raw_resources, Mapping)
            or list(raw_resources) != list(RESOURCE_NAMES)
        ):
            raise ValueError("invalid change resources")
        resources: dict[str, tuple[str, str]] = {}
        for name in RESOURCE_NAMES:
            raw = raw_resources[name]
            if not isinstance(raw, Mapping) or set(raw) != {"status", "revision"}:
                raise ValueError("invalid change resource")
            status = raw.get("status")
            revision = raw.get("revision")
            if status not in RESOURCE_STATUSES or not isinstance(
                revision, str
            ) or not _REVISION_RE.fullmatch(revision):
                raise ValueError("invalid change resource")
            resources[name] = (status, revision)
        poll_after_ms = snapshot.get("pollAfterMs", 2_000)
        if (
            isinstance(poll_after_ms, bool)
            or not isinstance(poll_after_ms, int)
            or not 1_000 <= poll_after_ms <= 10_000
        ):
            poll_after_ms = 2_000
        return resources, poll_after_ms / 1_000

    def _append_changed(
        self,
        resources: dict[str, tuple[str, str]],
        changed: list[str],
    ) -> None:
        self._sequence += 1
        payload = {
            "schemaVersion": 2,
            "type": "resources.changed",
            "generatedAt": _iso_time(self._clock()),
            "resources": [
                {
                    "name": name,
                    "status": resources[name][0],
                    "revision": resources[name][1],
                }
                for name in changed
            ],
        }
        frame = self._event_frame("resources.changed", self._sequence, payload)
        self._ring.append(_StoredEvent(self._sequence, frame))
        self._condition.notify_all()

    def _event_frame(
        self,
        event_type: str,
        sequence: int,
        payload: Mapping[str, object],
        *,
        retry_ms: int | None = None,
    ) -> bytes:
        data = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        if "\n" in data or "\r" in data:
            raise ValueError("SSE data must be one line")
        event_id = f"evt_{self._epoch}_{sequence:016x}"
        lines = [f"id: {event_id}", f"event: {event_type}"]
        if retry_ms is not None:
            lines.append(f"retry: {retry_ms}")
        lines.extend((f"data: {data}", "", ""))
        encoded = "\n".join(lines).encode("utf-8")
        if len(encoded) > _MAX_EVENT_BYTES:
            raise ValueError("SSE event exceeds the byte limit")
        return encoded

    def _next_batch(
        self,
        subscription: DashboardEventSubscription,
        *,
        timeout: float | None,
        limit: int,
    ) -> tuple[bytes, ...]:
        with self._condition:
            if subscription._closed:
                return ()
            if subscription._initial_frames:
                return tuple(
                    subscription._initial_frames.popleft()
                    for _ in range(min(limit, len(subscription._initial_frames)))
                )
            if self._cursor_is_too_old(subscription._cursor):
                subscription._cursor = self._sequence
                return (self._reset_frame("replay_unavailable"),)

            def available() -> bool:
                return self._closed or self._cursor_is_too_old(
                    subscription._cursor
                ) or any(
                    event.sequence > subscription._cursor for event in self._ring
                )

            wait_seconds = self._heartbeat_seconds if timeout is None else timeout
            if not available() and wait_seconds > 0:
                self._condition.wait_for(available, timeout=wait_seconds)
            if self._closed or subscription._closed:
                return ()
            if self._cursor_is_too_old(subscription._cursor):
                subscription._cursor = self._sequence
                return (self._reset_frame("replay_unavailable"),)
            events = [
                event
                for event in self._ring
                if event.sequence > subscription._cursor
            ][:limit]
            if events:
                subscription._cursor = events[-1].sequence
                return tuple(event.frame for event in events)
            return (HEARTBEAT_FRAME,)

    def _close_subscription(
        self, subscription: DashboardEventSubscription
    ) -> None:
        with self._condition:
            if subscription._closed:
                return
            subscription._closed = True
            subscription._initial_frames.clear()
            self._subscriptions.discard(subscription)
            self._condition.notify_all()


__all__ = [
    "DashboardEventStream",
    "DashboardEventSubscription",
    "EventStreamBusy",
    "InvalidEventCursor",
]
