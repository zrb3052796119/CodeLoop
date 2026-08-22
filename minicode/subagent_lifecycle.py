"""Turn-scoped asynchronous lifecycle for read-only sub-agents.

The registry is deliberately small and ownership-oriented: callers receive an
opaque ID, can observe or cancel only jobs in this registry, and the owning
agent turn cancels every unfinished job during shutdown.  Python cannot kill a
thread blocked inside a provider call, so asynchronous execution is restricted
to read-only agent types and cancellation is cooperative.
"""

from __future__ import annotations

import concurrent.futures
import math
import threading
from dataclasses import dataclass, field
from typing import Callable, Literal

from minicode.subagent_journal import new_subagent_id
from minicode.tooling import ToolResult


LIFECYCLE_VERSION = 1
ASYNC_AGENT_TYPES = frozenset({"explore", "plan"})
_DEFAULT_MAX_JOBS = 16
_DEFAULT_MAX_RESULT_CHARS = 12_000
_DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 0.5

LifecycleStatus = Literal[
    "queued",
    "running",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
]
AsyncRunner = Callable[[str, threading.Event], ToolResult]


class SubagentLifecycleError(RuntimeError):
    """Base error for a rejected lifecycle operation."""


class SubagentLifecycleNotFound(SubagentLifecycleError):
    """Raised when an ID is not owned by this turn-scoped registry."""


class SubagentWorkerCancelled(RuntimeError):
    """Internal worker signal proving cooperative cancellation completed."""


@dataclass(slots=True)
class _Job:
    subagent_id: str
    agent_type: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    future: concurrent.futures.Future[ToolResult] | None = None
    terminal_status: LifecycleStatus | None = None
    terminal_result: dict[str, object] | None = None
    terminal_observed: bool = False


class AsyncSubagentLifecycle:
    """Own a bounded set of asynchronous, read-only sub-agent jobs."""

    def __init__(
        self,
        *,
        max_workers: int = 4,
        max_jobs: int = _DEFAULT_MAX_JOBS,
        max_result_chars: int = _DEFAULT_MAX_RESULT_CHARS,
    ) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int):
            raise ValueError("max_workers must be a positive integer")
        if isinstance(max_jobs, bool) or not isinstance(max_jobs, int):
            raise ValueError("max_jobs must be a positive integer")
        if (
            isinstance(max_result_chars, bool)
            or not isinstance(max_result_chars, int)
        ):
            raise ValueError("max_result_chars must be a positive integer")
        if max_workers < 1 or max_jobs < 1 or max_result_chars < 256:
            raise ValueError("lifecycle bounds must be positive and non-trivial")

        self._max_jobs = max_jobs
        self._max_result_chars = max_result_chars
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}
        self._closed = False
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=min(max_workers, max_jobs),
            thread_name_prefix="minicode-subagent",
        )

    def spawn(
        self,
        *,
        agent_type: str,
        runner: AsyncRunner,
    ) -> dict[str, object]:
        """Submit one read-only job and return without waiting for completion."""
        if agent_type not in ASYNC_AGENT_TYPES:
            raise SubagentLifecycleError(
                "asynchronous sub-agents must use explore or plan"
            )
        if not callable(runner):
            raise SubagentLifecycleError("runner must be callable")

        with self._lock:
            if self._closed:
                raise SubagentLifecycleError("sub-agent lifecycle is closed")
            if len(self._jobs) >= self._max_jobs:
                raise SubagentLifecycleError("sub-agent lifecycle capacity exceeded")
            subagent_id = new_subagent_id()
            job = _Job(subagent_id=subagent_id, agent_type=agent_type)
            self._jobs[subagent_id] = job
            try:
                job.future = self._executor.submit(
                    runner,
                    subagent_id,
                    job.cancel_event,
                )
            except BaseException:
                self._jobs.pop(subagent_id, None)
                raise
            snapshot = self._snapshot_locked(job)
            if snapshot["terminal"]:
                job.terminal_observed = True
            return snapshot

    def poll(
        self,
        subagent_id: str,
        *,
        wait_seconds: float = 0.0,
    ) -> dict[str, object]:
        """Return a projection, optionally waiting briefly for completion."""
        if (
            isinstance(wait_seconds, bool)
            or not isinstance(wait_seconds, (int, float))
            or not math.isfinite(float(wait_seconds))
            or not 0 <= float(wait_seconds) <= 30
        ):
            raise SubagentLifecycleError("poll wait_seconds must be between 0 and 30")
        with self._lock:
            job = self._job_locked(subagent_id)
            future = job.future
            terminal = job.terminal_status is not None or bool(
                future is not None and future.done()
            )
        if not terminal and future is not None and wait_seconds:
            concurrent.futures.wait([future], timeout=float(wait_seconds))
        with self._lock:
            job = self._job_locked(subagent_id)
            snapshot = self._snapshot_locked(job)
            if snapshot["terminal"]:
                job.terminal_observed = True
            return snapshot

    def cancel(self, subagent_id: str) -> dict[str, object]:
        """Request cooperative cancellation; repeated calls are idempotent."""
        with self._lock:
            job = self._job_locked(subagent_id)
            if job.terminal_status is not None:
                snapshot = self._snapshot_locked(job)
                if snapshot["terminal"]:
                    job.terminal_observed = True
                return snapshot
            if job.future is not None and job.future.done():
                snapshot = self._snapshot_locked(job)
                if snapshot["terminal"]:
                    job.terminal_observed = True
                return snapshot
            job.cancel_event.set()
            cancelled_before_start = bool(
                job.future is not None and job.future.cancel()
            )
            if cancelled_before_start:
                job.terminal_status = "cancelled"
                job.terminal_result = None
            snapshot = self._snapshot_locked(job)
            if snapshot["terminal"]:
                job.terminal_observed = True
            return snapshot

    def finalization_barrier(
        self,
        *,
        wait_seconds: float = 0.0,
    ) -> dict[str, object]:
        """Project unfinished and newly completed jobs before parent final.

        A terminal result returned by ``poll``/``cancel`` is already visible
        in the parent's transcript. A result that completes between model
        turns is returned exactly once here, so the agent loop can inject it
        before accepting a final answer. Non-terminal jobs keep the barrier
        closed without blocking a model thread.
        """
        if (
            isinstance(wait_seconds, bool)
            or not isinstance(wait_seconds, (int, float))
            or not math.isfinite(float(wait_seconds))
            or not 0 <= float(wait_seconds) <= 5
        ):
            raise SubagentLifecycleError(
                "finalization wait_seconds must be between 0 and 5"
            )
        with self._lock:
            unfinished_futures = [
                job.future
                for job in self._jobs.values()
                if job.terminal_status is None
                and job.future is not None
                and not job.future.done()
            ]
        if unfinished_futures and wait_seconds:
            concurrent.futures.wait(
                unfinished_futures,
                timeout=float(wait_seconds),
            )

        with self._lock:
            pending: list[dict[str, object]] = []
            completed: list[dict[str, object]] = []
            for job in self._jobs.values():
                snapshot = self._snapshot_locked(job)
                if not snapshot["terminal"]:
                    pending.append(snapshot)
                elif not job.terminal_observed:
                    completed.append(snapshot)
                    job.terminal_observed = True
            return {
                "lifecycleVersion": LIFECYCLE_VERSION,
                "ready": not pending and not completed,
                "pending": pending,
                "completed": completed,
            }

    def shutdown(
        self,
        *,
        timeout_seconds: float = _DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    ) -> None:
        """Cancel unfinished jobs and perform bounded cooperative cleanup."""
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        with self._lock:
            self._closed = True
            futures: list[concurrent.futures.Future[ToolResult]] = []
            for job in self._jobs.values():
                if job.terminal_status is not None:
                    continue
                if job.future is not None and job.future.done():
                    self._snapshot_locked(job)
                    continue
                job.cancel_event.set()
                if job.future is not None:
                    if job.future.cancel():
                        job.terminal_status = "cancelled"
                        job.terminal_result = None
                    futures.append(job.future)
        if futures:
            concurrent.futures.wait(futures, timeout=timeout_seconds)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _job_locked(self, subagent_id: str) -> _Job:
        if not isinstance(subagent_id, str):
            raise SubagentLifecycleNotFound("sub-agent ID is not owned by this turn")
        job = self._jobs.get(subagent_id)
        if job is None:
            raise SubagentLifecycleNotFound("sub-agent ID is not owned by this turn")
        return job

    def _snapshot_locked(self, job: _Job) -> dict[str, object]:
        if job.terminal_status is None:
            future = job.future
            if future is not None and future.done():
                self._materialize_terminal_locked(job, future)

        status: LifecycleStatus
        if job.terminal_status is not None:
            status = job.terminal_status
        elif job.cancel_event.is_set():
            status = "cancelling"
        elif job.future is not None and job.future.running():
            status = "running"
        else:
            status = "queued"
        terminal = status in {"completed", "failed", "cancelled"}
        return {
            "lifecycleVersion": LIFECYCLE_VERSION,
            "subagentId": job.subagent_id,
            "agentType": job.agent_type,
            "status": status,
            "terminal": terminal,
            "result": job.terminal_result if terminal else None,
        }

    def _materialize_terminal_locked(
        self,
        job: _Job,
        future: concurrent.futures.Future[ToolResult],
    ) -> None:
        if job.terminal_status is not None:
            return
        if future.cancelled():
            job.terminal_status = "cancelled"
            job.terminal_result = None
            return
        try:
            result = future.result()
        except concurrent.futures.CancelledError:
            job.terminal_status = "cancelled"
            job.terminal_result = None
            return
        except SubagentWorkerCancelled:
            job.terminal_status = "cancelled"
            job.terminal_result = None
            return
        except BaseException as exc:  # noqa: BLE001 - worker boundary
            job.terminal_status = "failed"
            job.terminal_result = {
                "ok": False,
                "output": (
                    "error[sub_agent_worker_failed]: "
                    f"{type(exc).__name__}"
                ),
                "truncated": False,
            }
            return
        if not isinstance(result, ToolResult):
            job.terminal_status = "failed"
            job.terminal_result = {
                "ok": False,
                "output": "error[sub_agent_worker_failed]: invalid result type",
                "truncated": False,
            }
            return

        output, truncated = self._bounded_output(result.output)
        job.terminal_status = "completed" if result.ok else "failed"
        job.terminal_result = {
            "ok": result.ok,
            "output": output,
            "truncated": truncated,
        }

    def _bounded_output(self, output: str) -> tuple[str, bool]:
        text = output if isinstance(output, str) else str(output)
        if len(text) <= self._max_result_chars:
            return text, False
        marker = "\n...[sub-agent result truncated]"
        kept = max(0, self._max_result_chars - len(marker))
        return f"{text[:kept]}{marker}", True
