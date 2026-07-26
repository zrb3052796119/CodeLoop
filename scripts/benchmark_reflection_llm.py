#!/usr/bin/env python3
"""Benchmark deterministic and scripted portions of optional LLM reflection."""

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

from minicode.reflection_evidence import TraceEvidenceExtractor  # noqa: E402
from minicode.reflection_llm import (  # noqa: E402
    LLMReflectionSynthesizer,
    ReflectionLLMConfig,
    ReflectionLLMEligibilityGate,
    StructuredGenerationResponse,
    build_llm_evidence_envelope,
)
from minicode.reflection_synthesis import (  # noqa: E402
    ReflectionClaimValidator,
    ReflectionValueGate,
    RuleReflectionSynthesizer,
)


TRACE = [
    {"event_id": "event-1", "type": "user_constraint", "content": "The public parse API must remain stable."},
    {"event_id": "event-2", "type": "task_result", "status": "success"},
]


class _Client:
    def generate_json(self, messages, *, timeout_seconds, max_output_tokens):
        del messages, timeout_seconds, max_output_tokens
        return StructuredGenerationResponse(
            text=json.dumps(
                {
                    "task_summary": "Preserve parser API",
                    "outcome": "success",
                    "claims": [
                        {
                            "claim_type": "constraint",
                            "semantic_key": "preserve_parse_api",
                            "statement": "The public parse API must remain stable.",
                            "evidence_ids": ["event-1"],
                            "epistemic_status": "confirmed",
                            "applies_when": "When modifying parser interfaces.",
                            "limitations": [],
                            "verification_ids": [],
                            "related_error_ids": [],
                            "related_recovery_ids": [],
                        }
                    ],
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            input_tokens=300,
            output_tokens=110,
            estimated_cost_usd=0.0002,
            latency_ms=4.0,
        )


def _measure(label: str, callback: Callable[[], Any], iterations: int) -> dict[str, Any]:
    callback()
    values: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        callback()
        values.append((time.perf_counter() - started) * 1000)
    gc.collect()
    tracemalloc.start()
    callback()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    ordered = sorted(values)
    return {
        "label": label,
        "iterations": iterations,
        "median_ms": round(statistics.median(values), 4),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 4),
        "peak_kib": round(peak / 1024, 1),
    }


def benchmark() -> dict[str, Any]:
    evidence = TraceEvidenceExtractor().extract("Preserve parser API", TRACE)
    config = ReflectionLLMConfig(mode="llm_shadow")
    gate = ReflectionLLMEligibilityGate()
    rule = RuleReflectionSynthesizer()
    validator = ReflectionClaimValidator()
    value_gate = ReflectionValueGate()
    candidate = rule.synthesize("Preserve parser API", evidence)
    validation = validator.validate(candidate, evidence)

    def rule_path():
        current = rule.synthesize("Preserve parser API", evidence)
        checked = validator.validate(current, evidence)
        return value_gate.evaluate(current, checked, evidence)

    def llm_local_path():
        attempt = LLMReflectionSynthesizer(_Client(), config).attempt(
            "Preserve parser API", evidence
        )
        checked = validator.validate(attempt.candidate, evidence)  # type: ignore[arg-type]
        return value_gate.evaluate(attempt.candidate, checked, evidence)  # type: ignore[arg-type]

    results = [
        _measure("eligibility_gate", lambda: gate.evaluate(evidence, model_call_allowed=True), 5000),
        _measure("allowlisted_envelope", lambda: build_llm_evidence_envelope("Preserve parser API", evidence, config), 1000),
        _measure("rule_synthesis_validation_value", rule_path, 1000),
        _measure("validator_value_only", lambda: value_gate.evaluate(candidate, validation, evidence), 2000),
        _measure("scripted_llm_parse_validation_value", llm_local_path, 500),
    ]
    indexed = {row["label"]: row for row in results}
    return {
        "benchmark": "Optional reflection LLM local overhead",
        "results": results,
        "eligibility_under_1ms": indexed["eligibility_gate"]["median_ms"] < 1.0,
        "rule_path_under_1ms": indexed["rule_synthesis_validation_value"]["median_ms"] < 1.0,
        "provider_latency_excluded": True,
        "scripted_provider_reported_latency_ms": 4.0,
        "resource_limits": {
            "timeout_seconds": config.timeout_seconds,
            "max_input_bytes": config.max_input_bytes,
            "max_output_bytes": config.max_output_bytes,
            "max_output_tokens": config.max_output_tokens,
            "max_claims": config.max_claims,
        },
    }


def _render(report: dict[str, Any]) -> str:
    lines = [
        "# Reflection LLM Shadow Performance",
        "",
        "Local deterministic measurements exclude real provider/network latency.",
        "",
        "| Path | Median | p95 | Peak |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in report["results"]:
        lines.append(
            f"| `{row['label']}` | {row['median_ms']:.4f} ms | {row['p95_ms']:.4f} ms | {row['peak_kib']:.1f} KiB |"
        )
    limits = report["resource_limits"]
    lines.extend(
        [
            "",
            f"- Eligibility median below 1 ms: `{report['eligibility_under_1ms']}`",
            f"- Rule synthesis/validation/value median below 1 ms: `{report['rule_path_under_1ms']}`",
            f"- Timeout: `{limits['timeout_seconds']}` s; input/output bytes: `{limits['max_input_bytes']}` / `{limits['max_output_bytes']}`; output tokens: `{limits['max_output_tokens']}`; claims: `{limits['max_claims']}`.",
            "- Shadow persistence completes before the optional model comparison. The configured provider timeout bounds the remaining synchronous diagnostic work.",
            "- Scripted holdout latency and token/cost fields validate reporting only; real-provider distributions remain unmeasured.",
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
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown.write_text(_render(report), encoding="utf-8")
    print(json.dumps({"output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
