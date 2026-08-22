#!/usr/bin/env python3
"""Analyze the frozen non-path persistent-Memory live study.

The task family is the inferential unit.  Provider blocks are nested repeats,
and all first-attempt observations remain in the intent-to-treat dataset.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import Counter, defaultdict
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence

import analyze_persistent_memory_large_study as base


BOOTSTRAP_SEED = 20260822
BOOTSTRAP_SAMPLES = 20_000
PRIMARY_METRICS = ("tool_calls", "task_input_tokens")
SECONDARY_METRICS = (
    "task_model_calls",
    "task_output_tokens",
    "tool_failures",
    "duration_ms",
)
METRIC_LABELS = {
    "tool_calls": "Repository tool calls",
    "task_input_tokens": "Task input tokens",
    "task_model_calls": "Task model calls",
    "task_output_tokens": "Task output tokens",
    "tool_failures": "Tool failures",
    "duration_ms": "Elapsed duration (ms)",
}
TARGET_ORACLE_IDS = {
    "run-completed",
    "canonical-success",
    "verification-ran",
    "no-source-edits",
    "target-content",
    "target-verifier",
    "target-marker",
}
BLUE = "#0072B2"
ORANGE = "#E69F00"
GREEN = "#009E73"
RED = "#D55E00"
GREY = "#D9D9D9"


def _tool_metrics(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, int | bool | str | None]:
    starts: list[tuple[str, str]] = []
    finishes: dict[str, Mapping[str, Any]] = {}
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        operation_id = payload.get("operationId")
        if not isinstance(operation_id, str):
            continue
        if event.get("type") == "tool.started":
            tool_name = payload.get("toolName")
            if isinstance(tool_name, str):
                starts.append((operation_id, tool_name))
        elif event.get("type") == "tool.finished":
            finishes.setdefault(operation_id, payload)

    def succeeded(operation_id: str) -> bool:
        payload = finishes.get(operation_id, {})
        return payload.get("outcome") == "success" and payload.get("paired") is True

    successful_by_tool = Counter(
        tool_name for operation_id, tool_name in starts if succeeded(operation_id)
    )
    first_name = starts[0][1] if starts else None
    first_success = bool(starts and succeeded(starts[0][0]))
    first_successful_run_command_position = next(
        (
            index
            for index, (operation_id, tool_name) in enumerate(starts, start=1)
            if tool_name == "run_command" and succeeded(operation_id)
        ),
        None,
    )
    return {
        "tool_calls": len(starts),
        "tool_failures": sum(
            finishes.get(operation_id, {}).get("outcome") == "error"
            for operation_id, _tool_name in starts
        ),
        "successful_run_commands": successful_by_tool["run_command"],
        "successful_reads": successful_by_tool["read_file"],
        "successful_edits": sum(
            successful_by_tool[name]
            for name in ("edit_file", "patch_file", "write_file")
        ),
        "first_tool": first_name,
        "first_tool_succeeded": first_success,
        "first_successful_run_command_position": first_successful_run_command_position,
    }


def _run_metrics(events: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    task_models: list[Mapping[str, Any]] = []
    all_models: list[Mapping[str, Any]] = []
    run_started = None
    run_completed = None
    task_outcome_success = False
    memory_injected = False
    memory_rendered_count = 0
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        event_type = event.get("type")
        if event_type == "model.completed":
            all_models.append(payload)
            if payload.get("purpose") is None:
                task_models.append(payload)
        elif event_type == "run.started":
            run_started = base._parse_timestamp(str(event["timestamp"]))
        elif event_type == "run.completed":
            run_completed = base._parse_timestamp(str(event["timestamp"]))
        elif event_type == "task.outcome":
            task_outcome_success = payload.get("outcomeStatus") == "success"
        elif event_type == "memory.rendered":
            memory_injected = memory_injected or payload.get("injected") is True
            count = payload.get("renderedCount")
            if isinstance(count, int) and not isinstance(count, bool):
                memory_rendered_count += count

    def usage_sum(model_events: Sequence[Mapping[str, Any]], key: str) -> int:
        total = 0
        for payload in model_events:
            usage = payload.get("usage")
            if not isinstance(usage, dict):
                raise ValueError("model.completed event is missing usage")
            value = usage.get(key)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"model usage is missing {key}")
            total += value
        return total

    duration_ms = None
    if run_started is not None and run_completed is not None:
        duration_ms = max(
            0,
            round((run_completed - run_started).total_seconds() * 1000),
        )
    return {
        "run_completed": run_completed is not None,
        "task_outcome_success": task_outcome_success,
        "task_model_calls": len(task_models),
        "total_model_calls": len(all_models),
        "reflection_model_calls": len(all_models) - len(task_models),
        "task_input_tokens": usage_sum(task_models, "inputTokens"),
        "task_output_tokens": usage_sum(task_models, "outputTokens"),
        "total_input_tokens": usage_sum(all_models, "inputTokens"),
        "total_output_tokens": usage_sum(all_models, "outputTokens"),
        "memory_injected": memory_injected,
        "memory_rendered_count": memory_rendered_count,
        "duration_ms": duration_ms,
        **_tool_metrics(events),
    }


def _memory_written(case_root: Path, run_id: str) -> bool:
    matches = list(case_root.glob(f"journal/**/runs/{run_id}/memory_written.json"))
    if not matches:
        return False
    if len(matches) != 1:
        raise ValueError(f"expected at most one memory-written record for {run_id}")
    value = base._load_json(matches[0])
    entries = value.get("entryIds")
    return isinstance(entries, list) and bool(entries)


def extract_rows(
    manifest: Mapping[str, Any],
    result: Mapping[str, Any],
    evidence_root: Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    cases = manifest.get("cases")
    results = result.get("results")
    if not isinstance(cases, list) or not isinstance(results, list):
        raise ValueError("manifest cases or results are missing")
    if manifest.get("suiteId") != result.get("suiteId"):
        raise ValueError("manifest and result suite IDs differ")
    result_by_id = {
        item["id"]: item
        for item in results
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if set(result_by_id) != {
        case.get("id") for case in cases if isinstance(case, dict)
    }:
        raise ValueError("manifest and result case IDs differ")

    target_rows: list[dict[str, object]] = []
    learning_rows: list[dict[str, object]] = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("study"), dict):
            raise TypeError("manifest case lacks study metadata")
        study = case["study"]
        case_id = str(case["id"])
        case_root = evidence_root / "cases" / case_id
        evidence = base._load_json(case_root / "evidence.json")
        run_ids = evidence.get("runIds")
        responses = evidence.get("responses")
        target_index = study.get("targetTurnIndex")
        if (
            not isinstance(run_ids, list)
            or not isinstance(responses, list)
            or not isinstance(target_index, int)
            or target_index >= len(run_ids)
            or target_index >= len(responses)
        ):
            raise ValueError(f"incomplete Turn evidence for {case_id}")
        case_result = result_by_id[case_id]
        passed_ids = set(case_result.get("passedOracleIds", []))
        required_target_ids = {
            str(oracle["id"])
            for oracle in case.get("oracles", [])
            if isinstance(oracle, dict) and oracle.get("id") in TARGET_ORACLE_IDS
        }
        run_id = str(run_ids[target_index])
        response = str(responses[target_index])
        metrics = _run_metrics(base._load_events(case_root, run_id))
        marker_present = str(study["targetMarker"]).casefold() in response.casefold()
        target_oracle_success = required_target_ids <= passed_ids
        semantic_required_ids = required_target_ids - {"target-content"}
        semantic_target_oracle_success = semantic_required_ids <= passed_ids
        target_execution_success = bool(
            target_oracle_success
            and metrics["run_completed"]
            and metrics["task_outcome_success"]
            and int(metrics["successful_run_commands"]) >= 1
            and marker_present
        )
        semantic_target_execution_success = bool(
            semantic_target_oracle_success
            and metrics["run_completed"]
            and metrics["task_outcome_success"]
            and int(metrics["successful_run_commands"]) >= 1
            and marker_present
        )
        target_rows.append(
            {
                "case_id": case_id,
                "run_id": run_id,
                "block": int(study["block"]),
                "family_id": str(study["familyId"]),
                "stratum": str(study["stratum"]),
                "lesson_mode": str(study["lessonMode"]),
                "condition": str(study["condition"]),
                "condition_order": int(study["conditionOrder"]),
                "case_chain_success": case_result.get("status") == "passed",
                "target_oracle_success": target_oracle_success,
                "target_execution_success": target_execution_success,
                "semantic_target_oracle_success": semantic_target_oracle_success,
                "semantic_target_execution_success": semantic_target_execution_success,
                "marker_present": marker_present,
                "failed_target_oracles": ";".join(sorted(required_target_ids - passed_ids)),
                **metrics,
            }
        )

        if (
            study.get("condition") == "warm"
            and study.get("lessonMode") == "learned"
        ):
            if target_index != 1 or len(run_ids) != 2:
                raise ValueError(f"unexpected learned chain for {case_id}")
            learning_run_id = str(run_ids[0])
            learning_metrics = _run_metrics(
                base._load_events(case_root, learning_run_id)
            )
            lesson_written = _memory_written(case_root, learning_run_id)
            learning_rows.append(
                {
                    "case_id": case_id,
                    "run_id": learning_run_id,
                    "block": int(study["block"]),
                    "family_id": str(study["familyId"]),
                    "stratum": str(study["stratum"]),
                    "failure_required": bool(study["learningFailureRequired"]),
                    "lesson_written": lesson_written,
                    "verified_learning": bool(
                        lesson_written
                        and learning_metrics["run_completed"]
                        and learning_metrics["task_outcome_success"]
                        and int(learning_metrics["successful_run_commands"]) >= 1
                        and (
                            not study["learningFailureRequired"]
                            or int(learning_metrics["tool_failures"]) >= 1
                        )
                    ),
                    **learning_metrics,
                }
            )
    return target_rows, learning_rows


PAIR_METRICS = (
    "tool_calls",
    "tool_failures",
    "task_model_calls",
    "task_input_tokens",
    "task_output_tokens",
    "total_model_calls",
    "total_input_tokens",
    "total_output_tokens",
    "duration_ms",
)


def build_pair_rows(
    target_rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[tuple[int, str], dict[str, Mapping[str, object]]] = defaultdict(dict)
    for row in target_rows:
        grouped[(int(row["block"]), str(row["family_id"]))][
            str(row["condition"])
        ] = row
    rows: list[dict[str, object]] = []
    for (block, family_id), conditions in sorted(grouped.items()):
        if set(conditions) != {"warm", "cold"}:
            raise ValueError(f"incomplete pair for block {block}, family {family_id}")
        warm = conditions["warm"]
        cold = conditions["cold"]
        pair: dict[str, object] = {
            "block": block,
            "family_id": family_id,
            "stratum": warm["stratum"],
            "lesson_mode": warm["lesson_mode"],
            "warm_first": int(warm["condition_order"]) == 1,
            "warm_success": warm["target_execution_success"],
            "cold_success": cold["target_execution_success"],
            "warm_case_chain_success": warm["case_chain_success"],
            "cold_case_chain_success": cold["case_chain_success"],
            "warm_memory_injected": warm["memory_injected"],
            "cold_memory_injected": cold["memory_injected"],
        }
        for name in PAIR_METRICS:
            warm_value = float(warm[name])
            cold_value = float(cold[name])
            pair[f"warm_{name}"] = warm[name]
            pair[f"cold_{name}"] = cold[name]
            pair[f"saving_{name}"] = cold_value - warm_value
            pair[f"reduction_percent_{name}"] = (
                100 * (cold_value - warm_value) / cold_value
                if cold_value
                else None
            )
        rows.append(pair)
    return rows


def build_family_rows(
    pair_rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in pair_rows:
        grouped[str(row["family_id"])].append(row)
    rows: list[dict[str, object]] = []
    for family_id, observations in sorted(grouped.items()):
        if len(observations) != 3:
            raise ValueError(f"family {family_id} lacks three blocks")
        row: dict[str, object] = {
            "family_id": family_id,
            "stratum": observations[0]["stratum"],
            "lesson_mode": observations[0]["lesson_mode"],
            "blocks": 3,
            "warm_success_rate": statistics.fmean(
                int(bool(item["warm_success"])) for item in observations
            ),
            "cold_success_rate": statistics.fmean(
                int(bool(item["cold_success"])) for item in observations
            ),
        }
        for name in PAIR_METRICS:
            warm_mean = statistics.fmean(
                float(item[f"warm_{name}"]) for item in observations
            )
            cold_mean = statistics.fmean(
                float(item[f"cold_{name}"]) for item in observations
            )
            row[f"warm_{name}"] = warm_mean
            row[f"cold_{name}"] = cold_mean
            row[f"saving_{name}"] = cold_mean - warm_mean
            row[f"reduction_percent_{name}"] = (
                100 * (cold_mean - warm_mean) / cold_mean if cold_mean else None
            )
        rows.append(row)
    return rows


def _metric_analysis(
    family_rows: Sequence[Mapping[str, object]], metric: str
) -> dict[str, object]:
    cold = [float(row[f"cold_{metric}"]) for row in family_rows]
    warm = [float(row[f"warm_{metric}"]) for row in family_rows]
    differences = [left - right for left, right in zip(cold, warm)]
    return {
        "label": METRIC_LABELS[metric],
        "direction": "positive savings favor Memory",
        "cold_family_summary": base._summary(cold),
        "warm_family_summary": base._summary(warm),
        "saving_family_summary": base._summary(differences),
        "hodges_lehmann_saving": base.hodges_lehmann(differences),
        "wilcoxon_exact": base.exact_wilcoxon(differences),
        "sign_test_exact": base.exact_sign_test(differences),
        "family_cluster_bootstrap": base.cluster_bootstrap(
            cold,
            warm,
            samples=BOOTSTRAP_SAMPLES,
            seed=BOOTSTRAP_SEED,
        ),
    }


def analyze(
    target_rows: Sequence[Mapping[str, object]],
    pair_rows: Sequence[Mapping[str, object]],
    family_rows: Sequence[Mapping[str, object]],
    learning_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    metric_names = (*PRIMARY_METRICS, *SECONDARY_METRICS)
    metrics = {name: _metric_analysis(family_rows, name) for name in metric_names}
    for name, probability in base.holm_adjust(
        {
            name: float(metrics[name]["wilcoxon_exact"]["p_two_sided"])
            for name in PRIMARY_METRICS
        }
    ).items():
        metrics[name]["wilcoxon_holm_p_primary_family"] = probability
    for name, probability in base.holm_adjust(
        {
            name: float(metrics[name]["wilcoxon_exact"]["p_two_sided"])
            for name in SECONDARY_METRICS
        }
    ).items():
        metrics[name]["wilcoxon_holm_p_exploratory_family"] = probability

    condition_summary: dict[str, object] = {}
    for condition in ("warm", "cold"):
        selected = [row for row in target_rows if row["condition"] == condition]
        condition_summary[condition] = {
            "targetTurns": len(selected),
            "targetExecutionSuccesses": sum(
                bool(row["target_execution_success"]) for row in selected
            ),
            "targetOracleSuccesses": sum(
                bool(row["target_oracle_success"]) for row in selected
            ),
            "semanticTargetExecutionSuccesses": sum(
                bool(row["semantic_target_execution_success"]) for row in selected
            ),
            "caseChainSuccesses": sum(
                bool(row["case_chain_success"]) for row in selected
            ),
            "memoryInjections": sum(bool(row["memory_injected"]) for row in selected),
            "successfulRunCommandTurns": sum(
                int(row["successful_run_commands"]) >= 1 for row in selected
            ),
            "toolCalls": base._summary([float(row["tool_calls"]) for row in selected]),
            "toolFailures": base._summary(
                [float(row["tool_failures"]) for row in selected]
            ),
            "taskModelCalls": base._summary(
                [float(row["task_model_calls"]) for row in selected]
            ),
            "taskInputTokens": base._summary(
                [float(row["task_input_tokens"]) for row in selected]
            ),
            "taskOutputTokens": base._summary(
                [float(row["task_output_tokens"]) for row in selected]
            ),
            "durationMs": base._summary([float(row["duration_ms"]) for row in selected]),
            "firstToolCounts": dict(Counter(str(row["first_tool"]) for row in selected)),
            "failedTargetOracleCounts": dict(
                Counter(
                    oracle
                    for row in selected
                    for oracle in str(row["failed_target_oracles"]).split(";")
                    if oracle
                )
            ),
        }

    stratum_summary: dict[str, object] = {}
    for stratum in sorted({str(row["stratum"]) for row in target_rows}):
        stratum_summary[stratum] = {}
        for condition in ("warm", "cold"):
            selected = [
                row
                for row in target_rows
                if row["stratum"] == stratum and row["condition"] == condition
            ]
            stratum_summary[stratum][condition] = {
                "n": len(selected),
                "successes": sum(
                    bool(row["target_execution_success"]) for row in selected
                ),
                "meanToolCalls": statistics.fmean(
                    float(row["tool_calls"]) for row in selected
                ),
                "meanTaskInputTokens": statistics.fmean(
                    float(row["task_input_tokens"]) for row in selected
                ),
                "meanToolFailures": statistics.fmean(
                    float(row["tool_failures"]) for row in selected
                ),
            }

    learned_pairs = [row for row in pair_rows if row["lesson_mode"] == "learned"]
    creation_task_input = statistics.fmean(
        float(row["task_input_tokens"]) for row in learning_rows
    )
    reuse_saving = statistics.fmean(
        float(row["saving_task_input_tokens"]) for row in learned_pairs
    )
    return {
        "schemaVersion": 1,
        "inferentialUnit": "family",
        "familyCount": len(family_rows),
        "nestedBlocksPerFamily": 3,
        "pairCount": len(pair_rows),
        "targetTurnCount": len(target_rows),
        "conditionSummary": condition_summary,
        "stratumSummary": stratum_summary,
        "metrics": metrics,
        "pairDirections": {
            name: {
                "positive": sum(float(row[f"saving_{name}"]) > 0 for row in pair_rows),
                "ties": sum(float(row[f"saving_{name}"]) == 0 for row in pair_rows),
                "negative": sum(float(row[f"saving_{name}"]) < 0 for row in pair_rows),
            }
            for name in metric_names
        },
        "lessonModeCounts": dict(Counter(row["lesson_mode"] for row in family_rows)),
        "stratumCounts": dict(Counter(row["stratum"] for row in family_rows)),
        "learningSummary": {
            "lessonCreationTurns": len(learning_rows),
            "lessonsWritten": sum(bool(row["lesson_written"]) for row in learning_rows),
            "verifiedLearningTurns": sum(
                bool(row["verified_learning"]) for row in learning_rows
            ),
            "toolCalls": base._summary(
                [float(row["tool_calls"]) for row in learning_rows]
            ),
            "taskInputTokens": base._summary(
                [float(row["task_input_tokens"]) for row in learning_rows]
            ),
            "conservativeBreakEvenReuses": (
                creation_task_input / reuse_saving if reuse_saving > 0 else None
            ),
        },
        "bootstrap": {
            "method": "family-cluster percentile bootstrap",
            "samples": BOOTSTRAP_SAMPLES,
            "seed": BOOTSTRAP_SEED,
        },
    }


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _svg_header(width: int, height: int, title: str) -> list[str]:
    return [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" role="img">'
        ),
        f"<title>{escape(title)}</title>",
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,Helvetica,sans-serif;fill:#222}</style>',
    ]


def write_family_tool_figure(
    path: Path, family_rows: Sequence[Mapping[str, object]]
) -> None:
    width, height = 1140, 760
    left, right, top, bottom = 245, 60, 85, 65
    plot_width = width - left - right
    axis_max = math.ceil(
        max(
            float(row[f"{condition}_tool_calls"])
            for row in family_rows
            for condition in ("warm", "cold")
        )
        + 1
    )

    def x(value: float) -> float:
        return left + plot_width * value / axis_max

    lines = _svg_header(width, height, "Family-level target tool calls")
    lines += [
        '<text x="245" y="36" font-size="24" font-weight="700">Non-path Memory and target tool cost</text>',
        '<text x="245" y="62" font-size="14">Family means across three nested provider blocks; lower is better.</text>',
    ]
    for tick in range(axis_max + 1):
        xpos = x(tick)
        lines.append(
            f'<line x1="{xpos:.1f}" y1="{top}" x2="{xpos:.1f}" y2="{height-bottom}" stroke="#ECECEC"/>'
        )
        lines.append(
            f'<text x="{xpos:.1f}" y="{height-bottom+23}" font-size="12" text-anchor="middle">{tick}</text>'
        )
    row_height = (height - top - bottom) / len(family_rows)
    for index, row in enumerate(family_rows):
        y = top + row_height * (index + 0.5)
        warm = float(row["warm_tool_calls"])
        cold = float(row["cold_tool_calls"])
        lines += [
            f'<text x="{left-12}" y="{y+4:.1f}" font-size="12" text-anchor="end">{escape(str(row["family_id"]))}</text>',
            f'<line x1="{x(cold):.1f}" y1="{y:.1f}" x2="{x(warm):.1f}" y2="{y:.1f}" stroke="#777" stroke-width="2"/>',
            f'<circle cx="{x(cold):.1f}" cy="{y:.1f}" r="6" fill="{ORANGE}"/>',
            f'<circle cx="{x(warm):.1f}" cy="{y:.1f}" r="6" fill="{BLUE}"/>',
        ]
    lines += [
        f'<circle cx="{left}" cy="{height-18}" r="6" fill="{BLUE}"/>',
        f'<text x="{left+12}" y="{height-13}" font-size="12">Memory</text>',
        f'<circle cx="{left+100}" cy="{height-18}" r="6" fill="{ORANGE}"/>',
        f'<text x="{left+112}" y="{height-13}" font-size="12">Cold</text>',
        "</svg>",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_reduction_figure(path: Path, statistics_doc: Mapping[str, object]) -> None:
    selected = ("tool_calls", "task_input_tokens", "task_model_calls", "tool_failures")
    points = []
    for metric in selected:
        relative = statistics_doc["metrics"][metric]["family_cluster_bootstrap"][
            "relative_percent"
        ]
        points.append(
            (
                METRIC_LABELS[metric],
                float(relative["estimate"]),
                float(relative["ci95"][0]),
                float(relative["ci95"][1]),
            )
        )
    low = math.floor((min(0.0, *(p[2] for p in points)) - 10) / 10) * 10
    high = math.ceil((max(0.0, *(p[3] for p in points)) + 10) / 10) * 10
    width, height = 1080, 500
    left, right, top, bottom = 250, 70, 95, 65
    plot_width = width - left - right

    def x(value: float) -> float:
        return left + plot_width * (value - low) / (high - low)

    lines = _svg_header(width, height, "Family-cluster efficiency effects")
    lines += [
        '<text x="250" y="36" font-size="24" font-weight="700">Efficiency effects across 12 task families</text>',
        '<text x="250" y="62" font-size="14">Point estimate and 95% family-cluster bootstrap interval; positive favors Memory.</text>',
    ]
    for tick in range(int(low), int(high) + 1, 20):
        xpos = x(tick)
        stroke = "#333" if tick == 0 else "#ECECEC"
        lines.append(
            f'<line x1="{xpos:.1f}" y1="{top}" x2="{xpos:.1f}" y2="{height-bottom}" stroke="{stroke}"/>'
        )
        lines.append(
            f'<text x="{xpos:.1f}" y="{height-bottom+23}" font-size="12" text-anchor="middle">{tick}%</text>'
        )
    row_height = (height - top - bottom) / len(points)
    for index, (label, estimate, ci_low, ci_high) in enumerate(points):
        y = top + row_height * (index + 0.5)
        lines += [
            f'<text x="{left-13}" y="{y+5:.1f}" font-size="13" text-anchor="end">{escape(label)}</text>',
            f'<line x1="{x(ci_low):.1f}" y1="{y:.1f}" x2="{x(ci_high):.1f}" y2="{y:.1f}" stroke="{BLUE}" stroke-width="4"/>',
            f'<circle cx="{x(estimate):.1f}" cy="{y:.1f}" r="7" fill="{BLUE}"/>',
            f'<text x="{min(width-5, x(ci_high)+8):.1f}" y="{y+5:.1f}" font-size="11">{estimate:.1f}% [{ci_low:.1f}, {ci_high:.1f}]</text>',
        ]
    lines.append("</svg>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_success_heatmap(
    path: Path, pair_rows: Sequence[Mapping[str, object]]
) -> None:
    families = sorted({str(row["family_id"]) for row in pair_rows})
    lookup = {
        (str(row["family_id"]), int(row["block"]), condition): bool(
            row[f"{condition}_success"]
        )
        for row in pair_rows
        for condition in ("warm", "cold")
    }
    width, height = 990, 710
    left, top = 245, 110
    cell_width, cell_height = 112, 40
    lines = _svg_header(width, height, "Target execution success matrix")
    lines += [
        '<text x="245" y="36" font-size="24" font-weight="700">Target execution success by block</text>',
        '<text x="245" y="62" font-size="14">Green: all target gates including an in-Turn successful verifier; red: at least one failed.</text>',
    ]
    columns = [(block, condition) for block in range(1, 4) for condition in ("warm", "cold")]
    for column, (block, condition) in enumerate(columns):
        x = left + column * cell_width
        label = f"B{block} {'Memory' if condition == 'warm' else 'Cold'}"
        lines.append(
            f'<text x="{x+cell_width/2:.1f}" y="{top-14}" font-size="12" text-anchor="middle">{label}</text>'
        )
    for row_index, family in enumerate(families):
        y = top + row_index * cell_height
        lines.append(
            f'<text x="{left-12}" y="{y+26:.1f}" font-size="12" text-anchor="end">{escape(family)}</text>'
        )
        for column, (block, condition) in enumerate(columns):
            x = left + column * cell_width
            success = lookup[(family, block, condition)]
            lines += [
                f'<rect x="{x+2}" y="{y+2}" width="{cell_width-4}" height="{cell_height-4}" rx="3" fill="{GREEN if success else RED}"/>',
                f'<text x="{x+cell_width/2:.1f}" y="{y+26:.1f}" font-size="11" text-anchor="middle" fill="white">{"pass" if success else "fail"}</text>',
            ]
    lines.append("</svg>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt(value: object, digits: int = 2) -> str:
    if value is None:
        return "NA"
    return f"{float(value):.{digits}f}"


def _fmt_p(value: object) -> str:
    probability = float(value)
    return f"{probability:.6f}" if probability < 0.001 else f"{probability:.4f}"


def _metric_line(statistics_doc: Mapping[str, object], metric: str) -> str:
    values = statistics_doc["metrics"][metric]
    bootstrap = values["family_cluster_bootstrap"]["relative_percent"]
    adjusted_key = (
        "wilcoxon_holm_p_primary_family"
        if metric in PRIMARY_METRICS
        else "wilcoxon_holm_p_exploratory_family"
    )
    return (
        f"{METRIC_LABELS[metric]}: {_fmt(bootstrap['estimate'], 1)}% reduction "
        f"(family-cluster 95% CI {_fmt(bootstrap['ci95'][0], 1)}% to "
        f"{_fmt(bootstrap['ci95'][1], 1)}%); exact Wilcoxon "
        f"p={_fmt_p(values['wilcoxon_exact']['p_two_sided'])}, Holm-adjusted "
        f"p={_fmt_p(values[adjusted_key])}."
    )


def write_analysis_report(
    path: Path,
    statistics_doc: Mapping[str, object],
    manifest_path: Path,
    result_path: Path,
) -> None:
    warm = statistics_doc["conditionSummary"]["warm"]
    cold = statistics_doc["conditionSummary"]["cold"]
    learning = statistics_doc["learningSummary"]
    strata = statistics_doc["stratumSummary"]
    stratum_lines = []
    for name, values in strata.items():
        stratum_lines.append(
            f"- `{name}`: Memory {values['warm']['successes']}/{values['warm']['n']}, "
            f"cold {values['cold']['successes']}/{values['cold']['n']}; mean tool calls "
            f"{values['warm']['meanToolCalls']:.2f} vs {values['cold']['meanToolCalls']:.2f}."
        )
    content = f"""# Analysis Report: Non-Path Persistent Memory

## Analysis contract

- Design: 12 independent synthetic families in four non-path strata, three randomized provider blocks, 36 paired target comparisons and 72 target Turns.
- Inferential unit: family (`n=12`); blocks are nested stochastic repeats.
- Target success requires the external content/command/marker gates and a successful `run_command` in the target Turn. An independent post-Run verifier cannot conceal failure to verify inside the agent Turn.
- All first-attempt formal-study observations are included. The four-case development smoke is not pooled.

## Outcome

Memory achieved strict target execution success in {warm['targetExecutionSuccesses']}/{warm['targetTurns']} Turns ({100 * warm['targetExecutionSuccesses'] / warm['targetTurns']:.1f}%) versus {cold['targetExecutionSuccesses']}/{cold['targetTurns']} ({100 * cold['targetExecutionSuccesses'] / cold['targetTurns']:.1f}%) for cold controls. Full case-chain success was {warm['caseChainSuccesses']}/{warm['targetTurns']} versus {cold['caseChainSuccesses']}/{cold['targetTurns']}. Memory was injected in {warm['memoryInjections']}/{warm['targetTurns']} intended target Turns and in {cold['memoryInjections']} cold Turns.

The result is favorable but not perfect: Memory had {warm['targetTurns'] - warm['targetExecutionSuccesses']} strict failures and cold had {cold['targetTurns'] - cold['targetExecutionSuccesses']}. A pre-registered exact-source-string oracle rejected two semantically correct `expired-session` fixes—one per condition—even though the independent unittest, in-Turn verifier and marker passed. The transparent semantic sensitivity count is therefore {warm['semanticTargetExecutionSuccesses']}/{warm['targetTurns']} versus {cold['semanticTargetExecutionSuccesses']}/{cold['targetTurns']}; the strict count remains primary. The other failures were mostly command-shape or working-directory mistakes rejected by the permission layer.

## Results by lesson stratum

{chr(10).join(stratum_lines)}

## Primary efficiency endpoints

- {_metric_line(statistics_doc, 'tool_calls')}
- {_metric_line(statistics_doc, 'task_input_tokens')}

## Secondary endpoints

- {_metric_line(statistics_doc, 'task_model_calls')}
- {_metric_line(statistics_doc, 'task_output_tokens')}
- {_metric_line(statistics_doc, 'tool_failures')}
- {_metric_line(statistics_doc, 'duration_ms')}

## Lesson creation and reuse

All {learning['lessonsWritten']}/{learning['lessonCreationTurns']} learned-condition creation Turns wrote a durable lesson; {learning['verifiedLearningTurns']}/{learning['lessonCreationTurns']} also met the strict learning evidence gate. A creation Turn used a mean {_fmt(learning['taskInputTokens']['mean'], 0)} task input tokens. Treating the whole useful creation Turn as overhead gives a descriptive break-even of {_fmt(learning['conservativeBreakEvenReuses'], 2)} comparable reuses when the denominator is positive.

The stored examples are genuinely non-path: corrected command invocations, code-repair actions, project compatibility constraints and required verification commands. This study therefore extends the earlier path-only evidence boundary, but only for these synthetic task forms.

## Interpretation boundary

The randomized paired result supports the claim that relevant approved Memory can improve non-path engineering work in these four strata. It does not prove general benefit on arbitrary repositories, multi-file architecture work or unseen tool families. Provider behavior was noisy, the same high-level unittest mechanism appears across families, and 12 families give limited power for small effects. Safety denials are correct behavior, while repeated attempts after denial remain an agent-efficiency defect.

## Reproducibility authority

- Manifest: `{manifest_path}` (SHA-256 `{base._sha256(manifest_path)}`)
- First-attempt result: `{result_path}` (SHA-256 `{base._sha256(result_path)}`)
- Bootstrap: {BOOTSTRAP_SAMPLES:,} family-cluster samples, seed `{BOOTSTRAP_SEED}`.
"""
    path.write_text(content, encoding="utf-8")


def write_stats_appendix(path: Path, statistics_doc: Mapping[str, object]) -> None:
    lines = [
        "# Statistical Appendix",
        "",
        "## Methods",
        "",
        "Each family is averaged across three provider blocks. Paired differences are cold minus Memory. Exact two-sided Wilcoxon signed-rank tests enumerate all sign assignments after zero removal. Confidence intervals are deterministic percentile intervals from 20,000 family-cluster bootstrap resamples. The two primary endpoints form one Holm family; four secondary endpoints form a separate exploratory Holm family.",
        "",
        "## Family-level results",
        "",
        "| Metric | Cold mean | Memory mean | Mean saving | 95% CI saving | Relative reduction | Wilcoxon p | Holm p | Sign +/−/0 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for metric in (*PRIMARY_METRICS, *SECONDARY_METRICS):
        values = statistics_doc["metrics"][metric]
        absolute = values["family_cluster_bootstrap"]["absolute"]
        relative = values["family_cluster_bootstrap"]["relative_percent"]
        sign = values["sign_test_exact"]
        adjusted_key = (
            "wilcoxon_holm_p_primary_family"
            if metric in PRIMARY_METRICS
            else "wilcoxon_holm_p_exploratory_family"
        )
        lines.append(
            f"| {METRIC_LABELS[metric]} | {_fmt(values['cold_family_summary']['mean'])} | "
            f"{_fmt(values['warm_family_summary']['mean'])} | {_fmt(values['saving_family_summary']['mean'])} | "
            f"[{_fmt(absolute['ci95'][0])}, {_fmt(absolute['ci95'][1])}] | "
            f"{_fmt(relative['estimate'], 1)}% | {_fmt_p(values['wilcoxon_exact']['p_two_sided'])} | "
            f"{_fmt_p(values[adjusted_key])} | {sign['positive']}/{sign['negative']}/{sign['zero']} |"
        )
    lines += [
        "",
        "## Missingness, multiplicity and limits",
        "",
        "There are no missing Run Journals or provider-usage records across 72 formal target Turns. No formal observation was excluded or replaced. The separate four-case smoke is design-development evidence only. Success counts are gates rather than post-hoc superiority tests. Bootstrap intervals capture family variation within this suite, not future model-version or repository drift.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_figure_catalog(path: Path) -> None:
    path.write_text(
        """# Figure Catalog

## Figure 1 — Family-level target tool calls

- File: `figures/figure-01-family-tool-calls.svg`
- Claim: compares paired Memory and cold means for each of 12 families.
- Encoding: x-position is the three-block mean; blue is Memory, orange is cold.
- Caveat: family means hide block-level variance; exact observations remain in `pair-level.csv`.

## Figure 2 — Relative efficiency effects

- File: `figures/figure-02-relative-reduction.svg`
- Claim: summarizes percentage savings and family-cluster uncertainty.
- Encoding: points are pooled relative effects; lines are 95% percentile bootstrap intervals; positive favors Memory.
- Caveat: relative tool-failure effects can be unstable when the cold denominator is small; absolute effects are in the statistical appendix.

## Figure 3 — Strict target success matrix

- File: `figures/figure-03-target-success-heatmap.svg`
- Claim: exposes every pass and failure by family, block and condition.
- Encoding: green requires all target gates plus an in-Turn successful verifier; red means at least one gate failed.
- Caveat: it does not distinguish different failure severities; `turn-level.csv` contains failed-oracle labels and tool metrics.

All figures are deterministic vector SVGs generated from the first-attempt Run Journals; no plotted value was transcribed manually.
""",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest_path = args.manifest.resolve()
    result_path = args.result.resolve()
    output_dir = args.output_dir.resolve()
    evidence_root = result_path.parent / f"{result_path.stem}-evidence"
    manifest = base._load_json(manifest_path)
    result = base._load_json(result_path)
    target_rows, learning_rows = extract_rows(manifest, result, evidence_root)
    pair_rows = build_pair_rows(target_rows)
    family_rows = build_family_rows(pair_rows)
    statistics_doc = analyze(target_rows, pair_rows, family_rows, learning_rows)
    statistics_doc["suiteId"] = manifest["suiteId"]
    statistics_doc["manifestSha256"] = base._sha256(manifest_path)
    statistics_doc["resultSha256"] = base._sha256(result_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    figures = output_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "turn-level.csv", target_rows)
    _write_csv(output_dir / "learning-turn-level.csv", learning_rows)
    _write_csv(output_dir / "pair-level.csv", pair_rows)
    _write_csv(output_dir / "family-summary.csv", family_rows)
    base._write_json(output_dir / "statistics.json", statistics_doc)
    write_family_tool_figure(figures / "figure-01-family-tool-calls.svg", family_rows)
    write_reduction_figure(figures / "figure-02-relative-reduction.svg", statistics_doc)
    write_success_heatmap(figures / "figure-03-target-success-heatmap.svg", pair_rows)
    write_analysis_report(
        output_dir / "analysis-report.md",
        statistics_doc,
        manifest_path,
        result_path,
    )
    write_stats_appendix(output_dir / "stats-appendix.md", statistics_doc)
    write_figure_catalog(output_dir / "figure-catalog.md")
    generated = [
        output_dir / name
        for name in (
            "turn-level.csv",
            "learning-turn-level.csv",
            "pair-level.csv",
            "family-summary.csv",
            "statistics.json",
            "analysis-report.md",
            "stats-appendix.md",
            "figure-catalog.md",
        )
    ] + sorted(figures.glob("*.svg"))
    base._write_json(
        output_dir / "reproducibility-index.json",
        {
            "schemaVersion": 1,
            "suiteId": manifest["suiteId"],
            "source": {
                "manifest": {
                    "path": str(manifest_path),
                    "sha256": base._sha256(manifest_path),
                },
                "result": {
                    "path": str(result_path),
                    "sha256": base._sha256(result_path),
                },
            },
            "generated": {
                str(path.relative_to(output_dir)): base._sha256(path)
                for path in generated
            },
        },
    )
    print(
        f"analyzed {len(target_rows)} target Turns, {len(pair_rows)} pairs and "
        f"{len(family_rows)} families into {output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
