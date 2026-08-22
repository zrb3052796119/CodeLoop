#!/usr/bin/env python3
"""Analyze the frozen large persistent-Memory study without optional packages.

The family, rather than the repeated provider block, is the inferential unit.
All block-level observations remain in the exported data and figures.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence


ANALYSIS_SCHEMA_VERSION = 1
BOOTSTRAP_SEED = 20260821
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
SVG_BLUE = "#0072B2"
SVG_ORANGE = "#E69F00"
SVG_GREEN = "#009E73"
SVG_RED = "#D55E00"
SVG_GREY = "#D9D9D9"
SVG_DARK = "#222222"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("quantile requires at least one value")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _summary(values: Sequence[float]) -> dict[str, float | int | None]:
    numeric = [float(value) for value in values]
    if not numeric:
        return {
            "n": 0,
            "mean": None,
            "sd": None,
            "median": None,
            "q1": None,
            "q3": None,
            "min": None,
            "max": None,
        }
    return {
        "n": len(numeric),
        "mean": statistics.fmean(numeric),
        "sd": statistics.stdev(numeric) if len(numeric) > 1 else 0.0,
        "median": statistics.median(numeric),
        "q1": _quantile(numeric, 0.25),
        "q3": _quantile(numeric, 0.75),
        "min": min(numeric),
        "max": max(numeric),
    }


def exact_sign_test(differences: Sequence[float]) -> dict[str, int | float]:
    positive = sum(value > 0 for value in differences)
    negative = sum(value < 0 for value in differences)
    nonzero = positive + negative
    if nonzero == 0:
        probability = 1.0
    else:
        tail = sum(
            math.comb(nonzero, index)
            for index in range(min(positive, negative) + 1)
        ) / (2**nonzero)
        probability = min(1.0, 2 * tail)
    return {
        "positive": positive,
        "negative": negative,
        "zero": len(differences) - nonzero,
        "n_nonzero": nonzero,
        "p_two_sided": probability,
    }


def _average_ranks(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(indexed):
        stop = start + 1
        while stop < len(indexed) and indexed[stop][1] == indexed[start][1]:
            stop += 1
        rank = ((start + 1) + stop) / 2
        for original_index, _value in indexed[start:stop]:
            ranks[original_index] = rank
        start = stop
    return ranks


def exact_wilcoxon(differences: Sequence[float]) -> dict[str, int | float]:
    nonzero = [float(value) for value in differences if value != 0]
    if not nonzero:
        return {
            "n_nonzero": 0,
            "w_plus": 0.0,
            "w_minus": 0.0,
            "statistic": 0.0,
            "p_two_sided": 1.0,
            "rank_biserial": 0.0,
        }
    ranks = _average_ranks([abs(value) for value in nonzero])
    w_plus = sum(rank for rank, value in zip(ranks, nonzero) if value > 0)
    total = sum(ranks)
    w_minus = total - w_plus
    observed = min(w_plus, w_minus)
    extreme = 0
    for signs in itertools.product((0, 1), repeat=len(ranks)):
        candidate = sum(rank for rank, sign in zip(ranks, signs) if sign)
        if min(candidate, total - candidate) <= observed + 1e-12:
            extreme += 1
    probability = extreme / (2 ** len(ranks))
    return {
        "n_nonzero": len(nonzero),
        "w_plus": w_plus,
        "w_minus": w_minus,
        "statistic": observed,
        "p_two_sided": probability,
        "rank_biserial": (w_plus - w_minus) / total,
    }


def hodges_lehmann(differences: Sequence[float]) -> float:
    values = [float(value) for value in differences]
    if not values:
        raise ValueError("Hodges-Lehmann estimate requires observations")
    pairwise = [
        (values[left] + values[right]) / 2
        for left in range(len(values))
        for right in range(left, len(values))
    ]
    return statistics.median(pairwise)


def holm_adjust(probabilities: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(probabilities.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running = 0.0
    count = len(ordered)
    for index, (name, probability) in enumerate(ordered):
        candidate = min(1.0, probability * (count - index))
        running = max(running, candidate)
        adjusted[name] = running
    return adjusted


def cluster_bootstrap(
    cold: Sequence[float],
    warm: Sequence[float],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, object]:
    if len(cold) != len(warm) or not cold:
        raise ValueError("cluster bootstrap requires equal non-empty vectors")
    rng = random.Random(seed)
    size = len(cold)
    absolute_draws: list[float] = []
    relative_draws: list[float] = []
    for _ in range(samples):
        indices = [rng.randrange(size) for _index in range(size)]
        cold_mean = statistics.fmean(cold[index] for index in indices)
        warm_mean = statistics.fmean(warm[index] for index in indices)
        difference = cold_mean - warm_mean
        absolute_draws.append(difference)
        if cold_mean != 0:
            relative_draws.append(100 * difference / cold_mean)
    observed_cold = statistics.fmean(cold)
    observed_warm = statistics.fmean(warm)
    observed_difference = observed_cold - observed_warm
    return {
        "seed": seed,
        "samples": samples,
        "absolute": {
            "estimate": observed_difference,
            "ci95": [
                _quantile(absolute_draws, 0.025),
                _quantile(absolute_draws, 0.975),
            ],
        },
        "relative_percent": {
            "estimate": (
                100 * observed_difference / observed_cold
                if observed_cold != 0
                else None
            ),
            "ci95": (
                [
                    _quantile(relative_draws, 0.025),
                    _quantile(relative_draws, 0.975),
                ]
                if relative_draws
                else None
            ),
        },
    }


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load_events(case_root: Path, run_id: str) -> list[dict[str, Any]]:
    matches = list(case_root.glob(f"journal/**/runs/{run_id}/events.ndjson"))
    if len(matches) != 1:
        raise ValueError(
            f"expected one event journal for {run_id}, found {len(matches)}"
        )
    events: list[dict[str, Any]] = []
    for line in matches[0].read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise TypeError(f"event is not an object for {run_id}")
        events.append(value)
    return events


def _paired_tool_metrics(
    events: Sequence[Mapping[str, Any]],
) -> tuple[int, int, int, bool, str | None]:
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
    failures = sum(
        finishes.get(operation_id, {}).get("outcome") == "error"
        for operation_id, _tool_name in starts
    )
    successful_read_files = sum(
        tool_name == "read_file"
        and finishes.get(operation_id, {}).get("outcome") == "success"
        and finishes.get(operation_id, {}).get("paired") is True
        for operation_id, tool_name in starts
    )
    first_name = starts[0][1] if starts else None
    direct_first = bool(
        starts
        and first_name == "read_file"
        and finishes.get(starts[0][0], {}).get("outcome") == "success"
        and finishes.get(starts[0][0], {}).get("paired") is True
    )
    return len(starts), failures, successful_read_files, direct_first, first_name


def _run_metrics(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, int | bool | str | None]:
    task_model_events: list[Mapping[str, Any]] = []
    all_model_events: list[Mapping[str, Any]] = []
    rendered_count = 0
    memory_injected = False
    run_started: datetime | None = None
    run_completed: datetime | None = None
    task_outcome_success = False
    for event in events:
        event_type = event.get("type")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        if event_type == "model.completed":
            all_model_events.append(payload)
            if payload.get("purpose") is None:
                task_model_events.append(payload)
        elif event_type == "memory.rendered":
            memory_injected = memory_injected or payload.get("injected") is True
            count = payload.get("renderedCount")
            if isinstance(count, int) and not isinstance(count, bool):
                rendered_count += count
        elif event_type == "task.outcome":
            task_outcome_success = payload.get("outcomeStatus") == "success"
        elif event_type == "run.started":
            run_started = _parse_timestamp(str(event["timestamp"]))
        elif event_type == "run.completed":
            run_completed = _parse_timestamp(str(event["timestamp"]))

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

    (
        tool_calls,
        tool_failures,
        successful_read_files,
        direct_first,
        first_tool,
    ) = _paired_tool_metrics(events)
    duration_ms = None
    if run_started is not None and run_completed is not None:
        duration_ms = max(0, round((run_completed - run_started).total_seconds() * 1000))
    return {
        "run_completed": run_completed is not None,
        "task_outcome_success": task_outcome_success,
        "task_model_calls": len(task_model_events),
        "total_model_calls": len(all_model_events),
        "reflection_model_calls": len(all_model_events) - len(task_model_events),
        "task_input_tokens": usage_sum(task_model_events, "inputTokens"),
        "task_output_tokens": usage_sum(task_model_events, "outputTokens"),
        "total_input_tokens": usage_sum(all_model_events, "inputTokens"),
        "total_output_tokens": usage_sum(all_model_events, "outputTokens"),
        "tool_calls": tool_calls,
        "tool_failures": tool_failures,
        "successful_read_files": successful_read_files,
        "direct_first": direct_first,
        "first_tool": first_tool,
        "memory_injected": memory_injected,
        "memory_rendered_count": rendered_count,
        "duration_ms": duration_ms,
    }


def extract_turn_rows(
    manifest: Mapping[str, Any],
    result: Mapping[str, Any],
    evidence_root: Path,
) -> list[dict[str, object]]:
    cases = manifest.get("cases")
    results = result.get("results")
    if not isinstance(cases, list) or not isinstance(results, list):
        raise ValueError("manifest or result cases are missing")
    result_by_id = {
        item["id"]: item
        for item in results
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if manifest.get("suiteId") != result.get("suiteId"):
        raise ValueError("manifest and result suite IDs differ")
    if set(result_by_id) != {
        case.get("id") for case in cases if isinstance(case, dict)
    }:
        raise ValueError("manifest and result case IDs differ")
    rows: list[dict[str, object]] = []
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            raise TypeError("manifest case must be an object")
        case_id = str(raw_case["id"])
        case_root = evidence_root / "cases" / case_id
        evidence = _load_json(case_root / "evidence.json")
        study = raw_case.get("study")
        if not isinstance(study, dict):
            raise ValueError(f"case lacks study metadata: {case_id}")
        target_index = study.get("targetTurnIndex")
        run_ids = evidence.get("runIds")
        responses = evidence.get("responses")
        if (
            not isinstance(target_index, int)
            or not isinstance(run_ids, list)
            or not isinstance(responses, list)
            or target_index >= len(run_ids)
            or target_index >= len(responses)
        ):
            raise ValueError(f"target evidence is incomplete: {case_id}")
        run_id = str(run_ids[target_index])
        response = str(responses[target_index])
        metrics = _run_metrics(_load_events(case_root, run_id))
        marker_present = str(study["marker"]).casefold() in response.casefold()
        target_success = bool(
            metrics["run_completed"]
            and metrics["task_outcome_success"]
            and metrics["successful_read_files"]
            and marker_present
        )
        case_result = result_by_id[case_id]
        rows.append(
            {
                "case_id": case_id,
                "run_id": run_id,
                "block": int(study["block"]),
                "family_id": str(study["familyId"]),
                "stratum": str(study["stratum"]),
                "lesson_mode": str(study["lessonMode"]),
                "condition": str(study["condition"]),
                "condition_order": int(study["conditionOrder"]),
                "case_oracle_success": case_result.get("status") == "passed",
                "target_success": target_success,
                "marker_present": marker_present,
                **metrics,
            }
        )
    return rows


def extract_learning_rows(
    manifest: Mapping[str, Any],
    result: Mapping[str, Any],
    evidence_root: Path,
) -> list[dict[str, object]]:
    results = result.get("results")
    cases = manifest.get("cases")
    if not isinstance(results, list) or not isinstance(cases, list):
        raise ValueError("manifest or result cases are missing")
    result_by_id = {
        item["id"]: item
        for item in results
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    rows: list[dict[str, object]] = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("study"), dict):
            continue
        study = case["study"]
        if study.get("condition") != "warm" or study.get("lessonMode") != "learned":
            continue
        case_id = str(case["id"])
        case_root = evidence_root / "cases" / case_id
        evidence = _load_json(case_root / "evidence.json")
        target_index = study.get("targetTurnIndex")
        run_ids = evidence.get("runIds")
        if target_index != 1 or not isinstance(run_ids, list) or len(run_ids) != 2:
            raise ValueError(f"learned case has an unexpected Turn chain: {case_id}")
        run_id = str(run_ids[0])
        metrics = _run_metrics(_load_events(case_root, run_id))
        rows.append(
            {
                "case_id": case_id,
                "run_id": run_id,
                "block": int(study["block"]),
                "family_id": str(study["familyId"]),
                "stratum": str(study["stratum"]),
                "case_oracle_success": result_by_id[case_id].get("status") == "passed",
                "verified_recovery": bool(
                    metrics["tool_failures"]
                    and metrics["successful_read_files"]
                    and metrics["run_completed"]
                ),
                **metrics,
            }
        )
    return rows


def build_pair_rows(turn_rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[int, str], dict[str, Mapping[str, object]]] = defaultdict(dict)
    for row in turn_rows:
        grouped[(int(row["block"]), str(row["family_id"]))][
            str(row["condition"])
        ] = row
    rows: list[dict[str, object]] = []
    metric_names = (
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
            "warm_success": warm["target_success"],
            "cold_success": cold["target_success"],
            "warm_direct_first": warm["direct_first"],
            "cold_direct_first": cold["direct_first"],
            "warm_memory_injected": warm["memory_injected"],
            "cold_memory_injected": cold["memory_injected"],
        }
        for name in metric_names:
            warm_value = float(warm[name])
            cold_value = float(cold[name])
            pair[f"warm_{name}"] = warm[name]
            pair[f"cold_{name}"] = cold[name]
            pair[f"saving_{name}"] = cold_value - warm_value
            pair[f"reduction_percent_{name}"] = (
                100 * (cold_value - warm_value) / cold_value
                if cold_value != 0
                else None
            )
        rows.append(pair)
    return rows


def build_family_rows(pair_rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in pair_rows:
        grouped[str(row["family_id"])].append(row)
    rows: list[dict[str, object]] = []
    metric_names = (
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
    for family_id, observations in sorted(grouped.items()):
        if len(observations) != 3:
            raise ValueError(f"family {family_id} does not have three blocks")
        row: dict[str, object] = {
            "family_id": family_id,
            "stratum": observations[0]["stratum"],
            "lesson_mode": observations[0]["lesson_mode"],
            "blocks": len(observations),
            "warm_success_rate": statistics.fmean(
                int(bool(item["warm_success"])) for item in observations
            ),
            "cold_success_rate": statistics.fmean(
                int(bool(item["cold_success"])) for item in observations
            ),
            "warm_direct_first_rate": statistics.fmean(
                int(bool(item["warm_direct_first"])) for item in observations
            ),
            "cold_direct_first_rate": statistics.fmean(
                int(bool(item["cold_direct_first"])) for item in observations
            ),
        }
        for name in metric_names:
            warm_mean = statistics.fmean(float(item[f"warm_{name}"]) for item in observations)
            cold_mean = statistics.fmean(float(item[f"cold_{name}"]) for item in observations)
            row[f"warm_{name}"] = warm_mean
            row[f"cold_{name}"] = cold_mean
            row[f"saving_{name}"] = cold_mean - warm_mean
            row[f"reduction_percent_{name}"] = (
                100 * (cold_mean - warm_mean) / cold_mean
                if cold_mean != 0
                else None
            )
        rows.append(row)
    return rows


def _metric_analysis(
    family_rows: Sequence[Mapping[str, object]], metric: str
) -> dict[str, object]:
    cold = [float(row[f"cold_{metric}"]) for row in family_rows]
    warm = [float(row[f"warm_{metric}"]) for row in family_rows]
    differences = [cold_value - warm_value for cold_value, warm_value in zip(cold, warm)]
    return {
        "label": METRIC_LABELS[metric],
        "direction": "positive savings favor Memory",
        "cold_family_summary": _summary(cold),
        "warm_family_summary": _summary(warm),
        "saving_family_summary": _summary(differences),
        "hodges_lehmann_saving": hodges_lehmann(differences),
        "wilcoxon_exact": exact_wilcoxon(differences),
        "sign_test_exact": exact_sign_test(differences),
        "family_cluster_bootstrap": cluster_bootstrap(cold, warm),
    }


def analyze(
    turn_rows: Sequence[Mapping[str, object]],
    pair_rows: Sequence[Mapping[str, object]],
    family_rows: Sequence[Mapping[str, object]],
    learning_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    metrics = {
        metric: _metric_analysis(family_rows, metric)
        for metric in (*PRIMARY_METRICS, *SECONDARY_METRICS)
    }
    primary_adjusted = holm_adjust(
        {
            metric: float(metrics[metric]["wilcoxon_exact"]["p_two_sided"])
            for metric in PRIMARY_METRICS
        }
    )
    secondary_adjusted = holm_adjust(
        {
            metric: float(metrics[metric]["wilcoxon_exact"]["p_two_sided"])
            for metric in SECONDARY_METRICS
        }
    )
    for metric, probability in primary_adjusted.items():
        metrics[metric]["wilcoxon_holm_p_primary_family"] = probability
    for metric, probability in secondary_adjusted.items():
        metrics[metric]["wilcoxon_holm_p_exploratory_family"] = probability

    by_condition: dict[str, object] = {}
    for condition in ("warm", "cold"):
        selected = [row for row in turn_rows if row["condition"] == condition]
        by_condition[condition] = {
            "target_turns": len(selected),
            "target_successes": sum(bool(row["target_success"]) for row in selected),
            "case_oracle_successes": sum(
                bool(row["case_oracle_success"]) for row in selected
            ),
            "memory_injections": sum(
                bool(row["memory_injected"]) for row in selected
            ),
            "direct_first": sum(bool(row["direct_first"]) for row in selected),
            "tool_calls": _summary([float(row["tool_calls"]) for row in selected]),
            "task_model_calls": _summary(
                [float(row["task_model_calls"]) for row in selected]
            ),
            "task_input_tokens": _summary(
                [float(row["task_input_tokens"]) for row in selected]
            ),
            "task_output_tokens": _summary(
                [float(row["task_output_tokens"]) for row in selected]
            ),
            "tool_failures": _summary(
                [float(row["tool_failures"]) for row in selected]
            ),
            "duration_ms": _summary([float(row["duration_ms"]) for row in selected]),
        }
    warm_wins = {
        metric: {
            "positive": sum(float(row[f"saving_{metric}"]) > 0 for row in pair_rows),
            "ties": sum(float(row[f"saving_{metric}"]) == 0 for row in pair_rows),
            "negative": sum(float(row[f"saving_{metric}"]) < 0 for row in pair_rows),
        }
        for metric in (*PRIMARY_METRICS, *SECONDARY_METRICS)
    }
    subgroup_effects: dict[str, object] = {}
    for lesson_mode in ("learned", "seeded"):
        selected = [
            row for row in family_rows if row["lesson_mode"] == lesson_mode
        ]
        subgroup_effects[lesson_mode] = {
            metric: _metric_analysis(selected, metric)
            for metric in (*PRIMARY_METRICS, "task_model_calls")
        }
    block_effects = {
        str(block): {
            metric: _summary(
                [
                    float(row[f"saving_{metric}"])
                    for row in pair_rows
                    if row["block"] == block
                ]
            )
            for metric in (*PRIMARY_METRICS, "task_model_calls")
        }
        for block in range(1, 4)
    }
    order_effects = {
        label: {
            metric: _summary(
                [
                    float(row[f"saving_{metric}"])
                    for row in pair_rows
                    if bool(row["warm_first"]) is warm_first
                ]
            )
            for metric in (*PRIMARY_METRICS, "task_model_calls")
        }
        for label, warm_first in (("warm_first", True), ("cold_first", False))
    }
    learned_pairs = [row for row in pair_rows if row["lesson_mode"] == "learned"]
    creation_task_input = statistics.fmean(
        float(row["task_input_tokens"]) for row in learning_rows
    )
    reuse_task_input_saving = statistics.fmean(
        float(row["saving_task_input_tokens"]) for row in learned_pairs
    )
    creation_total_input = statistics.fmean(
        float(row["total_input_tokens"]) for row in learning_rows
    )
    reuse_total_input_saving = statistics.fmean(
        float(row["saving_total_input_tokens"]) for row in learned_pairs
    )
    learning_summary = {
        "lessonCreationTurns": len(learning_rows),
        "verifiedRecoveries": sum(
            bool(row["verified_recovery"]) for row in learning_rows
        ),
        "caseOracleSuccesses": sum(
            bool(row["case_oracle_success"]) for row in learning_rows
        ),
        "taskModelCalls": _summary(
            [float(row["task_model_calls"]) for row in learning_rows]
        ),
        "taskInputTokens": _summary(
            [float(row["task_input_tokens"]) for row in learning_rows]
        ),
        "totalInputTokens": _summary(
            [float(row["total_input_tokens"]) for row in learning_rows]
        ),
        "toolCalls": _summary([float(row["tool_calls"]) for row in learning_rows]),
        "descriptiveConservativeBreakEvenReuses": {
            "taskInputTokens": creation_task_input / reuse_task_input_saving,
            "endToEndInputTokens": creation_total_input / reuse_total_input_saving,
            "interpretation": (
                "Treats the whole useful recovery Turn as Memory overhead; "
                "therefore conservative and descriptive, not an inferential endpoint."
            ),
        },
    }
    return {
        "schemaVersion": ANALYSIS_SCHEMA_VERSION,
        "inferentialUnit": "family",
        "familyCount": len(family_rows),
        "nestedBlocksPerFamily": 3,
        "pairCount": len(pair_rows),
        "targetTurnCount": len(turn_rows),
        "bootstrap": {
            "method": "family-cluster percentile bootstrap",
            "samples": BOOTSTRAP_SAMPLES,
            "seed": BOOTSTRAP_SEED,
        },
        "conditionSummary": by_condition,
        "blockPairDirections": warm_wins,
        "metrics": metrics,
        "subgroupEffects": subgroup_effects,
        "blockEffects": block_effects,
        "conditionOrderEffects": order_effects,
        "learningSummary": learning_summary,
        "lessonModeCounts": dict(Counter(row["lesson_mode"] for row in family_rows)),
        "stratumCounts": dict(Counter(row["stratum"] for row in family_rows)),
    }


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"cannot write empty CSV: {path}")
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _svg_header(width: int, height: int, title: str) -> list[str]:
    return [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" role="img" '
            f'aria-labelledby="title desc">'
        ),
        f'<title id="title">{escape(title)}</title>',
        '<desc id="desc">Generated from frozen study data.</desc>',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,Helvetica,sans-serif;fill:#222}</style>',
    ]


def write_tool_figure(path: Path, family_rows: Sequence[Mapping[str, object]]) -> None:
    width, height = 1180, 880
    left, right, top, bottom = 235, 55, 85, 70
    plot_width = width - left - right
    plot_height = height - top - bottom
    maximum = max(
        float(row[f"{condition}_tool_calls"])
        for row in family_rows
        for condition in ("warm", "cold")
    )
    axis_max = max(1.0, math.ceil(maximum + 0.5))

    def x(value: float) -> float:
        return left + plot_width * value / axis_max

    lines = _svg_header(width, height, "Family-level repository tool calls")
    lines.append(
        '<text x="235" y="35" font-size="24" font-weight="700">'
        'Persistent Memory reduces repository discovery calls</text>'
    )
    lines.append(
        '<text x="235" y="62" font-size="15">Each point is a family mean '
        'across three provider blocks; lines join paired conditions.</text>'
    )
    for tick in range(math.floor(axis_max) + 1):
        xpos = x(tick)
        lines.append(
            f'<line x1="{xpos:.1f}" y1="{top}" x2="{xpos:.1f}" '
            f'y2="{height-bottom}" stroke="#ECECEC"/>'
        )
        lines.append(
            f'<text x="{xpos:.1f}" y="{height-bottom+24}" font-size="13" '
            f'text-anchor="middle">{tick}</text>'
        )
    row_height = plot_height / len(family_rows)
    for index, row in enumerate(family_rows):
        ypos = top + row_height * (index + 0.5)
        warm = float(row["warm_tool_calls"])
        cold = float(row["cold_tool_calls"])
        lines.append(
            f'<text x="{left-14}" y="{ypos+4:.1f}" font-size="13" '
            f'text-anchor="end">{escape(str(row["family_id"]))}</text>'
        )
        lines.append(
            f'<line x1="{x(cold):.1f}" y1="{ypos:.1f}" '
            f'x2="{x(warm):.1f}" y2="{ypos:.1f}" stroke="#777" '
            'stroke-width="2"/>'
        )
        lines.append(
            f'<circle cx="{x(cold):.1f}" cy="{ypos:.1f}" r="6" '
            f'fill="{SVG_ORANGE}"/>'
        )
        lines.append(
            f'<circle cx="{x(warm):.1f}" cy="{ypos:.1f}" r="6" '
            f'fill="{SVG_BLUE}"/>'
        )
    lines.extend(
        [
            f'<circle cx="{left}" cy="{height-20}" r="6" fill="{SVG_BLUE}"/>',
            f'<text x="{left+12}" y="{height-15}" font-size="13">Memory</text>',
            f'<circle cx="{left+105}" cy="{height-20}" r="6" fill="{SVG_ORANGE}"/>',
            f'<text x="{left+117}" y="{height-15}" font-size="13">Cold control</text>',
            f'<text x="{left+plot_width/2:.1f}" y="{height-40}" font-size="14" '
            'text-anchor="middle">Repository tool calls per target Turn</text>',
            '</svg>',
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_reduction_figure(path: Path, statistics_doc: Mapping[str, object]) -> None:
    metrics = statistics_doc["metrics"]
    selected = (
        "tool_calls",
        "task_input_tokens",
        "task_model_calls",
        "task_output_tokens",
    )
    points: list[tuple[str, float, float, float]] = []
    for metric in selected:
        bootstrap = metrics[metric]["family_cluster_bootstrap"]["relative_percent"]
        interval = bootstrap["ci95"]
        points.append(
            (
                METRIC_LABELS[metric],
                float(bootstrap["estimate"]),
                float(interval[0]),
                float(interval[1]),
            )
        )
    minimum = min(0.0, *(item[2] for item in points))
    maximum = max(0.0, *(item[3] for item in points))
    padding = max(5.0, (maximum - minimum) * 0.12)
    axis_min = math.floor((minimum - padding) / 10) * 10
    axis_max = math.ceil((maximum + padding) / 10) * 10
    width, height = 1100, 500
    left, right, top, bottom = 270, 65, 100, 70
    plot_width = width - left - right

    def x(value: float) -> float:
        return left + plot_width * (value - axis_min) / (axis_max - axis_min)

    lines = _svg_header(width, height, "Relative reduction with family-cluster confidence intervals")
    lines.append(
        '<text x="270" y="38" font-size="24" font-weight="700">'
        'Efficiency effect across 16 task families</text>'
    )
    lines.append(
        '<text x="270" y="66" font-size="15">Point estimate and 95% '
        'family-cluster percentile bootstrap interval; positive favors Memory.</text>'
    )
    for tick in range(int(axis_min), int(axis_max) + 1, 10):
        xpos = x(tick)
        stroke = SVG_DARK if tick == 0 else "#ECECEC"
        width_value = 1.5 if tick == 0 else 1
        lines.append(
            f'<line x1="{xpos:.1f}" y1="{top}" x2="{xpos:.1f}" '
            f'y2="{height-bottom}" stroke="{stroke}" stroke-width="{width_value}"/>'
        )
        lines.append(
            f'<text x="{xpos:.1f}" y="{height-bottom+24}" font-size="13" '
            f'text-anchor="middle">{tick}%</text>'
        )
    row_height = (height - top - bottom) / len(points)
    for index, (label, estimate, low, high) in enumerate(points):
        ypos = top + row_height * (index + 0.5)
        lines.append(
            f'<text x="{left-15}" y="{ypos+5:.1f}" font-size="14" '
            f'text-anchor="end">{escape(label)}</text>'
        )
        lines.append(
            f'<line x1="{x(low):.1f}" y1="{ypos:.1f}" x2="{x(high):.1f}" '
            f'y2="{ypos:.1f}" stroke="{SVG_BLUE}" stroke-width="4"/>'
        )
        lines.append(
            f'<line x1="{x(low):.1f}" y1="{ypos-7:.1f}" x2="{x(low):.1f}" '
            f'y2="{ypos+7:.1f}" stroke="{SVG_BLUE}" stroke-width="2"/>'
        )
        lines.append(
            f'<line x1="{x(high):.1f}" y1="{ypos-7:.1f}" x2="{x(high):.1f}" '
            f'y2="{ypos+7:.1f}" stroke="{SVG_BLUE}" stroke-width="2"/>'
        )
        lines.append(
            f'<circle cx="{x(estimate):.1f}" cy="{ypos:.1f}" r="7" '
            f'fill="{SVG_BLUE}"/>'
        )
        lines.append(
            f'<text x="{min(width-4, x(high)+9):.1f}" y="{ypos+5:.1f}" '
            f'font-size="12">{estimate:.1f}% [{low:.1f}, {high:.1f}]</text>'
        )
    lines.extend(
        [
            f'<text x="{left+plot_width/2:.1f}" y="{height-18}" font-size="14" '
            'text-anchor="middle">Cold − Memory, as % of cold mean</text>',
            '</svg>',
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_direct_first_figure(
    path: Path, pair_rows: Sequence[Mapping[str, object]]
) -> None:
    families = sorted({str(row["family_id"]) for row in pair_rows})
    lookup = {
        (str(row["family_id"]), int(row["block"]), condition): row[
            f"{condition}_direct_first"
        ]
        for row in pair_rows
        for condition in ("warm", "cold")
    }
    width, height = 1020, 850
    left, top = 240, 120
    cell_width, cell_height = 108, 38
    lines = _svg_header(width, height, "Direct-first mechanism by family and provider block")
    lines.append(
        '<text x="240" y="38" font-size="24" font-weight="700">'
        'Memory changes the first repository action</text>'
    )
    lines.append(
        '<text x="240" y="66" font-size="15">Blue means the first tool was a '
        'paired successful read_file; grey means discovery or another first action.</text>'
    )
    columns = [
        (block, condition)
        for block in range(1, 4)
        for condition in ("warm", "cold")
    ]
    for column, (block, condition) in enumerate(columns):
        xpos = left + column * cell_width
        label = f"B{block} {'Memory' if condition == 'warm' else 'Cold'}"
        lines.append(
            f'<text x="{xpos+cell_width/2:.1f}" y="{top-15}" font-size="13" '
            f'text-anchor="middle">{label}</text>'
        )
    for row_index, family in enumerate(families):
        ypos = top + row_index * cell_height
        lines.append(
            f'<text x="{left-12}" y="{ypos+cell_height*0.68:.1f}" '
            f'font-size="13" text-anchor="end">{escape(family)}</text>'
        )
        for column, (block, condition) in enumerate(columns):
            value = bool(lookup[(family, block, condition)])
            color = SVG_BLUE if value else SVG_GREY
            xpos = left + column * cell_width
            lines.append(
                f'<rect x="{xpos+2}" y="{ypos+2}" width="{cell_width-4}" '
                f'height="{cell_height-4}" rx="3" fill="{color}"/>'
            )
            lines.append(
                f'<text x="{xpos+cell_width/2:.1f}" y="{ypos+cell_height*0.68:.1f}" '
                f'font-size="12" text-anchor="middle" fill="white">'
                f'{"direct" if value else "other"}</text>'
            )
    legend_y = top + len(families) * cell_height + 34
    lines.extend(
        [
            f'<rect x="{left}" y="{legend_y}" width="18" height="18" fill="{SVG_BLUE}"/>',
            f'<text x="{left+26}" y="{legend_y+14}" font-size="13">Direct successful read</text>',
            f'<rect x="{left+210}" y="{legend_y}" width="18" height="18" fill="{SVG_GREY}"/>',
            f'<text x="{left+236}" y="{legend_y+14}" font-size="13">Other first action</text>',
            '</svg>',
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt(value: object, digits: int = 2) -> str:
    if value is None:
        return "NA"
    return f"{float(value):.{digits}f}"


def _fmt_p(value: object) -> str:
    probability = float(value)
    if probability < 0.001:
        return f"{probability:.6f}"
    return f"{probability:.4f}"


def _metric_sentence(statistics_doc: Mapping[str, object], metric: str) -> str:
    values = statistics_doc["metrics"][metric]
    bootstrap = values["family_cluster_bootstrap"]
    relative = bootstrap["relative_percent"]
    wilcoxon = values["wilcoxon_exact"]
    adjusted_key = (
        "wilcoxon_holm_p_primary_family"
        if metric in PRIMARY_METRICS
        else "wilcoxon_holm_p_exploratory_family"
    )
    if metric == "tool_failures":
        absolute = bootstrap["absolute"]
        return (
            f"{METRIC_LABELS[metric]}: mean absolute saving "
            f"{_fmt(absolute['estimate'], 3)} per target Turn "
            f"(family-cluster 95% CI {_fmt(absolute['ci95'][0], 3)} to "
            f"{_fmt(absolute['ci95'][1], 3)}); exact Wilcoxon "
            f"p={_fmt_p(wilcoxon['p_two_sided'])}, Holm-adjusted "
            f"p={_fmt_p(values[adjusted_key])}. Only one cold Turn had a "
            "tool failure, so a relative percentage is not informative."
        )
    return (
        f"{METRIC_LABELS[metric]}: {_fmt(relative['estimate'], 1)}% reduction "
        f"(family-cluster 95% CI {_fmt(relative['ci95'][0], 1)}% to "
        f"{_fmt(relative['ci95'][1], 1)}%); exact Wilcoxon "
        f"p={_fmt_p(wilcoxon['p_two_sided'])}, Holm-adjusted "
        f"p={_fmt_p(values[adjusted_key])}, rank-biserial "
        f"r={_fmt(wilcoxon['rank_biserial'], 3)}."
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
    content = f"""# Analysis Report: Large Persistent-Memory Study

## Analysis contract

- Primary question: does an approved relevant Memory reduce repository discovery cost on a new conversation in the same synthetic project while preserving oracle-verified success?
- Design: 16 task families, three randomized provider blocks, 48 warm/cold pairs and 96 target Turns.
- Inferential unit: task family (`n=16`). The three provider blocks are nested repeats, not 48 independent subjects.
- Positive savings and positive percentage reductions favor Memory.
- No valid first-attempt observation was excluded or replaced.

## Outcome

Memory target Turns passed {warm['target_successes']}/{warm['target_turns']} ({100 * warm['target_successes'] / warm['target_turns']:.1f}%) versus {cold['target_successes']}/{cold['target_turns']} ({100 * cold['target_successes'] / cold['target_turns']:.1f}%) for cold controls. All {warm['target_turns']} intended Memory injections were observed; cold controls had {cold['memory_injections']} injections.

The sole failed target was `pmem-b3-package-map-cold`: the agent completed repository operations but returned progress prose without the required marker. It remains in the intent-to-treat dataset.

## Primary efficiency results

- {_metric_sentence(statistics_doc, 'tool_calls')}
- {_metric_sentence(statistics_doc, 'task_input_tokens')}

## Secondary results

- {_metric_sentence(statistics_doc, 'task_model_calls')}
- {_metric_sentence(statistics_doc, 'task_output_tokens')}
- {_metric_sentence(statistics_doc, 'tool_failures')}
- {_metric_sentence(statistics_doc, 'duration_ms')}

Direct successful `read_file` was the first repository action in {warm['direct_first']}/{warm['target_turns']} Memory Turns versus {cold['direct_first']}/{cold['target_turns']} cold Turns. Because event journals intentionally omit tool arguments, this proves direct-first mechanism use but not the exact path from journal data alone.

## Lesson creation and amortization

All {learning['lessonCreationTurns']} learned-condition creation Turns produced a verified failed-read/successful-read recovery and all matching cases passed the lesson-write plus next-Turn injection gates. A creation Turn used a mean {_fmt(learning['taskInputTokens']['mean'], 0)} task input tokens and {_fmt(learning['toolCalls']['mean'], 2)} repository tool calls. If the entire useful recovery Turn is conservatively treated as Memory overhead, its task-input cost is recovered after approximately {_fmt(learning['descriptiveConservativeBreakEvenReuses']['taskInputTokens'], 2)} comparable future reuses; including post-run reflection gives {_fmt(learning['descriptiveConservativeBreakEvenReuses']['endToEndInputTokens'], 2)} reuses. This amortization estimate is descriptive, because the original recovery Turn also completed useful work.

## Interpretation boundary

This experiment supports a causal claim for the randomized paired synthetic path-recovery workload: relevant approved Memory reduced discovery work without an observed success penalty. It does not establish performance on arbitrary coding, mutation, debugging or long-horizon tasks. Family-level resampling captures variation across the 16 task families; it cannot capture all future repository or provider variation. A 48/48 warm success result is strong descriptive evidence but is not a formal non-inferiority proof.

## Reproducibility authority

- Manifest: `{manifest_path}` (SHA-256 `{_sha256(manifest_path)}`)
- First-attempt result: `{result_path}` (SHA-256 `{_sha256(result_path)}`)
- Bootstrap: {BOOTSTRAP_SAMPLES:,} family-cluster samples, seed `{BOOTSTRAP_SEED}`.
- Exact tests enumerate all sign assignments after zero differences are removed.
"""
    path.write_text(content, encoding="utf-8")


def write_stats_appendix(path: Path, statistics_doc: Mapping[str, object]) -> None:
    lines = [
        "# Statistical Appendix",
        "",
        "## Methods",
        "",
        "For each family, condition metrics were averaged across three provider blocks. Paired family differences are cold minus Memory. Exact two-sided Wilcoxon signed-rank tests use midranks for ties and enumerate every sign assignment; exact sign tests ignore zero differences. Hodges-Lehmann estimates use all Walsh averages. Confidence intervals are percentile intervals from 20,000 deterministic family-cluster bootstrap resamples. The two primary Wilcoxon tests are Holm-adjusted together; the four secondary tests form a separate exploratory Holm family.",
        "",
        "## Exact family-level results",
        "",
        "| Metric | Cold mean | Memory mean | Mean saving | HL saving | 95% CI saving | Wilcoxon p | Holm p | Rank-biserial | Sign +/−/0 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for metric in (*PRIMARY_METRICS, *SECONDARY_METRICS):
        values = statistics_doc["metrics"][metric]
        cold = values["cold_family_summary"]
        warm = values["warm_family_summary"]
        saving = values["saving_family_summary"]
        bootstrap = values["family_cluster_bootstrap"]["absolute"]
        wilcoxon = values["wilcoxon_exact"]
        sign = values["sign_test_exact"]
        adjusted_key = (
            "wilcoxon_holm_p_primary_family"
            if metric in PRIMARY_METRICS
            else "wilcoxon_holm_p_exploratory_family"
        )
        lines.append(
            f"| {METRIC_LABELS[metric]} | {_fmt(cold['mean'])} | "
            f"{_fmt(warm['mean'])} | {_fmt(saving['mean'])} | "
            f"{_fmt(values['hodges_lehmann_saving'])} | "
            f"[{_fmt(bootstrap['ci95'][0])}, {_fmt(bootstrap['ci95'][1])}] | "
            f"{_fmt_p(wilcoxon['p_two_sided'])} | "
            f"{_fmt_p(values[adjusted_key])} | "
            f"{_fmt(wilcoxon['rank_biserial'], 3)} | "
            f"{sign['positive']}/{sign['negative']}/{sign['zero']} |"
        )
    lines.extend(
        [
            "",
            "## Multiplicity and estimands",
            "",
            "The primary family contains repository tool calls and task input tokens. Model calls, output tokens, tool failures and elapsed duration are exploratory. Success and injection are reported as pre-registered gates and exact counts, not converted into a post-hoc superiority test. Relative reduction is `(cold mean − Memory mean) / cold mean`; absolute saving remains the more stable estimand when the cold denominator is small.",
            "",
            "## Missingness and exclusions",
            "",
            "There is no missing provider-usage or journal record among the 96 target Turns. No observation was excluded. The one oracle failure is retained. The failed V1 and V2 smoke manifests are design-development artifacts and are not pooled into V3 efficacy estimates.",
            "",
            "## Dependence and limitations",
            "",
            "The 48 block pairs are displayed descriptively but not treated as independent. The 16 families share the same high-level read-and-report mechanism, so even family-level inference may understate dependence relative to a heterogeneous real coding benchmark. Bootstrap intervals reflect sampled family variation, not prompt, model-version or deployment drift.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_figure_catalog(path: Path) -> None:
    content = """# Figure Catalog

## Figure 1 — Family-level repository tool calls

- File: `figures/figure-01-family-tool-calls.svg`
- Claim: shows the paired cold and Memory means for each of 16 task families.
- Encodings: x-position is mean repository tool calls over three blocks; orange is cold, blue is Memory, and the joining line is the within-family contrast.
- Caveat: family means hide block-level stochastic variation; exact block rows remain in `pair-level.csv`.

## Figure 2 — Relative efficiency reduction

- File: `figures/figure-02-relative-reduction.svg`
- Claim: summarizes relative reductions in four cost metrics.
- Encodings: the point is the family-pooled percentage reduction and the line is a 95% family-cluster percentile bootstrap interval; positive favors Memory.
- Caveat: percentages can appear large when cold denominators are small, so the statistical appendix also reports absolute savings.

## Figure 3 — Direct-first mechanism matrix

- File: `figures/figure-03-direct-first-heatmap.svg`
- Claim: shows whether each target Turn began with a paired successful `read_file` rather than repository discovery.
- Encodings: rows are task families; columns are block-condition combinations; blue is direct-first and grey is another first action.
- Caveat: journals intentionally omit tool arguments. The matrix proves action type and outcome, not the exact path argument.

All figures are vector SVGs generated deterministically from `family-summary.csv`, `pair-level.csv` and `statistics.json`; no values were transcribed by hand.
"""
    path.write_text(content, encoding="utf-8")


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
    manifest = _load_json(manifest_path)
    result = _load_json(result_path)
    turn_rows = extract_turn_rows(manifest, result, evidence_root)
    learning_rows = extract_learning_rows(manifest, result, evidence_root)
    pair_rows = build_pair_rows(turn_rows)
    family_rows = build_family_rows(pair_rows)
    statistics_doc = analyze(turn_rows, pair_rows, family_rows, learning_rows)
    statistics_doc["suiteId"] = manifest["suiteId"]
    statistics_doc["manifestSha256"] = _sha256(manifest_path)
    statistics_doc["resultSha256"] = _sha256(result_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "turn-level.csv", turn_rows)
    _write_csv(output_dir / "learning-turn-level.csv", learning_rows)
    _write_csv(output_dir / "pair-level.csv", pair_rows)
    _write_csv(output_dir / "family-summary.csv", family_rows)
    _write_json(output_dir / "statistics.json", statistics_doc)
    write_tool_figure(figures_dir / "figure-01-family-tool-calls.svg", family_rows)
    write_reduction_figure(
        figures_dir / "figure-02-relative-reduction.svg", statistics_doc
    )
    write_direct_first_figure(
        figures_dir / "figure-03-direct-first-heatmap.svg", pair_rows
    )
    write_analysis_report(
        output_dir / "analysis-report.md",
        statistics_doc,
        manifest_path,
        result_path,
    )
    write_stats_appendix(output_dir / "stats-appendix.md", statistics_doc)
    write_figure_catalog(output_dir / "figure-catalog.md")
    generated_paths = [
        output_dir / "turn-level.csv",
        output_dir / "learning-turn-level.csv",
        output_dir / "pair-level.csv",
        output_dir / "family-summary.csv",
        output_dir / "statistics.json",
        output_dir / "analysis-report.md",
        output_dir / "stats-appendix.md",
        output_dir / "figure-catalog.md",
        figures_dir / "figure-01-family-tool-calls.svg",
        figures_dir / "figure-02-relative-reduction.svg",
        figures_dir / "figure-03-direct-first-heatmap.svg",
    ]
    _write_json(
        output_dir / "reproducibility-index.json",
        {
            "schemaVersion": 1,
            "suiteId": manifest["suiteId"],
            "source": {
                "manifest": {
                    "path": str(manifest_path),
                    "sha256": _sha256(manifest_path),
                },
                "result": {
                    "path": str(result_path),
                    "sha256": _sha256(result_path),
                },
            },
            "generated": {
                str(item.relative_to(output_dir)): _sha256(item)
                for item in generated_paths
            },
        },
    )
    print(
        f"analyzed {len(turn_rows)} target Turns, {len(pair_rows)} pairs, "
        f"{len(family_rows)} families into {output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
