#!/usr/bin/env python3
"""Project and analyze sealed context-compaction live-run journals."""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import random
import statistics
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from minicode.run_journal import RunJournal
from scripts.run_north_star_live import (
    _all_events,
    _usage_from_events,
)

ANALYSIS_VERSION = 1
CANONICAL = "canonical"
CONTROL = "high-window-control"
BOOTSTRAP_SEED = 20260825
BOOTSTRAP_SAMPLES = 20_000


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _event_payloads(events: Iterable[object], event_type: str) -> list[dict]:
    return [
        payload
        for event in events
        if getattr(event, "type", None) == event_type
        and isinstance((payload := getattr(event, "payload", None)), dict)
    ]


def _turn_projection(journal: RunJournal, run_id: str) -> dict[str, object]:
    events = list(_all_events(journal, run_id))
    model_calls, input_tokens, output_tokens = _usage_from_events(journal, run_id)
    compacted = _event_payloads(events, "context.compacted")
    failed = _event_payloads(events, "context.compaction.failed")
    recoveries = _event_payloads(events, "recovery.started")
    paired_tool_calls = sum(
        payload.get("paired") is True
        for payload in _event_payloads(events, "tool.finished")
    )
    return {
        "runId": run_id,
        "modelCalls": model_calls,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "toolCalls": paired_tool_calls,
        "compactions": sum(payload.get("effective") is True for payload in compacted),
        "failedCompactions": len(failed),
        "estimatedTokensFreed": sum(
            int(value)
            for payload in compacted
            if isinstance((value := payload.get("tokensFreed")), int)
            and not isinstance(value, bool)
            and value >= 0
        ),
        "modelFailures": len(_event_payloads(events, "model.failed")),
        "overflowRecoveries": sum(
            payload.get("reason") == "context_overflow" for payload in recoveries
        ),
    }


def _case_projection(
    *,
    case: Mapping[str, object],
    result: Mapping[str, object],
    evidence_root: Path,
) -> dict[str, object]:
    case_id = str(case["id"])
    experiment = case.get("experiment")
    if not isinstance(experiment, dict):
        raise TypeError(f"case {case_id} has no experiment metadata")
    related = result.get("relatedRunIds", [])
    run_ids = list(related) if isinstance(related, list) else []
    if result.get("runId") is not None:
        run_ids.append(result["runId"])
    if any(not isinstance(run_id, str) for run_id in run_ids):
        raise ValueError(f"case {case_id} has invalid run identities")
    journal_root = evidence_root / "cases" / case_id / "journal"
    workspace = evidence_root / "cases" / case_id / "workspace"
    journal = RunJournal(workspace, data_dir=journal_root)
    turns = [_turn_projection(journal, str(run_id)) for run_id in run_ids]

    def total(field: str) -> int | None:
        values = [turn[field] for turn in turns]
        if field in {"inputTokens", "outputTokens"} and any(
            value is None for value in values
        ):
            return None
        return sum(int(value or 0) for value in values)

    observed_input = total("inputTokens")
    observed_output = total("outputTokens")
    observed_calls = total("modelCalls")
    if observed_input != result.get("inputTokens"):
        raise ValueError(f"case {case_id} input-token journal mismatch")
    if observed_output != result.get("outputTokens"):
        raise ValueError(f"case {case_id} output-token journal mismatch")
    if observed_calls != result.get("modelCalls"):
        raise ValueError(f"case {case_id} model-call journal mismatch")

    cumulative: list[int | None] = []
    running = 0
    complete = True
    for turn in turns:
        value = turn["inputTokens"]
        if value is None:
            complete = False
        elif complete:
            running += int(value)
        cumulative.append(running if complete else None)
    return {
        "id": case_id,
        "pairId": experiment.get("pairId"),
        "family": experiment.get("family"),
        "replicate": experiment.get("replicate"),
        "condition": experiment.get("condition"),
        "contextWindow": experiment.get("contextWindow"),
        "status": result.get("status"),
        "verificationPassed": result.get("verificationPassed"),
        "durationMs": result.get("durationMs"),
        "modelCalls": observed_calls,
        "inputTokens": observed_input,
        "outputTokens": observed_output,
        "toolCalls": total("toolCalls"),
        "compactions": total("compactions"),
        "failedCompactions": total("failedCompactions"),
        "estimatedTokensFreed": total("estimatedTokensFreed"),
        "modelFailures": total("modelFailures"),
        "overflowRecoveries": total("overflowRecoveries"),
        "cumulativeInputTokensByTurn": cumulative,
        "turns": turns,
    }


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile requires data")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)


def _describe(values: Sequence[float]) -> dict[str, float | int]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "sd": statistics.stdev(values) if len(values) > 1 else 0.0,
        "median": statistics.median(values),
        "q1": _quantile(values, 0.25),
        "q3": _quantile(values, 0.75),
        "min": min(values),
        "max": max(values),
    }


def _bootstrap_mean_ci(values: Sequence[float]) -> list[float] | None:
    if not values:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    count = len(values)
    sampled = [
        statistics.fmean(values[rng.randrange(count)] for _ in range(count))
        for _ in range(BOOTSTRAP_SAMPLES)
    ]
    return [_quantile(sampled, 0.025), _quantile(sampled, 0.975)]


def _exact_sign_permutation_p(values: Sequence[float]) -> float | None:
    nonzero = [float(value) for value in values if value != 0]
    if not nonzero:
        return 1.0 if values else None
    observed = abs(statistics.fmean(nonzero))
    extreme = 0
    total = 0
    magnitudes = [abs(value) for value in nonzero]
    for signs in itertools.product((-1.0, 1.0), repeat=len(magnitudes)):
        statistic = abs(
            statistics.fmean(sign * magnitude for sign, magnitude in zip(signs, magnitudes))
        )
        extreme += statistic >= observed - 1e-12
        total += 1
    return extreme / total


def _rank_biserial(values: Sequence[float]) -> float | None:
    ranked = sorted(
        (abs(float(value)), float(value)) for value in values if value != 0
    )
    if not ranked:
        return 0.0 if values else None
    ranks: list[tuple[float, float]] = []
    index = 0
    while index < len(ranked):
        end = index + 1
        while end < len(ranked) and ranked[end][0] == ranked[index][0]:
            end += 1
        average_rank = ((index + 1) + end) / 2.0
        ranks.extend((average_rank, value) for _magnitude, value in ranked[index:end])
        index = end
    positive = sum(rank for rank, value in ranks if value > 0)
    negative = sum(rank for rank, value in ranks if value < 0)
    denominator = positive + negative
    return (positive - negative) / denominator if denominator else 0.0


def _holm(p_values: Mapping[str, float | None]) -> dict[str, float | None]:
    available = sorted(
        ((key, value) for key, value in p_values.items() if value is not None),
        key=lambda item: float(item[1]),
    )
    adjusted: dict[str, float | None] = {key: None for key in p_values}
    running = 0.0
    count = len(available)
    for rank, (key, value) in enumerate(available):
        candidate = min(1.0, float(value) * (count - rank))
        running = max(running, candidate)
        adjusted[key] = running
    return adjusted


def _mcnemar_exact(canonical_only: int, control_only: int) -> float | None:
    discordant = canonical_only + control_only
    if discordant == 0:
        return None
    smaller = min(canonical_only, control_only)
    tail = sum(
        math.comb(discordant, index) for index in range(smaller + 1)
    ) / (2**discordant)
    return min(1.0, 2 * tail)


def _sustained_crossover(
    canonical: Sequence[int | None], control: Sequence[int | None]
) -> int | None:
    if len(canonical) != len(control) or any(
        value is None for value in [*canonical, *control]
    ):
        return None
    for index in range(len(canonical)):
        if all(
            int(canonical[later]) < int(control[later])
            for later in range(index, len(canonical))
        ):
            return index + 1
    return None


def _pair_cases(cases: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, dict[str, object]]] = {}
    for case in cases:
        pair_id = case.get("pairId")
        condition = case.get("condition")
        if not isinstance(pair_id, str) or condition not in {CANONICAL, CONTROL}:
            raise ValueError("invalid projected pair metadata")
        grouped.setdefault(pair_id, {})[str(condition)] = case
    pairs: list[dict[str, object]] = []
    for pair_id in sorted(grouped):
        conditions = grouped[pair_id]
        if set(conditions) != {CANONICAL, CONTROL}:
            raise ValueError(f"incomplete pair: {pair_id}")
        canonical = conditions[CANONICAL]
        control = conditions[CONTROL]
        canonical_tokens = canonical.get("inputTokens")
        control_tokens = control.get("inputTokens")
        token_difference = None
        percent_reduction = None
        if isinstance(canonical_tokens, int) and isinstance(control_tokens, int):
            token_difference = control_tokens - canonical_tokens
            if control_tokens > 0:
                percent_reduction = 100.0 * token_difference / control_tokens
        pairs.append(
            {
                "pairId": pair_id,
                "family": canonical.get("family"),
                "replicate": canonical.get("replicate"),
                "canonicalStatus": canonical.get("status"),
                "controlStatus": control.get("status"),
                "canonicalInputTokens": canonical_tokens,
                "controlInputTokens": control_tokens,
                "canonicalOutputTokens": canonical.get("outputTokens"),
                "controlOutputTokens": control.get("outputTokens"),
                "tokenDifferenceControlMinusCanonical": token_difference,
                "percentReduction": percent_reduction,
                "canonicalModelCalls": canonical.get("modelCalls"),
                "controlModelCalls": control.get("modelCalls"),
                "canonicalToolCalls": canonical.get("toolCalls"),
                "controlToolCalls": control.get("toolCalls"),
                "canonicalCompactions": canonical.get("compactions"),
                "controlCompactions": control.get("compactions"),
                "canonicalFailedCompactions": canonical.get("failedCompactions"),
                "controlFailedCompactions": control.get("failedCompactions"),
                "canonicalModelFailures": canonical.get("modelFailures"),
                "controlModelFailures": control.get("modelFailures"),
                "canonicalOverflowRecoveries": canonical.get("overflowRecoveries"),
                "controlOverflowRecoveries": control.get("overflowRecoveries"),
                "canonicalEstimatedTokensFreed": canonical.get("estimatedTokensFreed"),
                "controlEstimatedTokensFreed": control.get("estimatedTokensFreed"),
                "canonicalDurationMs": canonical.get("durationMs"),
                "controlDurationMs": control.get("durationMs"),
                "sustainedCrossoverTurn": _sustained_crossover(
                    canonical.get("cumulativeInputTokensByTurn", []),
                    control.get("cumulativeInputTokensByTurn", []),
                ),
                "canonicalCumulativeInputTokensByTurn": canonical.get(
                    "cumulativeInputTokensByTurn"
                ),
                "controlCumulativeInputTokensByTurn": control.get(
                    "cumulativeInputTokensByTurn"
                ),
            }
        )
    return pairs


def _paired_metric(
    pairs: Sequence[Mapping[str, object]],
    *,
    canonical_key: str,
    control_key: str,
) -> dict[str, object]:
    complete = [
        pair
        for pair in pairs
        if isinstance(pair.get(canonical_key), int)
        and isinstance(pair.get(control_key), int)
    ]
    canonical = [float(pair[canonical_key]) for pair in complete]
    control = [float(pair[control_key]) for pair in complete]
    differences = [
        float(pair[control_key]) - float(pair[canonical_key])
        for pair in complete
    ]
    canonical_total = sum(canonical)
    control_total = sum(control)
    return {
        "pairCount": len(complete),
        "canonicalTotal": canonical_total,
        "controlTotal": control_total,
        "aggregatePercentReduction": (
            100.0 * (control_total - canonical_total) / control_total
            if control_total
            else None
        ),
        "pairedDifferenceControlMinusCanonical": _describe(differences),
    }


def _analysis(cases: list[dict[str, object]]) -> dict[str, object]:
    pairs = _pair_cases(cases)
    complete_pairs = [
        pair
        for pair in pairs
        if isinstance(pair["canonicalInputTokens"], int)
        and isinstance(pair["controlInputTokens"], int)
    ]
    differences = [
        float(pair["tokenDifferenceControlMinusCanonical"])
        for pair in complete_pairs
    ]
    percentages = [
        float(pair["percentReduction"])
        for pair in complete_pairs
        if pair["percentReduction"] is not None
    ]
    canonical_tokens = [float(pair["canonicalInputTokens"]) for pair in complete_pairs]
    control_tokens = [float(pair["controlInputTokens"]) for pair in complete_pairs]

    per_turn_differences: dict[str, list[float]] = {}
    for turn_index in range(5):
        values: list[float] = []
        for pair in pairs:
            canonical = pair["canonicalCumulativeInputTokensByTurn"]
            control = pair["controlCumulativeInputTokensByTurn"]
            if (
                isinstance(canonical, list)
                and isinstance(control, list)
                and len(canonical) > turn_index
                and len(control) > turn_index
                and isinstance(canonical[turn_index], int)
                and isinstance(control[turn_index], int)
            ):
                values.append(float(control[turn_index] - canonical[turn_index]))
        per_turn_differences[str(turn_index + 1)] = values
    secondary_raw = {
        turn: _exact_sign_permutation_p(values)
        for turn, values in per_turn_differences.items()
        if turn != "5"
    }
    secondary_adjusted = _holm(secondary_raw)

    canonical_only = sum(
        pair["canonicalStatus"] == "passed" and pair["controlStatus"] != "passed"
        for pair in pairs
    )
    control_only = sum(
        pair["canonicalStatus"] != "passed" and pair["controlStatus"] == "passed"
        for pair in pairs
    )
    return {
        "pairCount": len(pairs),
        "completeUsagePairCount": len(complete_pairs),
        "allCaseCount": len(cases),
        "allPassed": all(case.get("status") == "passed" for case in cases),
        "canonicalPassed": sum(
            case.get("condition") == CANONICAL and case.get("status") == "passed"
            for case in cases
        ),
        "controlPassed": sum(
            case.get("condition") == CONTROL and case.get("status") == "passed"
            for case in cases
        ),
        "canonicalCompactions": sum(int(case.get("compactions") or 0) for case in cases if case.get("condition") == CANONICAL),
        "controlCompactions": sum(int(case.get("compactions") or 0) for case in cases if case.get("condition") == CONTROL),
        "canonicalFailedCompactions": sum(int(case.get("failedCompactions") or 0) for case in cases if case.get("condition") == CANONICAL),
        "controlFailedCompactions": sum(int(case.get("failedCompactions") or 0) for case in cases if case.get("condition") == CONTROL),
        "inputTokens": {
            "canonical": _describe(canonical_tokens),
            "control": _describe(control_tokens),
            "pairedDifferenceControlMinusCanonical": _describe(differences),
            "pairedPercentReduction": _describe(percentages),
            "bootstrap95CiMeanDifference": _bootstrap_mean_ci(differences),
            "bootstrap95CiMeanPercentReduction": _bootstrap_mean_ci(percentages),
            "exactSignPermutationP": _exact_sign_permutation_p(differences),
            "pairedRankBiserial": _rank_biserial(differences),
        },
        "successDiscordance": {
            "canonicalOnlyPassed": canonical_only,
            "controlOnlyPassed": control_only,
            "exactMcNemarP": _mcnemar_exact(canonical_only, control_only),
        },
        "supportingMetrics": {
            "outputTokens": _paired_metric(
                pairs,
                canonical_key="canonicalOutputTokens",
                control_key="controlOutputTokens",
            ),
            "modelCalls": _paired_metric(
                pairs,
                canonical_key="canonicalModelCalls",
                control_key="controlModelCalls",
            ),
            "toolCalls": _paired_metric(
                pairs,
                canonical_key="canonicalToolCalls",
                control_key="controlToolCalls",
            ),
            "durationMs": _paired_metric(
                pairs,
                canonical_key="canonicalDurationMs",
                control_key="controlDurationMs",
            ),
            "modelFailures": _paired_metric(
                pairs,
                canonical_key="canonicalModelFailures",
                control_key="controlModelFailures",
            ),
            "overflowRecoveries": _paired_metric(
                pairs,
                canonical_key="canonicalOverflowRecoveries",
                control_key="controlOverflowRecoveries",
            ),
        },
        "perTurnCumulativeTokenDifference": {
            turn: {
                "description": _describe(values),
                "exactSignPermutationP": _exact_sign_permutation_p(values),
                "holmAdjustedP": (
                    secondary_adjusted.get(turn) if turn != "5" else None
                ),
            }
            for turn, values in per_turn_differences.items()
        },
        "pairs": pairs,
    }


def _svg_escape(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _line_svg(pairs: Sequence[Mapping[str, object]]) -> str:
    canonical_by_turn: list[list[float]] = [[] for _ in range(5)]
    control_by_turn: list[list[float]] = [[] for _ in range(5)]
    for pair in pairs:
        canonical = pair.get("canonicalCumulativeInputTokensByTurn")
        control = pair.get("controlCumulativeInputTokensByTurn")
        if not isinstance(canonical, list) or not isinstance(control, list):
            continue
        for index in range(min(5, len(canonical), len(control))):
            if isinstance(canonical[index], int) and isinstance(control[index], int):
                canonical_by_turn[index].append(float(canonical[index]))
                control_by_turn[index].append(float(control[index]))
    canonical_means = [statistics.fmean(values) if values else 0 for values in canonical_by_turn]
    control_means = [statistics.fmean(values) if values else 0 for values in control_by_turn]
    maximum = max([*canonical_means, *control_means, 1]) * 1.08
    width, height = 900, 520
    left, right, top, bottom = 90, 35, 55, 80
    plot_w, plot_h = width - left - right, height - top - bottom

    def x(index: int) -> float:
        return left + index * plot_w / 4

    def y(value: float) -> float:
        return top + plot_h * (1 - value / maximum)

    rows = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">Mean cumulative provider input tokens by turn</title>',
        '<desc id="desc">Canonical context compaction compared with a matched high-window control.</desc>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="90" y="30" font-family="sans-serif" font-size="20" font-weight="700">Mean cumulative provider input tokens</text>',
    ]
    for tick in range(6):
        value = maximum * tick / 5
        yy = y(value)
        rows.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{width-right}" y2="{yy:.1f}" stroke="#d8dee9" stroke-width="1"/>')
        rows.append(f'<text x="{left-10}" y="{yy+4:.1f}" text-anchor="end" font-family="sans-serif" font-size="12" fill="#4b5563">{value/1000:.0f}k</text>')
    rows.append(f'<line x1="{left}" y1="{top+plot_h}" x2="{width-right}" y2="{top+plot_h}" stroke="#111827"/>')
    for index in range(5):
        rows.append(f'<text x="{x(index):.1f}" y="{top+plot_h+28}" text-anchor="middle" font-family="sans-serif" font-size="13">Turn {index+1}</text>')
    for values, color, label in (
        (control_means, "#D55E00", "High-window control"),
        (canonical_means, "#0072B2", "Canonical compaction"),
    ):
        points = " ".join(f"{x(index):.1f},{y(value):.1f}" for index, value in enumerate(values))
        rows.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="4"/>')
        for index, value in enumerate(values):
            rows.append(f'<circle cx="{x(index):.1f}" cy="{y(value):.1f}" r="5" fill="{color}"/>')
        legend_x = 565
        legend_y = 58 if label.startswith("High") else 82
        rows.append(f'<line x1="{legend_x}" y1="{legend_y}" x2="{legend_x+28}" y2="{legend_y}" stroke="{color}" stroke-width="4"/>')
        rows.append(f'<text x="{legend_x+36}" y="{legend_y+4}" font-family="sans-serif" font-size="13">{_svg_escape(label)}</text>')
    rows.append(f'<text x="{left + plot_w/2:.1f}" y="{height-18}" text-anchor="middle" font-family="sans-serif" font-size="13" fill="#374151">Matched synthetic coding cases; provider-observed usage</text>')
    rows.append("</svg>")
    return "\n".join(rows) + "\n"


def _paired_svg(pairs: Sequence[Mapping[str, object]]) -> str:
    valid = [
        pair
        for pair in pairs
        if isinstance(pair.get("canonicalInputTokens"), int)
        and isinstance(pair.get("controlInputTokens"), int)
    ]
    width = 980
    row_h = 34
    height = 105 + max(1, len(valid)) * row_h
    left, right = 210, 45
    plot_w = width - left - right
    maximum = max(
        [
            float(pair[key])
            for pair in valid
            for key in ("canonicalInputTokens", "controlInputTokens")
        ]
        or [1.0]
    ) * 1.05

    def x(value: float) -> float:
        return left + plot_w * value / maximum

    rows = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">Paired cumulative input tokens through turn five</title>',
        '<desc id="desc">Each row joins one canonical compaction run to its matched high-window control.</desc>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="30" y="30" font-family="sans-serif" font-size="20" font-weight="700">Paired total input tokens through turn five</text>',
        '<circle cx="650" cy="25" r="5" fill="#0072B2"/><text x="662" y="29" font-family="sans-serif" font-size="12">canonical</text>',
        '<circle cx="760" cy="25" r="5" fill="#D55E00"/><text x="772" y="29" font-family="sans-serif" font-size="12">control</text>',
    ]
    for tick in range(6):
        value = maximum * tick / 5
        xx = x(value)
        rows.append(f'<line x1="{xx:.1f}" y1="55" x2="{xx:.1f}" y2="{height-35}" stroke="#e5e7eb"/>')
        rows.append(f'<text x="{xx:.1f}" y="{height-15}" text-anchor="middle" font-family="sans-serif" font-size="11" fill="#4b5563">{value/1000:.0f}k</text>')
    for index, pair in enumerate(valid):
        yy = 70 + index * row_h
        canonical = float(pair["canonicalInputTokens"])
        control = float(pair["controlInputTokens"])
        rows.append(f'<text x="{left-10}" y="{yy+4}" text-anchor="end" font-family="sans-serif" font-size="11">{_svg_escape(pair["pairId"])}</text>')
        rows.append(f'<line x1="{x(canonical):.1f}" y1="{yy}" x2="{x(control):.1f}" y2="{yy}" stroke="#9ca3af" stroke-width="2"/>')
        rows.append(f'<circle cx="{x(control):.1f}" cy="{yy}" r="5" fill="#D55E00"/>')
        rows.append(f'<circle cx="{x(canonical):.1f}" cy="{yy}" r="5" fill="#0072B2"/>')
    rows.append("</svg>")
    return "\n".join(rows) + "\n"


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fields = [
        "pairId",
        "family",
        "replicate",
        "canonicalStatus",
        "controlStatus",
        "canonicalInputTokens",
        "controlInputTokens",
        "canonicalOutputTokens",
        "controlOutputTokens",
        "tokenDifferenceControlMinusCanonical",
        "percentReduction",
        "sustainedCrossoverTurn",
        "canonicalModelCalls",
        "controlModelCalls",
        "canonicalToolCalls",
        "controlToolCalls",
        "canonicalCompactions",
        "controlCompactions",
        "canonicalFailedCompactions",
        "controlFailedCompactions",
        "canonicalModelFailures",
        "controlModelFailures",
        "canonicalOverflowRecoveries",
        "controlOverflowRecoveries",
        "canonicalEstimatedTokensFreed",
        "controlEstimatedTokensFreed",
        "canonicalDurationMs",
        "controlDurationMs",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def analyze(
    manifest_path: Path,
    result_path: Path,
    *,
    selected_case_ids: set[str] | None = None,
) -> dict[str, object]:
    manifest = _load_json(manifest_path)
    result = _load_json(result_path)
    if result.get("manifestSha256") != _sha256(manifest_path):
        raise ValueError("result is not bound to the supplied manifest")
    raw_cases = manifest.get("cases")
    raw_results = result.get("results")
    if not isinstance(raw_cases, list) or not isinstance(raw_results, list):
        raise TypeError("manifest/result cases are invalid")
    results_by_id = {
        item["id"]: item
        for item in raw_results
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    known_case_ids = {
        str(case.get("id")) for case in raw_cases if isinstance(case, dict)
    }
    if selected_case_ids is not None:
        unknown = selected_case_ids - known_case_ids
        unavailable = selected_case_ids - set(results_by_id)
        if unknown:
            raise ValueError(f"unknown selected case ids: {sorted(unknown)}")
        if unavailable:
            raise ValueError(f"selected cases have no sealed result: {sorted(unavailable)}")
    evidence_root = result_path.parent / (result_path.stem + "-evidence")
    cases = [
        _case_projection(
            case=case,
            result=results_by_id[str(case["id"])],
            evidence_root=evidence_root,
        )
        for case in raw_cases
        if isinstance(case, dict)
        and str(case.get("id")) in results_by_id
        and (
            selected_case_ids is None
            or str(case.get("id")) in selected_case_ids
        )
    ]
    analysis = _analysis(cases)
    expected = len(raw_cases)
    selection_ids = result.get("selectedCaseIds")
    return {
        "schemaVersion": 1,
        "analysisVersion": ANALYSIS_VERSION,
        "suiteId": manifest.get("suiteId"),
        "manifestSha256": _sha256(manifest_path),
        "resultSha256": _sha256(result_path),
        "sourceCodeSha256": result.get("sourceCodeSha256"),
        "runtimeProfile": result.get("runtimeProfile"),
        "bootstrap": {
            "seed": BOOTSTRAP_SEED,
            "samples": BOOTSTRAP_SAMPLES,
        },
        "selection": {
            "manifestCaseCount": expected,
            "selectedCaseIds": selection_ids,
            "analyzedCaseCount": len(cases),
            "suiteFinalized": result.get("suiteFinalized"),
            "analysisSelectionIsPostInfrastructureCutoff": (
                selected_case_ids is not None
            ),
        },
        "operationalChecks": {
            "allUsageComplete": all(
                isinstance(case.get("inputTokens"), int)
                and isinstance(case.get("outputTokens"), int)
                for case in cases
            ),
            "allPassed": all(case.get("status") == "passed" for case in cases),
            "canonicalAllCompacted": all(
                int(case.get("compactions") or 0) >= 1
                for case in cases
                if case.get("condition") == CANONICAL
            ),
            "controlNeverCompacted": all(
                int(case.get("compactions") or 0) == 0
                for case in cases
                if case.get("condition") == CONTROL
            ),
            "journalUsageMatchesPublicResults": True,
        },
        "analysis": analysis,
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case-id", action="append", default=[])
    args = parser.parse_args()
    manifest = args.manifest.resolve()
    result = args.result.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = analyze(
        manifest,
        result,
        selected_case_ids=set(args.case_id) if args.case_id else None,
    )
    report_path = output / "analysis.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    pairs = report["analysis"]["pairs"]
    assert isinstance(pairs, list)
    _write_csv(output / "paired-results.csv", pairs)
    (output / "cumulative-input-tokens.svg").write_text(
        _line_svg(pairs), encoding="utf-8"
    )
    (output / "paired-total-input-tokens.svg").write_text(
        _paired_svg(pairs), encoding="utf-8"
    )
    checks = report["operationalChecks"]
    print(json.dumps({"analysis": report_path.as_posix(), "checks": checks}))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
