from __future__ import annotations

import copy
import json
from pathlib import Path

from minicode.reflection_evidence import TraceEvidenceExtractor
from minicode.reflection_llm import (
    ReflectionLLMEligibilityGate,
    get_reflection_output_schema,
)
from scripts.reflection_claim_precision_evaluator import (
    _evaluate_case,
    evaluate_precision,
    load_precision_dataset,
)


HOLDOUT = Path(__file__).parent / "fixtures" / "reflection_claim_precision_holdout"


def _dataset() -> tuple[dict, list[dict]]:
    return load_precision_dataset(HOLDOUT)


def test_precision_holdout_has_balanced_required_size() -> None:
    _manifest, cases = _dataset()

    assert len(cases) == 22
    assert sum(not case["should_write_memory"] for case in cases) == 10
    assert sum(case["should_write_memory"] for case in cases) == 12


def test_every_precision_case_has_manual_claim_annotations() -> None:
    _manifest, cases = _dataset()

    for case in cases:
        assert "primary_claims" in case
        assert "secondary_allowed_claims" in case
        assert "expected_rule_behavior" in case
        assert "expected_gap_fill_behavior" in case
        assert "evidence_chain_ids" in case
        assert case["allowed_claim_count"] >= 0


def test_precision_holdout_has_eight_provider_eligible_negatives() -> None:
    _manifest, cases = _dataset()
    eligible = []
    for case in cases:
        evidence = TraceEvidenceExtractor().extract(
            case["task_description"], case["trace"]
        )
        decision = ReflectionLLMEligibilityGate().evaluate(
            evidence,
            model_call_allowed=True,
        )
        if not case["should_write_memory"] and decision.eligible:
            eligible.append(case["case_id"])

    assert len(eligible) == 8


def test_real_ab_selection_has_fixed_fifteen_same_cases() -> None:
    manifest, cases = _dataset()

    assert len(manifest["real_ab_case_ids"]) == 15
    assert set(manifest["real_ab_case_ids"]) <= {
        case["case_id"] for case in cases
    }


def test_real_ab_selection_contains_eight_negatives() -> None:
    manifest, cases = _dataset()
    selected = {
        case["case_id"]: case
        for case in cases
        if case["case_id"] in manifest["real_ab_case_ids"]
    }

    assert sum(not case["should_write_memory"] for case in selected.values()) == 8


def test_real_ab_selection_contains_six_verified_recoveries() -> None:
    manifest, cases = _dataset()
    selected = [
        case for case in cases if case["case_id"] in manifest["real_ab_case_ids"]
    ]

    assert sum("recovery" in case["category"] for case in selected) >= 6


def test_real_ab_selection_contains_four_decision_or_constraint_cases() -> None:
    manifest, cases = _dataset()
    selected = [
        case for case in cases if case["case_id"] in manifest["real_ab_case_ids"]
    ]
    decision_cases = 0
    for case in selected:
        evidence = TraceEvidenceExtractor().extract(
            case["task_description"], case["trace"]
        )
        decision_cases += bool(evidence.decisions)

    assert decision_cases >= 4


def test_real_ab_selection_contains_two_rule_gap_controls() -> None:
    manifest, cases = _dataset()
    selected = [
        case for case in cases if case["case_id"] in manifest["real_ab_case_ids"]
    ]

    assert sum(
        case["expected_gap_fill_behavior"] == "llm_gap_fill"
        for case in selected
    ) >= 2


def test_scripted_precision_evaluation_is_deterministic() -> None:
    first = evaluate_precision(HOLDOUT)
    second = evaluate_precision(HOLDOUT)

    assert first == second


def test_exact_and_adjudicated_metrics_remain_separate_for_split_decision() -> None:
    report = evaluate_precision(HOLDOUT)
    cases = report["arms"]["calibrated_compact_gap_fill"]["cases"]
    case = next(
        item for item in cases if item["case_id"] == "precision-positive-cross-decision-110"
    )

    assert case["final_persistable"]["exact"]["false_positives"] == 2
    assert case["final_persistable"]["exact"]["false_negatives"] == 1
    assert case["final_persistable"]["adjudicated"]["false_positives"] == 0
    assert case["final_persistable"]["adjudicated"]["false_negatives"] == 0


def test_primary_lesson_recall_combines_legal_split_claims() -> None:
    report = evaluate_precision(HOLDOUT)
    cases = report["arms"]["calibrated_compact_gap_fill"]["cases"]
    case = next(
        item for item in cases if item["case_id"] == "precision-positive-cross-decision-110"
    )

    assert case["final_persistable"]["adjudicated"]["primary_lesson_recall"] == 1.0


def test_scripted_gap_fill_counts_at_least_two_correct_rule_gaps() -> None:
    report = evaluate_precision(HOLDOUT)
    metrics = report["arms"]["calibrated_compact_gap_fill"]["metrics"]

    assert metrics["gap_fill_success_count"] >= 2
    assert metrics["gap_fill_false_positive_count"] == 0
    assert metrics["rule_regression_count"] == 0


def test_replace_metric_records_weaker_llm_regression() -> None:
    _manifest, cases = _dataset()
    case = copy.deepcopy(
        next(
            item
            for item in cases
            if item["case_id"] == "precision-positive-verified-recovery-101"
        )
    )
    case["llm_script"] = {
        "kind": "candidate",
        "claims": [
            {
                "claim_type": "error_pattern",
                "semantic_key": "stale_fencing_token_error",
                "statement": "Renewal used a stale fencing token",
                "evidence_ids": ["event-1"],
                "epistemic_status": "confirmed",
                "applies_when": "When lease renewal is attempted.",
                "limitations": [],
                "verification_ids": [],
                "related_error_ids": ["error-000001"],
                "related_recovery_ids": [],
            }
        ],
    }

    result = _evaluate_case(
        case,
        prompt_version="calibrated_compact",
        strategy="replace",
        capture=None,
    )

    assert result["selection_source"] == "llm_replace"
    assert result["replace_regression"] is True
    assert result["rule_regression"] is True


def test_gap_fill_same_weaker_llm_cannot_replace_rule() -> None:
    _manifest, cases = _dataset()
    case = copy.deepcopy(
        next(
            item
            for item in cases
            if item["case_id"] == "precision-positive-verified-recovery-101"
        )
    )
    case["llm_script"]["claims"] = []

    result = _evaluate_case(
        case,
        prompt_version="calibrated_compact",
        strategy="gap_fill",
        capture=None,
    )

    assert result["selection_source"] == "rule"
    assert result["selection_reason"] == "rule_already_durable"
    assert result["rule_regression"] is False


def test_compact_and_verbose_arms_use_identical_schema() -> None:
    assert get_reflection_output_schema(
        "calibrated_verbose"
    ) == get_reflection_output_schema("calibrated_compact")


def test_offline_evaluation_does_not_require_capture_or_network(tmp_path: Path) -> None:
    report = evaluate_precision(HOLDOUT)
    output = tmp_path / "report.json"
    output.write_text(json.dumps(report, sort_keys=True))

    assert report["evaluation_mode"] == "scripted_offline"
    assert output.stat().st_size > 0
