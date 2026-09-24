"""Offline probes: no provider calls and no memory writes."""
from __future__ import annotations

import json
from dataclasses import replace

from minicode.reflection_evidence import (
    DecisionEvidence, ErrorEvidence, RecoveryEvidence, TaskEvidence,
    TraceEvidenceExtractor, VerificationEvidence,
)
from minicode.reflection_llm import (
    LLMReflectionSynthesizer, ReflectionLLMConfig, StructuredGenerationResponse,
    parse_llm_candidate,
)
from minicode.reflection_synthesis import (
    ReflectionClaimValidator, ReflectionValueGate, RuleReflectionSynthesizer,
)


def evaluate(evidence, claim):
    candidate = parse_llm_candidate(json.dumps({
        "task_summary": "Repair auth", "outcome": evidence.outcome, "claims": [claim],
    }), "Repair auth", evidence, ReflectionLLMConfig())
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    decision = ReflectionValueGate().evaluate(candidate, validation, evidence)
    return {
        "valid": bool(validation.valid_claims),
        "issues": [issue.code for issue in validation.issues],
        "persistable": decision.accepted,
        "durable_signals": list(decision.durable_signals),
    }


def inferred_status_bypass():
    trace = [
        {"event_id": "decision-event", "type": "assistant", "message":
         "The root cause is checking token expiry after account lookup."},
        {"event_id": "done", "type": "task_result", "status": "success"},
    ]
    evidence = TraceEvidenceExtractor().extract("Repair auth", trace)
    source = evidence.decisions[0]
    assert source.epistemic_status == "inferred"
    claim = {
        "claim_type": "decision", "semantic_key": "expiry_order",
        "statement": "Expiry validation after looking up an account is the identified defect.",
        "epistemic_status": "confirmed", "applies_when": "When checking authentication tokens.",
        "limitations": [], "evidence_ids": ["decision-event"], "verification_ids": [],
        "related_error_ids": [], "related_recovery_ids": [],
    }
    quoted = {**claim, "evidence_quotes": [source.statement]}
    class ScriptedClient:
        def generate_json(self, messages, *, timeout_seconds, max_output_tokens):
            return StructuredGenerationResponse(text=json.dumps({
                "task_summary": "Repair auth", "outcome": evidence.outcome,
                "claims": [quoted],
            }), input_tokens=1, output_tokens=1)
    from minicode.agent_reflection import ReflectionEngine
    config = ReflectionLLMConfig(mode="llm", selection_strategy="gap_fill")
    engine_result = ReflectionEngine(
        llm_config=config,
        llm_synthesizer=LLMReflectionSynthesizer(ScriptedClient(), config),
    ).reflect("Repair auth", trace)
    return {
        "source_status": source.epistemic_status,
        "without_quotes": evaluate(evidence, claim),
        "with_real_quote": evaluate(evidence, quoted),
        "verbatim_control": evaluate(evidence, {**claim, "statement": source.statement}),
        "existing_production_engine_scripted_client": {
            "selection_source": engine_result.selection_source,
            "claims": [c.to_dict() for c in engine_result.structured_claims],
        },
    }


def unrelated_quote_control():
    evidence = TaskEvidence(decisions=[
        DecisionEvidence("d1", "Keep token expiry validation before account lookup", None,
                         ("auth-event",), "confirmed"),
        DecisionEvidence("d2", "Retain immutable invoice ledger entries", None,
                         ("billing-event",), "confirmed"),
    ], outcome="success")
    claim = {
        "claim_type": "decision", "semantic_key": "expiry_order",
        "statement": "Validate token expiration before looking up the account.",
        "epistemic_status": "confirmed", "applies_when": "When checking authentication tokens.",
        "limitations": [], "evidence_ids": ["auth-event"], "verification_ids": [],
        "related_error_ids": [], "related_recovery_ids": [],
        "evidence_quotes": ["Retain immutable invoice ledger entries"],
    }
    return evaluate(evidence, claim)


def inferred_root_cause_bypass():
    error = ErrorEvidence("error-1", "failed-call", "run_command", "AssertionError",
                          "Expired token was accepted", ("error-event",))
    recovery = RecoveryEvidence("recovery-1", ("error-1",), "Moved expiry validation before lookup",
                                ("recovery-event",), ("src/auth.py",), "confirmed",
                                verification_call_ids=("passed-call",))
    verification = VerificationEvidence("verify-1", "run_command", "passed-call", "test", "targeted",
                                        "passed", ("verify-event",), "Auth expiry regression passed")
    source = DecisionEvidence("decision-1", "Expiry checking after lookup caused the authentication failure",
                              None, ("decision-event",), "inferred", "inferred_rationale")
    evidence = TaskEvidence(errors=[error], recoveries=[recovery], verification=[verification],
                            decisions=[source], outcome="success",
                            event_positions={"error-event": 0, "recovery-event": 1,
                                             "decision-event": 2, "verify-event": 3})
    claim = {
        "claim_type": "root_cause", "semantic_key": "expiry_order",
        "statement": "Account lookup preceding token expiration validation is the root cause of accepting an expired token.",
        "epistemic_status": "confirmed", "applies_when": "When checking authentication tokens.",
        "limitations": ["Only the targeted auth check passed."],
        "evidence_ids": ["error-event", "recovery-event", "decision-event", "verify-event"],
        "verification_ids": ["verify-1"], "related_error_ids": ["error-1"],
        "related_recovery_ids": ["recovery-1"], "evidence_quotes": [source.statement],
    }
    return {"source_status": source.epistemic_status, "candidate": evaluate(evidence, claim)}


def wrong_red_green_target():
    error = ErrorEvidence("error-1", "auth-failure", "run_command", "AssertionError",
                          "Expired token was accepted", ("auth-error",))
    recovery = RecoveryEvidence("recovery-1", ("error-1",), "Moved expiry validation before lookup",
                                ("recovery-event",), ("src/auth.py",), "confirmed")
    failed = VerificationEvidence("verify-auth", "run_command", "auth-failure", "test", "targeted",
                                  "failed", ("auth-failed-check",), "Auth expiry check failed")
    passed = VerificationEvidence("verify-billing", "run_command", "billing-success", "test", "targeted",
                                  "passed", ("billing-passed-check",), "Billing invoice check passed")
    evidence = TaskEvidence(errors=[error], recoveries=[recovery], verification=[failed, passed],
                            outcome="success", event_positions={"auth-error": 0, "auth-failed-check": 1,
                                                               "recovery-event": 2, "billing-passed-check": 3})
    claim = {
        "claim_type": "recovery", "semantic_key": "expiry_order",
        "statement": "After Expired token was accepted, Moved expiry validation before lookup.",
        "epistemic_status": "confirmed", "applies_when": "When checking authentication tokens.",
        "limitations": ["Only the targeted billing check passed."],
        "evidence_ids": ["auth-error", "recovery-event", "billing-passed-check"],
        "verification_ids": ["verify-billing"], "related_error_ids": ["error-1"],
        "related_recovery_ids": ["recovery-1"],
    }
    result = evaluate(evidence, claim)
    control = evaluate(replace(evidence, verification=[passed]), claim)
    candidate = RuleReflectionSynthesizer().synthesize("Repair auth", evidence)
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    return {"with_unrelated_earlier_failed_test": result, "without_failed_test": control,
            "rule_recovery_statuses": [c.epistemic_status for c in validation.valid_claims if c.claim_type == "recovery"]}


if __name__ == "__main__":
    print(json.dumps({"inferred_status_bypass": inferred_status_bypass(),
                      "inferred_root_cause_bypass": inferred_root_cause_bypass(),
                      "wrong_red_green_target": wrong_red_green_target(),
                      "unrelated_quote_control": unrelated_quote_control()}, indent=2, ensure_ascii=False))
