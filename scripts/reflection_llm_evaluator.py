"""Offline holdout evaluation for optional LLM reflection synthesis.

The evaluator never accesses a network model. Each holdout case contains a
manually authored, deterministic provider response so parser, validation,
value, fallback, and shadow semantics can be compared reproducibly.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minicode.memory import assess_memory_safety
from minicode.reflection_evidence import TraceEvidenceExtractor, sanitize_evidence_text
from minicode.reflection_llm import (
    LLMReflectionSynthesizer,
    ReflectionLLMConfig,
    ReflectionLLMEligibilityGate,
    StructuredGenerationResponse,
)
from minicode.reflection_synthesis import (
    ClaimValidationResult,
    ReflectionCandidate,
    ReflectionClaim,
    ReflectionClaimValidator,
    ReflectionValueDecision,
    ReflectionValueGate,
    RuleReflectionSynthesizer,
)
from scripts.reflection_evaluator import (
    DatasetValidationError,
    _forbidden_claim_count,
    _match_structured_claims,
)


_REQUIRED_CASE_FIELDS = {
    "case_id",
    "category",
    "task_description",
    "trace",
    "expected_claims",
    "forbidden_claims",
    "should_write_memory",
    "llm_script",
    "notes",
}
_SCRIPT_KINDS = {
    "candidate",
    "empty",
    "invalid_reference",
    "malformed",
    "provider_error",
    "timeout",
    "tool_call",
}
_INVALID_REFERENCE_CODES = {
    "invalid_evidence_reference",
    "invalid_verification_reference",
    "invalid_error_reference",
    "invalid_recovery_reference",
}


def _fail(path: Path, message: str) -> None:
    raise DatasetValidationError(f"{path}: {message}")


def load_holdout_dataset(root: str | Path) -> list[dict[str, Any]]:
    """Load the independent LLM holdout and enforce its manual label schema."""
    dataset_root = Path(root)
    cases_dir = dataset_root / "cases"
    if not cases_dir.is_dir():
        _fail(dataset_root, "missing cases directory")
    loaded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(cases_dir.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _fail(path, f"cannot parse JSON: {exc}")
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            _fail(path, "unsupported document schema")
        cases = document.get("cases")
        if not isinstance(cases, list):
            _fail(path, "cases must be a list")
        for case in cases:
            if not isinstance(case, dict):
                _fail(path, "case must be an object")
            missing = sorted(_REQUIRED_CASE_FIELDS - set(case))
            if missing:
                _fail(path, f"case missing fields: {missing}")
            case_id = case["case_id"]
            if not isinstance(case_id, str) or not case_id or case_id in seen:
                _fail(path, f"invalid or duplicate case_id: {case_id!r}")
            seen.add(case_id)
            if not isinstance(case["trace"], list):
                _fail(path, f"{case_id} trace must be a list")
            if not isinstance(case["expected_claims"], list):
                _fail(path, f"{case_id} expected_claims must be a list")
            if not isinstance(case["forbidden_claims"], list):
                _fail(path, f"{case_id} forbidden_claims must be a list")
            if not isinstance(case["should_write_memory"], bool):
                _fail(path, f"{case_id} should_write_memory must be boolean")
            script = case["llm_script"]
            if not isinstance(script, dict) or script.get("kind") not in _SCRIPT_KINDS:
                _fail(path, f"{case_id} has invalid llm_script")
            if script["kind"] == "candidate" and not isinstance(script.get("claims"), list):
                _fail(path, f"{case_id} candidate script requires claims")
            loaded.append(case)
    return sorted(loaded, key=lambda item: item["case_id"])


class ScriptedHoldoutClient:
    """One-response, deterministic structured client used only by evaluation."""

    def __init__(self, case: dict[str, Any], outcome: str) -> None:
        self._case = case
        self._outcome = outcome
        self.calls = 0

    def generate_json(
        self,
        messages: list[dict[str, str]],
        *,
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> StructuredGenerationResponse:
        del messages, timeout_seconds, max_output_tokens
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("holdout client called more than once")
        script = self._case["llm_script"]
        kind = script["kind"]
        if kind == "timeout":
            raise TimeoutError("scripted timeout")
        if kind == "provider_error":
            raise RuntimeError("scripted provider error")
        if kind == "empty":
            text = ""
        elif kind == "malformed":
            text = '{"claims":'
        else:
            text = json.dumps(
                {
                    "task_summary": sanitize_evidence_text(
                        self._case["task_description"], 200
                    ),
                    "outcome": self._outcome,
                    "claims": script.get("claims", []),
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        tool_calls = [{"name": "read_file"}] if kind == "tool_call" else []
        return StructuredGenerationResponse(
            text=text,
            tool_calls=tool_calls,
            input_tokens=int(script.get("input_tokens", 320)),
            output_tokens=int(script.get("output_tokens", max(1, len(text) // 4))),
            estimated_cost_usd=float(script.get("estimated_cost_usd", 0.0002)),
            latency_ms=float(script.get("latency_ms", 4.0)),
        )


@dataclass
class _PipelineStage:
    candidate: ReflectionCandidate
    validation: ClaimValidationResult
    value: ReflectionValueDecision

    def claims_for(self, stage: str) -> list[ReflectionClaim]:
        if stage == "candidate":
            return list(self.candidate.claims)
        if stage == "validator":
            return list(self.validation.valid_claims)
        accepted_ids = set(self.value.accepted_claim_ids)
        claims = [
            claim
            for claim in self.validation.valid_claims
            if self.value.accepted and claim.claim_id in accepted_ids
        ]
        if stage == "value":
            return claims
        if stage == "persistable":
            return [
                claim
                for claim in claims
                if assess_memory_safety(
                    claim.statement, source="reflection_llm_holdout"
                ).allowed
            ]
        raise ValueError(f"unknown stage: {stage}")


def _claim_dicts(claims: list[ReflectionClaim]) -> list[dict[str, Any]]:
    return [claim.to_dict() for claim in claims]


def _branch_metrics(
    case: dict[str, Any],
    pipeline: _PipelineStage,
) -> dict[str, Any]:
    stages: dict[str, Any] = {}
    match_sets: dict[str, list[int]] = {}
    for stage in ("candidate", "validator", "value", "persistable"):
        actual = _claim_dicts(pipeline.claims_for(stage))
        metrics, matched_expected, matched_actual, semantic_mismatches = (
            _match_structured_claims(case["expected_claims"], actual)
        )
        stages[stage] = {
            **metrics,
            "semantic_key_mismatches": semantic_mismatches,
            "actual_claim_count": len(actual),
        }
        match_sets[stage] = sorted(matched_expected)
    issue_codes = [issue.code for issue in pipeline.validation.issues]
    final_actual = _claim_dicts(pipeline.claims_for("persistable"))
    _, final_matches, _, _ = _match_structured_claims(
        case["expected_claims"], final_actual
    )
    expected_root_cause = {
        index
        for index, claim in enumerate(case["expected_claims"])
        if claim.get("claim_type") == "root_cause"
    }
    actual_root_count = sum(
        claim.claim_type == "root_cause"
        for claim in pipeline.claims_for("persistable")
    )
    candidate_root_count = sum(
        claim.claim_type == "root_cause" for claim in pipeline.candidate.claims
    )
    unsupported_codes = {
        "claim_type_evidence_mismatch",
        "claim_statement_not_grounded",
        "constraint_not_stable",
        "dependency_not_confirmed",
        "correction_not_explicit",
        "verification_rule_not_stable",
    }
    accepted_claim_ids = {claim.claim_id for claim in pipeline.claims_for("persistable")}
    unsupported_accepted = sum(
        issue.code in unsupported_codes and issue.claim_id in accepted_claim_ids
        for issue in pipeline.validation.issues
    )
    return {
        "stages": stages,
        "matched_expected": match_sets,
        "value_accepted": pipeline.value.accepted,
        "should_write_memory": case["should_write_memory"],
        "value_true_positive": pipeline.value.accepted and case["should_write_memory"],
        "value_false_positive": pipeline.value.accepted and not case["should_write_memory"],
        "value_false_negative": not pipeline.value.accepted and case["should_write_memory"],
        "value_true_negative": not pipeline.value.accepted and not case["should_write_memory"],
        "invalid_evidence_references": sum(
            code in _INVALID_REFERENCE_CODES for code in issue_codes
        ),
        "epistemic_mismatches": issue_codes.count("epistemic_status_overclaim"),
        "duplicate_semantic_keys": sum(
            code in {"duplicate_semantic_key_merged", "conflicting_semantic_key"}
            for code in issue_codes
        ),
        "root_cause_candidate_overclaim": max(
            0, candidate_root_count - len(expected_root_cause)
        ),
        "root_cause_overclaim": max(
            0, actual_root_count - len(expected_root_cause & final_matches)
        ),
        "validation_issue_codes": issue_codes,
        "supported_accepted_claims": stages["persistable"]["true_positives"],
        "unexpected_accepted_claims": stages["persistable"]["false_positives"],
        "unsupported_accepted_claims": unsupported_accepted,
        "forbidden_accepted_claims": _forbidden_claim_count(
            case["forbidden_claims"],
            [claim["statement"] for claim in final_actual],
        ),
        "missing_required_claims": stages["persistable"]["false_negatives"],
    }


def _empty_pipeline(task: str, outcome: str) -> _PipelineStage:
    candidate = ReflectionCandidate(task_summary=task[:200], outcome=outcome)
    return _PipelineStage(
        candidate,
        ClaimValidationResult(),
        ReflectionValueDecision(
            accepted=False,
            reason_codes=["llm_branch_unavailable"],
        ),
    )


def evaluate_holdout_case(case: dict[str, Any]) -> dict[str, Any]:
    """Evaluate rule, shadow branches, and production LLM selection for one case."""
    evidence = TraceEvidenceExtractor().extract(
        case["task_description"], case["trace"]
    )
    validator = ReflectionClaimValidator()
    value_gate = ReflectionValueGate()
    rule_candidate = RuleReflectionSynthesizer().synthesize(
        case["task_description"], evidence
    )
    rule_validation = validator.validate(rule_candidate, evidence)
    rule_value = value_gate.evaluate(rule_candidate, rule_validation, evidence)
    rule_pipeline = _PipelineStage(rule_candidate, rule_validation, rule_value)

    client = ScriptedHoldoutClient(case, evidence.outcome)
    eligibility = ReflectionLLMEligibilityGate().evaluate(
        evidence,
        model_call_allowed=True,
    )
    llm_pipeline = _empty_pipeline(case["task_description"], evidence.outcome)
    attempt = None
    fallback_reason = eligibility.reason_codes[0] if not eligibility.eligible else None
    if eligibility.eligible:
        synthesizer = LLMReflectionSynthesizer(
            client,
            ReflectionLLMConfig(mode="llm_shadow"),
        )
        attempt = synthesizer.attempt(case["task_description"], evidence)
        fallback_reason = attempt.failure_code if not attempt.success else None
        if attempt.success and attempt.candidate is not None:
            llm_validation = validator.validate(attempt.candidate, evidence)
            llm_value = value_gate.evaluate(
                attempt.candidate, llm_validation, evidence
            )
            llm_pipeline = _PipelineStage(
                attempt.candidate,
                llm_validation,
                llm_value,
            )
            if not llm_validation.valid_claims:
                fallback_reason = "all_llm_claims_rejected"

    use_llm = bool(
        attempt
        and attempt.success
        and llm_pipeline.validation.valid_claims
    )
    production_pipeline = llm_pipeline if use_llm else rule_pipeline
    rule_metrics = _branch_metrics(case, rule_pipeline)
    llm_metrics = _branch_metrics(case, llm_pipeline)
    production_metrics = _branch_metrics(case, production_pipeline)
    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "eligibility": eligibility.to_dict(),
        "llm_called": client.calls == 1,
        "llm_script_kind": case["llm_script"]["kind"],
        "fallback_reason": fallback_reason,
        "production_source": "llm" if use_llm else "rule_fallback",
        "rule": rule_metrics,
        "llm_shadow_rule": rule_metrics,
        "llm_shadow_llm": llm_metrics,
        "llm": production_metrics,
        "latency_ms": (
            float(case["llm_script"].get("latency_ms", 4.0))
            if client.calls == 1
            else 0.0
        ),
        "input_tokens": attempt.input_tokens if attempt else None,
        "output_tokens": attempt.output_tokens if attempt else None,
        "estimated_cost_usd": attempt.estimated_cost_usd if attempt else None,
        "timeout": bool(attempt and attempt.failure_code == "provider_timeout"),
        "provider_failure": bool(attempt and attempt.failure_code == "provider_error"),
        "parse_failure": bool(
            attempt
            and attempt.failure_code
            and attempt.failure_code
            not in {"provider_timeout", "provider_error", "tool_call_rejected"}
        ),
        "tool_call_rejected": bool(
            attempt and attempt.failure_code == "tool_call_rejected"
        ),
    }


def _rate_metrics(tp: int, fp: int, fn: int) -> dict[str, int | float]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _aggregate_branch(cases: list[dict[str, Any]], branch: str) -> dict[str, Any]:
    stages: dict[str, Any] = {}
    for stage in ("candidate", "validator", "value", "persistable"):
        values = [case[branch]["stages"][stage] for case in cases]
        stages[stage] = _rate_metrics(
            sum(item["true_positives"] for item in values),
            sum(item["false_positives"] for item in values),
            sum(item["false_negatives"] for item in values),
        )
    tp = sum(case[branch]["value_true_positive"] for case in cases)
    fp = sum(case[branch]["value_false_positive"] for case in cases)
    fn = sum(case[branch]["value_false_negative"] for case in cases)
    tn = sum(case[branch]["value_true_negative"] for case in cases)
    value = _rate_metrics(tp, fp, fn)
    value["true_negatives"] = tn
    value["low_value_false_write_rate"] = fp / (fp + tn) if fp + tn else 0.0
    return {
        "claim_quality": stages,
        "value_quality": value,
        "supported_accepted_claims": sum(
            case[branch]["supported_accepted_claims"] for case in cases
        ),
        "unsupported_accepted_claims": sum(
            case[branch]["unsupported_accepted_claims"] for case in cases
        ),
        "unexpected_accepted_claims": sum(
            case[branch]["unexpected_accepted_claims"] for case in cases
        ),
        "forbidden_accepted_claims": sum(
            case[branch]["forbidden_accepted_claims"] for case in cases
        ),
        "missing_required_claims": sum(
            case[branch]["missing_required_claims"] for case in cases
        ),
        "invalid_evidence_references": sum(
            case[branch]["invalid_evidence_references"] for case in cases
        ),
        "epistemic_mismatches": sum(
            case[branch]["epistemic_mismatches"] for case in cases
        ),
        "duplicate_semantic_keys": sum(
            case[branch]["duplicate_semantic_keys"] for case in cases
        ),
        "root_cause_overclaim": sum(
            case[branch]["root_cause_overclaim"] for case in cases
        ),
        "root_cause_candidate_overclaim": sum(
            case[branch]["root_cause_candidate_overclaim"] for case in cases
        ),
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1)
    return ordered[max(0, index)]


def evaluate_holdout(root: str | Path) -> dict[str, Any]:
    """Evaluate the complete holdout and return a deterministic aggregate report."""
    cases = [evaluate_holdout_case(case) for case in load_holdout_dataset(root)]
    called = [case for case in cases if case["llm_called"]]
    latencies = [float(case["latency_ms"]) for case in called]
    input_tokens = [case["input_tokens"] for case in called if case["input_tokens"] is not None]
    output_tokens = [case["output_tokens"] for case in called if case["output_tokens"] is not None]
    costs = [case["estimated_cost_usd"] for case in called if case["estimated_cost_usd"] is not None]
    rule_only: list[str] = []
    llm_only: list[str] = []
    rule_only_errors: list[str] = []
    llm_only_errors: list[str] = []
    for case in cases:
        rule_matches = set(case["rule"]["matched_expected"]["validator"])
        llm_matches = set(case["llm_shadow_llm"]["matched_expected"]["validator"])
        if rule_matches - llm_matches:
            rule_only.append(case["case_id"])
        if llm_matches - rule_matches:
            llm_only.append(case["case_id"])
        if case["rule"]["stages"]["validator"]["false_positives"] > case["llm_shadow_llm"]["stages"]["validator"]["false_positives"]:
            rule_only_errors.append(case["case_id"])
        if case["llm_shadow_llm"]["stages"]["validator"]["false_positives"] > case["rule"]["stages"]["validator"]["false_positives"]:
            llm_only_errors.append(case["case_id"])
    return {
        "schema_version": 1,
        "dataset": "reflection_llm_holdout",
        "evaluation_client": "scripted_offline_fixture",
        "limitations": [
            "Scripted responses validate architecture and scoring deterministically; they are not evidence of a real provider's model quality.",
            "No network model was called and no production memory was written.",
        ],
        "case_count": len(cases),
        "modes": {
            branch: _aggregate_branch(cases, branch)
            for branch in ("rule", "llm_shadow_rule", "llm_shadow_llm", "llm")
        },
        "llm_runtime": {
            "eligibility_rate": sum(case["eligibility"]["eligible"] for case in cases) / len(cases) if cases else 0.0,
            "call_rate": len(called) / len(cases) if cases else 0.0,
            "fallback_rate": sum(case["production_source"] != "llm" for case in cases) / len(cases) if cases else 0.0,
            "timeout_rate": sum(case["timeout"] for case in cases) / len(cases) if cases else 0.0,
            "parse_failure_rate": sum(case["parse_failure"] for case in cases) / len(cases) if cases else 0.0,
            "provider_failure_rate": sum(case["provider_failure"] for case in cases) / len(cases) if cases else 0.0,
            "tool_call_rejection_rate": sum(case["tool_call_rejected"] for case in cases) / len(cases) if cases else 0.0,
            "latency_ms": {
                "average": statistics.fmean(latencies) if latencies else 0.0,
                "median": statistics.median(latencies) if latencies else 0.0,
                "p95": _percentile(latencies, 0.95),
            },
            "average_input_tokens": statistics.fmean(input_tokens) if input_tokens else None,
            "average_output_tokens": statistics.fmean(output_tokens) if output_tokens else None,
            "estimated_cost_usd": sum(costs) if costs else None,
        },
        "comparative_cases": {
            "rule_only_correct_claims": rule_only,
            "llm_only_correct_claims": llm_only,
            "rule_only_errors": rule_only_errors,
            "llm_only_errors": llm_only_errors,
        },
        "fallback_reasons": dict(
            sorted(
                Counter(
                    case["fallback_reason"]
                    for case in cases
                    if case["fallback_reason"]
                ).items()
            )
        ),
        "cases": cases,
    }


def write_report(report: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "ScriptedHoldoutClient",
    "evaluate_holdout",
    "evaluate_holdout_case",
    "load_holdout_dataset",
    "write_report",
]
