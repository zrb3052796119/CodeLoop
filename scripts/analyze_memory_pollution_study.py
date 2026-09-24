#!/usr/bin/env python3
"""Analyze the memory-pollution self-correction study.

Inputs: the frozen manifest, the runner's public result JSON and its private
evidence sidecar (``<result stem>-evidence/cases/<case>/``), which holds the
per-run event journals and the final memory-pool snapshot.

Statistics reuse the self-contained exact implementations from the
large-study analyzer (exact Wilcoxon, family-cluster bootstrap, Holm).  The
family (n=6) is the inferential unit throughout; the three arms are compared
within families at rounds 1 and 3.  Pool self-correction endpoints (stale
lesson quarantined / still rendered) are reported as rates by arm with exact
sign counts, not as powered claims.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.analyze_persistent_memory_large_study import (
    cluster_bootstrap,
    exact_wilcoxon,
    holm_adjust,
)

BOOTSTRAP_SAMPLES = 20_000
BOOTSTRAP_SEED = 202609091
PRIMARY_METRICS = ("tool_calls", "task_input_tokens")
PRIMARY_CONTRASTS = (
    ("clean", "polluted-rule"),
    ("clean", "polluted-off"),
    ("polluted-off", "polluted-rule"),
)
PRIMARY_ROUNDS = (1, 3)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_events(case_root: Path, run_id: str) -> list[dict[str, Any]]:
    matches = list(case_root.glob(f"journal/**/runs/{run_id}/events.ndjson"))
    if len(matches) != 1:
        raise ValueError(
            f"expected one event journal for {run_id}, found {len(matches)}"
        )
    events: list[dict[str, Any]] = []
    for line in matches[0].read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise TypeError(f"event is not an object for {run_id}")
        events.append(value)
    return events


def _run_metrics(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    task_model_events: list[Mapping[str, Any]] = []
    side_model_events: list[Mapping[str, Any]] = []
    starts: list[tuple[str, str]] = []
    finishes: dict[str, Mapping[str, Any]] = {}
    task_outcome_success = False
    memory_injected = False
    run_started: str | None = None
    run_completed: str | None = None
    for event in events:
        event_type = event.get("type")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        if event_type == "model.completed":
            if payload.get("purpose") is None:
                task_model_events.append(payload)
            else:
                side_model_events.append(payload)
        elif event_type == "tool.started":
            operation_id = payload.get("operationId")
            tool_name = payload.get("toolName")
            if isinstance(operation_id, str) and isinstance(tool_name, str):
                starts.append((operation_id, tool_name))
        elif event_type == "tool.finished":
            operation_id = payload.get("operationId")
            if isinstance(operation_id, str):
                finishes.setdefault(operation_id, payload)
        elif event_type == "task.outcome":
            task_outcome_success = payload.get("outcomeStatus") == "success"
        elif event_type == "memory.rendered":
            memory_injected = memory_injected or payload.get("injected") is True
        elif event_type == "run.started":
            run_started = str(event.get("timestamp") or "") or None
        elif event_type == "run.completed":
            run_completed = str(event.get("timestamp") or "") or None

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

    tool_failures = sum(
        finishes.get(operation_id, {}).get("outcome") == "error"
        for operation_id, _tool_name in starts
    )
    duration_ms: int | None = None
    if run_started is not None and run_completed is not None:
        started_at = datetime.fromisoformat(run_started)
        completed_at = datetime.fromisoformat(run_completed)
        duration_ms = max(
            0, round((completed_at - started_at).total_seconds() * 1000)
        )
    return {
        "tool_calls": len(starts),
        "tool_failures": tool_failures,
        "task_model_calls": len(task_model_events),
        "side_model_calls": len(side_model_events),
        "task_input_tokens": usage_sum(task_model_events, "inputTokens"),
        "task_output_tokens": usage_sum(task_model_events, "outputTokens"),
        "duration_ms": duration_ms,
        "task_outcome_success": task_outcome_success,
        "memory_injected": memory_injected,
    }


def _content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _role_by_hash(case: Mapping[str, Any]) -> dict[str, str]:
    study = case.get("study") or {}
    roles = study.get("seedRoles") or []
    mapping: dict[str, str] = {}
    for index, entry in enumerate(case.get("memoryEntries") or []):
        if index < len(roles):
            mapping[_content_sha256(str(entry.get("content", "")))] = str(
                roles[index]
            )
    return mapping


def _case_run_ids(public_result: Mapping[str, Any]) -> list[str]:
    run_ids = list(public_result.get("relatedRunIds") or [])
    final_run_id = public_result.get("runId")
    if isinstance(final_run_id, str):
        run_ids.append(final_run_id)
    return [str(run_id) for run_id in run_ids]


def extract_case_rows(
    case: Mapping[str, Any],
    public_result: Mapping[str, Any],
    case_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    study = case.get("study") or {}
    arm = str(study.get("arm", ""))
    family_id = str(study.get("familyId", ""))
    stratum = str(study.get("stratum", ""))
    run_ids = _case_run_ids(public_result)
    evidence = _load_json(case_root / "evidence.json")
    memory_pool = evidence.get("memoryPool") or {}
    entries_by_id = {
        str(entry.get("id")): entry for entry in memory_pool.get("entries") or []
    }
    role_by_hash = _role_by_hash(case)
    role_by_id = {
        entry_id: role_by_hash.get(str(entry.get("content_sha256")), "other")
        for entry_id, entry in entries_by_id.items()
    }
    rendered_by_turn: dict[int, list[str]] = {}
    for turn_key, ids in (memory_pool.get("renderedByTurn") or {}).items():
        rendered_by_turn[int(turn_key)] = [str(item) for item in ids or []]

    turn_rows: list[dict[str, Any]] = []
    for round_index, run_id in enumerate(run_ids, start=1):
        metrics = _run_metrics(_load_events(case_root, run_id))
        rendered_roles = sorted(
            {role_by_id.get(item, "other") for item in rendered_by_turn.get(round_index - 1, [])}
        )
        turn_rows.append(
            {
                "caseId": case.get("id"),
                "familyId": family_id,
                "stratum": stratum,
                "arm": arm,
                "round": round_index,
                "status": public_result.get("status"),
                **metrics,
                "renderedRoles": ";".join(rendered_roles),
            }
        )

    pool_rows: list[dict[str, Any]] = []
    rounds_rendered: dict[str, list[int]] = {}
    for turn_index, ids in sorted(rendered_by_turn.items()):
        for entry_id in ids:
            rounds_rendered.setdefault(entry_id, []).append(turn_index + 1)
    for entry_id, entry in entries_by_id.items():
        role = role_by_id.get(entry_id, "other")
        pool_rows.append(
            {
                "caseId": case.get("id"),
                "familyId": family_id,
                "arm": arm,
                "role": role,
                "lifecycleStatus": entry.get("lifecycle_status"),
                "approvalStatus": entry.get("approval_status"),
                "successCount": entry.get("success_count"),
                "failureCount": entry.get("failure_count"),
                "corroboratedFailureCount": entry.get("corroborated_failure_count"),
                "injectionCount": entry.get("injection_count"),
                "roundsRendered": ";".join(
                    str(value) for value in rounds_rendered.get(entry_id, [])
                ),
            }
        )
    return turn_rows, pool_rows


def _cell(turn_rows: Sequence[Mapping[str, Any]], family_id: str, arm: str, round_index: int) -> Mapping[str, Any] | None:
    for row in turn_rows:
        if (
            row["familyId"] == family_id
            and row["arm"] == arm
            and row["round"] == round_index
        ):
            return row
    return None


def _family_vectors(
    turn_rows: Sequence[Mapping[str, Any]],
    metric: str,
    arm_a: str,
    arm_b: str,
    round_index: int,
) -> tuple[list[float], list[float]]:
    family_ids = sorted({str(row["familyId"]) for row in turn_rows})
    values_a: list[float] = []
    values_b: list[float] = []
    for family_id in family_ids:
        cell_a = _cell(turn_rows, family_id, arm_a, round_index)
        cell_b = _cell(turn_rows, family_id, arm_b, round_index)
        if cell_a is None or cell_b is None:
            continue
        values_a.append(float(cell_a[metric]))
        values_b.append(float(cell_b[metric]))
    return values_a, values_b


def _pool_endpoint(
    pool_rows: Sequence[Mapping[str, Any]],
    arm: str,
    role: str,
    predicate,
) -> tuple[int, int]:
    rows = [row for row in pool_rows if row["arm"] == arm and row["role"] == role]
    hits = sum(1 for row in rows if predicate(row))
    return hits, len(rows)


_SUMMARY_METRICS = (
    "tool_calls",
    "tool_failures",
    "task_model_calls",
    "side_model_calls",
    "task_input_tokens",
    "task_output_tokens",
    "duration_ms",
)


def _round_summary(
    turn_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Raw per-arm, per-round means over families (n=6 per cell)."""
    cells: dict[tuple[str, int], list[Mapping[str, Any]]] = {}
    for row in turn_rows:
        cells.setdefault((str(row["arm"]), int(row["round"])), []).append(row)
    summary: list[dict[str, Any]] = []
    for (arm, round_index), rows in sorted(cells.items()):
        entry: dict[str, Any] = {
            "arm": arm,
            "round": round_index,
            "families": len(rows),
            "outcomeSuccess": sum(
                1 for row in rows if row["task_outcome_success"]
            ),
        }
        for metric in _SUMMARY_METRICS:
            values = [
                float(row[metric])
                for row in rows
                if row.get(metric) is not None
            ]
            entry[f"{metric}_mean"] = (
                round(statistics.fmean(values), 2) if values else None
            )
        summary.append(entry)
    return summary


def analyze(manifest_path: Path, result_path: Path, output_dir: Path) -> dict[str, Any]:
    manifest = _load_json(manifest_path)
    result = _load_json(result_path)
    results_by_id = {
        str(item.get("id")): item for item in result.get("results") or []
    }
    evidence_root = result_path.parent / f"{result_path.stem}-evidence"
    turn_rows: list[dict[str, Any]] = []
    pool_rows: list[dict[str, Any]] = []
    for case in manifest.get("cases") or []:
        case_id = str(case.get("id"))
        public_result = results_by_id.get(case_id)
        if public_result is None:
            raise ValueError(f"result is missing case {case_id}")
        case_turn_rows, case_pool_rows = extract_case_rows(
            case, public_result, evidence_root / "cases" / case_id
        )
        turn_rows.extend(case_turn_rows)
        pool_rows.extend(case_pool_rows)

    # Primary family-level contrasts (metric means by arm/round are the
    # per-family cell values; one case per cell in this design).
    tests: dict[str, dict[str, Any]] = {}
    for metric in PRIMARY_METRICS:
        for arm_a, arm_b in PRIMARY_CONTRASTS:
            for round_index in PRIMARY_ROUNDS:
                values_a, values_b = _family_vectors(
                    turn_rows, metric, arm_a, arm_b, round_index
                )
                if len(values_a) < 3:
                    continue
                differences = [
                    b - a for a, b in zip(values_a, values_b)
                ]
                label = f"{metric}:{arm_b}-minus-{arm_a}:round{round_index}"
                wilcoxon = exact_wilcoxon(differences)
                bootstrap = cluster_bootstrap(
                    values_b,
                    values_a,
                    samples=BOOTSTRAP_SAMPLES,
                    seed=BOOTSTRAP_SEED,
                )
                absolute = bootstrap["absolute"]
                tests[label] = {
                    "differences": differences,
                    "meanDifference": statistics.fmean(differences),
                    "wilcoxon": wilcoxon,
                    "bootstrapCI95": absolute["ci95"],
                }
    holm = holm_adjust(
        {
            label: float(test["wilcoxon"].get("p_two_sided", 1.0))
            for label, test in tests.items()
        }
    )
    for label, adjusted in holm.items():
        tests[label]["holmP"] = adjusted

    def _rate(arm: str, role: str, predicate) -> float | None:
        hits, total = _pool_endpoint(pool_rows, arm, role, predicate)
        return (hits / total) if total else None

    pool_endpoints = {
        "staleQuarantinedRate": {
            arm: _rate(arm, "stale", lambda row: row["lifecycleStatus"] == "rejected")
            for arm in ("clean", "polluted-off", "polluted-rule")
        },
        "staleRenderedRound3Rate": {
            arm: _rate(
                arm,
                "stale",
                lambda row: "3" in str(row["roundsRendered"]).split(";"),
            )
            for arm in ("polluted-off", "polluted-rule")
        },
        "correctRenderedAnyRoundRate": {
            arm: _rate(arm, "correct", lambda row: bool(row["roundsRendered"]))
            for arm in ("clean", "polluted-off", "polluted-rule")
        },
    }
    strict_success = {
        arm: (
            sum(
                1
                for row in turn_rows
                if row["arm"] == arm and row["round"] == 3 and row["status"] == "passed"
            ),
            sum(1 for row in turn_rows if row["arm"] == arm and row["round"] == 3),
        )
        for arm in ("clean", "polluted-off", "polluted-rule")
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "turn-level.csv", turn_rows)
    _write_csv(output_dir / "pool-evolution.csv", pool_rows)
    round_summary = _round_summary(turn_rows)
    _write_csv(output_dir / "round-summary.csv", round_summary)
    statistics_payload = {
        "suiteId": manifest.get("suiteId"),
        "primaryUnit": "family",
        "bootstrap": {"samples": BOOTSTRAP_SAMPLES, "seed": BOOTSTRAP_SEED},
        "roundSummary": round_summary,
        "primaryTests": tests,
        "poolEndpoints": pool_endpoints,
        "strictSuccessRound3": strict_success,
    }
    (output_dir / "statistics.json").write_text(
        json.dumps(statistics_payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    _write_analysis_report(
        output_dir, statistics_payload, turn_rows, pool_rows
    )
    return statistics_payload


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _write_analysis_report(
    output_dir: Path,
    statistics_payload: Mapping[str, Any],
    turn_rows: Sequence[Mapping[str, Any]],
    pool_rows: Sequence[Mapping[str, Any]],
) -> None:
    lines: list[str] = [
        "# Memory pollution & self-correction — analysis report",
        "",
        "## Analysis contract",
        "",
        "- Inferential unit: task family (n=6); arms compared within families.",
        "- Primary contrasts: clean vs polluted-rule, clean vs polluted-off,",
        "  polluted-off vs polluted-rule, at rounds 1 and 3, for tool calls and",
        "  task input tokens. Exact Wilcoxon with Holm correction over the",
        "  primary family; family-cluster percentile bootstrap 95% CIs.",
        "- Pool endpoints are descriptive rates by arm (no power claim).",
        "",
        "## Outcomes",
        "",
    ]
    for arm, (hits, total) in statistics_payload["strictSuccessRound3"].items():
        lines.append(f"- {arm}: strict round-3 case success {hits}/{total}")
    lines.extend(
        [
            "",
            "## Raw per-arm, per-round metrics (family means, n=6)",
            "",
            "| arm | round | outcome success | tool calls | tool failures | task model calls | side model calls | input tokens | output tokens | duration s |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for cell in statistics_payload.get("roundSummary") or []:
        duration = cell.get("duration_ms_mean")
        duration_s = (
            f"{duration / 1000:.1f}"
            if isinstance(duration, (int, float))
            else "n/a"
        )
        lines.append(
            f"| {cell['arm']} | {cell['round']} | "
            f"{cell['outcomeSuccess']}/{cell['families']} | "
            f"{_fmt(cell.get('tool_calls_mean'))} | "
            f"{_fmt(cell.get('tool_failures_mean'))} | "
            f"{_fmt(cell.get('task_model_calls_mean'))} | "
            f"{_fmt(cell.get('side_model_calls_mean'))} | "
            f"{_fmt(cell.get('task_input_tokens_mean'))} | "
            f"{_fmt(cell.get('task_output_tokens_mean'))} | "
            f"{duration_s} |"
        )
    lines.extend(["", "## Primary efficiency results", ""])
    for label, test in statistics_payload["primaryTests"].items():
        lines.append(
            f"- {label}: mean diff {_fmt(test['meanDifference'])}, "
            f"95% CI [{_fmt(test['bootstrapCI95'][0])}, {_fmt(test['bootstrapCI95'][1])}], "
            f"exact Wilcoxon p={_fmt(test['wilcoxon'].get('p_two_sided'))}, "
            f"Holm p={_fmt(test.get('holmP'))}"
        )
    lines.extend(["", "## Pool self-correction endpoints", ""])
    for name, by_arm in statistics_payload["poolEndpoints"].items():
        for arm, rate in by_arm.items():
            lines.append(f"- {name} [{arm}]: {_fmt(rate)}")
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            "- Single block, six families, seeded lessons: this round measures",
            "  mechanism behaviour and directions, not powered effect sizes.",
            "- 'Stale rendered' rates depend on retrieval competition; a stale",
            "  lesson never rendered cannot be corrected by feedback (by design,",
            "  no evidence means no verdict).",
            "",
            "## Reproducibility",
            "",
            "- Analyzer: scripts/analyze_memory_pollution_study.py",
            f"- Bootstrap: {statistics_payload['bootstrap']['samples']} samples, "
            f"seed {statistics_payload['bootstrap']['seed']}",
            f"- turn-level rows: {len(turn_rows)}; pool rows: {len(pool_rows)}",
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
    tests = payload["primaryTests"]
    print(f"wrote analysis for {len(tests)} primary tests to {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
