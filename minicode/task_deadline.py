"""Shared wall-clock deadline contract for sub-agent tasks."""

from __future__ import annotations

DEFAULT_TASK_TIMEOUT_SECONDS = 120.0


class TaskDeadlineExceeded(RuntimeError):
    """One sub-agent task exceeded its cooperative wall-clock budget."""

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"sub-agent deadline exceeded after {timeout_seconds:g}s"
        )


__all__ = ["DEFAULT_TASK_TIMEOUT_SECONDS", "TaskDeadlineExceeded"]
