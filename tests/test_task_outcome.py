from __future__ import annotations

from minicode.task_outcome import canonicalize_task_outcome
from minicode.task_outcome_event import (
    normalize_task_outcome_payload,
    project_task_outcome_event,
)


def test_successful_completion_without_verification_is_not_trusted_success() -> None:
    outcome = canonicalize_task_outcome("success", 0)

    assert outcome.status == "success"
    assert outcome.completion_succeeded is True
    assert outcome.verification_status == "unverified"
    assert outcome.goal_achieved is False
    assert outcome.learning_success is None


def test_passed_verification_confirms_goal_and_learning_success() -> None:
    outcome = canonicalize_task_outcome(
        "success",
        0,
        verification_passed=1,
        verification_failed=0,
    )

    assert outcome.completion_succeeded is True
    assert outcome.verification_status == "verified"
    assert outcome.goal_achieved is True
    assert outcome.learning_success is True


def test_failed_verification_overrides_successful_completion() -> None:
    outcome = canonicalize_task_outcome(
        "success",
        0,
        verification_passed=1,
        verification_failed=1,
    )

    assert outcome.completion_succeeded is True
    assert outcome.verification_status == "failed"
    assert outcome.goal_achieved is False
    assert outcome.learning_success is False


def test_v2_outcome_projection_is_strict_and_v1_remains_readable() -> None:
    outcome = canonicalize_task_outcome("success", 0)
    payload = project_task_outcome_event(outcome)

    assert payload == {
        "outcomeVersion": 2,
        "outcomeStatus": "success",
        "completionSucceeded": True,
        "verificationStatus": "unverified",
        "verificationPassedCount": 0,
        "verificationFailedCount": 0,
        "goalAchieved": False,
        "learningSuccess": None,
        "hadToolErrors": False,
        "errorsRecovered": False,
        "toolErrorCount": 0,
    }
    assert normalize_task_outcome_payload(payload) == payload
    assert normalize_task_outcome_payload(
        {
            "outcomeVersion": 1,
            "outcomeStatus": "success",
            "goalAchieved": True,
            "learningSuccess": True,
            "hadToolErrors": False,
            "errorsRecovered": False,
            "toolErrorCount": 0,
        }
    ) is not None
