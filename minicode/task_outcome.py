"""Canonical task outcome shared by every post-task learning consumer."""

from __future__ import annotations

from dataclasses import dataclass


_TASK_OUTCOMES = frozenset({"success", "failed", "unknown", "cancelled"})
_VERIFICATION_STATUSES = frozenset({"verified", "failed", "unverified"})


@dataclass(frozen=True, slots=True)
class CanonicalTaskOutcome:
    """Completion, verification-backed goal, and tool reliability evidence."""

    status: str
    completion_succeeded: bool
    verification_status: str
    verification_passed_count: int
    verification_failed_count: int
    goal_achieved: bool
    learning_success: bool | None
    tool_error_count: int
    had_tool_errors: bool
    errors_recovered: bool


@dataclass(slots=True)
class AgentOutcomeCapture:
    """Optional typed result channel for callers that need status, not text."""

    outcome: CanonicalTaskOutcome | None = None

    def record(self, outcome: CanonicalTaskOutcome) -> None:
        self.outcome = outcome


def canonicalize_task_outcome(
    turn_outcome: str,
    tool_error_count: int,
    *,
    verification_passed: int = 0,
    verification_failed: int = 0,
) -> CanonicalTaskOutcome:
    """Resolve completion separately from independently verified success."""
    for name, value in (
        ("tool_error_count", tool_error_count),
        ("verification_passed", verification_passed),
        ("verification_failed", verification_failed),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")

    status = turn_outcome if turn_outcome in _TASK_OUTCOMES else "unknown"
    completion_succeeded = status == "success"
    if verification_failed > 0:
        verification_status = "failed"
    elif verification_passed > 0:
        verification_status = "verified"
    else:
        verification_status = "unverified"
    if verification_status not in _VERIFICATION_STATUSES:  # pragma: no cover
        raise ValueError("invalid verification status")
    goal_achieved = completion_succeeded and verification_status == "verified"
    learning_success: bool | None
    if goal_achieved:
        learning_success = True
    elif status == "failed" or verification_status == "failed":
        learning_success = False
    else:
        learning_success = None

    had_tool_errors = tool_error_count > 0
    return CanonicalTaskOutcome(
        status=status,
        completion_succeeded=completion_succeeded,
        verification_status=verification_status,
        verification_passed_count=verification_passed,
        verification_failed_count=verification_failed,
        goal_achieved=goal_achieved,
        learning_success=learning_success,
        tool_error_count=tool_error_count,
        had_tool_errors=had_tool_errors,
        errors_recovered=had_tool_errors and completion_succeeded,
    )
