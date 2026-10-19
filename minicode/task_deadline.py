"""Shared wall-clock deadline contract for sub-agent tasks."""

from __future__ import annotations

import os
import math
import time

# No cumulative wall-clock cutoff unless explicitly configured. Existing
# ancestor deadlines and cancellation always remain authoritative.
DEFAULT_TASK_TIMEOUT_SECONDS = None
# Compatibility exports; explicit positive durations are no longer clamped.
MIN_TASK_TIMEOUT_SECONDS = 0.001
MAX_TASK_TIMEOUT_SECONDS = 86_400.0


class TaskDeadlineExceeded(RuntimeError):
    """One sub-agent task exceeded its cooperative wall-clock budget."""

    def __init__(self, timeout_seconds: float | None) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__("sub-agent deadline exceeded" + (
            f" after {timeout_seconds:g}s" if timeout_seconds is not None else ""
        ))


def task_timeout_seconds(context: object) -> float | None:
    """Resolve an explicit timeout, preserving None/zero as unlimited."""
    runtime = getattr(context, "_runtime", None)
    runtime_value = None
    if isinstance(runtime, dict):
        settings = runtime.get("task")
        if "taskTimeoutSeconds" in runtime:
            runtime_value = runtime["taskTimeoutSeconds"]
        elif "agentTaskTimeoutSeconds" in runtime:
            runtime_value = runtime["agentTaskTimeoutSeconds"]
        elif isinstance(settings, dict):
            runtime_value = settings.get("timeoutSeconds")
    configured_env = os.environ.get("MINICODE_TASK_TIMEOUT_SECONDS")
    raw = configured_env if configured_env not in (None, "") else runtime_value
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise ValueError("task timeout must be finite, non-negative, or None")
    try:
        parsed = float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("task timeout must be finite, non-negative, or None") from error
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError("task timeout must be finite, non-negative, or None")
    return parsed or None


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
        raise TaskDeadlineExceeded(float(timeout) if timeout is not None else None)


__all__ = (
    "DEFAULT_TASK_TIMEOUT_SECONDS",
    "MAX_TASK_TIMEOUT_SECONDS",
    "MIN_TASK_TIMEOUT_SECONDS",
    "TaskDeadlineExceeded",
    "raise_task_deadline",
    "task_timeout_seconds",
)
