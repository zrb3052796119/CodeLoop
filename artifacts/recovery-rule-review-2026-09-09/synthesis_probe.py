"""Offline probes of the current recovery claim validator.

No provider/model calls and no MemoryManager/persistence API are used.
All evidence is synthetic and evaluated only in memory.
"""

from __future__ import annotations

import json
from pathlib import Path

from minicode.reflection_evidence import (
    ErrorEvidence,
    RecoveryEvidence,
    TaskEvidence,
    VerificationEvidence,
)
from minicode.reflection_synthesis import (
    ReflectionCandidate,
    ReflectionClaim,
    ReflectionClaimValidator,
    ReflectionValueGate,
    RuleReflectionSynthesizer,
)


def evidence(*, linked_verification: bool = True) -> TaskEvidence:
    error = ErrorEvidence(
        "error-1", "call-error", "run_command", "AssertionError",
        "Expired token was accepted", ("event-error",),
    )
    recovery = RecoveryEvidence(
        "recovery-1", ("error-1",), "Moved the expiry check before lookup",
        ("event-recovery",), ("src/auth.py",), "confirmed",
        verification_call_ids=("call-verify",) if linked_verification else (),
    )
    verification = VerificationEvidence(
        "verify-1", "run_command", "call-verify", "test", "targeted", "passed",
        ("event-verify",),
        "Auth expiry regression passed" if linked_verification else "logging format checks passed",
    )
    return TaskEvidence(
        errors=[error], recoveries=[recovery], verification=[verification],
        outcome="success",
        event_positions={"event-error": 0, "event-recovery": 1, "event-verify": 2},
    )


def evaluate(statement: str, source: TaskEvidence) -> dict:
    claim = ReflectionClaim(
        "llm-claim-1", "recovery", "expiry_before_lookup", statement,
        ["event-error", "event-recovery", "event-verify"], "confirmed",
        applies_when="When validating an authentication token before a lookup.",
        limitations=["Only targeted checks passed."],
        verification_ids=["verify-1"], related_error_ids=["error-1"],
        related_recovery_ids=["recovery-1"],
    )
    candidate = ReflectionCandidate("Repair auth", "success", [claim])
    validation = ReflectionClaimValidator().validate(candidate, source)
    value = ReflectionValueGate().evaluate(candidate, validation, source)
    return {
        "statement": statement,
        "valid": bool(validation.valid_claims),
        "validation_issue_codes": [item.code for item in validation.issues],
        "durable": value.accepted,
        "value_reason_codes": value.reason_codes,
    }


def main() -> None:
    verbatim = (
        "After Expired token was accepted, the recovery was: "
        "Moved the expiry check before lookup."
    )
    linked = evidence()
    unrelated = evidence(linked_verification=False)
    results = {
        "scope": "synthetic offline candidate validation only; no model or memory writes",
        "evidence": linked.to_dict(),
        "cases": {
            "verbatim_baseline": evaluate(verbatim, linked),
            "semantic_paraphrase": evaluate(
                "The lookup accepted an expired token; moving expiration validation "
                "ahead of the lookup was the verified repair.",
                linked,
            ),
            "verbatim_plus_unsupported_conclusion": evaluate(
                verbatim + " The same change also resolves every clock synchronization "
                "defect in distributed databases.",
                linked,
            ),
            "verbatim_with_unrelated_verification": evaluate(verbatim, unrelated),
        },
        "rule_claims_for_unrelated_verification": [
            {"claim_type": item.claim_type, "epistemic_status": item.epistemic_status,
             "verification_ids": item.verification_ids}
            for item in RuleReflectionSynthesizer().synthesize("Repair auth", unrelated).claims
        ],
    }
    output = Path(__file__).with_name("synthesis_probe.json")
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(output), "cases": results["cases"],
                      "rule_claims_for_unrelated_verification": results["rule_claims_for_unrelated_verification"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
