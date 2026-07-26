#!/usr/bin/env python3
"""Compare replay-isolated reflection prompt versions and render calibration reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("cases"), list):
        raise ValueError(f"invalid pilot report: {path}")
    return value


def _quality_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "call_count": int(report["call_count"]),
        "parser_success_rate": float(report["parser_success_rate"]),
        "schema_failure_count": int(report["schema_failure_count"]),
        "schema_failure_rate": float(report["schema_failure_rate"]),
        "semantic_key_failure_count": int(report["semantic_key_failure_count"]),
        "semantic_key_failure_rate": float(report["semantic_key_failure_rate"]),
        "parser_failure_codes": dict(report["parser_failure_codes"]),
        "parser_failure_detail_codes": dict(
            report["parser_failure_detail_codes"]
        ),
        "validator_claim_quality": dict(report["validator_claim_quality"]),
        "all_claims_rejected_count": int(report["all_claims_rejected_count"]),
        "all_claims_rejected_rate": float(report["all_claims_rejected_rate"]),
        "validator_issue_code_counts": dict(
            report["validator_issue_code_counts"]
        ),
        "value_quality": dict(report["value_quality"]),
        "low_value_false_write_count": int(
            report["low_value_false_write_count"]
        ),
        "invalid_evidence_references": int(report["invalid_evidence_references"]),
        "epistemic_mismatches": int(report["epistemic_mismatches"]),
        "root_cause_candidate_overclaim": int(
            report["root_cause_candidate_overclaim"]
        ),
        "root_cause_overclaim": int(report["root_cause_overclaim"]),
        "forbidden_accepted_claims": int(report["forbidden_accepted_claims"]),
        "candidate_claim_type_counts": dict(
            report["candidate_claim_type_counts"]
        ),
        "candidate_epistemic_status_counts": dict(
            report["candidate_epistemic_status_counts"]
        ),
        "candidate_reference_counts": dict(report["candidate_reference_counts"]),
        "value_reason_code_counts": dict(report["value_reason_code_counts"]),
        "value_durable_signal_code_counts": dict(
            report["value_durable_signal_code_counts"]
        ),
        "accepted_claim_type_counts": dict(report["accepted_claim_type_counts"]),
        "rule_only_correct_cases": list(report["rule_only_correct_cases"]),
        "llm_only_correct_cases": list(report["llm_only_correct_cases"]),
    }


def _provider_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt_version": report["prompt_version"],
        "prompt_version_hash": report["prompt_version_hash"],
        "output_schema_version": report["output_schema_version"],
        "selected_case_count": int(report["selected_case_count"]),
        "eligible_count": int(report["eligible_count"]),
        "call_count": int(report["call_count"]),
        "negative_sample_count": int(report["negative_sample_count"]),
        "input_safety_rejected_count": int(
            report["skip_reasons"].get("input_safety_rejected", 0)
        ),
        "usage_sources": dict(report["usage_sources"]),
        "tokens": dict(report["tokens"]),
        "estimated_cost_usd": float(report["estimated_cost_usd"]),
        "latency_ms": dict(report["latency_ms"]),
    }


def _case_snapshot(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "parser_success": bool(case["parser_success"]),
        "parser_failure_code": case.get("parser_failure_code"),
        "parser_failure_detail_code": case.get("parser_failure_detail_code"),
        "candidate_claim_type_counts": dict(case["candidate_claim_type_counts"]),
        "candidate_epistemic_status_counts": dict(
            case["candidate_epistemic_status_counts"]
        ),
        "candidate_reference_counts": dict(case["candidate_reference_counts"]),
        "valid_claim_count": int(case["valid_claim_count"]),
        "rejected_claim_count": int(case["rejected_claim_count"]),
        "validator_issue_code_counts": dict(case["validator_issue_code_counts"]),
        "value_accepted": case["llm_value_accepted"],
        "value_reason_codes": list(case["value_reason_codes"]),
        "durable_signal_codes": list(case["value_durable_signal_codes"]),
        "expected_claim_count": int(case["expected_claim_count"]),
        "matched_claim_count": int(case["matched_claim_count"]),
        "false_positive_claim_count": int(case["false_positive_claim_count"]),
        "false_negative_claim_count": int(case["false_negative_claim_count"]),
        "expected_memory_write": bool(case["expected_memory_write"]),
        "production_source": case["production_source"],
        "fallback_reason": case.get("fallback_reason"),
    }


def build_calibration_report(
    *,
    baseline_pilot: dict[str, Any],
    baseline_replay: dict[str, Any],
    intermediate_pilot: dict[str, Any],
    calibrated_pilot: dict[str, Any],
    calibrated_replay: dict[str, Any],
    local_performance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    baseline_cases = {
        case["case_id"]: case for case in baseline_replay["cases"]
    }
    calibrated_cases = {
        case["case_id"]: case for case in calibrated_replay["cases"]
    }
    case_ids = sorted(set(baseline_cases) | set(calibrated_cases))
    per_case = []
    for case_id in case_ids:
        per_case.append(
            {
                "case_id": case_id,
                "baseline": _case_snapshot(baseline_cases[case_id]),
                "calibrated": _case_snapshot(calibrated_cases[case_id]),
            }
        )

    quality = _quality_snapshot(calibrated_replay)
    validator_precision = float(
        quality["validator_claim_quality"]["precision"]
    )
    criteria = {
        "parser_success_at_least_95_percent": quality[
            "parser_success_rate"
        ]
        >= 0.95,
        "semantic_key_failure_at_most_5_percent": quality[
            "semantic_key_failure_rate"
        ]
        <= 0.05,
        "invalid_evidence_references_zero": quality[
            "invalid_evidence_references"
        ]
        == 0,
        "epistemic_mismatches_zero": quality["epistemic_mismatches"] == 0,
        "final_root_cause_overclaim_zero": quality["root_cause_overclaim"] == 0,
        "low_value_false_writes_zero": quality[
            "low_value_false_write_count"
        ]
        == 0,
        "negative_real_samples_at_least_8": int(
            calibrated_pilot["negative_sample_count"]
        )
        >= 8,
        "validator_precision_at_least_80_percent": validator_precision >= 0.80,
    }
    total_provider_calls = sum(
        int(report["call_count"])
        for report in (baseline_pilot, intermediate_pilot, calibrated_pilot)
    )
    total_cost = sum(
        float(report["estimated_cost_usd"])
        for report in (baseline_pilot, intermediate_pilot, calibrated_pilot)
    )
    baseline_average_input = (
        int(baseline_pilot["tokens"]["input"]) / int(baseline_pilot["call_count"])
        if baseline_pilot["call_count"]
        else 0.0
    )
    calibrated_average_input = (
        int(calibrated_pilot["tokens"]["input"])
        / int(calibrated_pilot["call_count"])
        if calibrated_pilot["call_count"]
        else 0.0
    )
    return {
        "schema_version": 1,
        "experiment": "deepseek_reflection_schema_value_calibration",
        "comparison_method": (
            "Captured synthetic responses replayed through the same final "
            "Parser, Validator, and ValueGate; provider reports supply usage only."
        ),
        "same_case_ids": case_ids,
        "baseline": {
            "provider": _provider_snapshot(baseline_pilot),
            "final_gate_replay": _quality_snapshot(baseline_replay),
        },
        "intermediate_calibrated_pilot": _provider_snapshot(intermediate_pilot),
        "calibrated": {
            "provider": _provider_snapshot(calibrated_pilot),
            "final_gate_replay": quality,
        },
        "provider_budget": {
            "baseline_calls": int(baseline_pilot["call_count"]),
            "intermediate_calls": int(intermediate_pilot["call_count"]),
            "calibrated_calls": int(calibrated_pilot["call_count"]),
            "total_calls": total_provider_calls,
            "allowed_calls": 30,
            "within_budget": total_provider_calls <= 30,
            "total_estimated_cost_usd": total_cost,
        },
        "prompt_input_token_effect": {
            "baseline_average_input_tokens": baseline_average_input,
            "calibrated_average_input_tokens": calibrated_average_input,
            "average_input_token_delta": (
                calibrated_average_input - baseline_average_input
            ),
            "average_input_token_delta_rate": (
                (calibrated_average_input - baseline_average_input)
                / baseline_average_input
                if baseline_average_input
                else 0.0
            ),
            "note": (
                "Same selected provider-response cases; delta includes the "
                "calibrated system prompt and output schema."
            ),
        },
        "adjudicated_metrics": {
            "exact_claim_metrics_preserved": dict(
                quality["validator_claim_quality"]
            ),
            "positive_expected_case_count": 6,
            "exact_expected_case_match_count": 3,
            "adjudicated_expected_case_match_count": 4,
            "exact_expected_case_recall": 0.5,
            "adjudicated_expected_case_recall": 4 / 6,
            "legal_synonym_or_split_claim_cases": [
                "holdout-project-constraint-005"
            ],
            "evaluator_label_disagreement_cases": [
                "holdout-project-constraint-005",
                "holdout-unverified-recovery-024",
                "holdout-partial-recovery-008",
            ],
            "policy_decisions": {
                "holdout-project-constraint-005": (
                    "Two separately grounded constraint/decision claims jointly "
                    "cover the combined expected policy; exact metric remains unchanged."
                ),
                "holdout-unverified-recovery-024": (
                    "The error fact is grounded, but the no-write label is retained "
                    "because failed unverified recovery lacks closed-loop value."
                ),
                "holdout-partial-recovery-008": (
                    "The error fact is grounded, but unknown outcome and unverified "
                    "recovery retain the no-write policy."
                ),
            },
        },
        "local_performance": dict(local_performance or {}),
        "per_case": per_case,
        "expansion_gate": {
            "criteria": criteria,
            "passed": all(criteria.values()),
            "failed_criteria": [
                name for name, passed in criteria.items() if not passed
            ],
            "recommendation": (
                "enter_200_500_shadow"
                if all(criteria.values())
                else "do_not_expand_shadow"
            ),
        },
        "known_limits": [
            "Only eight selected cases produced provider responses per arm; two security cases were rejected before provider invocation.",
            "Only two negative cases reached the provider in the final arm, below the required eight.",
            "Validator precision remains below the expansion threshold even though case-level write decisions improved.",
            "Synthetic holdout evidence does not establish production-distribution quality.",
        ],
    }


def render_calibration_markdown(report: dict[str, Any]) -> str:
    baseline = report["baseline"]["final_gate_replay"]
    calibrated = report["calibrated"]["final_gate_replay"]
    baseline_provider = report["baseline"]["provider"]
    calibrated_provider = report["calibrated"]["provider"]
    budget = report["provider_budget"]
    token_effect = report["prompt_input_token_effect"]
    adjudicated = report["adjudicated_metrics"]
    local_performance = report["local_performance"]
    gate = report["expansion_gate"]
    lines = [
        "# DeepSeek Reflection Schema & Value Calibration",
        "",
        "Quality metrics below come from captured synthetic responses replayed through the same final deterministic gates.",
        "",
        "| Metric | Baseline | Calibrated |",
        "|---|---:|---:|",
        f"| Parser success | {baseline['parser_success_rate'] * 100:.1f}% | {calibrated['parser_success_rate'] * 100:.1f}% |",
        f"| Semantic-key failures | {baseline['semantic_key_failure_count']} | {calibrated['semantic_key_failure_count']} |",
        f"| Validator precision | {baseline['validator_claim_quality']['precision'] * 100:.1f}% | {calibrated['validator_claim_quality']['precision'] * 100:.1f}% |",
        f"| Validator recall | {baseline['validator_claim_quality']['recall'] * 100:.1f}% | {calibrated['validator_claim_quality']['recall'] * 100:.1f}% |",
        f"| All claims rejected | {baseline['all_claims_rejected_count']} | {calibrated['all_claims_rejected_count']} |",
        f"| Value precision | {baseline['value_quality']['precision'] * 100:.1f}% | {calibrated['value_quality']['precision'] * 100:.1f}% |",
        f"| Value recall | {baseline['value_quality']['recall'] * 100:.1f}% | {calibrated['value_quality']['recall'] * 100:.1f}% |",
        f"| Low-value false writes | {baseline['low_value_false_write_count']} | {calibrated['low_value_false_write_count']} |",
        f"| Invalid references | {baseline['invalid_evidence_references']} | {calibrated['invalid_evidence_references']} |",
        f"| Epistemic mismatches | {baseline['epistemic_mismatches']} | {calibrated['epistemic_mismatches']} |",
        f"| Candidate root-cause overclaim | {baseline['root_cause_candidate_overclaim']} | {calibrated['root_cause_candidate_overclaim']} |",
        f"| Final root-cause overclaim | {baseline['root_cause_overclaim']} | {calibrated['root_cause_overclaim']} |",
        "",
        "## Provider Budget",
        "",
        f"- Calls baseline/intermediate/final: **{budget['baseline_calls']} / {budget['intermediate_calls']} / {budget['calibrated_calls']}**",
        f"- Total calls / limit: **{budget['total_calls']} / {budget['allowed_calls']}**",
        f"- Estimated total cost: **${budget['total_estimated_cost_usd']:.6f}**",
        f"- Average input tokens baseline -> calibrated: **{token_effect['baseline_average_input_tokens']:.1f} -> {token_effect['calibrated_average_input_tokens']:.1f}**",
        f"- Average input-token delta: **+{token_effect['average_input_token_delta']:.1f} ({token_effect['average_input_token_delta_rate'] * 100:.1f}%)**",
        "",
        "| Provider metric | Baseline | Calibrated |",
        "|---|---:|---:|",
        f"| Calls | {baseline_provider['call_count']} | {calibrated_provider['call_count']} |",
        f"| Input tokens | {baseline_provider['tokens']['input']} | {calibrated_provider['tokens']['input']} |",
        f"| Output tokens | {baseline_provider['tokens']['output']} | {calibrated_provider['tokens']['output']} |",
        f"| Cache-read tokens | {baseline_provider['tokens']['cache_read']} | {calibrated_provider['tokens']['cache_read']} |",
        f"| Latency avg/median/P95 ms | {baseline_provider['latency_ms']['average']:.1f}/{baseline_provider['latency_ms']['median']:.1f}/{baseline_provider['latency_ms']['p95']:.1f} | {calibrated_provider['latency_ms']['average']:.1f}/{calibrated_provider['latency_ms']['median']:.1f}/{calibrated_provider['latency_ms']['p95']:.1f} |",
        f"| Estimated cost USD | {baseline_provider['estimated_cost_usd']:.6f} | {calibrated_provider['estimated_cost_usd']:.6f} |",
        "",
        "## Adjudicated Metric",
        "",
        f"- Exact expected-case recall: **{adjudicated['exact_expected_case_recall'] * 100:.1f}%**",
        f"- Adjudicated expected-case recall: **{adjudicated['adjudicated_expected_case_recall'] * 100:.1f}%**",
        "- Legal synonym/split-claim case: `holdout-project-constraint-005`; exact claim metrics above remain unchanged.",
        "- Label-policy disagreements retained: `holdout-unverified-recovery-024`, `holdout-partial-recovery-008`.",
        "",
        "## Structural Distributions",
        "",
        f"- Baseline -> calibrated claim types: `{json.dumps(baseline['candidate_claim_type_counts'], sort_keys=True)}` -> `{json.dumps(calibrated['candidate_claim_type_counts'], sort_keys=True)}`",
        f"- Baseline -> calibrated epistemic statuses: `{json.dumps(baseline['candidate_epistemic_status_counts'], sort_keys=True)}` -> `{json.dumps(calibrated['candidate_epistemic_status_counts'], sort_keys=True)}`",
        f"- Calibrated Validator issues: `{json.dumps(calibrated['validator_issue_code_counts'], sort_keys=True)}`",
        f"- Calibrated durable signals: `{json.dumps(calibrated['value_durable_signal_code_counts'], sort_keys=True)}`",
        f"- Rule-only / LLM-only correct cases: `{json.dumps(calibrated['rule_only_correct_cases'])}` / `{json.dumps(calibrated['llm_only_correct_cases'])}`",
        "",
        "## Expansion Gate",
        "",
        f"- Result: **{gate['recommendation']}**",
    ]
    for name, passed in gate["criteria"].items():
        lines.append(f"- `{name}`: **{'PASS' if passed else 'FAIL'}**")
    if local_performance:
        timings = local_performance["milliseconds_per_operation"]
        sizes = local_performance["prompt_schema_size"]
        lines.extend(
            [
                "",
                "## Local Performance",
                "",
                f"- Valid/invalid parse: **{timings['valid_parse']:.4f} / {timings['invalid_parse_with_detail']:.4f} ms/op**",
                f"- Replay Parser+Validator+ValueGate: **{timings['replay_parser_validator_value']:.4f} ms/op**",
                f"- Prompt/schema growth: **+{sizes['prompt_character_delta']} chars / +{sizes['schema_byte_delta']} bytes**",
                "- Network calls: **0**",
            ]
        )
    lines.extend(
        [
            "",
            "## Case Comparison",
            "",
            "| Case | Baseline parser | Calibrated parser | Valid/rejected A -> B | Value A -> B | TP/FP/FN B |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    for item in report["per_case"]:
        baseline_case = item["baseline"]
        calibrated_case = item["calibrated"]
        baseline_parser = (
            "ok"
            if baseline_case["parser_success"]
            else baseline_case["parser_failure_code"]
        )
        calibrated_parser = (
            "ok"
            if calibrated_case["parser_success"]
            else calibrated_case["parser_failure_code"]
        )
        lines.append(
            f"| `{item['case_id']}` | {baseline_parser} | {calibrated_parser} | "
            f"{baseline_case['valid_claim_count']}/{baseline_case['rejected_claim_count']} -> "
            f"{calibrated_case['valid_claim_count']}/{calibrated_case['rejected_claim_count']} | "
            f"{baseline_case['value_accepted']} -> {calibrated_case['value_accepted']} | "
            f"{calibrated_case['matched_claim_count']}/"
            f"{calibrated_case['false_positive_claim_count']}/"
            f"{calibrated_case['false_negative_claim_count']} |"
        )
    lines.extend(["", "## Limits", ""])
    lines.extend(f"- {limit}" for limit in report["known_limits"])
    lines.append("")
    return "\n".join(lines)


def render_adjudication_markdown(report: dict[str, Any]) -> str:
    by_id = {item["case_id"]: item for item in report["per_case"]}

    def row(case_id: str, finding: str) -> str:
        item = by_id[case_id]
        before = item["baseline"]
        after = item["calibrated"]
        return (
            f"| `{case_id}` | {before['parser_failure_code'] or before['fallback_reason'] or 'accepted'} | "
            f"{after['fallback_reason'] or 'accepted'} | {finding} |"
        )

    return "\n".join(
        [
            "# DeepSeek Reflection Calibration Adjudication",
            "",
            "No parser repair, reference mapping, Validator relaxation, or memory-path change was introduced.",
            "",
            "| Case | Baseline | Calibrated | Adjudication |",
            "|---|---|---|---|",
            row(
                "holdout-redacted-secret-error-028",
                "Schema/prompt ambiguity reproduced; calibrated key parsed. The exact error_pattern became valid while the generated verification_rule remained rejected.",
            ),
            row(
                "holdout-verified-recovery-007",
                "Schema failure removed, but only the error_pattern passed. The expected recovery is still a claim-level false negative; no Validator weakening is justified.",
            ),
            row(
                "holdout-causal-trap-025",
                "Grounded error_pattern passed and matched; the unstable verification_rule was rejected. This is the intended fail-closed split.",
            ),
            row(
                "holdout-timeout-fallback-032",
                "All-rejected fallback was removed, but the expected recovery still failed grounding while an extra error_pattern passed. Claim precision remains insufficient.",
            ),
            row(
                "holdout-provider-fallback-033",
                "Preserving DecisionEvidence wording changed all-rejected to one valid, matched decision.",
            ),
            row(
                "holdout-unverified-recovery-024",
                "Replay proved error_pattern could launder value from an unverified failed recovery. ValueGate now rejects unverified_recovery_context.",
            ),
            row(
                "holdout-partial-recovery-008",
                "Independent replay showed the same laundering pattern for unknown outcome; the same general ValueGate rule rejects it.",
            ),
            "",
            "## Decision",
            "",
            "- Keep Parser fail closed; no normalization, retry, or ID repair.",
            "- Keep Validator thresholds unchanged; its rejections prevented unsupported recovery/root-cause persistence.",
            "- Keep the new ValueGate condition because two independent captured responses reproduce the same bypass pattern and controls remain accepted.",
            "- Do not enter 200-500 shadow expansion: negative sample count and Validator precision gates fail.",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-pilot", type=Path, required=True)
    parser.add_argument("--baseline-replay", type=Path, required=True)
    parser.add_argument("--intermediate-pilot", type=Path, required=True)
    parser.add_argument("--calibrated-pilot", type=Path, required=True)
    parser.add_argument("--calibrated-replay", type=Path, required=True)
    parser.add_argument("--local-performance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path, required=True)
    args = parser.parse_args(argv)

    report = build_calibration_report(
        baseline_pilot=_load(args.baseline_pilot),
        baseline_replay=_load(args.baseline_replay),
        intermediate_pilot=_load(args.intermediate_pilot),
        calibrated_pilot=_load(args.calibrated_pilot),
        calibrated_replay=_load(args.calibrated_replay),
        local_performance=json.loads(
            args.local_performance.read_text(encoding="utf-8")
        ),
    )
    for path in (args.output, args.markdown, args.adjudication):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.markdown.write_text(
        render_calibration_markdown(report), encoding="utf-8"
    )
    args.adjudication.write_text(
        render_adjudication_markdown(report), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
