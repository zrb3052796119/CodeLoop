"""Offline second-fix review: validator-only, no provider or memory writes.

Run from repository root:
    PYTHONPATH=. python3 artifacts/recovery-rule-review-2026-09-09/second-fix-review/grounding_probe.py
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from minicode.reflection_evidence import (
    ErrorEvidence, RecoveryEvidence, TaskEvidence, ToolEvidence,
    VerificationEvidence,
)
from minicode.reflection_llm import ReflectionLLMConfig, parse_llm_candidate
from minicode.reflection_synthesis import (
    ReflectionClaimValidator, ReflectionValueGate, RuleReflectionSynthesizer,
    _verification_target_matches,
)


def evaluate(evidence: TaskEvidence) -> dict:
    claim = {
        "claim_type": "recovery", "semantic_key": "expiry_order",
        "statement": "After Expired token was accepted, Moved expiry validation before lookup.",
        "epistemic_status": "confirmed",
        "applies_when": "When checking authentication tokens.",
        "limitations": ["Only the cited targeted check passed."],
        "evidence_ids": ["auth-error", "recovery-event", "passed-check"],
        "verification_ids": ["verify-passed"],
        "related_error_ids": ["error-auth"],
        "related_recovery_ids": ["recovery-auth"],
    }
    candidate = parse_llm_candidate(json.dumps({
        "task_summary": "Repair auth", "outcome": evidence.outcome,
        "claims": [claim],
    }), "Repair auth", evidence, ReflectionLLMConfig())
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    value = ReflectionValueGate().evaluate(candidate, validation, evidence)
    rule_candidate = RuleReflectionSynthesizer().synthesize("Repair auth", evidence)
    rule_validation = ReflectionClaimValidator().validate(rule_candidate, evidence)
    return {
        "valid": bool(validation.valid_claims),
        "issues": [issue.code for issue in validation.issues],
        "persistable": value.accepted,
        "rule_recovery_statuses": [
            claim.epistemic_status for claim in rule_validation.valid_claims
            if claim.claim_type == "recovery"
        ],
    }


def fixture(failed_invocation: str, passed_invocation: str) -> TaskEvidence:
    return TaskEvidence(
        errors=[ErrorEvidence(
            "error-auth", "failed-call", "run_command", "AssertionError",
            "Expired token was accepted", ("auth-error",),
        )],
        recoveries=[RecoveryEvidence(
            "recovery-auth", ("error-auth",),
            "Moved expiry validation before lookup", ("recovery-event",),
            ("src/auth.py",), "confirmed",
        )],
        verification=[
            VerificationEvidence(
                "verify-failed", "run_command", "failed-call", "test",
                "targeted", "failed", ("failed-check",), "1 failed",
            ),
            VerificationEvidence(
                "verify-passed", "run_command", "passed-call", "test",
                "targeted", "passed", ("passed-check",), "1 passed",
            ),
        ],
        tool_calls=[
            ToolEvidence("run_command", "failed-call", "failed-invocation",
                         ("auth-error", "failed-check"), "failed", failed_invocation),
            ToolEvidence("run_command", "passed-call", "passed-invocation",
                         ("passed-check",), "success", passed_invocation),
        ],
        outcome="success",
        event_positions={
            "failed-invocation": 0, "auth-error": 1, "failed-check": 2,
            "recovery-event": 3, "passed-invocation": 4, "passed-check": 5,
        },
    )


def red_green_cases() -> list[dict]:
    pairs = [
        ("same_target_control", "pytest tests/test_auth.py (cwd: /repo/backend)",
         "pytest tests/test_auth.py (cwd: /repo/backend)", True),
        ("different_target_control", "pytest tests/test_auth.py", "pytest tests/test_billing.py", False),
        ("different_cwd", "pytest tests/test_auth.py (cwd: /repo/backend)",
         "pytest tests/test_auth.py (cwd: /repo/frontend)", False),
        ("different_cwd_absolute_relative_false_negative",
         "pytest /repo/backend/tests/test_auth.py",
         "pytest tests/test_auth.py (cwd: /repo/backend)", True),
        ("one_side_pathless_target", "pytest tests/test_auth.py", "pytest smoke", False),
        ("dropped_failed_file", "pytest tests/test_auth.py tests/test_smoke.py",
         "pytest tests/test_smoke.py", False),
        ("changed_node_selector", "pytest tests/test_auth.py::test_expired",
         "pytest tests/test_auth.py::test_valid", False),
    ]
    results = []
    for name, failed, passed, expected in pairs:
        evidence = fixture(failed, passed)
        results.append({
            "name": name, "failed_invocation": failed, "passed_invocation": passed,
            "expected_valid": expected,
            "identity_match": _verification_target_matches(failed, passed),
            "with_failed_check": evaluate(evidence),
            "without_failed_check_control": evaluate(replace(
                evidence, verification=evidence.verification[1:]
            )),
        })
    return results


def unrelated_failed_check() -> dict:
    """The red check itself belongs to a different error, yet confirms auth."""
    evidence = fixture("pytest tests/test_billing.py", "pytest tests/test_billing.py")
    auth_error = replace(evidence.errors[0], call_id="auth-failure")
    billing_error = ErrorEvidence(
        "error-billing", "failed-call", "run_command", "AssertionError",
        "Invoice rounding was incorrect", ("billing-error",),
    )
    evidence = replace(
        evidence,
        errors=[auth_error, billing_error],
        tool_calls=[
            ToolEvidence("run_command", "auth-failure", "auth-invocation",
                         ("auth-error",), "failed", "pytest tests/test_auth.py"),
            replace(evidence.tool_calls[0], result_event_ids=("billing-error", "failed-check")),
            evidence.tool_calls[1],
        ],
        event_positions={**evidence.event_positions, "auth-invocation": -1,
                         "billing-error": 1},
    )
    return {
        "recovery_related_error_calls": [auth_error.call_id],
        "red_check_call": evidence.verification[0].call_id,
        "expected_valid": False,
        "with_unrelated_red_green": evaluate(evidence),
        "without_failed_check_control": evaluate(replace(
            evidence, verification=evidence.verification[1:]
        )),
    }


if __name__ == "__main__":
    report = {"red_green_target_identity": red_green_cases(),
              "unrelated_failed_check": unrelated_failed_check()}
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    Path(__file__).with_suffix(".json").write_text(serialized)
    print(serialized)
