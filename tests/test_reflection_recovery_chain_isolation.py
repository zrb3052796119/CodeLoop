"""A confirmed claim must have a complete proof within one recovery chain."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from minicode.reflection_evidence import (
    ErrorEvidence,
    RecoveryEvidence,
    TaskEvidence,
    ToolEvidence,
    VerificationEvidence,
)
from minicode.reflection_llm import ReflectionLLMConfig, parse_llm_candidate
from minicode.reflection_synthesis import (
    ClaimValidationResult,
    ReflectionClaimValidator,
    ReflectionValueDecision,
    ReflectionValueGate,
)


def _separate_recovery_chains() -> TaskEvidence:
    """The invoice check is unrelated to auth and precedes the invoice fix."""
    return TaskEvidence(
        errors=[
            ErrorEvidence(
                "error-auth", "auth-failed", "run_command", "AssertionError",
                "Expired token was accepted", ("auth-error",),
            ),
            ErrorEvidence(
                "error-billing", "billing-failed", "run_command", "AssertionError",
                "Invoice rounding was incorrect", ("billing-error",),
            ),
        ],
        recoveries=[
            RecoveryEvidence(
                "recovery-auth", ("error-auth",),
                "Moved expiry validation before lookup", ("auth-recovery",),
                ("src/auth.py",), "confirmed",
            ),
            RecoveryEvidence(
                "recovery-billing", ("error-billing",),
                "Changed integer rounding before settlement", ("billing-recovery",),
                ("src/billing.py",), "confirmed",
            ),
        ],
        verification=[
            VerificationEvidence(
                "verify-failed", "run_command", "billing-failed", "test",
                "targeted", "failed", ("failed-check",), "1 failed",
            ),
            VerificationEvidence(
                "verify-passed", "run_command", "billing-passed", "test",
                "targeted", "passed", ("passed-check",), "Invoice check passed",
            ),
        ],
        tool_calls=[
            ToolEvidence(
                "run_command", "auth-failed", "auth-invocation", ("auth-error",),
                "failed", "pytest tests/test_auth.py",
            ),
            ToolEvidence(
                "run_command", "billing-failed", "billing-failed-invocation",
                ("billing-error", "failed-check"), "failed",
                "pytest tests/test_billing.py",
            ),
            ToolEvidence(
                "run_command", "billing-passed", "billing-passed-invocation",
                ("passed-check",), "success", "pytest tests/test_billing.py",
            ),
        ],
        outcome="success",
        event_positions={
            "auth-invocation": 0,
            "auth-error": 1,
            "billing-failed-invocation": 2,
            "billing-error": 3,
            "failed-check": 4,
            "auth-recovery": 5,
            "billing-passed-invocation": 6,
            "passed-check": 7,
            "billing-recovery": 8,
        },
    )


def _admission(
    evidence: TaskEvidence,
    recovery_ids: list[str],
    *,
    error_ids: list[str] | None = None,
) -> tuple[ClaimValidationResult, ReflectionValueDecision]:
    claim = {
        "claim_type": "recovery",
        "semantic_key": "expiry_and_rounding",
        "statement": (
            "After Expired token was accepted, Moved expiry validation before lookup. "
            "After Invoice rounding was incorrect, Changed integer rounding before settlement."
        ),
        "epistemic_status": "confirmed",
        "applies_when": "When validating token expiry or invoice rounding.",
        "limitations": ["Only the cited targeted check passed."],
        "evidence_ids": [
            "auth-error", "billing-error", "auth-recovery", "billing-recovery", "passed-check",
        ],
        "verification_ids": ["verify-passed"],
        "related_error_ids": (
            ["error-auth", "error-billing"] if error_ids is None else error_ids
        ),
        "related_recovery_ids": recovery_ids,
    }
    candidate = parse_llm_candidate(
        json.dumps({
            "task_summary": "Repair expiry and invoice regressions",
            "outcome": evidence.outcome,
            "claims": [claim],
        }),
        "Repair expiry and invoice regressions",
        evidence,
        ReflectionLLMConfig(),
    )
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    return validation, ReflectionValueGate().evaluate(candidate, validation, evidence)


@pytest.mark.parametrize(
    "recovery_ids",
    [
        ["recovery-auth"],
        ["recovery-billing"],
        ["recovery-auth", "recovery-billing"],
        ["recovery-billing", "recovery-auth"],
    ],
)
def test_errors_and_temporal_boundaries_cannot_be_borrowed_across_recoveries(
    recovery_ids: list[str],
) -> None:
    validation, value = _admission(_separate_recovery_chains(), recovery_ids)

    assert validation.valid_claims == []
    assert [issue.code for issue in validation.issues] == [
        "confirmed_recovery_verification_unrelated"
    ]
    assert value.accepted is False


@pytest.mark.parametrize(
    "recovery_ids",
    [
        ["recovery-billing"],
        ["recovery-auth", "recovery-billing"],
        ["recovery-billing", "recovery-auth"],
    ],
)
def test_a_real_complete_chain_remains_admissible(
    recovery_ids: list[str],
) -> None:
    evidence = _separate_recovery_chains()
    evidence = replace(evidence, event_positions={
        **evidence.event_positions,
        "billing-passed-invocation": 9,
        "passed-check": 10,
    })
    validation, value = _admission(evidence, recovery_ids)

    assert len(validation.valid_claims) == 1
    assert validation.issues == []
    assert value.accepted is True


@pytest.mark.parametrize(
    "error_ids",
    [[], ["error-auth"], ["error-billing"], ["error-auth", "error-billing"]],
)
def test_candidate_error_references_cannot_widen_the_authoritative_chain(
    error_ids: list[str],
) -> None:
    validation, value = _admission(
        _separate_recovery_chains(), ["recovery-auth"], error_ids=error_ids,
    )

    assert validation.valid_claims == []
    assert value.accepted is False


def test_unbound_error_cannot_borrow_an_unrelated_red_green_pair() -> None:
    evidence = _separate_recovery_chains()
    evidence = replace(evidence, errors=[
        replace(evidence.errors[0], call_id=None), evidence.errors[1],
    ])
    validation, value = _admission(evidence, ["recovery-auth"])

    assert validation.valid_claims == []
    assert value.accepted is False
