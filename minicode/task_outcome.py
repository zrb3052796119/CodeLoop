"""Canonical task outcome shared by every post-task learning consumer."""

from __future__ import annotations

from dataclasses import dataclass


_TASK_OUTCOMES = frozenset({"success", "failed", "unknown", "cancelled"})


@dataclass(frozen=True, slots=True)
class CanonicalTaskOutcome:
    """One task-level verdict plus separate tool-reliability evidence."""

    status: str
    goal_achieved: bool
    learning_success: bool | None
    tool_error_count: int
    had_tool_errors: bool
    errors_recovered: bool


def canonicalize_task_outcome(
    turn_outcome: str,
    tool_error_count: int,
) -> CanonicalTaskOutcome:
    """Resolve one task verdict without treating recovered tool errors as failure."""
    if (
        isinstance(tool_error_count, bool)
        or not isinstance(tool_error_count, int)
        or tool_error_count < 0
    ):
        raise ValueError("tool_error_count must be a non-negative integer")

    status = turn_outcome if turn_outcome in _TASK_OUTCOMES else "unknown"
    goal_achieved = status == "success"
    learning_success: bool | None
    if status == "success":
        learning_success = True
    elif status == "failed":
        learning_success = False
    else:
        learning_success = None

    had_tool_errors = tool_error_count > 0
    return CanonicalTaskOutcome(
        status=status,
        goal_achieved=goal_achieved,
        learning_success=learning_success,
        tool_error_count=tool_error_count,
        had_tool_errors=had_tool_errors,
        errors_recovered=had_tool_errors and goal_achieved,
    )
