"""Offline review probes: no network, model call, or persistent memory writes.

Run from repository root:
  .venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/objective-contract-review/validator_episode_probe.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prior = load("prior_grounding", "artifacts/recovery-rule-review-2026-09-09/second-fix-review/grounding_probe.py")
fixtures = load("recovery_fixtures", "tests/test_recovery_lesson.py")

from minicode.reflection_evidence import ErrorEvidence, ToolEvidence, TraceEvidenceExtractor
from minicode.reflection_llm import ReflectionLLMConfig, parse_llm_candidate
from minicode.reflection_synthesis import ReflectionClaimValidator, ReflectionValueGate
from minicode.recovery_lesson import extract_recovery_episodes, episode_to_task_evidence


def candidate(evidence, *, related_errors=None, verification="verify-passed", verification_event="passed-check"):
    claim = {
        "claim_type": "recovery", "semantic_key": "expiry_order",
        "statement": "After Expired token was accepted, Moved expiry validation before lookup.",
        "epistemic_status": "confirmed",
        "applies_when": "When checking authentication tokens.",
        "limitations": ["Only the cited targeted check passed."],
        "evidence_ids": ["auth-error", "recovery-event", verification_event],
        "verification_ids": [verification],
        "related_error_ids": related_errors if related_errors is not None else ["error-auth"],
        "related_recovery_ids": ["recovery-auth"],
    }
    return parse_llm_candidate(json.dumps({"task_summary": "Repair auth", "outcome": evidence.outcome, "claims": [claim]}), "Repair auth", evidence, ReflectionLLMConfig())


def admission(candidate, evidence):
    validation = ReflectionClaimValidator().validate(candidate, evidence)
    value = ReflectionValueGate().evaluate(candidate, validation, evidence)
    return {"valid": bool(validation.valid_claims), "persistable": value.accepted,
            "issues": [issue.code for issue in validation.issues]}


def unrelated_evidence():
    evidence = prior.fixture("pytest tests/test_billing.py", "pytest tests/test_billing.py")
    auth = replace(evidence.errors[0], call_id="auth-failure")
    billing = ErrorEvidence("error-billing", "failed-call", "run_command", "AssertionError", "Invoice rounding was incorrect", ("billing-error",))
    return replace(evidence, errors=[auth, billing], tool_calls=[
        ToolEvidence("run_command", "auth-failure", "auth-invocation", ("auth-error",), "failed", "pytest tests/test_auth.py"),
        replace(evidence.tool_calls[0], result_event_ids=("billing-error", "failed-check")),
        evidence.tool_calls[1],
    ], event_positions={**evidence.event_positions, "auth-invocation": 0, "billing-error": 1})


def red_scope_claim_override():
    evidence = unrelated_evidence()
    return {
        "recovery_errors": evidence.recoveries[0].related_error_ids,
        "passed_invocation": evidence.tool_calls[-1].invocation,
        "auth_only_control": admission(candidate(evidence), evidence),
        "claim_adds_billing_error": admission(candidate(evidence, related_errors=["error-auth", "error-billing"]), evidence),
        "claim_omits_related_errors": admission(candidate(evidence, related_errors=[]), evidence),
        "claim_replaces_with_billing_error": admission(candidate(evidence, related_errors=["error-billing"]), evidence),
    }


def unbound_error_widens_red_scope():
    evidence = unrelated_evidence()
    evidence = replace(evidence, errors=[replace(evidence.errors[0], call_id=None), evidence.errors[1]])
    return {"auth_error_call_id": None, "actual_red_call_id": "failed-call", "admission": admission(candidate(evidence), evidence),
            "rule": prior.evaluate(evidence)}


def episode_ignores_rendered_cwd():
    trace = fixtures._timeout_retry_trace()
    trace[0]["input"]["cwd"] = "/repo/backend"
    trace[3]["input"]["cwd"] = "/repo/backend"
    noise = []
    for i in range(2):
        noise.extend(fixtures._call(30 + i * 3, f"noise{i}", "run_command", ok=True,
            tool_input={"command": "python", "args": ["scripts/report.py"], "cwd": f"/repo/other{i}", "timeout": 120},
            message="report created"))
    trace[3:3] = noise
    evidence = TraceEvidenceExtractor().extract(fixtures.TASK, trace)
    episodes = extract_recovery_episodes(evidence)
    return {"failed": episodes[0].failed_calls[0].invocation,
            "selected": [call.invocation for call in episodes[0].followup_calls],
            "real_retry": evidence.tool_calls[-1].invocation,
            "real_retry_retained": evidence.tool_calls[-1] in episodes[0].followup_calls,
            "causal_status": episodes[0].causal_status}


def projection_drops_fifth_green_invocation():
    evidence = prior.fixture("pytest tests/test_auth.py", "pytest tests/test_auth.py")
    checks, calls, positions = [evidence.verification[0]], [evidence.tool_calls[0]], dict(evidence.event_positions)
    for i in range(1, 6):
        checks.append(replace(evidence.verification[1], verification_id=f"verify-{i}", call_id=f"passed-{i}", event_ids=(f"check-{i}",)))
        calls.append(replace(evidence.tool_calls[1], call_id=f"passed-{i}", call_event_id=f"invoke-{i}", result_event_ids=(f"check-{i}",)))
        positions[f"invoke-{i}"], positions[f"check-{i}"] = 4 + 2*i, 5 + 2*i
    evidence = replace(evidence, verification=checks, tool_calls=calls, event_positions=positions)
    episode = extract_recovery_episodes(evidence)[0]
    sub = episode_to_task_evidence(episode, evidence)
    claim = candidate(evidence, verification="verify-5", verification_event="check-5")
    return {"parent": admission(claim, evidence), "episode": admission(claim, sub),
            "episode_verification_calls": [item.call_id for item in sub.verification],
            "episode_tool_calls": [item.call_id for item in sub.tool_calls]}


if __name__ == "__main__":
    print(json.dumps({
        "claim_controlled_red_scope": red_scope_claim_override(),
        "unbound_error_widens_red_scope": unbound_error_widens_red_scope(),
        "episode_cwd_displacement": episode_ignores_rendered_cwd(),
        "projection_loses_fifth_check_invocation": projection_drops_fifth_green_invocation(),
    }, ensure_ascii=False, indent=2))
