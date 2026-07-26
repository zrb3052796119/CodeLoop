"""Deterministic persistable-claim selection and conservative arbitration helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from minicode.reflection_evidence import TaskEvidence
from minicode.reflection_synthesis import (
    ClaimValidationResult,
    ReflectionCandidate,
    ReflectionClaim,
    ReflectionValueDecision,
)


@dataclass(frozen=True, order=True)
class EvidenceChainKey:
    """Non-text identity material for one structured evidence chain."""

    error_ids: tuple[str, ...] = ()
    recovery_ids: tuple[str, ...] = ()
    verification_ids: tuple[str, ...] = ()
    decision_event_ids: tuple[str, ...] = ()
    source_event_ids: tuple[str, ...] = ()


@dataclass
class ClaimSuppressionResult:
    kept_claims: list[ReflectionClaim] = field(default_factory=list)
    suppressed_claims: list[ReflectionClaim] = field(default_factory=list)
    suppression_reason_codes: dict[str, str] = field(default_factory=dict)


@dataclass
class PersistableClaimEvaluation:
    """Four-stage claim view plus final branch-selection diagnostics."""

    candidate_claims: list[ReflectionClaim] = field(default_factory=list)
    valid_claims: list[ReflectionClaim] = field(default_factory=list)
    value_accepted_claims: list[ReflectionClaim] = field(default_factory=list)
    persistable_claims: list[ReflectionClaim] = field(default_factory=list)
    rejected_claims: list[ReflectionClaim] = field(default_factory=list)
    suppressed_claims: list[ReflectionClaim] = field(default_factory=list)
    selection_source: str = "rule"
    selection_reason: str = "rule_mode"
    rule_regression: bool = False
    gap_fill_success: bool = False
    suppression_reason_codes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_pipeline(
        cls,
        candidate: ReflectionCandidate,
        validation: ClaimValidationResult,
        value_decision: ReflectionValueDecision,
        *,
        selection_source: str,
        selection_reason: str,
        suppression: ClaimSuppressionResult | None = None,
        rule_regression: bool = False,
        gap_fill_success: bool = False,
    ) -> PersistableClaimEvaluation:
        accepted_ids = set(value_decision.accepted_claim_ids)
        value_accepted = [
            claim
            for claim in validation.valid_claims
            if value_decision.accepted and claim.claim_id in accepted_ids
        ]
        suppressed = list(suppression.suppressed_claims) if suppression else []
        suppressed_ids = {claim.claim_id for claim in suppressed}
        return cls(
            candidate_claims=list(candidate.claims),
            valid_claims=list(validation.valid_claims),
            value_accepted_claims=value_accepted,
            persistable_claims=[
                claim
                for claim in value_accepted
                if claim.claim_id not in suppressed_ids
            ],
            rejected_claims=list(validation.rejected_claims),
            suppressed_claims=suppressed,
            selection_source=selection_source,
            selection_reason=selection_reason,
            rule_regression=rule_regression,
            gap_fill_success=gap_fill_success,
            suppression_reason_codes=(
                dict(suppression.suppression_reason_codes)
                if suppression
                else {}
            ),
        )

    def to_diagnostics(self) -> dict[str, Any]:
        return {
            "candidate_claim_ids": [claim.claim_id for claim in self.candidate_claims],
            "valid_claim_ids": [claim.claim_id for claim in self.valid_claims],
            "value_accepted_claim_ids": [
                claim.claim_id for claim in self.value_accepted_claims
            ],
            "persistable_claim_ids": [
                claim.claim_id for claim in self.persistable_claims
            ],
            "rejected_claim_ids": [claim.claim_id for claim in self.rejected_claims],
            "suppressed_claim_ids": [
                claim.claim_id for claim in self.suppressed_claims
            ],
            "selection_source": self.selection_source,
            "selection_reason": self.selection_reason,
            "rule_regression": self.rule_regression,
            "gap_fill_success": self.gap_fill_success,
            "suppression_reason_codes": dict(self.suppression_reason_codes),
        }


def build_evidence_chain_key(
    claim: ReflectionClaim,
    evidence: TaskEvidence,
) -> EvidenceChainKey:
    """Build a stable key without statement or semantic-key material."""
    evidence_ids = set(claim.evidence_ids)
    error_ids = set(claim.related_error_ids)
    recovery_ids = set(claim.related_recovery_ids)
    verification_ids = set(claim.verification_ids)

    for item in evidence.errors:
        if evidence_ids.intersection(item.source_event_ids):
            error_ids.add(item.error_id)
    for item in evidence.recoveries:
        if evidence_ids.intersection(item.event_ids) or item.recovery_id in recovery_ids:
            recovery_ids.add(item.recovery_id)
            error_ids.update(item.related_error_ids)
    for item in evidence.verification:
        if evidence_ids.intersection(item.event_ids):
            verification_ids.add(item.verification_id)

    decision_event_ids = {
        event_id
        for item in evidence.decisions
        for event_id in item.event_ids
        if event_id in evidence_ids
    }
    return EvidenceChainKey(
        error_ids=tuple(sorted(error_ids)),
        recovery_ids=tuple(sorted(recovery_ids)),
        verification_ids=tuple(sorted(verification_ids)),
        decision_event_ids=tuple(sorted(decision_event_ids)),
        source_event_ids=tuple(sorted(evidence_ids)),
    )


def _same_evidence_chain(left: EvidenceChainKey, right: EvidenceChainKey) -> bool:
    if left.error_ids and right.error_ids:
        return bool(set(left.error_ids).intersection(right.error_ids))
    if left.recovery_ids and right.recovery_ids:
        return bool(set(left.recovery_ids).intersection(right.recovery_ids))
    if left.verification_ids and right.verification_ids:
        return bool(set(left.verification_ids).intersection(right.verification_ids))
    return False


def _claim_priority(claim: ReflectionClaim, evidence: TaskEvidence) -> int:
    key = build_evidence_chain_key(claim, evidence)
    passed_verification_ids = {
        item.verification_id
        for item in evidence.verification
        if item.result == "passed"
    }
    has_passed_verification = bool(
        set(key.verification_ids).intersection(passed_verification_ids)
    )
    if (
        claim.claim_type == "root_cause"
        and claim.epistemic_status == "confirmed"
        and key.error_ids
        and key.recovery_ids
        and has_passed_verification
        and key.decision_event_ids
    ):
        return 4
    if (
        claim.claim_type == "recovery"
        and claim.epistemic_status == "confirmed"
        and key.error_ids
        and key.recovery_ids
        and has_passed_verification
    ):
        return 3
    if claim.claim_type == "error_pattern":
        return 2
    if claim.claim_type == "warning":
        return 1
    return 0


def suppress_redundant_llm_claims(
    valid_claims: list[ReflectionClaim],
    evidence: TaskEvidence,
) -> ClaimSuppressionResult:
    """Suppress only deterministic redundancy among Validator-valid LLM claims."""
    claims = list(valid_claims)
    keys = {
        claim.claim_id: build_evidence_chain_key(claim, evidence)
        for claim in claims
    }
    priorities = {
        claim.claim_id: _claim_priority(claim, evidence)
        for claim in claims
    }
    suppressed: dict[str, str] = {}

    ordered = sorted(
        enumerate(claims),
        key=lambda item: (-priorities[item[1].claim_id], item[0]),
    )
    for _index, stronger in ordered:
        if stronger.claim_id in suppressed:
            continue
        stronger_priority = priorities[stronger.claim_id]
        for weaker in claims:
            if weaker.claim_id == stronger.claim_id or weaker.claim_id in suppressed:
                continue
            if not _same_evidence_chain(
                keys[stronger.claim_id], keys[weaker.claim_id]
            ):
                continue
            weaker_priority = priorities[weaker.claim_id]
            reason: str | None = None
            if stronger_priority == 4 and weaker.claim_type in {
                "recovery",
                "error_pattern",
                "warning",
            }:
                reason = "subsumed_by_confirmed_root_cause"
            elif stronger_priority == 3 and weaker.claim_type in {
                "error_pattern",
                "warning",
            }:
                reason = "subsumed_by_verified_recovery"
            elif (
                stronger.claim_type == weaker.claim_type == "error_pattern"
                and stronger_priority == weaker_priority
            ):
                reason = "duplicate_error_pattern_same_chain"
            elif (
                stronger.claim_type == weaker.claim_type == "warning"
                and stronger_priority == weaker_priority
            ):
                reason = "duplicate_warning_same_chain"
            if reason:
                suppressed[weaker.claim_id] = reason

    return ClaimSuppressionResult(
        kept_claims=[claim for claim in claims if claim.claim_id not in suppressed],
        suppressed_claims=[claim for claim in claims if claim.claim_id in suppressed],
        suppression_reason_codes=suppressed,
    )


def _claim_covers(
    expected: ReflectionClaim,
    actual: ReflectionClaim,
    evidence: TaskEvidence,
) -> bool:
    if expected.semantic_key == actual.semantic_key:
        return True
    expected_key = build_evidence_chain_key(expected, evidence)
    actual_key = build_evidence_chain_key(actual, evidence)
    if (
        expected.claim_type == actual.claim_type
        and expected_key.decision_event_ids
        and set(expected_key.decision_event_ids).intersection(
            actual_key.decision_event_ids
        )
    ):
        return True
    if not _same_evidence_chain(expected_key, actual_key):
        return False
    if expected.claim_type == actual.claim_type:
        return True
    return _claim_priority(actual, evidence) >= _claim_priority(expected, evidence)


def detect_rule_regression(
    rule_persistable_claims: list[ReflectionClaim],
    llm_persistable_claims: list[ReflectionClaim],
    evidence: TaskEvidence,
) -> bool:
    """Flag deterministic loss of an already durable Rule claim."""
    return any(
        not any(
            _claim_covers(rule_claim, llm_claim, evidence)
            for llm_claim in llm_persistable_claims
        )
        for rule_claim in rule_persistable_claims
    )


__all__ = [
    "ClaimSuppressionResult",
    "EvidenceChainKey",
    "PersistableClaimEvaluation",
    "build_evidence_chain_key",
    "detect_rule_regression",
    "suppress_redundant_llm_claims",
]
