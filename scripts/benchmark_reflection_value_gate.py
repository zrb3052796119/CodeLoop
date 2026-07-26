#!/usr/bin/env python3
"""Measure deterministic reflection synthesis, validation, and value gating."""

from __future__ import annotations

import argparse
import gc
import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from minicode.reflection_evidence import DecisionEvidence, TaskEvidence  # noqa: E402
from minicode.reflection_synthesis import (  # noqa: E402
    ReflectionCandidate,
    ReflectionClaim,
    ReflectionClaimValidator,
    ReflectionValueGate,
    RuleReflectionSynthesizer,
)


def _evidence(count: int, *, duplicate: bool = False) -> TaskEvidence:
    decisions = [
        DecisionEvidence(
            f"decision-{index}",
            f"Keep project interface {'shared' if duplicate else index} stable",
            None,
            (f"event-{index}",),
            "confirmed",
            "user_constraint",
        )
        for index in range(count)
    ]
    return TaskEvidence(
        decisions=decisions,
        outcome="success",
        event_positions={f"event-{index}": index for index in range(count)},
    )


def _pipeline_callback(evidence: TaskEvidence) -> Callable[[], dict[str, int]]:
    synthesizer = RuleReflectionSynthesizer()
    validator = ReflectionClaimValidator()
    gate = ReflectionValueGate()

    def run() -> dict[str, int]:
        candidate = synthesizer.synthesize("Preserve project interfaces", evidence)
        validation = validator.validate(candidate, evidence)
        decision = gate.evaluate(candidate, validation, evidence)
        return {
            "generated": len(candidate.claims),
            "valid": len(validation.valid_claims),
            "rejected": len(validation.rejected_claims),
            "accepted": int(decision.accepted),
        }

    return run


def _measure(
    label: str,
    callback: Callable[[], dict[str, int]],
    *,
    iterations: int = 11,
) -> dict[str, Any]:
    elapsed: list[float] = []
    outcome = callback()
    for _ in range(iterations):
        gc.collect()
        started = time.perf_counter()
        outcome = callback()
        elapsed.append((time.perf_counter() - started) * 1000)
    gc.collect()
    tracemalloc.start()
    callback()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "label": label,
        "iterations": iterations,
        "median_ms": round(statistics.median(elapsed), 3),
        "p95_ms": round(sorted(elapsed)[max(0, int(len(elapsed) * 0.95) - 1)], 3),
        "peak_kib": round(peak / 1024, 1),
        "outcome": outcome,
    }


def benchmark() -> dict[str, Any]:
    results = [
        _measure(f"normal_{count}_claims", _pipeline_callback(_evidence(count)))
        for count in (1, 10, 100)
    ]

    duplicate_evidence = _evidence(100, duplicate=True)
    duplicate_claims = [
        ReflectionClaim(
            f"claim-{index}",
            "constraint",
            "shared_interface",
            "Project constraint: Keep project interface shared stable.",
            [f"event-{index}"],
            "confirmed",
        )
        for index in range(100)
    ]
    validator = ReflectionClaimValidator()
    gate = ReflectionValueGate()

    def duplicate_run() -> dict[str, int]:
        candidate = ReflectionCandidate(
            "Duplicate constraints", "success", duplicate_claims
        )
        validation = validator.validate(candidate, duplicate_evidence)
        decision = gate.evaluate(candidate, validation, duplicate_evidence)
        return {
            "generated": len(candidate.claims),
            "valid": len(validation.valid_claims),
            "rejected": len(validation.rejected_claims),
            "accepted": int(decision.accepted),
        }

    results.append(_measure("duplicate_semantic_key_100", duplicate_run))

    invalid_evidence = _evidence(1)
    invalid_claims = [
        ReflectionClaim(
            f"claim-{index}",
            "constraint",
            f"invalid_{index}",
            "Project constraint: Keep project interface 0 stable.",
            [f"missing-event-{index}"],
            "confirmed",
        )
        for index in range(100)
    ]

    def invalid_run() -> dict[str, int]:
        candidate = ReflectionCandidate("Invalid references", "success", invalid_claims)
        validation = validator.validate(candidate, invalid_evidence)
        decision = gate.evaluate(candidate, validation, invalid_evidence)
        return {
            "generated": len(candidate.claims),
            "valid": len(validation.valid_claims),
            "rejected": len(validation.rejected_claims),
            "accepted": int(decision.accepted),
        }

    results.append(_measure("invalid_evidence_reference_100", invalid_run))

    long_source = "Keep project interface 0 stable " + "x" * 100_000
    long_evidence = TaskEvidence(
        decisions=[
            DecisionEvidence(
                "decision-long",
                long_source,
                None,
                ("event-long",),
                "confirmed",
                "user_constraint",
            )
        ],
        outcome="success",
        event_positions={"event-long": 0},
    )
    results.append(_measure("extreme_text_100k", _pipeline_callback(long_evidence)))

    cyclic: dict[str, object] = {"name": "cycle"}
    cyclic["self"] = cyclic
    abnormal = ReflectionClaim(
        "claim-abnormal",
        "constraint",
        "abnormal_metadata",
        "Project constraint: Keep project interface 0 stable.",
        ["event-0"],
        "confirmed",
        limitations=[cyclic],  # type: ignore[list-item]
    )

    def abnormal_run() -> dict[str, int]:
        candidate = ReflectionCandidate("Abnormal metadata", "success", [abnormal])
        validation = validator.validate(candidate, invalid_evidence)
        decision = gate.evaluate(candidate, validation, invalid_evidence)
        return {
            "generated": 1,
            "valid": len(validation.valid_claims),
            "rejected": len(validation.rejected_claims),
            "accepted": int(decision.accepted),
        }

    results.append(_measure("cyclic_metadata", abnormal_run))
    normal = {row["label"]: row for row in results}
    ratio = normal["normal_100_claims"]["median_ms"] / max(
        normal["normal_10_claims"]["median_ms"], 0.001
    )
    return {
        "benchmark": "ReflectionValueGate deterministic end path",
        "results": results,
        "normal_100_under_10ms": normal["normal_100_claims"]["median_ms"] < 10.0,
        "normal_100_to_10_time_ratio": round(ratio, 3),
        "complexity_observation": "Evidence and claims are indexed once; validation work is proportional to claims plus referenced evidence IDs.",
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Reflection Value Gate Performance",
        "",
        "Measurements use `time.perf_counter` and `tracemalloc`, with 11 isolated iterations per scenario and no benchmark dependency.",
        "",
        "| Scenario | Median | p95 | Peak | Generated | Valid | Rejected | Accepted |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["results"]:
        outcome = row["outcome"]
        lines.append(
            f"| `{row['label']}` | {row['median_ms']:.3f} ms | {row['p95_ms']:.3f} ms | "
            f"{row['peak_kib']:.1f} KiB | {outcome['generated']} | {outcome['valid']} | "
            f"{outcome['rejected']} | {outcome['accepted']} |"
        )
    lines.extend(
        [
            "",
            f"- 100-claim normal path under 10 ms: `{report['normal_100_under_10ms']}`",
            f"- 100/10-claim median time ratio: `{report['normal_100_to_10_time_ratio']:.3f}`",
            f"- Complexity: {report['complexity_observation']}",
            "- Absolute timings are machine-specific; the fixed evidence-ID indexes avoid claim-to-all-evidence string cross-products.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    report = benchmark()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.markdown.write_text(_render_markdown(report), encoding="utf-8")
    print(f"Wrote {args.output} and {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
