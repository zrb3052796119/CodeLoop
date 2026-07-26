#!/usr/bin/env python3
"""Run a bounded real-provider pilot against the reflection LLM holdout."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ABSOLUTE_MAX_CALLS = 10
DEFAULT_MAX_CALLS = 5
_SECRET_IDENTIFIER_RE = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{12,}|bearer\s+\S+|"
    r"(?:api[_-]?key|password|secret|token|authorization)\s*[:=]\s*\S+)"
)
_NON_SCHEMA_FAILURE_CODES = {
    "input_envelope_error",
    "input_safety_rejected",
    "input_truncated",
    "provider_error",
    "provider_timeout",
    "tool_call_rejected",
    "unsafe_output",
}


def deterministic_case_order(
    cases: list[dict[str, Any]], seed: str
) -> list[dict[str, Any]]:
    return sorted(
        cases,
        key=lambda case: (
            hashlib.sha256(f"{seed}:{case['case_id']}".encode("utf-8")).hexdigest(),
            case["case_id"],
        ),
    )


def _rate(tp: int, fp: int, fn: int) -> dict[str, int | float]:
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


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _safe_id(value: Any) -> str:
    text = str(value or "unknown")[:120]
    if _SECRET_IDENTIFIER_RE.search(text):
        return "redacted"
    return "".join(char for char in text if char.isalnum() or char in "-_.") or "unknown"


def _semantic_key_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _claim_structure(claims: list[Any]) -> dict[str, Any]:
    return {
        "claim_count": len(claims),
        "claim_type_counts": dict(
            sorted(Counter(str(claim.claim_type) for claim in claims).items())
        ),
        "epistemic_status_counts": dict(
            sorted(Counter(str(claim.epistemic_status) for claim in claims).items())
        ),
        "reference_counts": {
            "evidence": sum(len(claim.evidence_ids) for claim in claims),
            "verification": sum(len(claim.verification_ids) for claim in claims),
            "error": sum(len(claim.related_error_ids) for claim in claims),
            "recovery": sum(len(claim.related_recovery_ids) for claim in claims),
        },
        "semantic_key_hashes": [
            _semantic_key_hash(claim.semantic_key) for claim in claims
        ],
    }


def run_pilot(
    cases: list[dict[str, Any]],
    *,
    execute: bool,
    client: Any | None,
    max_calls: int,
    seed: str,
    case_ids: set[str] | None,
    model: str,
    provider: str,
    unavailable_reason: str = "reflection_client_unavailable",
    delay_seconds: float = 0.0,
    validate_persistence: bool = False,
    timeout_seconds: float = 5.0,
    prompt_version: str = "calibrated",
    capture_writer: Any | None = None,
    pilot_kind: str | None = None,
) -> dict[str, Any]:
    """Evaluate actual responses without using fixture ``llm_script`` values."""
    if not 0 <= max_calls <= ABSOLUTE_MAX_CALLS:
        raise ValueError(f"max_calls must be between 0 and {ABSOLUTE_MAX_CALLS}")

    from minicode.memory import assess_memory_safety
    from minicode.reflection_evidence import TraceEvidenceExtractor
    from minicode.reflection_llm import (
        LLMReflectionSynthesizer,
        ReflectionLLMConfig,
        ReflectionLLMEligibilityGate,
    )
    from minicode.reflection_replay import ObservedStructuredGenerationClient
    from minicode.reflection_shadow_metrics import reflection_task_identifier
    from minicode.reflection_synthesis import (
        ReflectionClaimValidator,
        ReflectionValueGate,
        RuleReflectionSynthesizer,
    )
    from scripts.reflection_evaluator import _match_structured_claims

    selected = [
        case
        for case in deterministic_case_order(cases, seed)
        if not case_ids or case["case_id"] in case_ids
    ]
    extractor = TraceEvidenceExtractor()
    validator = ReflectionClaimValidator()
    value_gate = ReflectionValueGate()
    eligibility_gate = ReflectionLLMEligibilityGate()
    observed_client = (
        ObservedStructuredGenerationClient(client)
        if execute and client is not None and capture_writer is not None
        else None
    )
    synthesis_client = observed_client or client
    synthesizer = (
        LLMReflectionSynthesizer(
            synthesis_client,
            ReflectionLLMConfig(
                mode="llm_shadow",
                model=model,
                allow_remote_model=True,
                timeout_seconds=max(1.0, min(30.0, timeout_seconds)),
                prompt_version=(
                    prompt_version
                    if prompt_version
                    in {
                        "baseline",
                        "calibrated",
                        "calibrated_verbose",
                        "calibrated_compact",
                    }
                    else "calibrated_compact"
                ),
            ),
        )
        if execute and synthesis_client is not None
        else None
    )

    records: list[dict[str, Any]] = []
    called = 0
    last_call_at: float | None = None
    for case in selected:
        evidence = extractor.extract(case["task_description"], case["trace"])
        rule_candidate = RuleReflectionSynthesizer().synthesize(
            case["task_description"], evidence
        )
        rule_validation = validator.validate(rule_candidate, evidence)
        rule_value = value_gate.evaluate(rule_candidate, rule_validation, evidence)
        rule_actual = [claim.to_dict() for claim in rule_validation.valid_claims]
        rule_match, rule_expected, _, _ = _match_structured_claims(
            case["expected_claims"], rule_actual
        )
        eligibility = eligibility_gate.evaluate(
            evidence,
            model_call_allowed=(not execute or synthesizer is not None),
            unavailable_reason=unavailable_reason,
        )
        record: dict[str, Any] = {
            "case_id": _safe_id(case["case_id"]),
            "category": _safe_id(case["category"]),
            "eligible": eligibility.eligible,
            "eligibility_reason_codes": [
                _safe_id(code) for code in eligibility.reason_codes
            ],
            "called": False,
            "parser_success": False,
            "parser_failure_code": None,
            "parser_failure_detail_code": None,
            "fallback_reason": None,
            "production_source": "rule",
            "rule_value_accepted": rule_value.accepted,
            "llm_value_accepted": None,
            "value_accepted": None,
            "expected_memory_write": bool(case["should_write_memory"]),
            "rule_validator": rule_match,
            "llm_validator": _rate(0, 0, len(case["expected_claims"])),
            "invalid_evidence_references": 0,
            "epistemic_mismatches": 0,
            "root_cause_candidate_overclaim": 0,
            "root_cause_overclaim": 0,
            "forbidden_accepted_claims": 0,
            "latency_ms": 0.0,
            "input_tokens": None,
            "output_tokens": None,
            "cache_read_tokens": None,
            "cache_creation_tokens": None,
            "usage_source": "unavailable",
            "estimated_cost_usd": None,
            "candidate_claim_count": 0,
            "candidate_claim_type_counts": {},
            "candidate_claim_types": [],
            "candidate_epistemic_status_counts": {},
            "candidate_reference_counts": {
                "evidence": 0,
                "verification": 0,
                "error": 0,
                "recovery": 0,
            },
            "candidate_semantic_key_hashes": [],
            "valid_claim_count": 0,
            "rejected_claim_count": 0,
            "valid_claim_type_counts": {},
            "rejected_claim_type_counts": {},
            "valid_claim_types": [],
            "rejected_claim_types": [],
            "validator_issue_code_counts": {},
            "value_reason_codes": [],
            "value_durable_signal_codes": [],
            "accepted_claim_type_counts": {},
            "accepted_claim_types": [],
            "expected_claim_count": len(case["expected_claims"]),
            "matched_claim_count": 0,
            "matched_expected_claim_count": 0,
            "false_positive_claim_count": 0,
            "false_negative_claim_count": len(case["expected_claims"]),
            "capture_recorded": False,
        }
        if not execute:
            record["fallback_reason"] = "dry_run"
            records.append(record)
            continue
        if not eligibility.eligible:
            record["fallback_reason"] = eligibility.reason_codes[0]
            records.append(record)
            continue
        if called >= max_calls:
            record["fallback_reason"] = "max_calls_reached"
            records.append(record)
            continue
        if last_call_at is not None and delay_seconds > 0:
            time.sleep(max(0.0, delay_seconds - (time.monotonic() - last_call_at)))
        if observed_client is not None:
            observed_client.last_response = None
        attempt = synthesizer.attempt(case["task_description"], evidence)
        provider_called = attempt.failure_code not in {
            "input_truncated",
            "input_safety_rejected",
            "input_envelope_error",
        }
        if provider_called:
            last_call_at = time.monotonic()
            called += 1
        record.update(
            {
                "called": provider_called,
                "parser_success": attempt.success,
                "parser_failure_code": (
                    _safe_id(attempt.failure_code) if attempt.failure_code else None
                ),
                "parser_failure_detail_code": (
                    _safe_id(attempt.failure_detail_code)
                    if attempt.failure_detail_code
                    else None
                ),
                "fallback_reason": attempt.failure_code,
                "latency_ms": attempt.latency_ms,
                "input_tokens": attempt.input_tokens,
                "output_tokens": attempt.output_tokens,
                "cache_read_tokens": attempt.cache_read_tokens,
                "cache_creation_tokens": attempt.cache_creation_tokens,
                "usage_source": attempt.usage_source,
                "estimated_cost_usd": attempt.estimated_cost_usd,
                "invalid_evidence_references": int(
                    attempt.failure_code
                    in {
                        "invalid_evidence_id",
                        "invalid_verification_id",
                        "invalid_error_id",
                        "invalid_recovery_id",
                    }
                ),
            }
        )
        if capture_writer is not None and provider_called:
            record["capture_recorded"] = bool(
                capture_writer.record(
                    case_id=case["case_id"],
                    task_identifier=reflection_task_identifier(
                        case["task_description"]
                    ),
                    model=model,
                    provider=provider,
                    prompt_version=prompt_version,
                    response=(
                        observed_client.last_response
                        if observed_client is not None
                        else None
                    ),
                    attempt=attempt,
                )
            )
        if attempt.success and attempt.candidate is not None:
            candidate_structure = _claim_structure(attempt.candidate.claims)
            llm_validation = validator.validate(attempt.candidate, evidence)
            llm_value = value_gate.evaluate(
                attempt.candidate, llm_validation, evidence
            )
            accepted_ids = set(llm_value.accepted_claim_ids)
            persistable = [
                claim
                for claim in llm_validation.valid_claims
                if llm_value.accepted
                and claim.claim_id in accepted_ids
                and assess_memory_safety(
                    claim.statement, source="reflection_llm_pilot"
                ).allowed
            ]
            llm_actual = [claim.to_dict() for claim in llm_validation.valid_claims]
            llm_match, llm_expected, _, _ = _match_structured_claims(
                case["expected_claims"], llm_actual
            )
            issue_codes = [issue.code for issue in llm_validation.issues]
            valid_structure = _claim_structure(llm_validation.valid_claims)
            rejected_structure = _claim_structure(llm_validation.rejected_claims)
            invalid_codes = {
                "invalid_evidence_reference",
                "invalid_verification_reference",
                "invalid_error_reference",
                "invalid_recovery_reference",
            }
            expected_roots = sum(
                claim.get("claim_type") == "root_cause"
                for claim in case["expected_claims"]
            )
            candidate_roots = sum(
                claim.claim_type == "root_cause" for claim in attempt.candidate.claims
            )
            persistable_roots = sum(
                claim.claim_type == "root_cause" for claim in persistable
            )
            forbidden_count = 0
            for forbidden in case["forbidden_claims"]:
                required = [str(term).lower() for term in forbidden.get("required_terms", [])]
                forbidden_count += sum(
                    all(term in claim.statement.lower() for term in required)
                    for claim in persistable
                )
            accepted_intersection = accepted_ids.intersection(
                claim.claim_id for claim in llm_validation.valid_claims
            )
            accepted_claims = [
                claim
                for claim in llm_validation.valid_claims
                if claim.claim_id in accepted_intersection
            ]
            if not llm_validation.valid_claims:
                fallback = "all_llm_claims_rejected"
            elif not llm_value.accepted:
                fallback = "llm_value_rejected"
            elif not accepted_intersection:
                fallback = "no_accepted_llm_claims"
            else:
                fallback = None
                record["production_source"] = "llm"
            record.update(
                {
                    "fallback_reason": fallback,
                    "llm_value_accepted": llm_value.accepted,
                    "value_accepted": llm_value.accepted,
                    "llm_validator": llm_match,
                    "candidate_claim_count": candidate_structure["claim_count"],
                    "candidate_claim_type_counts": candidate_structure[
                        "claim_type_counts"
                    ],
                    "candidate_claim_types": sorted(
                        candidate_structure["claim_type_counts"]
                    ),
                    "candidate_epistemic_status_counts": candidate_structure[
                        "epistemic_status_counts"
                    ],
                    "candidate_reference_counts": candidate_structure[
                        "reference_counts"
                    ],
                    "candidate_semantic_key_hashes": candidate_structure[
                        "semantic_key_hashes"
                    ],
                    "valid_claim_count": valid_structure["claim_count"],
                    "rejected_claim_count": rejected_structure["claim_count"],
                    "valid_claim_type_counts": valid_structure[
                        "claim_type_counts"
                    ],
                    "rejected_claim_type_counts": rejected_structure[
                        "claim_type_counts"
                    ],
                    "valid_claim_types": sorted(
                        valid_structure["claim_type_counts"]
                    ),
                    "rejected_claim_types": sorted(
                        rejected_structure["claim_type_counts"]
                    ),
                    "validator_issue_code_counts": dict(
                        sorted(Counter(issue_codes).items())
                    ),
                    "value_reason_codes": [
                        _safe_id(code) for code in llm_value.reason_codes
                    ],
                    "value_durable_signal_codes": [
                        _safe_id(code) for code in llm_value.durable_signals
                    ],
                    "accepted_claim_type_counts": _claim_structure(
                        accepted_claims
                    )["claim_type_counts"],
                    "accepted_claim_types": sorted(
                        {claim.claim_type for claim in accepted_claims}
                    ),
                    "expected_claim_count": llm_match["true_positives"]
                    + llm_match["false_negatives"],
                    "matched_claim_count": llm_match["true_positives"],
                    "matched_expected_claim_count": llm_match[
                        "true_positives"
                    ],
                    "false_positive_claim_count": llm_match["false_positives"],
                    "false_negative_claim_count": llm_match["false_negatives"],
                    "invalid_evidence_references": sum(
                        code in invalid_codes for code in issue_codes
                    ),
                    "epistemic_mismatches": issue_codes.count(
                        "epistemic_status_overclaim"
                    ),
                    "root_cause_candidate_overclaim": max(
                        0, candidate_roots - expected_roots
                    ),
                    "root_cause_overclaim": max(
                        0, persistable_roots - expected_roots
                    ),
                    "forbidden_accepted_claims": forbidden_count,
                    "rule_only_expected": sorted(rule_expected - llm_expected),
                    "llm_only_expected": sorted(llm_expected - rule_expected),
                    "value_false_write": bool(
                        llm_value.accepted and not case["should_write_memory"]
                    ),
                }
            )
        records.append(record)

    persistence = _validate_temporary_persistence(selected) if validate_persistence else None
    return _aggregate_report(
        records,
        model=_safe_id(model),
        provider=_safe_id(provider),
        execute=execute,
        selected_case_count=len(selected),
        max_calls=max_calls,
        seed=seed,
        persistence=persistence,
        prompt_version=prompt_version,
        pilot_kind=pilot_kind,
    )


def _validate_temporary_persistence(cases: list[dict[str, Any]]) -> dict[str, Any]:
    from minicode.agent_reflection import ReflectionEngine
    from minicode.memory import MemoryManager, MemoryScope

    attempted = 0
    persisted = 0
    with tempfile.TemporaryDirectory(prefix="minicode-reflection-pilot-") as directory:
        manager = MemoryManager(project_root=Path(directory))
        engine = ReflectionEngine(
            memory_manager=manager,
            persist_reflections=True,
        )
        for case in cases[:3]:
            attempted += 1
            engine.reflect(case["task_description"], case["trace"])
        persisted = len(manager.memories[MemoryScope.PROJECT].entries)
    return {
        "temporary_only": True,
        "attempted": attempted,
        "persisted": persisted,
    }


def _aggregate_report(
    records: list[dict[str, Any]],
    *,
    model: str,
    provider: str,
    execute: bool,
    selected_case_count: int,
    max_calls: int,
    seed: str,
    persistence: dict[str, Any] | None,
    prompt_version: str,
    pilot_kind: str | None,
) -> dict[str, Any]:
    from minicode.reflection_llm import (
        reflection_output_schema_version,
        reflection_prompt_hash,
    )

    called = [record for record in records if record["called"]]
    parsed = [record for record in called if record["parser_success"]]
    llm_values = [
        record for record in parsed if record["llm_value_accepted"] is not None
    ]
    validator = _rate(
        sum(int(record["llm_validator"]["true_positives"]) for record in parsed),
        sum(int(record["llm_validator"]["false_positives"]) for record in parsed),
        sum(int(record["llm_validator"]["false_negatives"]) for record in parsed),
    )
    value_tp = sum(
        bool(record.get("llm_value_accepted"))
        and bool(record.get("expected_memory_write"))
        for record in llm_values
    )
    value_fp = sum(
        bool(record.get("llm_value_accepted"))
        and not bool(record.get("expected_memory_write"))
        for record in llm_values
    )
    value_fn = sum(
        not bool(record.get("llm_value_accepted"))
        and bool(record.get("expected_memory_write"))
        for record in llm_values
    )
    latencies = [float(record["latency_ms"]) for record in called]
    skip_reasons = Counter(
        str(record["fallback_reason"])
        for record in records
        if not record["called"] and record.get("fallback_reason")
    )
    fallback_reasons = Counter(
        str(record["fallback_reason"])
        for record in called
        if record.get("fallback_reason")
    )
    usage_sources = Counter(str(record["usage_source"]) for record in called)
    eligibility_reasons: Counter[str] = Counter()
    parser_failure_codes: Counter[str] = Counter()
    parser_failure_detail_codes: Counter[str] = Counter()
    candidate_claim_types: Counter[str] = Counter()
    candidate_epistemic_statuses: Counter[str] = Counter()
    validator_issue_codes: Counter[str] = Counter()
    value_reason_codes: Counter[str] = Counter()
    value_durable_signal_codes: Counter[str] = Counter()
    accepted_claim_types: Counter[str] = Counter()
    candidate_reference_counts: Counter[str] = Counter()
    for record in records:
        eligibility_reasons.update(record["eligibility_reason_codes"])
        if record.get("parser_failure_code"):
            parser_failure_codes.update([record["parser_failure_code"]])
        if record.get("parser_failure_detail_code"):
            parser_failure_detail_codes.update(
                [record["parser_failure_detail_code"]]
            )
        candidate_claim_types.update(record["candidate_claim_type_counts"])
        candidate_epistemic_statuses.update(
            record["candidate_epistemic_status_counts"]
        )
        validator_issue_codes.update(record["validator_issue_code_counts"])
        value_reason_codes.update(record["value_reason_codes"])
        value_durable_signal_codes.update(record["value_durable_signal_codes"])
        accepted_claim_types.update(record["accepted_claim_type_counts"])
        candidate_reference_counts.update(record["candidate_reference_counts"])
    llm_only = [
        record["case_id"] for record in parsed if record.get("llm_only_expected")
    ]
    rule_only = [
        record["case_id"] for record in parsed if record.get("rule_only_expected")
    ]
    semantic_key_failure_count = parser_failure_codes.get(
        "invalid_semantic_key", 0
    )
    schema_failure_count = sum(
        count
        for code, count in parser_failure_codes.items()
        if code not in _NON_SCHEMA_FAILURE_CODES
    )
    all_claims_rejected_count = sum(
        record.get("fallback_reason") == "all_llm_claims_rejected"
        for record in parsed
    )
    report_kind = pilot_kind or ("real_provider_holdout" if execute else "dry_run")
    return {
        "schema_version": 1,
        "pilot_kind": _safe_id(report_kind),
        "model": model,
        "provider": provider,
        "prompt_version": _safe_id(prompt_version),
        "prompt_version_hash": reflection_prompt_hash(prompt_version),
        "output_schema_version": reflection_output_schema_version(prompt_version),
        "selected_case_count": selected_case_count,
        "max_calls": max_calls,
        "seed": _safe_id(seed),
        "call_count": len(called),
        "eligible_count": sum(record["eligible"] for record in records),
        "eligibility_rate": (
            sum(record["eligible"] for record in records) / len(records)
            if records
            else 0.0
        ),
        "eligibility_reason_counts": dict(sorted(eligibility_reasons.items())),
        "skip_count": sum(skip_reasons.values()),
        "skip_reasons": dict(sorted(skip_reasons.items())),
        "parser_success_rate": len(parsed) / len(called) if called else 0.0,
        "parser_failure_codes": dict(sorted(parser_failure_codes.items())),
        "parser_failure_detail_codes": dict(
            sorted(parser_failure_detail_codes.items())
        ),
        "semantic_key_failure_count": semantic_key_failure_count,
        "semantic_key_failure_rate": (
            semantic_key_failure_count / len(called) if called else 0.0
        ),
        "schema_failure_count": schema_failure_count,
        "schema_failure_rate": (
            schema_failure_count / len(called) if called else 0.0
        ),
        "validator_claim_quality": validator,
        "all_claims_rejected_count": all_claims_rejected_count,
        "all_claims_rejected_rate": (
            all_claims_rejected_count / len(parsed) if parsed else 0.0
        ),
        "candidate_claim_type_counts": dict(sorted(candidate_claim_types.items())),
        "candidate_epistemic_status_counts": dict(
            sorted(candidate_epistemic_statuses.items())
        ),
        "candidate_reference_counts": dict(
            sorted(candidate_reference_counts.items())
        ),
        "validator_issue_code_counts": dict(
            sorted(validator_issue_codes.items())
        ),
        "value_reason_code_counts": dict(sorted(value_reason_codes.items())),
        "value_durable_signal_code_counts": dict(
            sorted(value_durable_signal_codes.items())
        ),
        "accepted_claim_type_counts": dict(sorted(accepted_claim_types.items())),
        "value_quality": _rate(value_tp, value_fp, value_fn),
        "low_value_false_write_count": value_fp,
        "low_value_false_write_rate": (
            value_fp
            / sum(
                not bool(record.get("expected_memory_write"))
                for record in llm_values
            )
            if any(
                not bool(record.get("expected_memory_write"))
                for record in llm_values
            )
            else 0.0
        ),
        "invalid_evidence_references": sum(
            record["invalid_evidence_references"] for record in records
        ),
        "epistemic_mismatches": sum(
            record["epistemic_mismatches"] for record in records
        ),
        "root_cause_candidate_overclaim": sum(
            record["root_cause_candidate_overclaim"] for record in records
        ),
        "root_cause_overclaim": sum(record["root_cause_overclaim"] for record in records),
        "forbidden_accepted_claims": sum(
            record["forbidden_accepted_claims"] for record in records
        ),
        "fallback_rate": sum(fallback_reasons.values()) / len(called) if called else 0.0,
        "fallback_reasons": dict(sorted(fallback_reasons.items())),
        "timeout_count": fallback_reasons.get("provider_timeout", 0),
        "timeout_rate": (
            fallback_reasons.get("provider_timeout", 0) / len(called)
            if called
            else 0.0
        ),
        "latency_ms": {
            "average": statistics.fmean(latencies) if latencies else 0.0,
            "median": statistics.median(latencies) if latencies else 0.0,
            "p95": _p95(latencies),
        },
        "usage_sources": dict(sorted(usage_sources.items())),
        "tokens": {
            "input": sum(int(record["input_tokens"] or 0) for record in called),
            "output": sum(int(record["output_tokens"] or 0) for record in called),
            "cache_read": sum(int(record["cache_read_tokens"] or 0) for record in called),
            "cache_creation": sum(
                int(record["cache_creation_tokens"] or 0) for record in called
            ),
        },
        "estimated_cost_usd": sum(
            float(record["estimated_cost_usd"] or 0.0) for record in called
        ),
        "production_sources": dict(
            sorted(Counter(record["production_source"] for record in records).items())
        ),
        "negative_sample_count": sum(
            not bool(record.get("expected_memory_write")) for record in called
        ),
        "capture_record_count": sum(
            bool(record.get("capture_recorded")) for record in records
        ),
        "llm_only_correct_cases": llm_only,
        "rule_only_correct_cases": rule_only,
        "blocked_cases": [
            record["case_id"]
            for record in called
            if record.get("fallback_reason")
        ],
        "validator_blocked_cases": [
            record["case_id"]
            for record in called
            if record.get("fallback_reason") == "all_llm_claims_rejected"
        ],
        "sample_insufficient": len(called) < 30,
        "temporary_persistence_validation": persistence,
        "cases": records,
        "limitations": [
            "At most ten remote calls are permitted per invocation.",
            "Holdout scripted provider responses are ignored.",
            "No formal memory is written; optional persistence validation uses a temporary directory.",
            "A pilot with fewer than 30 calls is directional and not statistically sufficient.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    claim = report["validator_claim_quality"]
    value = report["value_quality"]
    latency = report["latency_ms"]
    lines = [
            "# Reflection LLM Real-Provider Pilot",
            "",
            f"- Kind/model/provider: **{report['pilot_kind']} / {report['model']} / {report['provider']}**",
            f"- Prompt/schema: **{report['prompt_version']} / {report['output_schema_version']}**",
            f"- Selected / eligible / called: **{report['selected_case_count']} / {report['eligible_count']} / {report['call_count']}**",
            f"- Parser success: **{report['parser_success_rate'] * 100:.1f}%**",
            f"- Semantic-key failures: **{report['semantic_key_failure_count']} ({report['semantic_key_failure_rate'] * 100:.1f}%)**",
            f"- Validator P/R/F1: **{claim['precision'] * 100:.1f}% / {claim['recall'] * 100:.1f}% / {claim['f1'] * 100:.1f}%**",
            f"- All-claims-rejected: **{report['all_claims_rejected_count']} ({report['all_claims_rejected_rate'] * 100:.1f}%)**",
            f"- Value P/R/F1: **{value['precision'] * 100:.1f}% / {value['recall'] * 100:.1f}% / {value['f1'] * 100:.1f}%**",
            f"- Low-value false writes / invalid references / epistemic mismatches: **{report['low_value_false_write_count']} / {report['invalid_evidence_references']} / {report['epistemic_mismatches']}**",
            f"- Root-cause candidate/final overclaim: **{report['root_cause_candidate_overclaim']} / {report['root_cause_overclaim']}**",
            f"- Latency average/median/P95: **{latency['average']:.1f} / {latency['median']:.1f} / {latency['p95']:.1f} ms**",
            f"- Usage sources: **{json.dumps(report['usage_sources'], sort_keys=True)}**",
            f"- Tokens / estimated cost: **{json.dumps(report['tokens'], sort_keys=True)} / ${report['estimated_cost_usd']:.6f}**",
            f"- Fallbacks: **{json.dumps(report['fallback_reasons'], sort_keys=True)}**",
            f"- Sample insufficient: **{report['sample_insufficient']}**",
            "",
            "## Structural Diagnostics",
            "",
            f"- Parser details: `{json.dumps(report['parser_failure_detail_codes'], sort_keys=True)}`",
            f"- Candidate types: `{json.dumps(report['candidate_claim_type_counts'], sort_keys=True)}`",
            f"- Candidate statuses: `{json.dumps(report['candidate_epistemic_status_counts'], sort_keys=True)}`",
            f"- Validator issues: `{json.dumps(report['validator_issue_code_counts'], sort_keys=True)}`",
            f"- Value reasons: `{json.dumps(report['value_reason_code_counts'], sort_keys=True)}`",
            f"- Durable signals: `{json.dumps(report['value_durable_signal_code_counts'], sort_keys=True)}`",
            f"- Accepted types: `{json.dumps(report['accepted_claim_type_counts'], sort_keys=True)}`",
            "",
            "## Per-Case Diagnostics",
            "",
            "| Case | Parser / detail | Candidate types | Valid/rejected types | References | Key hashes | Issues | Value/reasons/signals/accepted types | Match TP/FP/FN |",
            "|---|---|---|---|---|---|---|---|---:|",
    ]
    for case in report["cases"]:
        parser_result = "ok" if case["parser_success"] else (
            case.get("parser_failure_code") or "not-called"
        )
        if case.get("parser_failure_detail_code"):
            parser_result += f" / {case['parser_failure_detail_code']}"
        value_summary = ",".join(case["value_reason_codes"]) or "-"
        signals = ",".join(case["value_durable_signal_codes"]) or "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{case['case_id']}`",
                    f"`{parser_result}`",
                    f"`{json.dumps(case['candidate_claim_type_counts'], sort_keys=True)}`",
                    (
                        f"{case['valid_claim_count']} {case['valid_claim_types']} / "
                        f"{case['rejected_claim_count']} {case['rejected_claim_types']}"
                    ),
                    f"`{json.dumps(case['candidate_reference_counts'], sort_keys=True)}`",
                    f"`{','.join(case['candidate_semantic_key_hashes']) or '-'}`",
                    f"`{json.dumps(case['validator_issue_code_counts'], sort_keys=True)}`",
                    (
                        f"`{case['value_accepted']}` / `{value_summary}` / "
                        f"`{signals}` / `{','.join(case['accepted_claim_types']) or '-'}`"
                    ),
                    (
                        f"{case['matched_claim_count']}/"
                        f"{case['false_positive_claim_count']}/"
                        f"{case['false_negative_claim_count']}"
                    ),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "tests" / "fixtures" / "reflection_llm_holdout",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--seed", default="pilot-v1")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--delay-seconds", type=float, default=0.5)
    parser.add_argument("--validate-persistence", action="store_true")
    parser.add_argument(
        "--prompt-version",
        choices=(
            "baseline",
            "calibrated",
            "calibrated_verbose",
            "calibrated_compact",
        ),
        default=None,
    )
    parser.add_argument("--capture-synthetic-responses", action="store_true")
    parser.add_argument("--capture-path", type=Path)
    parser.add_argument("--replay-responses", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "reflection-deepseek-pilot.json",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=PROJECT_ROOT / "docs" / "reflection-deepseek-pilot.md",
    )
    args = parser.parse_args(argv)
    if not 0 <= args.max_calls <= ABSOLUTE_MAX_CALLS:
        parser.error(f"--max-calls must be between 0 and {ABSOLUTE_MAX_CALLS}")
    if args.execute and not args.allow_remote:
        parser.error("--execute requires explicit --allow-remote")
    if args.capture_synthetic_responses and not args.execute:
        parser.error("--capture-synthetic-responses requires --execute")
    if args.capture_synthetic_responses and args.capture_path is None:
        parser.error("--capture-synthetic-responses requires --capture-path")
    if args.capture_path is not None and not args.capture_synthetic_responses:
        parser.error("--capture-path requires --capture-synthetic-responses")
    if args.replay_responses and (
        args.execute or args.allow_remote or args.capture_synthetic_responses
    ):
        parser.error("--replay-responses cannot be combined with remote execution")
    if args.replay_responses and args.validate_persistence:
        parser.error("replay cannot run persistence validation")

    from scripts.reflection_llm_evaluator import load_holdout_dataset

    cases = load_holdout_dataset(args.dataset)
    client = None
    unavailable_reason = "dry_run"
    model = args.model or "configured-reflection-model"
    provider = "unavailable"
    prompt_version = args.prompt_version or "calibrated"
    capture_writer = None
    requested_case_ids = set(args.case_id) or None
    pilot_kind = None
    execute_run = args.execute
    if args.replay_responses:
        from minicode.reflection_replay import (
            ReplayStructuredGenerationClient,
            load_synthetic_response_capture,
        )

        replay_records = load_synthetic_response_capture(args.replay_responses)
        if not replay_records:
            parser.error("--replay-responses contains no valid capture records")
        versions = {
            str(record.get("prompt_version"))
            for record in replay_records
            if record.get("prompt_version")
            in {
                "baseline",
                "calibrated",
                "calibrated_verbose",
                "calibrated_compact",
            }
        }
        if args.prompt_version is None:
            if len(versions) != 1:
                parser.error("replay records require one explicit prompt version")
            prompt_version = next(iter(versions))
        client = ReplayStructuredGenerationClient(replay_records)
        execute_run = True
        pilot_kind = "synthetic_replay"
        unavailable_reason = "replay_response_unavailable"
        provider = "synthetic-replay"
        model = args.model or _safe_id(replay_records[0].get("model"))
        if requested_case_ids is None:
            requested_case_ids = {
                str(record["case_id"])
                for record in replay_records
                if record.get("case_id")
            }
    elif args.execute:
        from minicode.config import load_runtime_config
        from minicode.model_registry import build_provider_config
        from minicode.reflection_llm import (
            ReflectionLLMConfig,
            create_structured_generation_client,
        )

        runtime = load_runtime_config(PROJECT_ROOT)
        model = args.model or str(
            runtime.get("reflectionModel") or runtime.get("model") or ""
        )
        runtime = dict(runtime)
        runtime.update(
            {
                "reflectionSynthesizerMode": "llm_shadow",
                "reflectionModel": model,
                "reflectionLLMTimeoutSeconds": max(1.0, min(30.0, args.timeout)),
                "allowRemoteReflectionModel": True,
                "reflectionPromptVersion": prompt_version,
            }
        )
        config = ReflectionLLMConfig.from_runtime(runtime)
        factory = create_structured_generation_client(runtime, config)
        client = factory.client
        unavailable_reason = factory.unavailable_reason or "reflection_client_unavailable"
        provider = build_provider_config(model, runtime).provider.value
        if args.capture_synthetic_responses:
            from minicode.reflection_replay import SyntheticResponseCaptureWriter

            capture_writer = SyntheticResponseCaptureWriter(
                args.capture_path,
                dataset_root=args.dataset,
                max_records=ABSOLUTE_MAX_CALLS,
            )

    report = run_pilot(
        cases,
        execute=execute_run,
        client=client,
        max_calls=args.max_calls,
        seed=args.seed,
        case_ids=requested_case_ids,
        model=model,
        provider=provider,
        unavailable_reason=unavailable_reason,
        delay_seconds=max(0.0, args.delay_seconds),
        validate_persistence=args.validate_persistence,
        timeout_seconds=args.timeout,
        prompt_version=prompt_version,
        capture_writer=capture_writer,
        pilot_kind=pilot_kind,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "kind": report["pilot_kind"],
                "calls": report["call_count"],
                "output": str(args.output),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
