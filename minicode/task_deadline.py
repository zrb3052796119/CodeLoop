"""Shared wall-clock deadline contract for sub-agent tasks."""

from __future__ import annotations

import os
import time

DEFAULT_TASK_TIMEOUT_SECONDS = 120.0
MIN_TASK_TIMEOUT_SECONDS = 0.001
MAX_TASK_TIMEOUT_SECONDS = 86_400.0


class TaskDeadlineExceeded(RuntimeError):
    """One sub-agent task exceeded its cooperative wall-clock budget."""

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"sub-agent deadline exceeded after {timeout_seconds:g}s")


def task_timeout_seconds(context: object) -> float:
    """Resolve and clamp the host-owned timeout for one task invocation."""
    runtime = getattr(context, "_runtime", None)
    runtime_value = None
    if isinstance(runtime, dict):
        settings = runtime.get("task")
        runtime_value = (
            runtime.get("taskTimeoutSeconds")
            or runtime.get("agentTaskTimeoutSeconds")
            or (settings.get("timeoutSeconds") if isinstance(settings, dict) else None)
        )
    raw = os.environ.get("MINICODE_TASK_TIMEOUT_SECONDS") or runtime_value
    try:
        parsed = float(raw) if raw not in (None, "") else DEFAULT_TASK_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        parsed = DEFAULT_TASK_TIMEOUT_SECONDS
    if not parsed > 0 or parsed != parsed:
        parsed = DEFAULT_TASK_TIMEOUT_SECONDS
    return min(
        MAX_TASK_TIMEOUT_SECONDS,
        max(MIN_TASK_TIMEOUT_SECONDS, parsed),
    )


def raise_task_deadline(context: object) -> None:
    """Raise when cancellation was requested or the task deadline elapsed."""
    token = getattr(context, "_cancellation_token", None)
    checker = getattr(token, "raise_if_requested", None)
    if callable(checker):
        checker()
    deadline = getattr(context, "_task_deadline_monotonic", None)
    if isinstance(deadline, (int, float)) and time.monotonic() >= deadline:
        timeout = getattr(context, "_task_timeout_seconds", None)
        if not isinstance(timeout, (int, float)):
            timeout = task_timeout_seconds(context)
        raise TaskDeadlineExceeded(float(timeout))


__all__ = (
    "DEFAULT_TASK_TIMEOUT_SECONDS",
    "MAX_TASK_TIMEOUT_SECONDS",
    "MIN_TASK_TIMEOUT_SECONDS",
    "TaskDeadlineExceeded",
    "raise_task_deadline",
    "task_timeout_seconds",
)
