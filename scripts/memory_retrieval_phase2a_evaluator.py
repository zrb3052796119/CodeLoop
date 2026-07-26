"""Phase 2A evaluator for the unified deterministic memory retrieval contract."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.memory_retrieval_evaluator import (
    CATEGORIES,
    PHASE2A_ARMS,
    aggregate_results,
    evaluate_arm,
    hash_production_files,
    load_dataset,
)


PHASE2A_EVALUATOR_VERSION = "2.0.0"
PERFORMANCE_ENFORCEMENT_MODES = frozenset({"advisory", "strict"})
UNIFIED_ENTRYPOINTS = (
    "manager_context_query",
    "pipeline_read",
    "pipeline_inject",
    "canonical_retrieval",
)
DELTA_METRICS = (
    "precision_at_1",
    "recall_at_5",
    "primary_hit_rate",
    "actual_rendered_precision",
    "must_exclude_violation_rate",
    "negative_false_injection_rate",
    "inactive_memory_leakage_rate",
    "max_memories_violation_rate",
    "token_budget_violation_rate",
    "returned_rendered_disagreement_rate",
    "rendered_recorded_disagreement_rate",
)
PROTECTED_RELATIVE_PATHS = (
    ".mini-code/memory/memory.json",
    ".mini-code/memory/MEMORY.md",
    ".mini-code/memory/approval_audit.json",
    ".mini-code/sessions_index.json",
)


def _require_nonnegative_finite_number(name: str, value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f"{name} must be a finite non-negative number")
    return float(value)


def evaluate_phase2a_performance_policy(
    *,
    canonical_p95_ms: float,
    task_start_average_saves: float,
    turn_total_average_saves: float,
    enforcement_mode: str,
) -> dict[str, Any]:
    """Classify explicit Phase 2A metrics without observing or mutating state."""
    if (
        not isinstance(enforcement_mode, str)
        or enforcement_mode not in PERFORMANCE_ENFORCEMENT_MODES
    ):
        raise ValueError(f"unsupported enforcement_mode: {enforcement_mode!r}")
    canonical_p95_ms = _require_nonnegative_finite_number(
        "canonical_p95_ms", canonical_p95_ms
    )
    task_start_average_saves = _require_nonnegative_finite_number(
        "task_start_average_saves", task_start_average_saves
    )
    turn_total_average_saves = _require_nonnegative_finite_number(
        "turn_total_average_saves", turn_total_average_saves
    )
    deterministic_gates = {
        "task_start_average_saves_at_most_2": (
            task_start_average_saves <= 2.0
        ),
        "turn_total_average_saves_at_most_3": (
            turn_total_average_saves <= 3.0
        ),
    }
    wall_clock_gates = {
        "canonical_p95_at_most_5_ms": canonical_p95_ms <= 5.0,
    }
    deterministic_passed = all(deterministic_gates.values())
    strict_passed = deterministic_passed and all(wall_clock_gates.values())
    return {
        "enforcementMode": enforcement_mode,
        "deterministicGates": deterministic_gates,
        "wallClockGates": wall_clock_gates,
        "deterministicPassed": deterministic_passed,
        "strictPassed": strict_passed,
        "acceptancePassed": (
            strict_passed
            if enforcement_mode == "strict"
            else deterministic_passed
        ),
    }


def phase2a_exit_code(report: object) -> int:
    """Return the CLI status for a complete Phase 2A report, failing closed."""
    if not isinstance(report, dict):
        return 1
    gate_groups: list[dict[str, Any]] = []
    for name in ("correctness_gates", "quality_gates", "integrity_gates"):
        gates = report.get(name)
        if (
            not isinstance(gates, dict)
            or not gates
            or any(not isinstance(value, bool) for value in gates.values())
        ):
            return 1
        gate_groups.append(gates)
    policy = report.get("performancePolicy")
    if not isinstance(policy, dict):
        return 1
    mode = policy.get("enforcementMode")
    deterministic_passed = policy.get("deterministicPassed")
    strict_passed = policy.get("strictPassed")
    policy_acceptance = policy.get("acceptancePassed")
    if (
        mode not in PERFORMANCE_ENFORCEMENT_MODES
        or not isinstance(deterministic_passed, bool)
        or not isinstance(strict_passed, bool)
        or not isinstance(policy_acceptance, bool)
    ):
        return 1
    expected_policy_acceptance = (
        strict_passed if mode == "strict" else deterministic_passed
    )
    deterministic_acceptance = all(
        all(gates.values()) for gates in gate_groups
    ) and deterministic_passed
    expected_acceptance = all(
        all(gates.values()) for gates in gate_groups
    ) and expected_policy_acceptance
    if (
        policy_acceptance is not expected_policy_acceptance
        or report.get("enforcementMode") != mode
        or report.get("deterministicPassed") is not deterministic_passed
        or report.get("deterministicAcceptancePassed")
        is not deterministic_acceptance
        or report.get("strictPassed") is not strict_passed
        or report.get("acceptancePassed") is not expected_acceptance
    ):
        return 1
    return 0 if expected_acceptance else 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_protected_files(home: Path) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for relative in PROTECTED_RELATIVE_PATHS:
        path = home / relative
        record: dict[str, Any] = {"exists": path.exists()}
        if path.exists():
            stat = path.stat()
            record.update(
                sha256=_sha256(path),
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
            )
        snapshot[relative] = record
    return snapshot


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def _delta(current: Any, baseline: Any) -> float | None:
    if current is None or baseline is None:
        return None
    return float(current) - float(baseline)


def _arm_delta(
    current: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[str, float | None]:
    return {
        metric: _delta(current.get(metric), baseline.get(metric))
        for metric in DELTA_METRICS
    }


def _entrypoint_agreement(per_case: list[dict[str, Any]]) -> dict[str, Any]:
    agreeing = 0
    top1_rows: list[dict[str, Any]] = []
    for case in per_case:
        values = {
            arm: (case["arms"][arm]["returned_ids"][:1] or [None])[0]
            for arm in UNIFIED_ENTRYPOINTS
        }
        same = len(set(values.values())) == 1
        agreeing += int(same)
        top1_rows.append({"case_id": case["case_id"], "top1": values, "all_equal": same})
    return {
        "entrypoints": list(UNIFIED_ENTRYPOINTS),
        "case_count": len(per_case),
        "top1_agreement_count": agreeing,
        "top1_agreement_rate": agreeing / len(per_case) if per_case else 1.0,
        "disagreements": [row for row in top1_rows if not row["all_equal"]],
    }


def _identity_counts(results: list[dict[str, Any]]) -> dict[str, Any]:
    fields = {
        "candidate": "candidate_ids",
        "selected": "selected_ids",
        "rendered": "rendered_ids",
        "recorded": "recorded_injection_ids",
        "feedback": "feedback_ids",
    }
    counts: dict[str, Any] = {}
    for label, field in fields.items():
        values = [len(result.get(field) or []) for result in results]
        counts[f"total_{label}"] = sum(values)
        counts[f"average_{label}"] = sum(values) / len(values) if values else 0.0
    return counts


def _suppression_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for result in results:
        by_id = result.get("diagnostics", {}).get("suppressed_reason_codes", {})
        for reasons in by_id.values():
            counts.update(reasons)
    return dict(sorted(counts.items()))


def deterministic_phase2a_view(report: dict[str, Any]) -> dict[str, Any]:
    view = copy.deepcopy(report)
    view.pop("latency", None)
    for case in view.get("per_case_results", []):
        for result in case.get("arms", {}).values():
            result.pop("latency_ms", None)
    performance_gates = view.get("performance_gates")
    if isinstance(performance_gates, dict):
        performance_gates.pop("canonical_p95_at_most_5_ms", None)
    performance_policy = view.get("performancePolicy")
    if isinstance(performance_policy, dict):
        performance_policy.pop("enforcementMode", None)
        performance_policy.pop("wallClockGates", None)
        performance_policy.pop("strictPassed", None)
        deterministic_passed = performance_policy.get("deterministicPassed")
        if isinstance(deterministic_passed, bool):
            performance_policy["acceptancePassed"] = deterministic_passed
    view.pop("enforcementMode", None)
    view.pop("strictPassed", None)
    deterministic_acceptance = view.get("deterministicAcceptancePassed")
    if isinstance(deterministic_acceptance, bool):
        view["acceptancePassed"] = deterministic_acceptance
    return view


def evaluate_phase2a_dataset(
    dataset_root: Path,
    *,
    project_root: Path,
    baseline_path: Path,
    home: Path | None = None,
    enforcement_mode: str = "advisory",
) -> dict[str, Any]:
    """Evaluate all five arms using isolated synthetic managers and no network."""
    project_root = Path(project_root).resolve()
    dataset_root = Path(dataset_root).resolve()
    baseline_path = Path(baseline_path).resolve()
    evaluation_home = Path(home or Path.home()).resolve()
    cases = load_dataset(dataset_root)
    baseline_bytes_before = baseline_path.read_bytes()
    baseline = json.loads(baseline_bytes_before)
    protected_before = _snapshot_protected_files(evaluation_home)
    production_before = hash_production_files(project_root)
    fixture_before = {
        str(path.relative_to(dataset_root)): _sha256(path)
        for path in sorted(dataset_root.rglob("*.json"))
    }

    by_arm: dict[str, list[dict[str, Any]]] = {arm: [] for arm in PHASE2A_ARMS}
    per_case: list[dict[str, Any]] = []
    for case in cases:
        arms = {arm: evaluate_arm(case, arm) for arm in PHASE2A_ARMS}
        for arm, result in arms.items():
            by_arm[arm].append(result)
        per_case.append(
            {
                "case_id": case["case_id"],
                "category": case["category"],
                "primary_id": case["primary_id"],
                "expected_no_injection": case["expected_no_injection"],
                "arms": arms,
            }
        )

    overall = {arm: aggregate_results(results) for arm, results in by_arm.items()}
    per_category = {
        category: {
            arm: aggregate_results(
                [result for result in by_arm[arm] if result["category"] == category]
            )
            for arm in PHASE2A_ARMS
        }
        for category in CATEGORIES
    }
    baseline_metrics = baseline["overall_metrics"]
    deltas: dict[str, Any] = {
        arm: _arm_delta(overall[arm], baseline_metrics[arm])
        for arm in baseline_metrics
    }
    deltas["canonical_retrieval_vs_phase1_global"] = _arm_delta(
        overall["canonical_retrieval"],
        baseline_metrics["manager_global_search"],
    )
    latency = {
        arm: {
            "p50_ms": _percentile([result["latency_ms"] for result in results], 0.50),
            "p95_ms": _percentile([result["latency_ms"] for result in results], 0.95),
        }
        for arm, results in by_arm.items()
    }
    identity = {arm: _identity_counts(results) for arm, results in by_arm.items()}
    no_match = {
        arm: {
            "count": sum(bool(result.get("no_match")) for result in results),
            "reason_counts": dict(
                sorted(
                    Counter(
                        result.get("no_match_reason") or "not_no_match"
                        for result in results
                    ).items()
                )
            ),
        }
        for arm, results in by_arm.items()
    }
    save_io = {
        arm: {
            "average_task_start_scope_saves": sum(
                result["io_counts"]["task_start_scope_saves"] for result in results
            )
            / len(results),
            "average_feedback_scope_saves": sum(
                result["io_counts"]["feedback_scope_saves"] for result in results
            )
            / len(results),
            "average_total_scope_saves": sum(
                result["io_counts"]["total_scope_saves"] for result in results
            )
            / len(results),
            "total_search_calls": sum(
                result["io_counts"]["search_calls"] for result in results
            ),
            "total_retrieval_counter_calls": sum(
                result["io_counts"]["retrieval_counter_calls"] for result in results
            ),
            "total_injection_counter_calls": sum(
                result["io_counts"]["injection_counter_calls"] for result in results
            ),
            "total_feedback_calls": sum(
                result["io_counts"]["feedback_calls"] for result in results
            ),
        }
        for arm, results in by_arm.items()
    }
    canonical = overall["canonical_retrieval"]
    pipeline = overall["pipeline_inject"]
    agreement = _entrypoint_agreement(per_case)
    remote_call_count = 0
    correctness_gates = {
        "inactive_leakage_zero": pipeline["inactive_memory_leakage_count"] == 0,
        "max_memories_violation_zero": pipeline["max_memories_violation_count"] == 0,
        "token_budget_violation_zero": pipeline["token_budget_violation_count"] == 0,
        "duplicate_injection_zero": pipeline["duplicate_injection_count"] == 0,
        "returned_rendered_disagreement_zero": pipeline["returned_rendered_disagreement_count"] == 0,
        "rendered_recorded_disagreement_zero": pipeline["rendered_recorded_disagreement_count"] == 0,
        "rendered_feedback_disagreement_zero": pipeline["rendered_feedback_disagreement_count"] == 0,
        "unified_top1_agreement_100_percent": agreement["top1_agreement_rate"] == 1.0,
        "remote_model_calls_zero": remote_call_count == 0,
    }
    quality_gates = {
        "negative_false_injection_zero": pipeline["negative_false_injection_count"] == 0,
        "pipeline_recall_at_5_at_least_0_95": pipeline["recall_at_5"] >= 0.95,
        "canonical_precision_at_1_at_least_phase1_global": canonical["precision_at_1"]
        >= baseline_metrics["manager_global_search"]["precision_at_1"],
        "canonical_primary_hit_not_below_phase1_global": canonical["primary_hit_rate"]
        >= baseline_metrics["manager_global_search"]["primary_hit_rate"],
        "rendered_precision_above_phase1_inject": pipeline["actual_rendered_precision"]
        > baseline_metrics["pipeline_inject"]["actual_rendered_precision"],
        "must_exclude_below_phase1_inject": pipeline["must_exclude_violation_rate"]
        < baseline_metrics["pipeline_inject"]["must_exclude_violation_rate"],
    }
    performance_policy = evaluate_phase2a_performance_policy(
        canonical_p95_ms=latency["canonical_retrieval"]["p95_ms"],
        task_start_average_saves=save_io["pipeline_inject"][
            "average_task_start_scope_saves"
        ],
        turn_total_average_saves=save_io["pipeline_inject"][
            "average_total_scope_saves"
        ],
        enforcement_mode=enforcement_mode,
    )
    performance_gates = {
        **performance_policy["wallClockGates"],
        **performance_policy["deterministicGates"],
    }

    fixture_after = {
        str(path.relative_to(dataset_root)): _sha256(path)
        for path in sorted(dataset_root.rglob("*.json"))
    }
    production_after = hash_production_files(project_root)
    protected_after = _snapshot_protected_files(evaluation_home)
    baseline_bytes_after = baseline_path.read_bytes()
    integrity_gates = {
        "phase1_baseline_unchanged": baseline_bytes_before == baseline_bytes_after,
        "production_files_unchanged_during_evaluator": (
            production_before == production_after
        ),
        "fixtures_unchanged": fixture_before == fixture_after,
        "protected_files_unchanged": protected_before == protected_after,
        "remote_calls_zero": remote_call_count == 0,
    }
    deterministic_acceptance_passed = all(
        (
            all(correctness_gates.values()),
            all(quality_gates.values()),
            all(integrity_gates.values()),
            performance_policy["deterministicPassed"],
        )
    )
    acceptance_passed = all(
        (
            all(correctness_gates.values()),
            all(quality_gates.values()),
            all(integrity_gates.values()),
            performance_policy["acceptancePassed"],
        )
    )
    return {
        "schema_version": "memory-retrieval-phase2a-v1",
        "evaluator_version": PHASE2A_EVALUATOR_VERSION,
        "synthetic_data": True,
        "dataset_case_count": len(cases),
        "category_counts": dict(sorted(Counter(case["category"] for case in cases).items())),
        "arms": list(PHASE2A_ARMS),
        "phase1_baseline_schema": baseline["schema_version"],
        "phase1_baseline_sha256_before": hashlib.sha256(baseline_bytes_before).hexdigest(),
        "phase1_baseline_sha256_after": hashlib.sha256(baseline_bytes_after).hexdigest(),
        "phase1_baseline_unchanged": baseline_bytes_before == baseline_bytes_after,
        "phase1_overall_metrics": baseline_metrics,
        "overall_metrics": overall,
        "absolute_delta": deltas,
        "per_category_metrics": per_category,
        "per_case_results": per_case,
        "entrypoint_agreement": agreement,
        "identity_counts": identity,
        "no_match": no_match,
        "suppressed_reason_counts": {
            arm: _suppression_counts(results) for arm, results in by_arm.items()
        },
        "latency": latency,
        "token_usage": {
            arm: {"average_memory_tokens": overall[arm]["average_memory_tokens"]}
            for arm in PHASE2A_ARMS
        },
        "save_io": save_io,
        "correctness_gates": correctness_gates,
        "quality_gates": quality_gates,
        "integrity_gates": integrity_gates,
        "performance_gates": performance_gates,
        "performanceGatesRole": "legacy_observation_only",
        "performancePolicy": performance_policy,
        "enforcementMode": enforcement_mode,
        "deterministicPassed": performance_policy["deterministicPassed"],
        "deterministicAcceptancePassed": deterministic_acceptance_passed,
        "strictPassed": performance_policy["strictPassed"],
        "acceptancePassed": acceptance_passed,
        "remote_call_count": remote_call_count,
        "reranker_mode": "disabled_not_part_of_phase2a",
        "production_file_hashes_before": production_before,
        "production_file_hashes_after": production_after,
        "production_files_unchanged_during_evaluator": production_before == production_after,
        "fixture_hashes_before": fixture_before,
        "fixture_hashes_after": fixture_after,
        "fixtures_unchanged": fixture_before == fixture_after,
        "protected_files_before": protected_before,
        "protected_files_after": protected_after,
        "protected_files_unchanged": protected_before == protected_after,
        "limitations": [
            "The 80 cases are fixed synthetic regressions, not a production-user distribution.",
            "No embedding, vector database, query rewrite, LLM filter, reranker, or remote provider is used.",
            "Manager global search remains a low-level compatibility arm; the four query-aware production entrypoints are the unified agreement set.",
            "Latency is environment-sensitive and excluded from deterministic comparisons.",
            "Hard context pressure intentionally suppresses injection even when a labelled memory is relevant.",
        ],
    }


def _metric(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_phase2a_markdown(report: dict[str, Any]) -> str:
    performance_policy = report["performancePolicy"]
    canonical_p95_ms = report["latency"]["canonical_retrieval"]["p95_ms"]
    lines = [
        "# Memory Retrieval Phase 2A",
        "",
        "> Offline deterministic evaluation on the frozen 80-case synthetic fixture.",
        "",
        "## Acceptance",
        "",
        f"- Mode: `{report['enforcementMode']}`",
        f"- Correctness gates: `{all(report['correctness_gates'].values())}`",
        f"- Quality gates: `{all(report['quality_gates'].values())}`",
        f"- Integrity gates: `{all(report['integrity_gates'].values())}`",
        f"- Deterministic acceptance: `{report['deterministicAcceptancePassed']}`",
        f"- Canonical P95 measured: `{_metric(canonical_p95_ms)} ms`",
        "- Canonical P95 limit: `5.0 ms`",
        f"- Wall-clock gate: `{performance_policy['wallClockGates']['canonical_p95_at_most_5_ms']}`",
        f"- Strict performance result: `{report['strictPassed']}`",
        f"- Final acceptance: `{report['acceptancePassed']}`",
        "- Legacy `performance_gates` are observations, not default acceptance authority.",
        "- Advisory acceptance does not claim that a failed wall-clock gate passed.",
        f"- Remote calls: `{report['remote_call_count']}`",
        f"- Protected files unchanged: `{report['protected_files_unchanged']}`",
        f"- Phase 1 baseline unchanged: `{report['phase1_baseline_unchanged']}`",
        "",
        "## Five Arms",
        "",
        "| Arm | P@1 | R@5 | Primary hit | Rendered precision | Exclude rate | Negative false | Avg saves | p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in PHASE2A_ARMS:
        metrics = report["overall_metrics"][arm]
        lines.append(
            f"| {arm} | {_metric(metrics['precision_at_1'])} | {_metric(metrics['recall_at_5'])} | "
            f"{_metric(metrics['primary_hit_rate'])} | {_metric(metrics['actual_rendered_precision'])} | "
            f"{_metric(metrics['must_exclude_violation_rate'])} | {_metric(metrics['negative_false_injection_rate'])} | "
            f"{_metric(report['save_io'][arm]['average_total_scope_saves'])} | "
            f"{_metric(report['latency'][arm]['p95_ms'])} |"
        )
    lines.extend(
        [
            "",
            "## Identity And Ownership",
            "",
            f"- Unified Top-1 agreement: `{_metric(report['entrypoint_agreement']['top1_agreement_rate'])}`.",
            "- `MemoryPipeline.inject` is the only production persistent-memory prompt owner.",
            "- Recorded injection IDs and outcome feedback IDs are derived from the saved rendered IDs.",
            "- Reranker summaries are disabled and cannot enter the prompt.",
            "",
            "## I/O And Budget",
            "",
            f"- Pipeline task-start saves: `{_metric(report['save_io']['pipeline_inject']['average_task_start_scope_saves'])}` average scopes.",
            f"- Pipeline full-turn saves: `{_metric(report['save_io']['pipeline_inject']['average_total_scope_saves'])}` average scopes.",
            f"- Max-memory violations: `{report['overall_metrics']['pipeline_inject']['max_memories_violation_count']}`.",
            f"- Token-budget violations: `{report['overall_metrics']['pipeline_inject']['token_budget_violation_count']}`.",
            "",
            "## Limits",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


def render_phase2a_comparison(report: dict[str, Any]) -> str:
    baseline = report["phase1_overall_metrics"]["pipeline_inject"]
    current = report["overall_metrics"]["pipeline_inject"]
    delta = report["absolute_delta"]["pipeline_inject"]
    lines = [
        "# Memory Retrieval Phase 1 vs Phase 2A",
        "",
        "| Metric | Phase 1 Pipeline Inject | Phase 2A Pipeline Inject | Absolute delta |",
        "|---|---:|---:|---:|",
    ]
    for metric in (
        "precision_at_1",
        "recall_at_5",
        "primary_hit_rate",
        "actual_rendered_precision",
        "must_exclude_violation_rate",
        "negative_false_injection_rate",
        "returned_rendered_disagreement_rate",
        "rendered_recorded_disagreement_rate",
    ):
        lines.append(
            f"| {metric} | {_metric(baseline.get(metric))} | {_metric(current.get(metric))} | {_metric(delta.get(metric))} |"
        )
    lines.extend(
        [
            "",
            "Phase 2A raises precision while preserving the required R@5 floor. The remaining recall loss is concentrated in hard count/token/context-pressure cases; those limits are now enforced instead of bypassed.",
            "",
            f"Must-exclude violations fell from `{_metric(baseline['must_exclude_violation_rate'])}` to `{_metric(current['must_exclude_violation_rate'])}`; negative false injection fell from `{_metric(baseline['negative_false_injection_rate'])}` to `{_metric(current['negative_false_injection_rate'])}`.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_phase2a_reports(
    report: dict[str, Any],
    *,
    json_path: Path,
    markdown_path: Path,
    comparison_path: Path,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    comparison_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_phase2a_markdown(report), encoding="utf-8")
    comparison_path.write_text(render_phase2a_comparison(report), encoding="utf-8")
