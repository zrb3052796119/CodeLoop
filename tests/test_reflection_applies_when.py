"""An applicability condition has to be checkable by a later run.

The generated form used to be ``When {tool} reports {error_type}.``, which
restates the observation rather than predicting anything: a memory carrying
``When web_search reports ToolError`` answers "does this apply to me?" with
"yes, if it applies". Naming the artifact and the underlying signal makes the
question answerable.
"""

from __future__ import annotations

import pytest

from minicode.reflection_evidence import ErrorEvidence, TaskEvidence
from minicode.reflection_synthesis import RuleReflectionSynthesizer


def _applies_when(
    message: str, *, tool: str | None = "run_command", error_type: str | None = "CommandError"
) -> str:
    error = ErrorEvidence("error-1", "call-1", tool, error_type, message, ("event-1",))
    return RuleReflectionSynthesizer()._error_applies_when(error)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "FAILED tests/test_lease.py::test_renew - StaleToken: not refreshed",
            "When run_command fails on tests/test_lease.py::test_renew with StaleToken.",
        ),
        (
            "mypy: src/proxy/pool.py:31 incompatible return type",
            "When run_command fails on src/proxy/pool.py with mypy: incompatible return type.",
        ),
        (
            "ModuleNotFoundError: No module named 'minicode'",
            "When run_command fails with ModuleNotFoundError.",
        ),
        (
            "the build failed",
            "When run_command fails with the build failed.",
        ),
    ],
)
def test_the_condition_names_the_artifact_and_the_signal(
    message: str, expected: str
) -> None:
    assert _applies_when(message) == expected


def test_a_tool_error_code_beats_the_generic_wrapper() -> None:
    """`ToolError` says nothing; the bracketed code is the real signal."""
    assert _applies_when(
        "error[not_found]: File does not exist. (src/config.py)",
        tool="read_file",
        error_type="ToolError",
    ) == "When read_file fails on src/config.py with not_found."


def test_a_specific_error_type_survives_when_the_message_has_no_signal() -> None:
    assert _applies_when("something went sideways", error_type="LeaseRenewalFault") == (
        "When run_command fails with LeaseRenewalFault."
    )


def test_no_condition_is_invented_when_nothing_is_nameable() -> None:
    assert _applies_when("", tool=None, error_type=None) == (
        "When the operation fails the same way."
    )


# ── Safety ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "/Users/example/secret/private.py:3: SyntaxError: bad token",
        "~/keys/id_rsa.pem could not be read",
        "C:/Users/zhou/work/secret.py failed",
    ],
)
def test_absolute_paths_never_reach_the_condition(message: str) -> None:
    """Only workspace-relative names travel to another machine or another user."""
    condition = _applies_when(message)

    assert "/Users/" not in condition
    assert "secret" not in condition
    assert "id_rsa" not in condition
    assert "C:/" not in condition


def test_a_redacted_secret_is_not_resurrected_as_an_artifact() -> None:
    condition = _applies_when("auth failed: api_key=sk-abcdefghijklmnopqrstuvwxyz012345")

    assert "sk-" not in condition


# ── Residue hygiene ─────────────────────────────────────────────────────


def test_artifacts_past_the_cap_are_stripped_from_the_residue() -> None:
    """Otherwise the leftovers read "with ... errors in and and src/c.py"."""
    condition = _applies_when(
        "ruff check .: 12 errors in src/a.py and src/b.py and src/c.py"
    )

    assert condition == "When run_command fails on src/a.py or src/b.py with ruff check .: 12 errors."
    assert " and and " not in condition
    assert "src/c.py" not in condition


def test_the_artifact_is_not_repeated_inside_the_signal() -> None:
    condition = _applies_when("old_string not found in minicode/memory.py", tool="edit_file")

    assert condition == "When edit_file fails on minicode/memory.py with old_string not found."
    assert condition.count("minicode/memory.py") == 1


def test_a_chinese_message_still_yields_a_usable_condition() -> None:
    condition = _applies_when("测试失败：tests/test_lease.py 里的租约续期用例没通过")

    assert condition.startswith("When run_command fails on tests/test_lease.py with ")
    assert "租约续期" in condition


# ── The claim actually carries it ───────────────────────────────────────


def test_a_synthesized_error_claim_carries_the_checkable_condition() -> None:
    """End to end: the improvement has to survive into the claim itself."""
    message = "FAILED tests/test_lease.py::test_renew - StaleToken: not refreshed"
    evidence = TaskEvidence(
        errors=[
            ErrorEvidence("error-1", "call-1", "run_command", "CommandError", message, ("event-1",)),
            ErrorEvidence("error-2", "call-2", "run_command", "CommandError", message, ("event-2",)),
        ],
        outcome="failed",
        had_errors=True,
    )

    candidate = RuleReflectionSynthesizer().synthesize("Run the suite", evidence)

    claims = [claim for claim in candidate.claims if claim.claim_type == "error_pattern"]
    assert claims
    assert all(
        claim.applies_when
        == "When run_command fails on tests/test_lease.py::test_renew with StaleToken."
        for claim in claims
    )
    # The defect being fixed: the condition must not merely restate the tool
    # and its generic wrapper.
    assert all("reports CommandError" not in claim.applies_when for claim in claims)
