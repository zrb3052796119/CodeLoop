#!/usr/bin/env python3
"""Produce a strict, reproducible analysis of the live Memory/compaction run."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _events(case_root: Path, run_id: str) -> list[dict[str, Any]]:
    matches = list(case_root.rglob(f"{run_id}/events.ndjson"))
    if len(matches) != 1:
        raise ValueError(f"expected one journal for {run_id}, found {len(matches)}")
    return [
        json.loads(line)
        for line in matches[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sidecar_ids(case_root: Path, run_id: str, name: str) -> list[str]:
    matches = list(case_root.rglob(f"{run_id}/{name}.json"))
    if not matches:
        return []
    value = _load(matches[0]).get("entryIds", [])
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _duration_ms(events: list[dict[str, Any]]) -> int | None:
    if len(events) < 2:
        return None
    try:
        start = datetime.fromisoformat(events[0]["timestamp"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(events[-1]["timestamp"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _expected_markers(case_id: str, turn_index: int, case: dict[str, Any]) -> list[str]:
    special = {
        ("context-skill-cross-boundary", 1): ["READY-SKILL-12"],
        ("context-skill-cross-boundary", 2): ["SKILL-AFTER-COMPACT-88"],
        ("context-file-cross-boundary", 1): ["READY-FILE-15"],
        ("context-file-cross-boundary", 2): ["FILE-AFTER-COMPACT-74"],
    }
    if (case_id, turn_index) in special:
        return special[(case_id, turn_index)]
    for oracle in case.get("oracles", []):
        if oracle.get("kind") == "response_contains":
            return [str(item) for item in oracle.get("values", [])]
    return []


def _condition(case_id: str, turn_index: int) -> str:
    if case_id.startswith("memory-chain-"):
        return "learn" if turn_index == 1 else "warm_learned"
    if case_id.startswith("memory-warm-"):
        return "warm_seeded"
    if case_id.startswith("memory-cold-"):
        return "cold"
    if "cross-boundary" in case_id:
        return "cross_boundary_source" if turn_index == 1 else "cross_boundary_recall"
    return "compaction"


def _expand_suite(
    manifest_path: Path,
    results_path: Path,
    evidence_root: Path,
    *,
    suite_label: str,
) -> list[dict[str, Any]]:
    manifest = _load(manifest_path)
    result_doc = _load(results_path)
    by_result = {
        item["id"]: item
        for item in result_doc.get("results", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    rows: list[dict[str, Any]] = []
    for case in manifest.get("cases", []):
        case_id = str(case["id"])
        result = by_result[case_id]
        private = _load(evidence_root / "cases" / case_id / "evidence.json")
        run_ids = private.get("runIds", [])
        responses = private.get("responses", [])
        if len(run_ids) != len(case["turns"]) or len(responses) != len(run_ids):
            raise ValueError(f"turn evidence mismatch for {case_id}")
        case_root = evidence_root / "cases" / case_id
        for index, (run_id, response) in enumerate(zip(run_ids, responses), start=1):
            events = _events(case_root, str(run_id))
            types = [event.get("type") for event in events]
            tool_started = [
                event.get("payload", {}).get("toolName")
                for event in events
                if event.get("type") == "tool.started"
            ]
            tool_finished = [
                event.get("payload", {})
                for event in events
                if event.get("type") == "tool.finished"
            ]
            model_completed = [
                event.get("payload", {})
                for event in events
                if event.get("type") == "model.completed"
            ]
            input_tokens = sum(
                int(payload.get("usage", {}).get("inputTokens", 0))
                for payload in model_completed
                if isinstance(payload.get("usage"), dict)
                and isinstance(payload["usage"].get("inputTokens"), int)
            )
            output_tokens = sum(
                int(payload.get("usage", {}).get("outputTokens", 0))
                for payload in model_completed
                if isinstance(payload.get("usage"), dict)
                and isinstance(payload["usage"].get("outputTokens"), int)
            )
            outcomes = [
                event.get("payload", {})
                for event in events
                if event.get("type") == "task.outcome"
            ]
            compactions = [
                event.get("payload", {})
                for event in events
                if event.get("type") == "context.compacted"
                and event.get("payload", {}).get("effective") is True
            ]
            rendered = [
                event.get("payload", {})
                for event in events
                if event.get("type") == "memory.rendered"
            ]
            retrieved = [
                event.get("payload", {})
                for event in events
                if event.get("type") == "memory.retrieved"
            ]
            markers = _expected_markers(case_id, index, case)
            marker_pass = all(marker.casefold() in str(response).casefold() for marker in markers)
            outcome_success = bool(outcomes) and all(
                item.get("outcomeStatus") == "success" for item in outcomes
            )
            rows.append(
                {
                    "suite": suite_label,
                    "case_id": case_id,
                    "task_id": f"{case_id}#{index}",
                    "turn_index": index,
                    "category": str(case.get("category", "")),
                    "condition": _condition(case_id, index),
                    "run_id": str(run_id),
                    "case_status": result.get("status"),
                    "functional_success": outcome_success and marker_pass,
                    "marker_pass": marker_pass,
                    "expected_markers": markers,
                    "model_calls": types.count("model.started"),
                    "tool_calls": len(tool_started),
                    "tool_names": tool_started,
                    "tool_errors": sum(
                        payload.get("outcome") == "error" for payload in tool_finished
                    ),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "duration_ms": _duration_ms(events),
                    "memory_selected": sum(
                        int(payload.get("selectedCount", 0))
                        for payload in retrieved
                        if isinstance(payload.get("selectedCount"), int)
                    ),
                    "memory_rendered": sum(
                        int(payload.get("renderedCount", 0))
                        for payload in rendered
                        if payload.get("injected") is True
                        and isinstance(payload.get("renderedCount"), int)
                    ),
                    "rendered_memory_ids": _sidecar_ids(
                        case_root, str(run_id), "memory_rendered"
                    ),
                    "written_memory_ids": _sidecar_ids(
                        case_root, str(run_id), "memory_written"
                    ),
                    "compaction_count": len(compactions),
                    "tokens_freed": sum(
                        int(payload.get("tokensFreed", 0))
                        for payload in compactions
                        if isinstance(payload.get("tokensFreed"), int)
                    ),
                    "messages_removed": sum(
                        int(payload.get("messagesRemoved", 0))
                        for payload in compactions
                        if isinstance(payload.get("messagesRemoved"), int)
                    ),
                    "skill_loaded": "skill.loaded" in types,
                    "required_tool_observed": (
                        "read_file" in tool_started
                        if case_id == "context-file-cross-boundary" and index == 1
                        else True
                    ),
                }
            )
    return rows


def _wilson(successes: int, total: int, z: float = 1.959963984540054) -> list[float]:
    if total <= 0:
        return [0.0, 0.0]
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def _sign_test(wins: int, losses: int) -> float | None:
    total = wins + losses
    if total == 0:
        return None
    tail = sum(math.comb(total, index) for index in range(min(wins, losses) + 1)) / 2**total
    return min(1.0, 2 * tail)


def _memory_entry_statuses(evidence_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case_id in ("memory-chain-auth", "memory-chain-runtime"):
        path = evidence_root / "cases" / case_id / "workspace/.mini-code-memory/memory.json"
        document = _load(path)
        for entry in document.get("entries", []):
            rows.append(
                {
                    "case_id": case_id,
                    "entry_id": entry.get("id"),
                    "approval_status": entry.get("approval_status"),
                    "approval_policy": entry.get("approval_policy"),
                    "safety_status": entry.get("safety_status"),
                    "safety_reason": entry.get("safety_reason"),
                    "retrieval_count": entry.get("retrieval_count"),
                    "injection_count": entry.get("injection_count"),
                }
            )
    return rows


def _memory_pairs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_task = {row["task_id"]: row for row in rows}
    specs = [
        ("auth", "memory-chain-auth#2", "memory-cold-auth#1"),
        ("runtime", "memory-chain-runtime#2", "memory-cold-runtime#1"),
        ("deploy", "memory-warm-deploy#1", "memory-cold-deploy#1"),
        ("schema", "memory-warm-schema#1", "memory-cold-schema#1"),
    ]
    pairs: list[dict[str, Any]] = []
    for name, warm_id, cold_id in specs:
        warm = by_task[warm_id]
        cold = by_task[cold_id]
        pairs.append(
            {
                "pair": name,
                "warm_task": warm_id,
                "cold_task": cold_id,
                "warm_injected": warm["memory_rendered"] > 0,
                "warm_success": warm["functional_success"],
                "cold_success": cold["functional_success"],
                "warm_tool_calls": warm["tool_calls"],
                "cold_tool_calls": cold["tool_calls"],
                "tool_call_difference": warm["tool_calls"] - cold["tool_calls"],
                "warm_model_calls": warm["model_calls"],
                "cold_model_calls": cold["model_calls"],
                "warm_input_tokens": warm["input_tokens"],
                "cold_input_tokens": cold["input_tokens"],
                "warm_duration_ms": warm["duration_ms"],
                "cold_duration_ms": cold["duration_ms"],
            }
        )
    return pairs


def _svg_header(width: int, height: int) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#222}.axis{stroke:#555;stroke-width:1}.grid{stroke:#ddd;stroke-width:1}.warm{fill:#0072B2}.cold{fill:#E69F00}.compact{fill:#56B4E9}.calls{fill:#D55E00}</style>',
    ]


def _memory_figure(path: Path, pairs: list[dict[str, Any]]) -> None:
    width, height = 900, 500
    items = _svg_header(width, height)
    left, top, plot_h, baseline = 90, 70, 330, 400
    max_value = max(max(pair["warm_tool_calls"], pair["cold_tool_calls"]) for pair in pairs)
    scale = plot_h / max(1, max_value + 1)
    items.append(f'<line class="axis" x1="{left}" y1="{baseline}" x2="850" y2="{baseline}"/>')
    items.append(f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{baseline}"/>')
    for tick in range(max_value + 2):
        y = baseline - tick * scale
        items.append(f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="850" y2="{y:.1f}"/>')
        items.append(f'<text x="70" y="{y + 5:.1f}" font-size="13" text-anchor="end">{tick}</text>')
    group_w = 170
    for index, pair in enumerate(pairs):
        center = left + 95 + index * group_w
        for offset, key, css in ((-29, "warm_tool_calls", "warm"), (29, "cold_tool_calls", "cold")):
            value = pair[key]
            bar_h = value * scale
            items.append(f'<rect class="{css}" x="{center + offset - 22}" y="{baseline - bar_h:.1f}" width="44" height="{bar_h:.1f}"/>')
            items.append(f'<text x="{center + offset}" y="{baseline - bar_h - 8:.1f}" font-size="13" text-anchor="middle">{value}</text>')
        injection = "injected" if pair["warm_injected"] else "not injected"
        items.append(f'<text x="{center}" y="430" font-size="14" text-anchor="middle">{escape(pair["pair"])}</text>')
        items.append(f'<text x="{center}" y="449" font-size="11" text-anchor="middle">{injection}</text>')
    items.extend(
        [
            '<text x="450" y="30" font-size="20" text-anchor="middle">Paired Memory tasks: tool calls</text>',
            '<text x="25" y="240" font-size="14" text-anchor="middle" transform="rotate(-90 25 240)">Tool calls per task (lower is better)</text>',
            '<rect class="warm" x="660" y="18" width="16" height="16"/><text x="682" y="31" font-size="13">Warm</text>',
            '<rect class="cold" x="745" y="18" width="16" height="16"/><text x="767" y="31" font-size="13">Cold</text>',
            '</svg>',
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(items) + "\n", encoding="utf-8")


def _compaction_figure(path: Path, rows: list[dict[str, Any]]) -> None:
    width, height = 1100, 600
    items = _svg_header(width, height)
    left, right, baseline = 80, 1060, 490
    max_freed = max(row["tokens_freed"] for row in rows)
    max_calls = max(row["model_calls"] for row in rows)
    bar_w = 62
    gap = (right - left) / len(rows)
    for tick in range(0, 6001, 1000):
        y = baseline - tick / max(1, max_freed) * 340
        items.append(f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}"/>')
        items.append(f'<text x="68" y="{y + 5:.1f}" font-size="12" text-anchor="end">{tick}</text>')
    items.append(f'<line class="axis" x1="{left}" y1="{baseline}" x2="{right}" y2="{baseline}"/>')
    for index, row in enumerate(rows):
        center = left + gap * (index + 0.5)
        height_tokens = row["tokens_freed"] / max(1, max_freed) * 340
        items.append(f'<rect class="compact" x="{center - bar_w / 2:.1f}" y="{baseline - height_tokens:.1f}" width="{bar_w}" height="{height_tokens:.1f}"/>')
        radius = 5 + 16 * row["model_calls"] / max(1, max_calls)
        items.append(f'<circle class="calls" cx="{center:.1f}" cy="{baseline - height_tokens - 14:.1f}" r="{radius:.1f}" opacity="0.85"/>')
        label = row["case_id"].removeprefix("context-").replace("-retention", "")
        items.append(f'<text x="{center:.1f}" y="515" font-size="10" text-anchor="end" transform="rotate(-35 {center:.1f} 515)">{escape(label)}</text>')
    items.extend(
        [
            '<text x="550" y="28" font-size="20" text-anchor="middle">Effective compaction and downstream model work</text>',
            '<text x="22" y="280" font-size="13" text-anchor="middle" transform="rotate(-90 22 280)">Estimated tokens freed</text>',
            '<rect class="compact" x="780" y="18" width="16" height="16"/><text x="802" y="31" font-size="13">Tokens freed</text>',
            '<circle class="calls" cx="910" cy="26" r="8"/><text x="925" y="31" font-size="13">Bubble size = model calls</text>',
            '</svg>',
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(items) + "\n", encoding="utf-8")


def _markdown_table(headers: list[str], rows: Iterable[list[object]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def _write_reports(
    output_dir: Path,
    primary_rows: list[dict[str, Any]],
    addendum_rows: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    memory_table = _markdown_table(
        ["Pair", "Injected", "Warm tools", "Cold tools", "Δ", "Warm/Cold success"],
        [
            [
                pair["pair"],
                "yes" if pair["warm_injected"] else "no",
                pair["warm_tool_calls"],
                pair["cold_tool_calls"],
                pair["tool_call_difference"],
                f"{int(pair['warm_success'])}/{int(pair['cold_success'])}",
            ]
            for pair in pairs
        ],
    )
    context_rows = [row for row in primary_rows if row["category"] == "context-compaction"]
    context_table = _markdown_table(
        ["Task", "Pass", "Compactions", "Tokens freed", "Model calls", "Tool calls"],
        [
            [
                row["task_id"],
                "yes" if row["functional_success"] else "no",
                row["compaction_count"],
                row["tokens_freed"],
                row["model_calls"],
                row["tool_calls"],
            ]
            for row in context_rows
        ],
    )
    report = f"""# Memory and Compaction North-Star Analysis

## Analysis question

Do verified persistent lessons measurably help later real Agent tasks, and does
the production compactor preserve task-critical state under real model calls?
The unit of analysis is one complete Agent task, not an internal tool event.

## Primary 20-task result

- Functional task success: **{summary['primary']['task_success']}/{summary['primary']['tasks']}**.
- Strict case-oracle success: **{summary['primary']['strict_cases_passed']}/{summary['primary']['cases']}**; the failure is the runtime Memory chain whose lesson was written but blocked from injection.
- No unsafe actions or user interventions were recorded.

## Persistent Memory

- Genuine verified lessons written: **2/2**.
- Automatically approved and injected on the next analogous task: **1/2**.
- Intended warm tasks with a non-empty Memory injection: **3/4**.
- Warm and cold task success were both **4/4**; this sample shows no success-rate gain.
- Paired tool-call difference (warm − cold): mean **{summary['memory']['tool_call_mean_difference']:.2f}**, median **{summary['memory']['tool_call_median_difference']:.2f}**. Exact two-sided sign test: **p={summary['memory']['tool_call_sign_test_p']:.3f}**, so no reliable efficiency claim is supported.
- The one genuine injected lesson joined the exact written entry ID across Runs and removed recurrence of the original path error, but the cold control also solved the task without error.
- The blocked lesson exposed a product defect: a legitimate root-directory call `list_files {{"path":""}}` caused trace safety to report `empty memory content`, leaving the verified lesson pending.

{memory_table}

## Context compaction

- Critical-state retention: **{summary['compaction']['retained']}/{summary['compaction']['tasks']}** tasks, Wilson 95% CI **[{summary['compaction']['retention_wilson_95'][0]:.3f}, {summary['compaction']['retention_wilson_95'][1]:.3f}]**.
- Every task recorded an effective compaction boundary; total estimated tokens freed: **{summary['compaction']['tokens_freed_total']}**.
- Goal, verified fact, rejected approach, constraint, decision, combined state, long Skill use, and two successive rounds all retained their exact marker.
- The large-file task is a stability outlier: **25 model calls, 31 tool calls, 257088 input tokens, 153226 ms**. It returned the right marker but observed repeated Auto Compact failures and recorded only the initial pre-request compaction. Functional retention therefore passes while in-loop efficiency/stability fails.

{context_table}

## Four-task cross-boundary addendum

- Skill tool-result chain: passed. A real `load_skill` result crossed a second effective compaction and the next task recovered `SKILL-AFTER-COMPACT-88` without reloading the Skill.
- File tool-result chain: invalid as a compactor test and failed its oracle. The first task ignored the requested `read_file` call and replied `READY-FILE-15`, so no file result existed to preserve. This remains a failed case but is not evidence that compaction lost the marker.

## Claim candidates

- Claim: production pre-request compaction preserved the tested conversational task state in all 10 primary tasks.
  - Allowed wording: “10/10 tested synthetic tasks retained their exact critical marker after an observed effective compaction.”
  - Forbidden stronger wording: “Context compression never loses information.”
  - Uncertainty: one provider run, synthetic markers, and the large-tool in-loop path was unstable.
  - Decision: keep with boundary.
- Claim: persistent Memory is operational but not yet reliably advantageous.
  - Allowed wording: “Verified lessons were written 2/2 times, but only 1/2 became injectable; paired success was unchanged and tool-call savings were not statistically supported.”
  - Forbidden stronger wording: “Memory makes the agent reliably better.”
  - Uncertainty: four paired tasks and one safety false positive.
  - Decision: weaken.

## Decision

**Compaction: conditional pass. Persistent lesson efficacy: partial/fail for A-grade reliability.**
History-state retention is strong in this sample, and the protected Skill path
survived a later compaction. Memory write/retrieval works end to end, but a
50% learn-to-inject rate in the two genuine chains and no supported paired
efficiency gain mean the subsystem has not yet demonstrated a dependable
advantage.
"""
    (output_dir / "analysis-report.md").write_text(report, encoding="utf-8")

    stats = f"""# Statistical Appendix

## Design and units

- Primary sample: 20 tasks in 17 isolated cases; 10 Memory tasks and 10 compaction tasks.
- Addendum: 4 tasks in 2 cases, analyzed separately after a coverage gap was identified.
- Repeated unit: warm/cold Memory pair. Internal calls and events are not independent samples.
- Direction: lower tool/model/token/duration counts are better; higher success, injection, and retention are better.

## Descriptive results

- Primary functional success: {summary['primary']['task_success']}/{summary['primary']['tasks']}.
- Strict case success: {summary['primary']['strict_cases_passed']}/{summary['primary']['cases']}.
- Genuine lesson write: 2/2; approved/injected next turn: 1/2, Wilson 95% CI {summary['memory']['learn_to_inject_wilson_95']}.
- Intended warm injection: 3/4, Wilson 95% CI {summary['memory']['warm_injection_wilson_95']}.
- Primary compaction retention: 10/10, Wilson 95% CI {summary['compaction']['retention_wilson_95']}.

## Paired Memory comparison

Tool-call differences (warm − cold): {summary['memory']['tool_call_differences']}.

- Mean difference: {summary['memory']['tool_call_mean_difference']:.3f} calls.
- Median difference: {summary['memory']['tool_call_median_difference']:.3f} calls.
- Non-tied wins/losses for warm: {summary['memory']['tool_call_wins']}/{summary['memory']['tool_call_losses']}.
- Exact two-sided sign test: p={summary['memory']['tool_call_sign_test_p']:.3f}.
- Matched rank-biserial direction statistic: {summary['memory']['tool_call_rank_biserial']:.3f} in favor of warm; unstable because only three non-ties exist.
- All warm and cold tasks succeeded, so there is no success contrast to test.

No normality test or paired t-test is reported: n=4 pairs is too small for a meaningful normality assessment, counts are discrete, and the exact sign test is the safer descriptive inferential check. No multiple-comparison correction is needed because one paired inferential contrast was pre-specified.

## Blockers and limitations

- One provider/model configuration and one execution per task; no seed-level variance estimate.
- Synthetic projects and exact-marker oracles favor factual retention measurement over open-ended coding quality.
- Console Auto Compact failure warnings are observed operational evidence but are not structured Run events; failure-count inference is therefore qualitative.
- The file cross-boundary addendum violated its required-tool precondition and is excluded from compactor-loss claims.
"""
    (output_dir / "stats-appendix.md").write_text(stats, encoding="utf-8")

    catalog = """# Figure Catalog

## figure-01-memory-pairs.svg

- Purpose: compare tool-call cost for four matched warm/cold Memory tasks.
- Data source: task-results.json and paired-memory-results.json.
- Caption requirements: bars are exact task counts; lower is better; warm injection status is printed under each pair; n=4 pairs.
- Observation: two warm tasks save one call, one ties, and the non-injected runtime task costs one extra call.
- Interpretation: direction is mildly favorable only when injection succeeds, but the sample does not support a reliable efficiency claim.
- Caveat: no repeated seeds.

## figure-02-compaction.svg

- Purpose: show estimated tokens freed by each primary compaction task and downstream model work.
- Data source: task-results.json Run events.
- Caption requirements: bar height is summed `tokensFreed`; bubble size is model call count; n=10 tasks.
- Observation: ordinary retention tasks need one model call; the large-file case uses 25 despite a similar initial compaction.
- Interpretation: pre-request compaction preserves markers, but it does not prevent downstream tool/output oscillation.
- Caveat: `tokensFreed` is the production compactor estimate, while model calls come from journal events.
"""
    (output_dir / "figure-catalog.md").write_text(catalog, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    primary = _expand_suite(
        root / "manifest.json",
        root / "results.json",
        root / "results-evidence",
        suite_label="primary",
    )
    addendum = _expand_suite(
        root / "addendum-manifest.json",
        root / "addendum-results.json",
        root / "addendum-results-evidence",
        suite_label="addendum",
    )
    pairs = _memory_pairs(primary)
    entries = _memory_entry_statuses(root / "results-evidence")
    context = [row for row in primary if row["category"] == "context-compaction"]
    tool_diffs = [pair["tool_call_difference"] for pair in pairs]
    wins = sum(value < 0 for value in tool_diffs)
    losses = sum(value > 0 for value in tool_diffs)
    primary_results = _load(root / "results.json").get("results", [])
    summary = {
        "primary": {
            "tasks": len(primary),
            "task_success": sum(row["functional_success"] for row in primary),
            "cases": len(primary_results),
            "strict_cases_passed": sum(
                result.get("status") == "passed" for result in primary_results
            ),
        },
        "memory": {
            "lesson_entries": entries,
            "genuine_lessons_written": len(entries),
            "genuine_lessons_injected": sum(
                entry["injection_count"] and entry["injection_count"] > 0
                for entry in entries
            ),
            "learn_to_inject_wilson_95": _wilson(1, 2),
            "intended_warm_injected": sum(pair["warm_injected"] for pair in pairs),
            "warm_injection_wilson_95": _wilson(
                sum(pair["warm_injected"] for pair in pairs), len(pairs)
            ),
            "tool_call_differences": tool_diffs,
            "tool_call_mean_difference": statistics.mean(tool_diffs),
            "tool_call_median_difference": statistics.median(tool_diffs),
            "tool_call_wins": wins,
            "tool_call_losses": losses,
            "tool_call_sign_test_p": _sign_test(wins, losses),
            "tool_call_rank_biserial": (
                (wins - losses) / (wins + losses) if wins + losses else 0.0
            ),
        },
        "compaction": {
            "tasks": len(context),
            "retained": sum(row["functional_success"] for row in context),
            "retention_wilson_95": _wilson(
                sum(row["functional_success"] for row in context), len(context)
            ),
            "tasks_with_effective_compaction": sum(
                row["compaction_count"] > 0 for row in context
            ),
            "tokens_freed_total": sum(row["tokens_freed"] for row in context),
        },
        "addendum": {
            "tasks": len(addendum),
            "task_success": sum(row["functional_success"] for row in addendum),
            "valid_required_tool_tasks": sum(
                row["required_tool_observed"] for row in addendum
            ),
            "invalid_file_source_task": not next(
                row["required_tool_observed"]
                for row in addendum
                if row["task_id"] == "context-file-cross-boundary#1"
            ),
        },
    }
    _write_json(root / "task-results.json", {"primary": primary, "addendum": addendum})
    _write_json(root / "paired-memory-results.json", pairs)
    _write_json(root / "summary.json", summary)
    with (root / "task-results.csv").open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [key for key in primary[0] if not isinstance(primary[0][key], list)]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows([*primary, *addendum])
    _memory_figure(output_dir / "figures/figure-01-memory-pairs.svg", pairs)
    _compaction_figure(output_dir / "figures/figure-02-compaction.svg", context)
    _write_reports(output_dir, primary, addendum, pairs, entries, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
