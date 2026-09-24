"""Bounded execution seam for host-owned conflict callbacks."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Protocol, TypeVar, cast

_PortResult = TypeVar("_PortResult")


class ConflictPortDeadlineExceeded(TimeoutError):
    """A host-owned conflict port did not finish inside its call budget."""

    def __init__(self, reason: str = "port_deadline_exceeded") -> None:
        super().__init__(reason)
        self.reason = reason


class ConflictPortCapacityExhausted(RuntimeError):
    """The runner refused to create another possibly hung port call."""


class ConflictPortCancelled(RuntimeError):
    """The injected cancellation token requested port-call cancellation."""

    def __init__(
        self,
        message: str = "conflict port call cancelled",
        *,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.cause = cause


class ConflictPortRunner(Protocol):
    """Host-owned hard boundary for one otherwise synchronous port call.

    ``timeout_seconds`` is a remaining duration measured by the caller, never
    an absolute timestamp.  Implementations apply that duration in their own
    monotonic clock domain.
    """

    def run(
        self,
        operation: Callable[[], _PortResult],
        *,
        port_name: str,
        operation_id: str,
        timeout_seconds: float | None,
        cancellation_token: object | None = None,
    ) -> _PortResult: ...


class BoundedConflictPortRunner:
    """Bound synchronous ports without accumulating unlimited hung threads.

    Python cannot forcibly stop an arbitrary in-process callback. Calls run in
    daemon threads and a non-blocking capacity gate limits how many late or
    permanently hung callbacks may remain alive. Once timeout or cancellation
    wins the per-call terminal gate, a late value or ordinary exception is
    discarded; typed cancellation and non-``Exception`` abort signals retain
    their identity when the callback itself has already completed.
    Capacity belongs to this runner instance, so the host must reuse a
    long-lived instance across conflict executions for the bound to be global.

    Hosts with a stronger isolation boundary (for example, a cancellable RPC
    or subprocess) should inject their own :class:`ConflictPortRunner`.
    """

    def __init__(
        self,
        *,
        max_in_flight: int = 4,
        default_timeout_seconds: float = 30.0,
        cancellation_poll_seconds: float = 0.01,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            isinstance(max_in_flight, bool)
            or not isinstance(max_in_flight, int)
            or max_in_flight < 1
        ):
            raise ValueError("max_in_flight must be a positive integer")
        timeout = self._positive_finite(
            default_timeout_seconds,
            name="default_timeout_seconds",
        )
        poll = self._positive_finite(
            cancellation_poll_seconds,
            name="cancellation_poll_seconds",
        )
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._capacity = threading.BoundedSemaphore(max_in_flight)
        self._default_timeout_seconds = timeout
        self._cancellation_poll_seconds = poll
        self._clock = clock

    @staticmethod
    def _positive_finite(value: object, *, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be positive and finite")
        normalized = float(value)
        if not math.isfinite(normalized) or normalized <= 0:
            raise ValueError(f"{name} must be positive and finite")
        return normalized

    def _clock_now(self) -> float:
        try:
            now = float(self._clock())
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise ConflictPortDeadlineExceeded(
                "port_clock_invalid"
            ) from exc
        if not math.isfinite(now):
            raise ConflictPortDeadlineExceeded("port_clock_invalid")
        return now

    @staticmethod
    def _raise_if_cancelled(cancellation_token: object | None) -> None:
        checker = getattr(cancellation_token, "raise_if_requested", None)
        if callable(checker):
            try:
                checker()
            except ConflictPortCancelled:
                raise
            except Exception as exc:
                raise ConflictPortCancelled(cause=exc) from exc
            return
        requested = getattr(cancellation_token, "is_requested", None)
        if callable(requested):
            try:
                is_requested = requested()
            except ConflictPortCancelled:
                raise
            except Exception as exc:
                raise ConflictPortCancelled(cause=exc) from exc
            if is_requested:
                raise ConflictPortCancelled()

    def run(
        self,
        operation: Callable[[], _PortResult],
        *,
        port_name: str,
        operation_id: str,
        timeout_seconds: float | None,
        cancellation_token: object | None = None,
    ) -> _PortResult:
        if not callable(operation):
            raise TypeError("operation must be callable")
        if not isinstance(port_name, str) or not port_name.strip():
            raise ValueError("port_name must be non-empty")
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be non-empty")

        now = self._clock_now()
        local_deadline = now + self._default_timeout_seconds
        timeout_reason = "port_deadline_exceeded"
        effective_deadline = local_deadline
        if timeout_seconds is not None:
            if isinstance(timeout_seconds, bool) or not isinstance(
                timeout_seconds, (int, float)
            ):
                raise TypeError("timeout_seconds must be numeric or null")
            supplied_timeout = float(timeout_seconds)
            if math.isnan(supplied_timeout):
                raise ConflictPortDeadlineExceeded("port_timeout_invalid")
            if supplied_timeout <= self._default_timeout_seconds:
                effective_deadline = now + max(0.0, supplied_timeout)
                timeout_reason = "global_deadline_exceeded"

        self._raise_if_cancelled(cancellation_token)
        if now >= effective_deadline:
            raise ConflictPortDeadlineExceeded(timeout_reason)
        if not self._capacity.acquire(blocking=False):
            raise ConflictPortCapacityExhausted(
                "port_runner_capacity_exhausted"
            )

        completed = threading.Event()
        state_lock = threading.Lock()
        terminal: list[tuple[str, object]] = []

        def settle(kind: str, value: object) -> bool:
            with state_lock:
                if terminal:
                    return False
                terminal.append((kind, value))
                completed.set()
                return True

        def settle_from_worker(kind: str, value: object) -> bool:
            with state_lock:
                # Make the slot reusable before waking this caller, while the
                # same lock prevents a deadline race from replacing an on-time
                # callback completion.
                self._capacity.release()
                if terminal:
                    return False
                terminal.append((kind, value))
                completed.set()
                return True

        def invoke() -> None:
            try:
                value = operation()
            except BaseException as exc:  # noqa: BLE001 - cross-thread signal
                completion: tuple[str, object] = ("exception", exc)
            else:
                completion = ("result", value)
            try:
                finished_at = self._clock_now()
            except BaseException as exc:  # noqa: BLE001 - preserve fatal signal
                if completion[0] == "result" or isinstance(
                    completion[1], Exception
                ) and not isinstance(completion[1], ConflictPortCancelled):
                    completion = ("exception", exc)
            else:
                if finished_at >= effective_deadline and (
                    completion[0] == "result"
                    or (
                        isinstance(completion[1], Exception)
                        and not isinstance(
                            completion[1], ConflictPortCancelled
                        )
                    )
                ):
                    # Completion time, not result kind, decides the race.  A
                    # late ordinary exception cannot outrank the deadline.
                    completion = (
                        "timeout",
                        ConflictPortDeadlineExceeded(timeout_reason),
                    )
            settle_from_worker(*completion)

        thread = threading.Thread(
            target=invoke,
            name=f"conflict-port-{port_name}-{operation_id}",
            daemon=True,
        )
        try:
            thread.start()
        except BaseException:
            self._capacity.release()
            raise

        while not completed.is_set():
            try:
                self._raise_if_cancelled(cancellation_token)
            except BaseException as exc:  # noqa: BLE001 - preserve cancellation
                settle("exception", exc)
                break
            try:
                current = self._clock_now()
            except BaseException as exc:  # noqa: BLE001 - preserve clock failure
                settle("exception", exc)
                break
            remaining = effective_deadline - current
            if remaining <= 0:
                settle(
                    "timeout",
                    ConflictPortDeadlineExceeded(timeout_reason),
                )
                break
            completed.wait(
                min(self._cancellation_poll_seconds, remaining)
            )

        with state_lock:
            kind, value = terminal[0]
        if kind == "result":
            return cast(_PortResult, value)
        if isinstance(value, BaseException):
            raise value
        raise RuntimeError("conflict port runner reached an invalid state")


__all__ = [
    "BoundedConflictPortRunner",
    "ConflictPortCancelled",
    "ConflictPortCapacityExhausted",
    "ConflictPortDeadlineExceeded",
    "ConflictPortRunner",
]
