"""Fixed-capacity process-local DNS resolution."""

from __future__ import annotations

import socket
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass


DEFAULT_RESOLVER_WORKER_LIMIT = 4
DEFAULT_RESOLVER_QUEUE_LIMIT = 8
_ERROR_CODES = frozenset(
    {"dns_error", "network_unavailable", "resolver_busy", "timeout"}
)
_ResolverCallable = Callable[..., list[tuple[object, ...]]]


class ResolverError(RuntimeError):
    """Content-free resolver failure."""

    def __init__(self, code: str) -> None:
        self.code = code if code in _ERROR_CODES else "dns_error"
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class ResolverSnapshot:
    worker_limit: int
    queue_limit: int
    active_count: int
    queued_count: int
    accepting: bool
    closed: bool


@dataclass(slots=True, eq=False, repr=False)
class _WorkItem:
    hostname: str | None
    port: int | None
    deadline: float
    completed: threading.Event
    state: str = "queued"
    abandoned: bool = False
    result: list[tuple[object, ...]] | None = None
    error_code: str | None = None
    completed_at: float | None = None


class BoundedResolver:
    """Resolve through fixed daemon workers and a fixed pending queue."""

    def __init__(
        self,
        *,
        worker_limit: int = DEFAULT_RESOLVER_WORKER_LIMIT,
        queue_limit: int = DEFAULT_RESOLVER_QUEUE_LIMIT,
        resolver: _ResolverCallable | None = None,
    ) -> None:
        if (
            isinstance(worker_limit, bool)
            or not isinstance(worker_limit, int)
            or worker_limit <= 0
            or isinstance(queue_limit, bool)
            or not isinstance(queue_limit, int)
            or queue_limit <= 0
        ):
            raise ValueError("resolver capacity must be positive integers")
        self._worker_limit = worker_limit
        self._queue_limit = queue_limit
        self._resolver = resolver
        self._condition = threading.Condition()
        self._queue: deque[_WorkItem] = deque()
        self._active_items: set[_WorkItem] = set()
        self._threads: list[threading.Thread] = []
        self._active_count = 0
        self._accepting = True
        self._closed = False
        self._started = False

    def resolve(
        self,
        hostname: str,
        port: int,
        *,
        deadline: float,
    ) -> list[tuple[object, ...]]:
        """Resolve before ``deadline`` or raise a content-free error."""
        if deadline <= time.monotonic():
            raise ResolverError("timeout")
        item = _WorkItem(hostname, port, deadline, threading.Event())
        with self._condition:
            if not self._accepting:
                raise ResolverError("network_unavailable")
            self._start_workers_locked()
            if len(self._queue) >= self._queue_limit:
                raise ResolverError("resolver_busy")
            self._queue.append(item)
            self._condition.notify()

        remaining = deadline - time.monotonic()
        completed = remaining > 0 and item.completed.wait(remaining)
        with self._condition:
            if not completed and item.error_code is None and item.result is None:
                item.abandoned = True
                if item.state == "queued":
                    self._remove_queued_locked(item)
                    item.state = "finished"
                    item.hostname = None
                    item.port = None
                    item.completed_at = time.monotonic()
                item.error_code = "timeout"
                item.completed.set()
            result = item.result
            error_code = item.error_code
            item.result = None

        if error_code is not None:
            raise ResolverError(error_code)
        if result is None:
            raise ResolverError("dns_error")
        return result

    def close(self) -> None:
        """Stop accepting work without joining an uninterruptible resolver."""
        with self._condition:
            if self._closed:
                return
            self._accepting = False
            self._closed = True
            now = time.monotonic()
            while self._queue:
                item = self._queue.popleft()
                item.abandoned = True
                item.state = "finished"
                item.hostname = None
                item.port = None
                item.error_code = "network_unavailable"
                item.completed_at = now
                item.completed.set()
            for item in self._active_items:
                item.abandoned = True
                item.error_code = "network_unavailable"
                item.completed_at = now
                item.completed.set()
            self._condition.notify_all()

    def snapshot(self) -> ResolverSnapshot:
        """Return only fixed-capacity counters and lifecycle flags."""
        with self._condition:
            return ResolverSnapshot(
                worker_limit=self._worker_limit,
                queue_limit=self._queue_limit,
                active_count=self._active_count,
                queued_count=len(self._queue),
                accepting=self._accepting,
                closed=self._closed,
            )

    def _start_workers_locked(self) -> None:
        if self._started:
            return
        self._started = True
        for index in range(self._worker_limit):
            thread = threading.Thread(
                target=self._worker,
                name=f"minicode-dns-resolver-{index + 1}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

    def _remove_queued_locked(self, item: _WorkItem) -> None:
        try:
            self._queue.remove(item)
        except ValueError:
            return
        self._condition.notify()

    def _worker(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._closed:
                    self._condition.wait()
                if self._closed and not self._queue:
                    return
                item = self._queue.popleft()
                if item.abandoned or item.deadline <= time.monotonic():
                    item.abandoned = True
                    item.state = "finished"
                    item.hostname = None
                    item.port = None
                    item.error_code = "timeout"
                    item.completed_at = time.monotonic()
                    item.completed.set()
                    continue
                item.state = "active"
                self._active_count += 1
                self._active_items.add(item)
                hostname = item.hostname
                port = item.port
                item.hostname = None
                item.port = None

            answers: list[tuple[object, ...]] | None = None
            error_code: str | None = None
            try:
                resolver = self._resolver or socket.getaddrinfo
                answers = resolver(
                    hostname,
                    port,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                )
            except Exception:
                error_code = "dns_error"
            completed_at = time.monotonic()

            with self._condition:
                self._active_count -= 1
                self._active_items.discard(item)
                if not item.abandoned:
                    if completed_at > item.deadline:
                        error_code = "timeout"
                    elif not answers:
                        error_code = "dns_error"
                    item.result = answers if error_code is None else None
                    item.error_code = error_code
                item.state = "finished"
                item.completed_at = completed_at
                item.completed.set()
                self._condition.notify_all()


__all__ = [
    "BoundedResolver",
    "DEFAULT_RESOLVER_QUEUE_LIMIT",
    "DEFAULT_RESOLVER_WORKER_LIMIT",
    "ResolverError",
    "ResolverSnapshot",
]
