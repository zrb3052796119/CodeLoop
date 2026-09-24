"""Offline multi-recovery linkage probe; no provider or memory API calls.

Run from repository root:
  .venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/three-state-review/multi_recovery_scope_probe.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location(
    "prior_validator_probe",
    ROOT / "artifacts/recovery-rule-review-2026-09-09/objective-contract-review/validator_episode_probe.py",
)
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)

from minicode.reflection_llm import ReflectionLLMConfig, parse_llm_candidate
from minicode.reflection_synthesis import (
    _passed_verifications_after_recovery,
    _verification_evidence_matches_recovery,
)


def evaluate(evidence, recovery_ids):
    claim = {
        "claim_type": "recovery", "semantic_key": "expiry_order",
        "statement": "After Expired token was accepted, Moved expiry validation before lookup.",
        "epistemic_status": "confirmed",
        "applies_when": "When checking authentication tokens.",
        "limitations": ["Only the cited targeted check passed."],
        "evidence_ids": ["auth-error", "recovery-event", "billing-recovery-event", "billing-error", "passed-check"],
        "verification_ids": ["verify-passed"],
        "related_error_ids": ["error-auth", "error-billing"],
        "related_recovery_ids": recovery_ids,
    }
    candidate = parse_llm_candidate(json.dumps({
        "task_summary": "Repair auth", "outcome": evidence.outcome, "claims": [claim],
    }), "Repair auth", evidence, ReflectionLLMConfig())
    return prior.admission(candidate, evidence)


def probe():
    evidence = prior.unrelated_evidence()
    billing_recovery = replace(
        evidence.recoveries[0], recovery_id="recovery-billing",
        related_error_ids=("error-billing",),
        action="Changed integer rounding before settlement",
        event_ids=("billing-recovery-event",), files_changed=("src/billing.py",),
    )
    evidence = replace(evidence,
        recoveries=[evidence.recoveries[0], billing_recovery],
        verification=[evidence.verification[0], replace(evidence.verification[1], summary="Invoice check passed")],
        event_positions={**evidence.event_positions, "billing-recovery-event": 6},
    )
    passed = evidence.verification[1]
    own_proofs = {}
    for recovery in evidence.recoveries:
        errors = [item for item in evidence.errors if item.error_id in recovery.related_error_ids]
        own_proofs[recovery.recovery_id] = (
            passed in _passed_verifications_after_recovery(recovery, evidence)
            and _verification_evidence_matches_recovery(passed, recovery, errors, evidence)
        )
    return {
        "timeline": {"auth_recovery": 3, "invoice_check_passed": 5, "billing_recovery": 6},
        "independently_linked_proofs": own_proofs,
        "auth_recovery_only": evaluate(evidence, ["recovery-auth"]),
        "billing_recovery_only": evaluate(evidence, ["recovery-billing"]),
        "both_recoveries": evaluate(evidence, ["recovery-auth", "recovery-billing"]),
    }


if __name__ == "__main__":
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
