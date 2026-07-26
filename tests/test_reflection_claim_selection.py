from __future__ import annotations

from minicode.reflection_claim_selection import (
    EvidenceChainKey,
    PersistableClaimEvaluation,
    build_evidence_chain_key,
    detect_rule_regression,
    suppress_redundant_llm_claims,
)
from minicode.reflection_evidence import (
    DecisionEvidence,
    ErrorEvidence,
    RecoveryEvidence,
    TaskEvidence,
    VerificationEvidence,
)
from minicode.reflection_synthesis import (
    ClaimValidationResult,
    ReflectionCandidate,
    ReflectionClaim,
    ReflectionValueDecision,
)


def _claim(
    claim_id: str,
    claim_type: str,
    *,
    status: str = "confirmed",
    evidence_ids: list[str] | None = None,
    error_ids: list[str] | None = None,
    recovery_ids: list[str] | None = None,
    verification_ids: list[str] | None = None,
) -> ReflectionClaim:
    return ReflectionClaim(
        claim_id=claim_id,
        claim_type=claim_type,  # type: ignore[arg-type]
        semantic_key=claim_id.replace("-", "_"),
        statement=f"Synthetic statement for {claim_id}.",
        evidence_ids=evidence_ids or [],
        epistemic_status=status,  # type: ignore[arg-type]
        applies_when="Synthetic condition.",
        limitations=[] if status == "confirmed" else ["Not verified."],
        related_error_ids=error_ids or [],
        related_recovery_ids=recovery_ids or [],
        verification_ids=verification_ids or [],
    )


def _evidence() -> TaskEvidence:
    return TaskEvidence(
        errors=[
            ErrorEvidence(
                "error-1",
                "call-1",
                "run_command",
                "LeaseError",
                "Lease renewal failed.",
                ("event-error-1",),
            ),
            ErrorEvidence(
                "error-2",
                "call-2",
                "run_command",
                "UploadError",
                "Upload failed.",
                ("event-error-2",),
            ),
        ],
        recoveries=[
            RecoveryEvidence(
                "recovery-1",
                ("error-1",),
                "Refresh lease token.",
                ("event-recovery-1",),
                (),
                "confirmed",
            ),
            RecoveryEvidence(
                "recovery-2",
                ("error-2",),
                "Retry upload.",
                ("event-recovery-2",),
                (),
                "confirmed",
            ),
        ],
        verification=[
            VerificationEvidence(
                "verify-1",
                "pytest",
                "call-3",
                "test",
                "targeted",
                "passed",
                ("event-verify-1",),
            ),
            VerificationEvidence(
                "verify-2",
                "pytest",
                "call-4",
                "test",
                "targeted",
                "passed",
                ("event-verify-2",),
            ),
        ],
        decisions=[
            DecisionEvidence(
                "decision-1",
                "The stale lease token caused renewal failure.",
                None,
                ("event-decision-1",),
                "confirmed",
                "assistant_decision",
            )
        ],
        outcome="success",
        had_errors=True,
        errors_recovered=True,
    )


def _chain_one_claims() -> tuple[ReflectionClaim, ReflectionClaim, ReflectionClaim]:
    error = _claim(
        "claim-error",
        "error_pattern",
        evidence_ids=["event-error-1"],
        error_ids=["error-1"],
    )
    recovery = _claim(
        "claim-recovery",
        "recovery",
        evidence_ids=["event-error-1", "event-recovery-1", "event-verify-1"],
        error_ids=["error-1"],
        recovery_ids=["recovery-1"],
        verification_ids=["verify-1"],
    )
    root = _claim(
        "claim-root",
        "root_cause",
        evidence_ids=[
            "event-error-1",
            "event-recovery-1",
            "event-verify-1",
            "event-decision-1",
        ],
        error_ids=["error-1"],
        recovery_ids=["recovery-1"],
        verification_ids=["verify-1"],
    )
    return error, recovery, root


def test_persistable_claims_are_exact_accepted_valid_intersection() -> None:
    valid = _claim("claim-valid", "constraint", evidence_ids=["event-decision-1"])
    other = _claim("claim-other", "decision", evidence_ids=["event-decision-1"])
    candidate = ReflectionCandidate("Synthetic", "success", [valid, other])
    validation = ClaimValidationResult(valid_claims=[valid], rejected_claims=[other])
    value = ReflectionValueDecision(
        accepted=True,
        reason_codes=["accepted_durable_reflection"],
        accepted_claim_ids=["claim-valid", "missing-id"],
    )

    result = PersistableClaimEvaluation.from_pipeline(
        candidate,
        validation,
        value,
        selection_source="llm_gap_fill",
        selection_reason="llm_filled_rule_gap",
    )

    assert [claim.claim_id for claim in result.value_accepted_claims] == [
        "claim-valid"
    ]
    assert [claim.claim_id for claim in result.persistable_claims] == [
        "claim-valid"
    ]
    assert [claim.claim_id for claim in result.rejected_claims] == ["claim-other"]


def test_value_accepted_with_empty_ids_has_no_persistable_claims() -> None:
    claim = _claim("claim-valid", "constraint", evidence_ids=["event-decision-1"])
    result = PersistableClaimEvaluation.from_pipeline(
        ReflectionCandidate("Synthetic", "success", [claim]),
        ClaimValidationResult(valid_claims=[claim]),
        ReflectionValueDecision(accepted=True, accepted_claim_ids=[]),
        selection_source="rule",
        selection_reason="rule_already_durable",
    )

    assert result.value_accepted_claims == []
    assert result.persistable_claims == []


def test_evidence_chain_key_is_deterministic_and_statement_independent() -> None:
    evidence = _evidence()
    first = _claim(
        "claim-a",
        "error_pattern",
        evidence_ids=["event-error-1"],
        error_ids=["error-1"],
    )
    second = _claim(
        "claim-b",
        "error_pattern",
        evidence_ids=["event-error-1"],
        error_ids=["error-1"],
    )
    second.statement = "Completely different synthetic wording."

    first_key = build_evidence_chain_key(first, evidence)
    second_key = build_evidence_chain_key(second, evidence)

    assert first_key == second_key
    assert isinstance(first_key, EvidenceChainKey)
    assert first_key.error_ids == ("error-1",)


def test_different_error_ids_are_distinct_chains() -> None:
    evidence = _evidence()
    first = _claim(
        "claim-a", "error_pattern", evidence_ids=["event-error-1"], error_ids=["error-1"]
    )
    second = _claim(
        "claim-b", "error_pattern", evidence_ids=["event-error-2"], error_ids=["error-2"]
    )

    result = suppress_redundant_llm_claims([first, second], evidence)

    assert [claim.claim_id for claim in result.kept_claims] == [
        "claim-a",
        "claim-b",
    ]
    assert result.suppressed_claims == []


def test_confirmed_root_cause_suppresses_weaker_same_chain_claims() -> None:
    error, recovery, root = _chain_one_claims()

    result = suppress_redundant_llm_claims(
        [error, recovery, root],
        _evidence(),
    )

    assert [claim.claim_id for claim in result.kept_claims] == ["claim-root"]
    assert {claim.claim_id for claim in result.suppressed_claims} == {
        "claim-error",
        "claim-recovery",
    }
    assert set(result.suppression_reason_codes.values()) == {
        "subsumed_by_confirmed_root_cause"
    }


def test_verified_recovery_suppresses_same_chain_error_pattern() -> None:
    error, recovery, _root = _chain_one_claims()

    result = suppress_redundant_llm_claims([error, recovery], _evidence())

    assert [claim.claim_id for claim in result.kept_claims] == [
        "claim-recovery"
    ]
    assert result.suppression_reason_codes == {
        "claim-error": "subsumed_by_verified_recovery"
    }


def test_inferred_recovery_does_not_suppress_error_pattern() -> None:
    error, recovery, _root = _chain_one_claims()
    recovery.epistemic_status = "inferred"
    recovery.verification_ids = []
    recovery.limitations = ["Not verified."]

    result = suppress_redundant_llm_claims([error, recovery], _evidence())

    assert [claim.claim_id for claim in result.kept_claims] == [
        "claim-error",
        "claim-recovery",
    ]


def test_invalid_stronger_candidate_cannot_suppress_valid_weaker_claim() -> None:
    error, _recovery, root = _chain_one_claims()
    candidate = ReflectionCandidate("Synthetic", "success", [error, root])
    validation = ClaimValidationResult(
        valid_claims=[error],
        rejected_claims=[root],
    )

    result = suppress_redundant_llm_claims(validation.valid_claims, _evidence())
    evaluation = PersistableClaimEvaluation.from_pipeline(
        candidate,
        validation,
        ReflectionValueDecision(
            accepted=True,
            accepted_claim_ids=["claim-error"],
        ),
        selection_source="llm_gap_fill",
        selection_reason="llm_filled_rule_gap",
        suppression=result,
    )

    assert [claim.claim_id for claim in result.kept_claims] == ["claim-error"]
    assert evaluation.suppressed_claims == []


def test_correction_and_constraint_are_not_suppressed() -> None:
    correction = _claim(
        "claim-correction",
        "correction",
        evidence_ids=["event-decision-1"],
    )
    constraint = _claim(
        "claim-constraint",
        "constraint",
        evidence_ids=["event-decision-1"],
    )
    decision = _claim(
        "claim-decision",
        "decision",
        evidence_ids=["event-decision-1"],
    )

    result = suppress_redundant_llm_claims(
        [correction, constraint, decision],
        _evidence(),
    )

    assert [claim.claim_id for claim in result.kept_claims] == [
        "claim-correction",
        "claim-constraint",
        "claim-decision",
    ]


def test_duplicate_error_pattern_same_chain_is_suppressed_deterministically() -> None:
    first = _claim(
        "claim-first",
        "error_pattern",
        evidence_ids=["event-error-1"],
        error_ids=["error-1"],
    )
    second = _claim(
        "claim-second",
        "error_pattern",
        evidence_ids=["event-error-1"],
        error_ids=["error-1"],
    )

    result = suppress_redundant_llm_claims([first, second], _evidence())

    assert [claim.claim_id for claim in result.kept_claims] == ["claim-first"]
    assert result.suppression_reason_codes == {
        "claim-second": "duplicate_error_pattern_same_chain"
    }


def test_rule_regression_detects_weaker_same_chain_replacement() -> None:
    error, recovery, _root = _chain_one_claims()

    assert detect_rule_regression([recovery], [error], _evidence()) is True
    assert detect_rule_regression([recovery], [recovery], _evidence()) is False


def test_rule_regression_accepts_same_decision_chain_with_different_key() -> None:
    rule = _claim(
        "rule-constraint",
        "constraint",
        evidence_ids=["event-decision-1"],
    )
    llm = _claim(
        "llm-constraint",
        "constraint",
        evidence_ids=["event-decision-1"],
    )

    assert detect_rule_regression([rule], [llm], _evidence()) is False


def test_selection_diagnostics_contain_ids_and_codes_but_no_claim_text() -> None:
    error, recovery, _root = _chain_one_claims()
    suppression = suppress_redundant_llm_claims([error, recovery], _evidence())
    evaluation = PersistableClaimEvaluation.from_pipeline(
        ReflectionCandidate("Synthetic", "success", [error, recovery]),
        ClaimValidationResult(valid_claims=[error, recovery]),
        ReflectionValueDecision(
            accepted=True,
            accepted_claim_ids=["claim-recovery"],
        ),
        selection_source="llm_gap_fill",
        selection_reason="llm_filled_rule_gap",
        suppression=suppression,
    )

    diagnostics = evaluation.to_diagnostics()
    serialized = str(diagnostics)
    assert diagnostics["suppressed_claim_ids"] == ["claim-error"]
    assert "Synthetic statement" not in serialized
