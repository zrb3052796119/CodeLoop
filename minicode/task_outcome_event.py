"""Versioned, content-free canonical task outcome event contract."""

from __future__ import annotations

from collections.abc import Mapping


_OUTCOME_STATUSES = frozenset({"success", "failed", "unknown", "cancelled"})
_MAX_TOOL_ERROR_COUNT = 100_000


def normalize_task_outcome_payload(
    payload: Mapping[str, object],
) -> dict[str, object] | None:
    """Return the strict v1 whitelist or ``None`` for non-canonical input."""
    if payload.get("outcomeVersion") != 1:
        return None
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
    """Project one canonical outcome object into the strict v1 contract."""
    payload = {
        "outcomeVersion": 1,
        "outcomeStatus": getattr(outcome, "status", None),
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
