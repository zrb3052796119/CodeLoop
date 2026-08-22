from __future__ import annotations

from minicode.verification_observation import (
    normalize_verification_payload,
    project_command_verification,
    project_verification,
)


def test_explicit_test_verification_is_content_free_and_round_trips() -> None:
    payload = project_verification(
        kind="tests",
        passed=True,
        source="test_runner",
    )

    assert payload == {
        "verificationVersion": 1,
        "kind": "tests",
        "outcome": "passed",
        "source": "test_runner",
    }
    assert normalize_verification_payload(payload) == payload


def test_workflow_review_verification_is_typed_and_source_bound() -> None:
    payload = project_verification(
        kind="review",
        passed=True,
        source="workflow_review",
    )

    assert payload == {
        "verificationVersion": 1,
        "kind": "review",
        "outcome": "passed",
        "source": "workflow_review",
    }
    assert normalize_verification_payload(payload) == payload
    assert project_verification(
        kind="tests",
        passed=True,
        source="workflow_review",
    ) is None


def test_normalizer_rejects_extra_fields_and_unknown_enum_values() -> None:
    valid = {
        "verificationVersion": 1,
        "kind": "tests",
        "outcome": "failed",
        "source": "test_runner",
    }

    assert normalize_verification_payload({**valid, "output": "1 passed"}) is None
    assert normalize_verification_payload({**valid, "kind": "review"}) is None
    assert normalize_verification_payload({**valid, "outcome": "unknown"}) is None
    assert normalize_verification_payload({**valid, "source": "model"}) is None


def test_direct_command_projection_uses_only_explicit_verifier_invocations() -> None:
    assert project_command_verification(
        {"command": "python", "args": ["-m", "pytest", "-q"]},
        passed=False,
    ) == {
        "verificationVersion": 1,
        "kind": "tests",
        "outcome": "failed",
        "source": "run_command_exit",
    }
    assert project_command_verification(
        {"command": "npm run lint"},
        passed=True,
    ) == {
        "verificationVersion": 1,
        "kind": "lint",
        "outcome": "passed",
        "source": "run_command_exit",
    }


def test_command_projection_rejects_shell_wrappers_background_and_deception() -> None:
    assert project_command_verification(
        {"command": "echo", "args": ["pytest", "-q"]},
        passed=True,
    ) is None
    assert project_command_verification(
        {"command": "pytest -q && curl https://example.invalid"},
        passed=True,
    ) is None
    assert project_command_verification(
        {"command": "pytest -q &"},
        passed=True,
    ) is None
    assert project_command_verification(
        {"command": "bash", "args": ["-lc", "pytest -q"]},
        passed=True,
    ) is None
    assert project_command_verification(
        {"command": "pytest", "args": "-q"},
        passed=True,
    ) is None
