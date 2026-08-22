"""Versioned, content-free canonical task outcome event contract."""

from __future__ import annotations

from collections.abc import Mapping


_OUTCOME_STATUSES = frozenset({"success", "failed", "unknown", "cancelled"})
_VERIFICATION_STATUSES = frozenset({"verified", "failed", "unverified"})
_MAX_TOOL_ERROR_COUNT = 100_000
_MAX_VERIFICATION_COUNT = 100_000


def normalize_task_outcome_payload(
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    """Return a strict v1/v2 whitelist or ``None`` for non-canonical input."""
    if payload.get("outcomeVersion") == 1:
        return _normalize_v1(payload)
    if payload.get("outcomeVersion") != 2:
        return None
    status = payload.get("outcomeStatus")
    completion_succeeded = payload.get("completionSucceeded")
    verification_status = payload.get("verificationStatus")
    verification_passed = payload.get("verificationPassedCount")
    verification_failed = payload.get("verificationFailedCount")
    goal_achieved = payload.get("goalAchieved")
    learning_success = payload.get("learningSuccess")
    had_tool_errors = payload.get("hadToolErrors")
    errors_recovered = payload.get("errorsRecovered")
    tool_error_count = payload.get("toolErrorCount")
    if (
        isinstance(verification_passed, bool)
        or not isinstance(verification_passed, int)
        or not 0 <= verification_passed <= _MAX_VERIFICATION_COUNT
        or isinstance(verification_failed, bool)
        or not isinstance(verification_failed, int)
        or not 0 <= verification_failed <= _MAX_VERIFICATION_COUNT
    ):
        return None
    expected_verification_status = (
        "failed"
        if verification_failed > 0
        else "verified"
        if verification_passed > 0
        else "unverified"
    )
    expected_completion = status == "success"
    expected_goal = expected_completion and verification_status == "verified"
    expected_learning_success = (
        True
        if expected_goal
        else False
        if status == "failed" or verification_status == "failed"
        else None
    )
    if (
        status not in _OUTCOME_STATUSES
        or not isinstance(completion_succeeded, bool)
        or completion_succeeded != expected_completion
        or verification_status not in _VERIFICATION_STATUSES
        or verification_status != expected_verification_status
        or not isinstance(goal_achieved, bool)
        or goal_achieved != expected_goal
        or learning_success is not expected_learning_success
        or not isinstance(had_tool_errors, bool)
        or not isinstance(errors_recovered, bool)
        or isinstance(tool_error_count, bool)
        or not isinstance(tool_error_count, int)
        or not 0 <= tool_error_count <= _MAX_TOOL_ERROR_COUNT
        or had_tool_errors != (tool_error_count > 0)
        or errors_recovered != (had_tool_errors and completion_succeeded)
    ):
        return None
    return {
        "outcomeVersion": 2,
        "outcomeStatus": status,
        "completionSucceeded": completion_succeeded,
        "verificationStatus": verification_status,
        "verificationPassedCount": verification_passed,
        "verificationFailedCount": verification_failed,
        "goalAchieved": goal_achieved,
        "learningSuccess": learning_success,
        "hadToolErrors": had_tool_errors,
        "errorsRecovered": errors_recovered,
        "toolErrorCount": tool_error_count,
    }


def _normalize_v1(payload: Mapping[str, object]) -> dict[str, object] | None:
    """Keep historical Run journals readable under the former semantics."""
    status = payload.get("outcomeStatus")
    goal_achieved = payload.get("goalAchieved")
    learning_success = payload.get("learningSuccess")
    had_tool_errors = payload.get("hadToolErrors")
    errors_recovered = payload.get("errorsRecovered")
    tool_error_count = payload.get("toolErrorCount")
    expected_learning_success = (
        True if status == "success" else False if status == "failed" else None
    )
    if (
        status not in _OUTCOME_STATUSES
        or not isinstance(goal_achieved, bool)
        or goal_achieved != (status == "success")
        or learning_success is not expected_learning_success
        or not isinstance(had_tool_errors, bool)
        or not isinstance(errors_recovered, bool)
        or isinstance(tool_error_count, bool)
        or not isinstance(tool_error_count, int)
        or not 0 <= tool_error_count <= _MAX_TOOL_ERROR_COUNT
        or had_tool_errors != (tool_error_count > 0)
        or errors_recovered != (had_tool_errors and goal_achieved)
    ):
        return None
    return {
        "outcomeVersion": 1,
        "outcomeStatus": status,
        "goalAchieved": goal_achieved,
        "learningSuccess": learning_success,
        "hadToolErrors": had_tool_errors,
        "errorsRecovered": errors_recovered,
        "toolErrorCount": tool_error_count,
    }


def project_task_outcome_event(outcome: object) -> dict[str, object]:
    """Project one canonical outcome object into the strict v2 contract."""
    payload = {
        "outcomeVersion": 2,
        "outcomeStatus": getattr(outcome, "status", None),
        "completionSucceeded": getattr(outcome, "completion_succeeded", None),
        "verificationStatus": getattr(outcome, "verification_status", None),
        "verificationPassedCount": getattr(
            outcome, "verification_passed_count", None
        ),
        "verificationFailedCount": getattr(
            outcome, "verification_failed_count", None
        ),
        "goalAchieved": getattr(outcome, "goal_achieved", None),
        "learningSuccess": getattr(outcome, "learning_success", None),
        "hadToolErrors": getattr(outcome, "had_tool_errors", None),
        "errorsRecovered": getattr(outcome, "errors_recovered", None),
        "toolErrorCount": getattr(outcome, "tool_error_count", None),
    }
    normalized = normalize_task_outcome_payload(payload)
    if normalized is None:
        raise ValueError("invalid canonical task outcome")
    return normalized


__all__ = [
    "normalize_task_outcome_payload",
    "project_task_outcome_event",
]
