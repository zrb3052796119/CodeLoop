from __future__ import annotations

import json

from scripts.analyze_reflection_deepseek_calibration import (
    build_calibration_report,
    render_adjudication_markdown,
    render_calibration_markdown,
)


CASE_IDS = [
    "holdout-causal-trap-025",
    "holdout-partial-recovery-008",
    "holdout-provider-fallback-033",
    "holdout-redacted-secret-error-028",
    "holdout-timeout-fallback-032",
    "holdout-unverified-recovery-024",
    "holdout-verified-recovery-007",
]


def _case(case_id: str, *, parser_success: bool) -> dict:
    return {
        "case_id": case_id,
        "parser_success": parser_success,
        "parser_failure_code": None if parser_success else "invalid_semantic_key",
        "parser_failure_detail_code": (
            None if parser_success else "semantic_key_contains_hyphen"
        ),
        "candidate_claim_type_counts": {"error_pattern": int(parser_success)},
        "candidate_epistemic_status_counts": {"confirmed": int(parser_success)},
        "candidate_reference_counts": {
            "evidence": int(parser_success),
            "verification": 0,
            "error": int(parser_success),
            "recovery": 0,
        },
        "valid_claim_count": int(parser_success),
        "rejected_claim_count": 0,
        "validator_issue_code_counts": {},
        "llm_value_accepted": parser_success,
        "value_reason_codes": ["accepted_durable_reflection"] if parser_success else [],
        "value_durable_signal_codes": ["reusable_error_pattern"] if parser_success else [],
        "expected_claim_count": 1,
        "matched_claim_count": int(parser_success),
        "false_positive_claim_count": 0,
        "false_negative_claim_count": int(not parser_success),
        "expected_memory_write": True,
        "production_source": "llm" if parser_success else "rule",
        "fallback_reason": None if parser_success else "invalid_semantic_key",
    }


def _report(
    *,
    parser_success: bool,
    validator_precision: float,
    negative_samples: int,
) -> dict:
    call_count = len(CASE_IDS)
    semantic_failures = 0 if parser_success else call_count
    return {
        "cases": [_case(case_id, parser_success=parser_success) for case_id in CASE_IDS],
        "call_count": call_count,
        "parser_success_rate": float(parser_success),
        "schema_failure_count": semantic_failures,
        "schema_failure_rate": semantic_failures / call_count,
        "semantic_key_failure_count": semantic_failures,
        "semantic_key_failure_rate": semantic_failures / call_count,
        "parser_failure_codes": (
            {} if parser_success else {"invalid_semantic_key": semantic_failures}
        ),
        "parser_failure_detail_codes": (
            {}
            if parser_success
            else {"semantic_key_contains_hyphen": semantic_failures}
        ),
        "validator_claim_quality": {
            "precision": validator_precision,
            "recall": 1.0,
            "f1": validator_precision,
            "true_positives": call_count,
            "false_positives": 0,
            "false_negatives": 0,
        },
        "all_claims_rejected_count": 0,
        "all_claims_rejected_rate": 0.0,
        "validator_issue_code_counts": {},
        "value_quality": {
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
            "true_positives": call_count,
            "false_positives": 0,
            "false_negatives": 0,
        },
        "low_value_false_write_count": 0,
        "invalid_evidence_references": 0,
        "epistemic_mismatches": 0,
        "root_cause_candidate_overclaim": 0,
        "root_cause_overclaim": 0,
        "forbidden_accepted_claims": 0,
        "candidate_claim_type_counts": {"error_pattern": call_count},
        "candidate_epistemic_status_counts": {"confirmed": call_count},
        "candidate_reference_counts": {
            "evidence": call_count,
            "verification": 0,
            "error": call_count,
            "recovery": 0,
        },
        "value_reason_code_counts": {"accepted_durable_reflection": call_count},
        "value_durable_signal_code_counts": {"reusable_error_pattern": call_count},
        "accepted_claim_type_counts": {"error_pattern": call_count},
        "rule_only_correct_cases": [],
        "llm_only_correct_cases": [],
        "prompt_version": "calibrated",
        "prompt_version_hash": "a" * 64,
        "output_schema_version": "b" * 64,
        "selected_case_count": 10,
        "eligible_count": 10,
        "negative_sample_count": negative_samples,
        "skip_reasons": {"input_safety_rejected": 2},
        "usage_sources": {"provider": call_count},
        "tokens": {
            "input": 100,
            "output": 20,
            "cache_read": 10,
            "cache_creation": 0,
        },
        "estimated_cost_usd": 0.001,
        "latency_ms": {"average": 10.0, "median": 10.0, "p95": 12.0},
    }


def _build(*, validator_precision: float, negative_samples: int) -> dict:
    baseline = _report(
        parser_success=False,
        validator_precision=0.0,
        negative_samples=negative_samples,
    )
    calibrated = _report(
        parser_success=True,
        validator_precision=validator_precision,
        negative_samples=negative_samples,
    )
    return build_calibration_report(
        baseline_pilot=baseline,
        baseline_replay=baseline,
        intermediate_pilot=calibrated,
        calibrated_pilot=calibrated,
        calibrated_replay=calibrated,
    )


def test_calibration_gate_fails_for_small_negative_sample_and_low_precision() -> None:
    report = _build(validator_precision=0.33, negative_samples=2)

    assert report["expansion_gate"]["passed"] is False
    assert report["expansion_gate"]["recommendation"] == "do_not_expand_shadow"
    assert set(report["expansion_gate"]["failed_criteria"]) == {
        "negative_real_samples_at_least_8",
        "validator_precision_at_least_80_percent",
    }


def test_calibration_gate_passes_only_when_every_threshold_passes() -> None:
    report = _build(validator_precision=0.80, negative_samples=8)

    assert report["expansion_gate"]["passed"] is True
    assert report["expansion_gate"]["failed_criteria"] == []


def test_calibration_report_contains_only_structural_case_diagnostics() -> None:
    report = _build(validator_precision=0.80, negative_samples=8)
    serialized = json.dumps(report)

    assert "statement" not in serialized
    assert "task_description" not in serialized
    assert '"semantic_key":' not in serialized


def test_calibration_markdown_and_adjudication_render_required_decisions() -> None:
    report = _build(validator_precision=0.33, negative_samples=2)

    comparison = render_calibration_markdown(report)
    adjudication = render_adjudication_markdown(report)

    assert "Parser success" in comparison
    assert "do_not_expand_shadow" in comparison
    assert "No parser repair" in adjudication
    assert "unverified_recovery_context" in adjudication
