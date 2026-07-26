"""Claim-level precision and conservative arbitration evaluation.

This evaluator is independent from the frozen reflection holdout evaluator. It
can consume scripted synthetic responses or bounded synthetic provider captures
and never writes production memory.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from minicode.reflection_claim_selection import (
    ClaimSuppressionResult,
    PersistableClaimEvaluation,
    detect_rule_regression,
    suppress_redundant_llm_claims,
)
from minicode.reflection_evidence import TaskEvidence, TraceEvidenceExtractor
from minicode.reflection_llm import (
    LLMReflectionSynthesizer,
    ReflectionLLMConfig,
    ReflectionLLMEligibilityGate,
    StructuredGenerationResponse,
    get_reflection_output_schema,
    get_reflection_prompt,
    reflection_output_schema_version,
    reflection_prompt_hash,
)
from minicode.reflection_replay import load_synthetic_response_capture
from minicode.reflection_synthesis import (
    ClaimValidationResult,
    ReflectionCandidate,
    ReflectionClaim,
    ReflectionClaimValidator,
    ReflectionValueDecision,
    ReflectionValueGate,
    RuleReflectionSynthesizer,
)
from scripts.reflection_evaluator import _match_structured_claims
from scripts.reflection_llm_evaluator import (
    ScriptedHoldoutClient,
    load_holdout_dataset,
)


_ANNOTATION_FIELDS = {
    "primary_claims",
    "secondary_allowed_claims",
    "expected_rule_behavior",
    "expected_gap_fill_behavior",
    "evidence_chain_ids",
    "allowed_claim_count",
}


@dataclass
class _Branch:
    candidate: ReflectionCandidate
    validation: ClaimValidationResult
    value: ReflectionValueDecision
    evaluation: PersistableClaimEvaluation
    suppression: ClaimSuppressionResult = field(
        default_factory=ClaimSuppressionResult
    )

    def claims(self, stage: str) -> list[ReflectionClaim]:
        return {
            "candidate": self.evaluation.candidate_claims,
            "validator": self.evaluation.valid_claims,
            "value": self.evaluation.value_accepted_claims,
            "persistable": self.evaluation.persistable_claims,
        }[stage]


class _CapturedResponseClient:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.call_count = 0

    def generate_json(
        self,
        messages: list[dict[str, str]],
        *,
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> StructuredGenerationResponse:
        del messages, timeout_seconds, max_output_tokens
        self.call_count += 1
        usage_source = str(self.record.get("usage_source") or "unavailable")
        if usage_source not in {"provider", "estimated", "unavailable"}:
            usage_source = "unavailable"
        return StructuredGenerationResponse(
            text=str(self.record.get("sanitized_response") or ""),
            input_tokens=_optional_int(self.record.get("input_tokens")),
            output_tokens=_optional_int(self.record.get("output_tokens")),
            cache_read_tokens=_optional_int(self.record.get("cache_read_tokens")),
            cache_creation_tokens=_optional_int(
                self.record.get("cache_creation_tokens")
            ),
            usage_source=usage_source,  # type: ignore[arg-type]
            estimated_cost_usd=_optional_float(
                self.record.get("estimated_cost_usd")
            ),
            latency_ms=_optional_float(self.record.get("latency_ms")),
        )


def _optional_int(value: Any) -> int | None:
    try:
        return None if value is None else max(0, int(value))
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, parsed) if math.isfinite(parsed) else None


def load_precision_dataset(root: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dataset_root = Path(root)
    manifest = json.loads((dataset_root / "manifest.json").read_text())
    if (
        manifest.get("schema_version") != 2
        or manifest.get("synthetic_data") is not True
        or manifest.get("response_capture_allowed") is not True
    ):
        raise ValueError("precision holdout manifest is invalid")
    cases = load_holdout_dataset(dataset_root)
    for case in cases:
        missing = sorted(_ANNOTATION_FIELDS - set(case))
        if missing:
            raise ValueError(f"{case['case_id']} missing annotations: {missing}")
        if not isinstance(case["primary_claims"], list):
            raise ValueError(f"{case['case_id']} primary_claims must be a list")
        if not isinstance(case["secondary_allowed_claims"], list):
            raise ValueError(
                f"{case['case_id']} secondary_allowed_claims must be a list"
            )
        if int(case["allowed_claim_count"]) < 0:
            raise ValueError(f"{case['case_id']} allowed_claim_count is invalid")
    known = {case["case_id"] for case in cases}
    selected = list(manifest.get("real_ab_case_ids") or [])
    if len(selected) != len(set(selected)) or set(selected) - known:
        raise ValueError("real_ab_case_ids are invalid")
    return manifest, cases


def _rule_branch(
    case: dict[str, Any],
    evidence: TaskEvidence,
    validator: ReflectionClaimValidator,
    value_gate: ReflectionValueGate,
) -> _Branch:
    candidate = RuleReflectionSynthesizer().synthesize(
        case["task_description"], evidence
    )
    validation = validator.validate(candidate, evidence)
    value = value_gate.evaluate(candidate, validation, evidence)
    evaluation = PersistableClaimEvaluation.from_pipeline(
        candidate,
        validation,
        value,
        selection_source="rule",
        selection_reason="rule_branch_evaluated",
    )
    return _Branch(candidate, validation, value, evaluation)


def _empty_llm_branch(case: dict[str, Any], evidence: TaskEvidence) -> _Branch:
    candidate = ReflectionCandidate(
        task_summary=case["task_description"][:200],
        outcome=evidence.outcome,
    )
    validation = ClaimValidationResult()
    value = ReflectionValueDecision(
        accepted=False,
        reason_codes=["llm_branch_unavailable"],
    )
    evaluation = PersistableClaimEvaluation.from_pipeline(
        candidate,
        validation,
        value,
        selection_source="llm",
        selection_reason="llm_branch_unavailable",
    )
    return _Branch(candidate, validation, value, evaluation)


def _llm_branch(
    case: dict[str, Any],
    evidence: TaskEvidence,
    validator: ReflectionClaimValidator,
    value_gate: ReflectionValueGate,
    *,
    prompt_version: str,
    capture: dict[str, Any] | None,
) -> tuple[_Branch, dict[str, Any]]:
    client: Any = (
        _CapturedResponseClient(capture)
        if capture is not None
        else ScriptedHoldoutClient(case, evidence.outcome)
    )
    config = ReflectionLLMConfig(
        mode="llm_shadow",
        prompt_version=prompt_version,  # type: ignore[arg-type]
    )
    attempt = LLMReflectionSynthesizer(client, config).attempt(
        case["task_description"], evidence
    )
    runtime = {
        "called": bool(getattr(client, "call_count", getattr(client, "calls", 0))),
        "parser_success": attempt.success,
        "failure_code": attempt.failure_code,
        "failure_detail_code": attempt.failure_detail_code,
        "input_tokens": attempt.input_tokens,
        "output_tokens": attempt.output_tokens,
        "cache_read_tokens": attempt.cache_read_tokens,
        "cache_creation_tokens": attempt.cache_creation_tokens,
        "usage_source": attempt.usage_source,
        "estimated_cost_usd": attempt.estimated_cost_usd,
        "latency_ms": attempt.latency_ms,
    }
    if not attempt.success or attempt.candidate is None:
        return _empty_llm_branch(case, evidence), runtime
    raw_validation = validator.validate(attempt.candidate, evidence)
    suppression = suppress_redundant_llm_claims(
        raw_validation.valid_claims,
        evidence,
    )
    filtered_validation = ClaimValidationResult(
        valid_claims=list(suppression.kept_claims),
        rejected_claims=list(raw_validation.rejected_claims),
        issues=list(raw_validation.issues),
    )
    value = value_gate.evaluate(
        attempt.candidate,
        filtered_validation,
        evidence,
    )
    evaluation = PersistableClaimEvaluation.from_pipeline(
        attempt.candidate,
        raw_validation,
        value,
        selection_source="llm",
        selection_reason="llm_branch_evaluated",
        suppression=suppression,
    )
    return (
        _Branch(
            attempt.candidate,
            filtered_validation,
            value,
            evaluation,
            suppression,
        ),
        runtime,
    )


def _claim_dicts(claims: list[ReflectionClaim]) -> list[dict[str, Any]]:
    return [claim.to_dict() for claim in claims]


def _exact_metrics(
    expected: list[dict[str, Any]],
    actual: list[ReflectionClaim],
) -> dict[str, Any]:
    values, matched_expected, matched_actual, semantic_mismatches = (
        _match_structured_claims(expected, _claim_dicts(actual))
    )
    return {
        **values,
        "matched_expected": sorted(matched_expected),
        "matched_actual": sorted(matched_actual),
        "semantic_key_mismatches": semantic_mismatches,
    }


def _label_matches(claim: ReflectionClaim, label: dict[str, Any]) -> bool:
    if label.get("claim_type") and claim.claim_type != label["claim_type"]:
        return False
    text = claim.statement.lower()
    required = [str(item).lower() for item in label.get("required_terms", [])]
    forbidden = [str(item).lower() for item in label.get("forbidden_terms", [])]
    return all(term in text for term in required) and not any(
        term in text for term in forbidden
    )


def _primary_covered(
    claims: list[ReflectionClaim],
    label: dict[str, Any],
) -> bool:
    if any(_label_matches(claim, label) for claim in claims):
        return True
    relevant = [
        claim.statement.lower()
        for claim in claims
        if not label.get("claim_type") or claim.claim_type == label["claim_type"]
    ]
    combined = " ".join(relevant)
    return bool(relevant) and all(
        str(term).lower() in combined for term in label.get("required_terms", [])
    )


def _adjudicated_metrics(
    case: dict[str, Any],
    actual: list[ReflectionClaim],
    evidence: TaskEvidence,
) -> dict[str, Any]:
    primary = list(case["primary_claims"])
    secondary = list(case["secondary_allowed_claims"])
    suppressed = suppress_redundant_llm_claims(actual, evidence)
    redundant_ids = {claim.claim_id for claim in suppressed.suppressed_claims}
    classifications: dict[str, str] = {}
    for claim in actual:
        if claim.claim_id in redundant_ids:
            classifications[claim.claim_id] = "redundant"
        elif any(_label_matches(claim, label) for label in primary):
            classifications[claim.claim_id] = "primary"
        elif any(_label_matches(claim, label) for label in secondary):
            classifications[claim.claim_id] = "legal_secondary"
        elif any(
            all(
                str(term).lower() in claim.statement.lower()
                for term in label.get("required_terms", [])
            )
            for label in case["forbidden_claims"]
        ):
            classifications[claim.claim_id] = "forbidden"
        else:
            classifications[claim.claim_id] = "incorrect"
    primary_matches = sum(_primary_covered(actual, label) for label in primary)
    correct_actual = sum(
        kind in {"primary", "legal_secondary"}
        for kind in classifications.values()
    )
    false_positives = len(actual) - correct_actual
    false_negatives = len(primary) - primary_matches
    precision = correct_actual / len(actual) if actual else 1.0
    recall = primary_matches / len(primary) if primary else 1.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return {
        "true_positives": correct_actual,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "primary_matches": primary_matches,
        "primary_count": len(primary),
        "primary_lesson_recall": recall,
        "legal_secondary_count": sum(
            kind == "legal_secondary" for kind in classifications.values()
        ),
        "redundant_claim_count": sum(
            kind == "redundant" for kind in classifications.values()
        ),
        "forbidden_claim_count": sum(
            kind == "forbidden" for kind in classifications.values()
        ),
        "incorrect_claim_count": sum(
            kind == "incorrect" for kind in classifications.values()
        ),
        "classifications": classifications,
    }


def _stage_metrics(
    case: dict[str, Any],
    claims: list[ReflectionClaim],
    evidence: TaskEvidence,
) -> dict[str, Any]:
    return {
        "exact": _exact_metrics(case["expected_claims"], claims),
        "adjudicated": _adjudicated_metrics(case, claims, evidence),
        "claim_count": len(claims),
    }


def _evaluate_case(
    case: dict[str, Any],
    *,
    prompt_version: str,
    strategy: str,
    capture: dict[str, Any] | None,
) -> dict[str, Any]:
    evidence = TraceEvidenceExtractor().extract(
        case["task_description"], case["trace"]
    )
    validator = ReflectionClaimValidator()
    value_gate = ReflectionValueGate()
    rule = _rule_branch(case, evidence, validator, value_gate)
    eligibility = ReflectionLLMEligibilityGate().evaluate(
        evidence,
        model_call_allowed=True,
    )
    llm = _empty_llm_branch(case, evidence)
    runtime: dict[str, Any] = {
        "called": False,
        "parser_success": False,
        "failure_code": eligibility.reason_codes[0]
        if not eligibility.eligible
        else "response_unavailable",
    }
    if eligibility.eligible:
        llm, runtime = _llm_branch(
            case,
            evidence,
            validator,
            value_gate,
            prompt_version=prompt_version,
            capture=capture,
        )

    rule_has = bool(rule.evaluation.persistable_claims)
    llm_has = bool(llm.evaluation.persistable_claims)
    gap_fill_attempted = not rule_has
    if strategy == "gap_fill":
        final = rule if rule_has or not llm_has else llm
        source = "rule" if final is rule else "llm_gap_fill"
    else:
        final = llm if llm_has else rule
        source = "llm_replace" if final is llm else "rule_fallback"

    final_claims = list(final.evaluation.persistable_claims)
    final_exact = _exact_metrics(case["expected_claims"], final_claims)
    final_adjudicated = _adjudicated_metrics(case, final_claims, evidence)
    rule_adjudicated = _adjudicated_metrics(
        case, rule.evaluation.persistable_claims, evidence
    )
    llm_adjudicated = _adjudicated_metrics(
        case, llm.evaluation.persistable_claims, evidence
    )
    replace_regression = bool(
        source == "llm_replace"
        and rule_adjudicated["primary_matches"]
        and llm_adjudicated["primary_matches"]
        < rule_adjudicated["primary_matches"]
    )
    deterministic_regression = bool(
        source == "llm_replace"
        and detect_rule_regression(
            rule.evaluation.persistable_claims,
            llm.evaluation.persistable_claims,
            evidence,
        )
    )
    gap_fill_success = bool(
        strategy == "gap_fill"
        and gap_fill_attempted
        and source == "llm_gap_fill"
        and final_adjudicated["primary_matches"]
        and final_adjudicated["false_positives"] == 0
        and case["should_write_memory"]
    )
    write_decision = bool(final_claims)
    llm_persistable_ids = {
        claim.claim_id for claim in llm.evaluation.persistable_claims
    }
    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "should_write_memory": case["should_write_memory"],
        "eligibility": eligibility.to_dict(),
        "selection_strategy": strategy,
        "selection_source": source,
        "selection_reason": (
            "rule_already_durable"
            if strategy == "gap_fill" and rule_has
            else (
                "llm_filled_rule_gap"
                if source == "llm_gap_fill"
                else runtime.get("failure_code") or "branch_selected"
            )
        ),
        "rule_persistable_claim_ids": [
            claim.claim_id for claim in rule.evaluation.persistable_claims
        ],
        "llm_persistable_claim_ids": [
            claim.claim_id for claim in llm.evaluation.persistable_claims
        ],
        "final_persistable_claim_ids": [claim.claim_id for claim in final_claims],
        "final_persistable_claim_types": [claim.claim_type for claim in final_claims],
        "expected_primary_claim_types": [
            label.get("claim_type") for label in case["primary_claims"]
        ],
        "llm_stages": {
            stage: _stage_metrics(case, llm.claims(stage), evidence)
            for stage in ("candidate", "validator", "value", "persistable")
        },
        "final_persistable": {
            "exact": final_exact,
            "adjudicated": final_adjudicated,
            "claim_count": len(final_claims),
        },
        "rule_persistable": {
            "exact": _exact_metrics(
                case["expected_claims"], rule.evaluation.persistable_claims
            ),
            "adjudicated": rule_adjudicated,
        },
        "llm_persistable": {
            "exact": _exact_metrics(
                case["expected_claims"], llm.evaluation.persistable_claims
            ),
            "adjudicated": llm_adjudicated,
        },
        "write_decision": write_decision,
        "write_decision_correct": write_decision == case["should_write_memory"],
        "low_value_false_accept": write_decision and not case["should_write_memory"],
        "rule_regression": replace_regression,
        "deterministic_rule_regression": deterministic_regression,
        "replace_regression": replace_regression,
        "gap_fill_attempted": gap_fill_attempted,
        "gap_fill_success": gap_fill_success,
        "gap_fill_false_positive": bool(
            strategy == "gap_fill"
            and source == "llm_gap_fill"
            and final_adjudicated["false_positives"]
        ),
        "suppressed_claim_ids": [
            claim.claim_id for claim in llm.suppression.suppressed_claims
        ],
        "suppression_reason_codes": dict(
            llm.suppression.suppression_reason_codes
        ),
        "validator_issue_codes": [issue.code for issue in llm.validation.issues],
        "accepted_epistemic_mismatch": any(
            issue.code == "epistemic_status_overclaim"
            and issue.claim_id in llm_persistable_ids
            for issue in llm.validation.issues
        ),
        "runtime": runtime,
    }


def _aggregate_metric(values: list[dict[str, Any]]) -> dict[str, Any]:
    tp = sum(int(value["true_positives"]) for value in values)
    fp = sum(int(value["false_positives"]) for value in values)
    fn = sum(int(value["false_negatives"]) for value in values)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
        "f1": (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        ),
    }


def _percentile(values: list[float], value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * value) - 1))
    return ordered[index]


def _aggregate_arm(cases: list[dict[str, Any]]) -> dict[str, Any]:
    llm_stage_quality: dict[str, Any] = {}
    for stage in ("candidate", "validator", "value", "persistable"):
        llm_stage_quality[stage] = {
            mode: _aggregate_metric(
                [case["llm_stages"][stage][mode] for case in cases]
            )
            for mode in ("exact", "adjudicated")
        }
    final_quality = {
        mode: _aggregate_metric(
            [case["final_persistable"][mode] for case in cases]
        )
        for mode in ("exact", "adjudicated")
    }
    primary_matches = sum(
        case["final_persistable"]["adjudicated"]["primary_matches"]
        for case in cases
    )
    primary_count = sum(
        case["final_persistable"]["adjudicated"]["primary_count"]
        for case in cases
    )
    redundant = sum(
        case["final_persistable"]["adjudicated"]["redundant_claim_count"]
        for case in cases
    )
    accepted = sum(case["final_persistable"]["claim_count"] for case in cases)
    called = [case for case in cases if case["runtime"].get("called")]
    latencies = [float(case["runtime"].get("latency_ms") or 0.0) for case in called]
    input_tokens = [
        int(case["runtime"]["input_tokens"])
        for case in called
        if case["runtime"].get("input_tokens") is not None
    ]
    cache_tokens = sum(
        int(case["runtime"].get("cache_read_tokens") or 0) for case in called
    )
    costs = sum(
        float(case["runtime"].get("estimated_cost_usd") or 0.0)
        for case in called
    )
    correct_gap_fills = sum(case["gap_fill_success"] for case in cases)
    correct_persistable = final_quality["adjudicated"]["true_positives"]
    return {
        "case_count": len(cases),
        "llm_branch_claim_quality": llm_stage_quality,
        "final_persistable_claim_quality": final_quality,
        "persistable_false_positive_count": final_quality["exact"][
            "false_positives"
        ],
        "persistable_false_negative_count": final_quality["exact"][
            "false_negatives"
        ],
        "accepted_redundant_claim_count": redundant,
        "accepted_redundant_claim_rate": redundant / accepted if accepted else 0.0,
        "primary_lesson_recall": (
            primary_matches / primary_count if primary_count else 1.0
        ),
        "rule_regression_count": sum(case["rule_regression"] for case in cases),
        "replace_regression_count": sum(
            case["replace_regression"] for case in cases
        ),
        "rule_only_correct_persistable_claims": sum(
            max(
                0,
                case["rule_persistable"]["adjudicated"]["primary_matches"]
                - case["llm_persistable"]["adjudicated"]["primary_matches"],
            )
            for case in cases
        ),
        "llm_only_correct_persistable_claims": sum(
            max(
                0,
                case["llm_persistable"]["adjudicated"]["primary_matches"]
                - case["rule_persistable"]["adjudicated"]["primary_matches"],
            )
            for case in cases
        ),
        "gap_fill_attempt_count": sum(case["gap_fill_attempted"] for case in cases),
        "gap_fill_success_count": correct_gap_fills,
        "gap_fill_false_positive_count": sum(
            case["gap_fill_false_positive"] for case in cases
        ),
        "low_value_false_accept_count": sum(
            case["low_value_false_accept"] for case in cases
        ),
        "invalid_evidence_reference_count": sum(
            code
            in {
                "invalid_evidence_reference",
                "invalid_verification_reference",
                "invalid_error_reference",
                "invalid_recovery_reference",
            }
            for case in cases
            for code in case["validator_issue_codes"]
        ),
        "accepted_epistemic_mismatch_count": sum(
            case["accepted_epistemic_mismatch"]
            for case in cases
        ),
        "final_root_cause_overclaim_count": sum(
            claim_type == "root_cause"
            and "root_cause" not in case["expected_primary_claim_types"]
            for case in cases
            for claim_type in case["final_persistable_claim_types"]
        ),
        "parser_success_rate": (
            sum(case["runtime"].get("parser_success") for case in called)
            / len(called)
            if called
            else 0.0
        ),
        "semantic_key_failure_count": sum(
            case["runtime"].get("failure_code") == "invalid_semantic_key"
            for case in called
        ),
        "provider_negative_sample_count": sum(
            case["runtime"].get("called") and not case["should_write_memory"]
            for case in cases
        ),
        "runtime": {
            "call_count": len(called),
            "usage_sources": dict(
                sorted(
                    Counter(
                        str(case["runtime"].get("usage_source") or "unavailable")
                        for case in called
                    ).items()
                )
            ),
            "input_tokens_total": sum(input_tokens),
            "average_input_tokens": (
                statistics.fmean(input_tokens) if input_tokens else None
            ),
            "cache_read_tokens_total": cache_tokens,
            "latency_ms": {
                "average": statistics.fmean(latencies) if latencies else 0.0,
                "median": statistics.median(latencies) if latencies else 0.0,
                "p95": _percentile(latencies, 0.95),
            },
            "estimated_cost_usd": costs,
            "cost_per_correct_gap_fill_usd": (
                costs / correct_gap_fills if correct_gap_fills else None
            ),
            "cost_per_correct_persistable_claim_usd": (
                costs / correct_persistable if correct_persistable else None
            ),
        },
    }


def _capture_map(path: str | Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    return {
        str(record["case_id"]): record
        for record in load_synthetic_response_capture(path)
        if record.get("case_id")
    }


def _load_pilot_cases(paths: list[str | Path] | None) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in paths or []:
        report = json.loads(Path(path).read_text())
        cases.extend(
            case for case in report.get("cases", []) if case.get("called")
        )
    return cases


def _aggregate_provider_pilot(cases: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(case.get("latency_ms") or 0.0) for case in cases]
    input_tokens = [int(case.get("input_tokens") or 0) for case in cases]
    validator = _aggregate_metric(
        [dict(case.get("llm_validator") or {}) for case in cases]
    ) if cases else _aggregate_metric([])
    return {
        "case_count": len(cases),
        "call_count": len(cases),
        "parser_success_count": sum(bool(case.get("parser_success")) for case in cases),
        "parser_success_rate": (
            sum(bool(case.get("parser_success")) for case in cases) / len(cases)
            if cases
            else 0.0
        ),
        "semantic_key_failure_count": sum(
            case.get("parser_failure_code") == "invalid_semantic_key"
            for case in cases
        ),
        "validator_exact_claim_quality": validator,
        "provider_negative_sample_count": sum(
            not case.get("expected_memory_write") for case in cases
        ),
        "low_value_false_write_count": sum(
            bool(case.get("value_false_write")) for case in cases
        ),
        "invalid_evidence_reference_count": sum(
            int(case.get("invalid_evidence_references") or 0) for case in cases
        ),
        "epistemic_mismatch_count": sum(
            int(case.get("epistemic_mismatches") or 0) for case in cases
        ),
        "root_cause_overclaim_count": sum(
            int(case.get("root_cause_overclaim") or 0) for case in cases
        ),
        "usage_sources": dict(
            sorted(Counter(str(case.get("usage_source")) for case in cases).items())
        ),
        "input_tokens_total": sum(input_tokens),
        "average_input_tokens": (
            statistics.fmean(input_tokens) if input_tokens else None
        ),
        "output_tokens_total": sum(
            int(case.get("output_tokens") or 0) for case in cases
        ),
        "cache_read_tokens_total": sum(
            int(case.get("cache_read_tokens") or 0) for case in cases
        ),
        "cache_creation_tokens_total": sum(
            int(case.get("cache_creation_tokens") or 0) for case in cases
        ),
        "latency_ms": {
            "average": statistics.fmean(latencies) if latencies else 0.0,
            "median": statistics.median(latencies) if latencies else 0.0,
            "p95": _percentile(latencies, 0.95),
        },
        "estimated_cost_usd": sum(
            float(case.get("estimated_cost_usd") or 0.0) for case in cases
        ),
        "cases": cases,
    }


def evaluate_precision(
    root: str | Path,
    *,
    verbose_capture: str | Path | None = None,
    compact_capture: str | Path | None = None,
    verbose_pilot_reports: list[str | Path] | None = None,
    compact_pilot_reports: list[str | Path] | None = None,
    selected_only: bool = False,
) -> dict[str, Any]:
    manifest, all_cases = load_precision_dataset(root)
    selected_ids = set(manifest["real_ab_case_ids"])
    requested_cases = (
        [case for case in all_cases if case["case_id"] in selected_ids]
        if selected_only
        else all_cases
    )
    verbose_records = _capture_map(verbose_capture)
    compact_records = _capture_map(compact_capture)
    common_replay_ids = set(verbose_records).intersection(compact_records)
    cases = (
        [case for case in requested_cases if case["case_id"] in common_replay_ids]
        if verbose_records or compact_records
        else requested_cases
    )
    verbose_pilot = _aggregate_provider_pilot(
        _load_pilot_cases(verbose_pilot_reports)
    )
    compact_pilot = _aggregate_provider_pilot(
        _load_pilot_cases(compact_pilot_reports)
    )
    arms: dict[str, Any] = {}
    specifications = {
        "calibrated_verbose_replace": (
            "calibrated_verbose",
            "replace",
            verbose_records,
        ),
        "calibrated_compact_replace": (
            "calibrated_compact",
            "replace",
            compact_records,
        ),
        "calibrated_compact_gap_fill": (
            "calibrated_compact",
            "gap_fill",
            compact_records,
        ),
    }
    for name, (prompt_version, strategy, captures) in specifications.items():
        case_results = [
            _evaluate_case(
                case,
                prompt_version=prompt_version,
                strategy=strategy,
                capture=captures.get(case["case_id"]),
            )
            for case in cases
        ]
        arms[name] = {
            "prompt_version": prompt_version,
            "prompt_version_hash": reflection_prompt_hash(prompt_version),
            "schema_version": reflection_output_schema_version(prompt_version),
            "prompt_characters": len(get_reflection_prompt(prompt_version)),
            "schema_bytes": len(
                json.dumps(
                    get_reflection_output_schema(prompt_version),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ),
            "selection_strategy": strategy,
            "metrics": _aggregate_arm(case_results),
            "cases": case_results,
        }
    verbose_tokens = (
        verbose_pilot["average_input_tokens"]
        or arms["calibrated_verbose_replace"]["metrics"]["runtime"][
            "average_input_tokens"
        ]
    )
    compact_tokens = (
        compact_pilot["average_input_tokens"]
        or arms["calibrated_compact_replace"]["metrics"]["runtime"][
            "average_input_tokens"
        ]
    )
    token_reduction = (
        (verbose_tokens - compact_tokens) / verbose_tokens
        if verbose_tokens and compact_tokens is not None
        else None
    )
    gap = arms["calibrated_compact_gap_fill"]["metrics"]
    compact = arms["calibrated_compact_replace"]["metrics"]
    gates = {
        "parser_success_at_least_95_percent": (
            compact_pilot["parser_success_rate"]
            if compact_pilot["call_count"]
            else compact["parser_success_rate"]
        )
        >= 0.95,
        "semantic_key_failure_at_most_5_percent": (
            (
                compact_pilot["semantic_key_failure_count"]
                if compact_pilot["call_count"]
                else compact["semantic_key_failure_count"]
            )
            / max(
                1,
                compact_pilot["call_count"]
                or compact["runtime"]["call_count"],
            )
            <= 0.05
        ),
        "provider_negative_samples_at_least_8": (
            compact_pilot["provider_negative_sample_count"]
            if compact_pilot["call_count"]
            else compact["provider_negative_sample_count"]
        )
        >= 8,
        "low_value_false_accept_zero": gap["low_value_false_accept_count"] == 0,
        "invalid_reference_zero": gap["invalid_evidence_reference_count"] == 0,
        "epistemic_mismatch_zero": gap[
            "accepted_epistemic_mismatch_count"
        ]
        == 0,
        "root_cause_overclaim_zero": gap["final_root_cause_overclaim_count"] == 0,
        "gap_fill_rule_regression_zero": gap["rule_regression_count"] == 0,
        "adjudicated_persistable_precision_at_least_90_percent": gap[
            "final_persistable_claim_quality"
        ]["adjudicated"]["precision"]
        >= 0.90,
        "exact_persistable_precision_at_least_80_percent": gap[
            "final_persistable_claim_quality"
        ]["exact"]["precision"]
        >= 0.80,
        "primary_lesson_recall_at_least_80_percent": gap[
            "primary_lesson_recall"
        ]
        >= 0.80,
        "accepted_redundant_rate_at_most_10_percent": gap[
            "accepted_redundant_claim_rate"
        ]
        <= 0.10,
        "gap_fill_success_at_least_2": gap["gap_fill_success_count"] >= 2,
        "gap_fill_false_positive_zero": gap["gap_fill_false_positive_count"] == 0,
        "input_token_reduction_at_least_20_percent": (
            token_reduction is not None and token_reduction >= 0.20
        ),
    }
    return {
        "schema_version": 3,
        "dataset": manifest["dataset_id"],
        "synthetic_data": True,
        "evaluation_mode": (
            "provider_capture_replay"
            if verbose_capture or compact_capture
            else "scripted_offline"
        ),
        "case_count": len(cases),
        "requested_case_count": len(requested_cases),
        "claim_replay_case_count": len(cases),
        "summary_only_case_count": len(requested_cases) - len(cases),
        "negative_case_count": sum(not case["should_write_memory"] for case in requested_cases),
        "positive_case_count": sum(case["should_write_memory"] for case in requested_cases),
        "provider_eligible_negative_count": sum(
            not case["should_write_memory"]
            and ReflectionLLMEligibilityGate()
            .evaluate(
                TraceEvidenceExtractor().extract(
                    case["task_description"], case["trace"]
                ),
                model_call_allowed=True,
            )
            .eligible
            for case in requested_cases
        ),
        "real_ab_case_ids": list(manifest["real_ab_case_ids"]),
        "arms": arms,
        "provider_pilot": {
            "calibrated_verbose": verbose_pilot,
            "calibrated_compact": compact_pilot,
        },
        "compact_input_token_reduction": token_reduction,
        "acceptance_gates": gates,
        "acceptance_gate_pass_count": sum(gates.values()),
        "acceptance_gate_count": len(gates),
        "limitations": [
            "All cases and captured responses are synthetic; this is not production traffic evidence.",
            "Adjudicated metrics preserve strict exact metrics and only recognize manually labeled primary/secondary expressions.",
            "Gap-fill success is evaluated against manual labels; operational ReflectionResult only records that a durable LLM branch filled an empty Rule branch.",
            "The Pilot capture writer retains at most ten records per file. Claim-level replay metrics use the ten responses retained in both arms; all fifteen calls remain represented by privacy-bounded Pilot summaries.",
        ],
    }


def write_reports(
    report: dict[str, Any],
    *,
    json_path: str | Path,
    markdown_path: str | Path,
    adjudication_path: str | Path,
) -> None:
    Path(json_path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    lines = [
        "# Reflection Claim Precision",
        "",
        f"- Evaluation: **{report['evaluation_mode']}**",
        f"- Requested provider cases: **{report['requested_case_count']}** ({report['positive_case_count']} positive / {report['negative_case_count']} negative)",
        f"- Claim-level replay cases: **{report['claim_replay_case_count']}**; summary-only provider cases: **{report['summary_only_case_count']}**",
        f"- Provider-eligible negatives: **{report['provider_eligible_negative_count']}**",
        f"- Acceptance gates: **{report['acceptance_gate_pass_count']}/{report['acceptance_gate_count']}**",
        "",
        "| Arm | Exact P/R/F1 | Adjudicated P/R/F1 | Primary recall | Redundant rate | Rule regressions | Gap successes | Calls | Avg input tokens |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in report["arms"].values():
        metrics = arm["metrics"]
        exact = metrics["final_persistable_claim_quality"]["exact"]
        adjudicated = metrics["final_persistable_claim_quality"]["adjudicated"]
        runtime = metrics["runtime"]
        lines.append(
            "| {prompt}+{strategy} | {ep:.1%}/{er:.1%}/{ef:.1%} | {ap:.1%}/{ar:.1%}/{af:.1%} | {primary:.1%} | {redundant:.1%} | {regressions} | {gaps} | {calls} | {tokens} |".format(
                prompt=arm["prompt_version"],
                strategy=arm["selection_strategy"],
                ep=exact["precision"], er=exact["recall"], ef=exact["f1"],
                ap=adjudicated["precision"], ar=adjudicated["recall"], af=adjudicated["f1"],
                primary=metrics["primary_lesson_recall"],
                redundant=metrics["accepted_redundant_claim_rate"],
                regressions=metrics["rule_regression_count"],
                gaps=metrics["gap_fill_success_count"],
                calls=runtime["call_count"],
                tokens=(f"{runtime['average_input_tokens']:.1f}" if runtime["average_input_tokens"] is not None else "n/a"),
            )
        )
    lines.extend(
        [
            "",
            f"Compact actual input-token reduction: **{report['compact_input_token_reduction']:.1%}**" if report["compact_input_token_reduction"] is not None else "Compact actual input-token reduction: **not available**",
            "",
            "## Provider A/B (15 Cases)",
            "",
            "| Prompt | Parser | Validator exact P/R/F1 | Negative false writes | Avg input | Cache read | Avg/median/P95 latency ms | Cost USD |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
            *[
                "| {name} | {parser:.1%} | {p:.1%}/{r:.1%}/{f1:.1%} | {false_writes} | {tokens:.1f} | {cache} | {avg:.1f}/{median:.1f}/{p95:.1f} | {cost:.6f} |".format(
                    name=name,
                    parser=pilot["parser_success_rate"],
                    p=pilot["validator_exact_claim_quality"]["precision"],
                    r=pilot["validator_exact_claim_quality"]["recall"],
                    f1=pilot["validator_exact_claim_quality"]["f1"],
                    false_writes=pilot["low_value_false_write_count"],
                    tokens=pilot["average_input_tokens"] or 0.0,
                    cache=pilot["cache_read_tokens_total"],
                    avg=pilot["latency_ms"]["average"],
                    median=pilot["latency_ms"]["median"],
                    p95=pilot["latency_ms"]["p95"],
                    cost=pilot["estimated_cost_usd"],
                )
                for name, pilot in report["provider_pilot"].items()
            ],
            "",
            "## Compact LLM Stages (10 Replay Cases)",
            "",
            "| Stage | Exact P/R/F1 | Adjudicated P/R/F1 |",
            "|---|---:|---:|",
            *[
                "| {stage} | {ep:.1%}/{er:.1%}/{ef:.1%} | {ap:.1%}/{ar:.1%}/{af:.1%} |".format(
                    stage=stage,
                    ep=values["exact"]["precision"],
                    er=values["exact"]["recall"],
                    ef=values["exact"]["f1"],
                    ap=values["adjudicated"]["precision"],
                    ar=values["adjudicated"]["recall"],
                    af=values["adjudicated"]["f1"],
                )
                for stage, values in report["arms"][
                    "calibrated_compact_gap_fill"
                ]["metrics"]["llm_branch_claim_quality"].items()
            ],
            "",
            "## Gates",
            "",
            *[
                f"- {'PASS' if passed else 'FAIL'}: `{name}`"
                for name, passed in report["acceptance_gates"].items()
            ],
        ]
    )
    Path(markdown_path).write_text("\n".join(lines) + "\n")

    gap_cases = report["arms"]["calibrated_compact_gap_fill"]["cases"]
    differences = [
        case
        for case in gap_cases
        if case["final_persistable"]["exact"]["false_positives"]
        != case["final_persistable"]["adjudicated"]["false_positives"]
        or case["final_persistable"]["exact"]["false_negatives"]
        != case["final_persistable"]["adjudicated"]["false_negatives"]
    ]
    adjudication = [
        "# Reflection Claim Arbitration Adjudication",
        "",
        "Strict exact scores remain unchanged. Adjudicated scoring additionally recognizes manually labeled legal split/secondary expressions and deterministic same-chain redundancy.",
        "",
        "## Exact/Adjudicated Differences",
        "",
    ]
    if differences:
        adjudication.extend(
            f"- `{case['case_id']}`: exact FP/FN {case['final_persistable']['exact']['false_positives']}/{case['final_persistable']['exact']['false_negatives']}; adjudicated FP/FN {case['final_persistable']['adjudicated']['false_positives']}/{case['final_persistable']['adjudicated']['false_negatives']}."
            for case in differences
        )
    else:
        adjudication.append("- No final-stage difference in this run.")
    adjudication.extend(
        [
            "",
            "## Arbitration",
            "",
            "- Frozen controls `holdout-verified-recovery-007` and `holdout-timeout-fallback-032`: production `gap_fill` keeps the Rule recovery without an LLM call; explicit `replace` with the weaker error-pattern fixture records a regression.",
            *[
                f"- `{case['case_id']}` selected `{case['selection_source']}`; gap attempt={case['gap_fill_attempted']}; gap success={case['gap_fill_success']}; replace regression={case['replace_regression']}."
                for case in gap_cases
                if case["gap_fill_attempted"] or case["replace_regression"]
            ],
        ]
    )
    Path(adjudication_path).write_text("\n".join(adjudication) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--verbose-capture", type=Path)
    parser.add_argument("--compact-capture", type=Path)
    parser.add_argument("--verbose-pilot-report", type=Path, action="append")
    parser.add_argument("--compact-pilot-report", type=Path, action="append")
    parser.add_argument("--selected-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_precision(
        args.dataset,
        verbose_capture=args.verbose_capture,
        compact_capture=args.compact_capture,
        verbose_pilot_reports=args.verbose_pilot_report,
        compact_pilot_reports=args.compact_pilot_report,
        selected_only=args.selected_only,
    )
    write_reports(
        report,
        json_path=args.output,
        markdown_path=args.markdown,
        adjudication_path=args.adjudication,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["evaluate_precision", "load_precision_dataset", "write_reports"]
