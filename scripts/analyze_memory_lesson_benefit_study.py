#!/usr/bin/env python3
"""Analyze the memory-lesson-benefit study.

Inputs: frozen manifest, runner public result JSON, private evidence sidecar
(``<result stem>-evidence/cases/<case>/``) with per-run event journals.

Reports, in this order:

1. Raw per-condition metrics (warm/cold target turns, family means over
   blocks): tool calls/failures, task + side model calls, input/output
   tokens, duration, success — absolute values first, differences second.
2. Paired family-level contrasts (cold − warm = savings) with exact
   Wilcoxon, family-cluster bootstrap CIs and Holm correction.
3. Learning-chain validity: misdirection actually hit, lesson written,
   lesson content covers the correct target, lesson attributed to the
   target turn.
4. LLM reviewer behaviour: verdict distribution, downgrade reasons,
   review source (llm vs rule fallback), reviewer model calls and tokens.

Statistics reuse the exact self-contained implementations from the
large-study analyzer; event parsing reuses the pollution-study analyzer.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.analyze_memory_pollution_study import (
    _load_events,
    _run_metrics,
    _write_csv,
)
from scripts.analyze_persistent_memory_large_study import (
    cluster_bootstrap,
    exact_wilcoxon,
    holm_adjust,
)

BOOTSTRAP_SAMPLES = 20_000
BOOTSTRAP_SEED = 202609092
PRIMARY_METRICS = ("tool_calls", "task_input_tokens")
SECONDARY_METRICS = ("task_output_tokens", "duration_ms")
LEARNING_ORACLES = (
    "source-failure",
    "lesson-written",
    "lesson-covers-target",
    "lesson-attributed",
)
_REVIEW_PURPOSE = "memory_contribution_review"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _case_run_ids(public_result: Mapping[str, Any]) -> list[str]:
    run_ids = list(public_result.get("relatedRunIds") or [])
    final_run_id = public_result.get("runId")
    if isinstance(final_run_id, str):
        run_ids.append(final_run_id)
    return [str(run_id) for run_id in run_ids]


def _reviewer_observations(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    verdicts: Counter[str] = Counter()
    downgrades: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    review_calls = 0
    review_input_tokens = 0
    review_output_tokens = 0
    for event in events:
        event_type = event.get("type")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if event_type == "memory.contribution_reviewed":
            sources[str(payload.get("reviewSource"))] += 1
            for verdict in payload.get("verdicts") or []:
                if isinstance(verdict, dict):
                    verdicts[str(verdict.get("outcome"))] += 1
                    reason = verdict.get("downgradeReason")
                    if reason:
                        downgrades[str(reason)] += 1
        elif event_type == "model.started" and payload.get("purpose") == _REVIEW_PURPOSE:
            review_calls += 1
        elif (
            event_type == "model.completed"
            and payload.get("purpose") == _REVIEW_PURPOSE
        ):
            usage = payload.get("usage") or {}
            review_input_tokens += int(usage.get("inputTokens") or 0)
            review_output_tokens += int(usage.get("outputTokens") or 0)
    return {
        "verdicts": dict(verdicts),
        "downgradeReasons": dict(downgrades),
        "reviewSources": dict(sources),
        "reviewModelCalls": review_calls,
        "reviewInputTokens": review_input_tokens,
        "reviewOutputTokens": review_output_tokens,
    }


def extract_case_rows(
    case: Mapping[str, Any],
    public_result: Mapping[str, Any],
    case_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    study = case.get("study") or {}
    condition = str(study.get("condition", ""))
    run_ids = _case_run_ids(public_result)
    rows: list[dict[str, Any]] = []
    reviewer: dict[str, Any] = {}
    for index, run_id in enumerate(run_ids):
        events = _load_events(case_root, run_id)
        metrics = _run_metrics(events)
        rows.append(
            {
                "caseId": case.get("id"),
                "familyId": study.get("familyId"),
                "stratum": study.get("stratum"),
                "block": study.get("block"),
                "condition": condition,
                "turn": index,
                "status": public_result.get("status"),
                **metrics,
            }
        )
        if condition == "warm":
            merged = reviewer or {
                "verdicts": {},
                "downgradeReasons": {},
                "reviewSources": {},
                "reviewModelCalls": 0,
                "reviewInputTokens": 0,
                "reviewOutputTokens": 0,
            }
            observations = _reviewer_observations(events)
            merged["reviewModelCalls"] += observations["reviewModelCalls"]
            merged["reviewInputTokens"] += observations["reviewInputTokens"]
            merged["reviewOutputTokens"] += observations["reviewOutputTokens"]
            for key in ("verdicts", "downgradeReasons", "reviewSources"):
                for name, count in observations[key].items():
                    merged[key][name] = merged[key].get(name, 0) + count
            reviewer = merged
    return rows, reviewer


def _target_row(rows: Sequence[Mapping[str, Any]], target_turn: int) -> Mapping[str, Any]:
    for row in rows:
        if int(row["turn"]) == target_turn:
            return row
    raise ValueError("target turn row missing")


def analyze(manifest_path: Path, result_path: Path, output_dir: Path) -> dict[str, Any]:
    manifest = _load_json(manifest_path)
    result = _load_json(result_path)
    results_by_id = {
        str(item.get("id")): item for item in result.get("results") or []
    }
    evidence_root = result_path.parent / f"{result_path.stem}-evidence"
    turn_rows: list[dict[str, Any]] = []
    reviewers: dict[str, dict[str, Any]] = {}
    pair_rows: list[dict[str, Any]] = []
    warm_cells: dict[tuple[str, int], Mapping[str, Any]] = {}
    cold_cells: dict[tuple[str, int], Mapping[str, Any]] = {}
    learning_pass: Counter[str] = Counter()
    learning_total = 0
    warm_case_status: Counter[str] = Counter()

    for case in manifest.get("cases") or []:
        case_id = str(case.get("id"))
        public_result = results_by_id.get(case_id)
        if public_result is None:
            raise ValueError(f"result is missing case {case_id}")
        study = case.get("study") or {}
        rows, reviewer = extract_case_rows(
            case, public_result, evidence_root / "cases" / case_id
        )
        turn_rows.extend(rows)
        condition = str(study.get("condition"))
        if condition == "warm":
            reviewers[case_id] = reviewer
            warm_case_status[str(public_result.get("status"))] += 1
            learning_total += 1
            passed = set(public_result.get("passedOracleIds") or [])
            for oracle_id in LEARNING_ORACLES:
                if oracle_id in passed:
                    learning_pass[oracle_id] += 1
            warm_cells[(str(study["familyId"]), int(study["block"]))] = _target_row(
                rows, int(study.get("targetTurnIndex", 1))
            )
        else:
            cold_cells[(str(study["familyId"]), int(study["block"]))] = _target_row(
                rows, int(study.get("targetTurnIndex", 0))
            )

    for key in sorted(set(warm_cells) & set(cold_cells)):
        warm = warm_cells[key]
        cold = cold_cells[key]
        pair_rows.append(
            {
                "familyId": key[0],
                "block": key[1],
                "tool_calls_warm": warm["tool_calls"],
                "tool_calls_cold": cold["tool_calls"],
                "task_input_tokens_warm": warm["task_input_tokens"],
                "task_input_tokens_cold": cold["task_input_tokens"],
                "task_output_tokens_warm": warm["task_output_tokens"],
                "task_output_tokens_cold": cold["task_output_tokens"],
                "duration_warm": warm["duration_ms"],
                "duration_cold": cold["duration_ms"],
                "warm_success": warm["task_outcome_success"],
                "cold_success": cold["task_outcome_success"],
            }
        )

    # Family-level paired contrasts: block means first, family as unit.
    families = sorted({key[0] for key in warm_cells})
    tests: dict[str, dict[str, Any]] = {}
    for metric in PRIMARY_METRICS + SECONDARY_METRICS:
        column = "duration" if metric == "duration_ms" else metric
        warm_values: list[float] = []
        cold_values: list[float] = []
        for family in families:
            warm_block = [
                float(row[f"{column}_warm"])
                for row in pair_rows
                if row["familyId"] == family and row.get(f"{column}_warm") is not None
            ]
            cold_block = [
                float(row[f"{column}_cold"])
                for row in pair_rows
                if row["familyId"] == family and row.get(f"{column}_cold") is not None
            ]
            if warm_block and cold_block:
                warm_values.append(statistics.fmean(warm_block))
                cold_values.append(statistics.fmean(cold_block))
        if len(warm_values) < 3:
            continue
        differences = [c - w for w, c in zip(warm_values, cold_values)]
        label = f"{metric}:cold-minus-warm"
        bootstrap = cluster_bootstrap(
            cold_values,
            warm_values,
            samples=BOOTSTRAP_SAMPLES,
            seed=BOOTSTRAP_SEED,
        )
        tests[label] = {
            "warmFamilyMeans": warm_values,
            "coldFamilyMeans": cold_values,
            "differences": differences,
            "meanWarm": statistics.fmean(warm_values),
            "meanCold": statistics.fmean(cold_values),
            "meanSaving": statistics.fmean(differences),
            "wilcoxon": exact_wilcoxon(differences),
            "bootstrapCI95": bootstrap["absolute"]["ci95"],
            "relativeSavingPercent": bootstrap["relative_percent"]["estimate"],
        }
    holm = holm_adjust(
        {
            f"{metric}:cold-minus-warm": float(
                tests[f"{metric}:cold-minus-warm"]["wilcoxon"]["p_two_sided"]
            )
            for metric in PRIMARY_METRICS
            if f"{metric}:cold-minus-warm" in tests
        }
    )
    for label, adjusted in holm.items():
        tests[label]["holmP"] = adjusted

    # Raw per-condition table.
    def _condition_summary(condition: str) -> dict[str, Any]:
        rows = [
            row
            for row in turn_rows
            if row["condition"] == condition
            and int(row["turn"])
            == (1 if condition == "warm" else 0)
        ]
        entry: dict[str, Any] = {
            "condition": condition,
            "cells": len(rows),
            "outcomeSuccess": sum(1 for row in rows if row["task_outcome_success"]),
        }
        for metric in PRIMARY_METRICS + SECONDARY_METRICS + (
            "tool_failures",
            "task_model_calls",
            "side_model_calls",
        ):
            values = [
                float(row[metric]) for row in rows if row.get(metric) is not None
            ]
            entry[f"{metric}_mean"] = (
                round(statistics.fmean(values), 2) if values else None
            )
        return entry

    condition_summary = [
        _condition_summary("warm"),
        _condition_summary("cold"),
    ]

    reviewer_totals: dict[str, Any] = {
        "verdicts": Counter(),
        "downgradeReasons": Counter(),
        "reviewSources": Counter(),
        "reviewModelCalls": 0,
        "reviewInputTokens": 0,
        "reviewOutputTokens": 0,
    }
    for reviewer in reviewers.values():
        reviewer_totals["reviewModelCalls"] += reviewer["reviewModelCalls"]
        reviewer_totals["reviewInputTokens"] += reviewer["reviewInputTokens"]
        reviewer_totals["reviewOutputTokens"] += reviewer["reviewOutputTokens"]
        for key in ("verdicts", "downgradeReasons", "reviewSources"):
            counter: Counter = reviewer_totals[key]
            for name, count in reviewer[key].items():
                counter[name] += count

    statistics_payload = {
        "suiteId": manifest.get("suiteId"),
        "primaryUnit": "family",
        "bootstrap": {"samples": BOOTSTRAP_SAMPLES, "seed": BOOTSTRAP_SEED},
        "conditionSummary": condition_summary,
        "tests": tests,
        "learningChain": {
            oracle: {"passed": learning_pass[oracle], "total": learning_total}
            for oracle in LEARNING_ORACLES
        },
        "warmCaseStatus": dict(warm_case_status),
        "reviewer": {
            key: dict(value) if isinstance(value, Counter) else value
            for key, value in reviewer_totals.items()
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "turn-level.csv", turn_rows)
    _write_csv(output_dir / "pair-level.csv", pair_rows)
    (output_dir / "statistics.json").write_text(
        json.dumps(statistics_payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    _write_analysis_report(output_dir, statistics_payload)
    return statistics_payload


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def _write_analysis_report(
    output_dir: Path, statistics_payload: Mapping[str, Any]
) -> None:
    lines: list[str] = [
        "# Memory lesson benefit — analysis report",
        "",
        "## Analysis contract",
        "",
        "- Inferential unit: task family (n=6); two blocks as nested repeats.",
        "- Primary contrasts: cold − warm on tool calls and task input tokens",
        "  (Holm-corrected exact Wilcoxon; family-cluster bootstrap CIs).",
        "- Learning-chain and reviewer endpoints are descriptive rates.",
        "",
        "## Raw per-condition target-turn metrics (cell means, 12 cells each)",
        "",
        "| condition | outcome success | tool calls | tool failures | task model calls | side model calls | input tokens | output tokens | duration s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cell in statistics_payload["conditionSummary"]:
        duration = cell.get("duration_ms_mean")
        duration_s = (
            f"{duration / 1000:.1f}" if isinstance(duration, (int, float)) else "n/a"
        )
        lines.append(
            f"| {cell['condition']} | {cell['outcomeSuccess']}/{cell['cells']} | "
            f"{_fmt(cell.get('tool_calls_mean'))} | "
            f"{_fmt(cell.get('tool_failures_mean'))} | "
            f"{_fmt(cell.get('task_model_calls_mean'))} | "
            f"{_fmt(cell.get('side_model_calls_mean'))} | "
            f"{_fmt(cell.get('task_input_tokens_mean'))} | "
            f"{_fmt(cell.get('task_output_tokens_mean'))} | "
            f"{duration_s} |"
        )
    lines.extend(
        [
            "",
            "## Paired savings (cold − warm, family means)",
            "",
            "| metric | warm | cold | mean saving | 95% CI | relative | Wilcoxon p | Holm p |",
            "|---|---:|---:|---:|---|---:|---:|---:|",
        ]
    )
    for label, test in statistics_payload["tests"].items():
        lines.append(
            f"| {label} | {_fmt(test['meanWarm'])} | {_fmt(test['meanCold'])} | "
            f"{_fmt(test['meanSaving'])} | "
            f"[{_fmt(test['bootstrapCI95'][0])}, {_fmt(test['bootstrapCI95'][1])}] | "
            f"{_fmt(test.get('relativeSavingPercent'))}% | "
            f"{_fmt(test['wilcoxon']['p_two_sided'])} | "
            f"{_fmt(test.get('holmP'))} |"
        )
    lines.extend(["", "## Learning-chain validity (12 warm cases)", ""])
    for oracle, counts in statistics_payload["learningChain"].items():
        lines.append(f"- {oracle}: {counts['passed']}/{counts['total']}")
    lines.extend(["", "## LLM contribution reviewer behaviour", ""])
    reviewer = statistics_payload["reviewer"]
    lines.append(f"- review model calls: {reviewer['reviewModelCalls']}")
    lines.append(
        f"- review tokens (in/out): {reviewer['reviewInputTokens']:,} / "
        f"{reviewer['reviewOutputTokens']:,}"
    )
    lines.append(f"- review sources: {reviewer['reviewSources']}")
    lines.append(f"- verdicts: {reviewer['verdicts']}")
    lines.append(f"- downgrade reasons: {reviewer['downgradeReasons']}")
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            "- Six synthetic families, single model, one day: directions and",
            "  mechanism behaviour, not powered effect sizes.",
            "- Savings are measured on target turns only; the warm learning",
            "  turn's cost is reported separately in turn-level.csv.",
            "- Lesson 'covers target' is a content containment check, not a",
            "  semantic correctness proof.",
        ]
    )
    (output_dir / "analysis-report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    payload = analyze(args.manifest, args.result, args.output_dir)
    print(
        f"wrote analysis: {len(payload['tests'])} contrasts, "
        f"{len(payload['learningChain'])} learning endpoints"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
