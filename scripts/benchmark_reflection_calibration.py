#!/usr/bin/env python3
"""Measure local parser/diagnostic and synthetic replay calibration overhead."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _per_operation_ms(iterations: int, operation: Callable[[], None]) -> float:
    started = time.perf_counter()
    for _ in range(iterations):
        operation()
    return (time.perf_counter() - started) * 1_000 / iterations


def _response_text(semantic_key: str) -> str:
    return json.dumps(
        {
            "task_summary": "Preserve parser API",
            "outcome": "success",
            "claims": [
                {
                    "claim_type": "constraint",
                    "semantic_key": semantic_key,
                    "statement": "Do not change the public parse API.",
                    "evidence_ids": ["event-1"],
                    "epistemic_status": "confirmed",
                    "applies_when": "When changing parser interfaces.",
                    "limitations": [],
                    "verification_ids": [],
                    "related_error_ids": [],
                    "related_recovery_ids": [],
                }
            ],
        },
        separators=(",", ":"),
    )


def run_benchmark(iterations: int = 5_000) -> dict[str, Any]:
    from minicode.reflection_evidence import TraceEvidenceExtractor
    from minicode.reflection_llm import (
        LLMCandidateParseError,
        LLMReflectionSynthesizer,
        ReflectionLLMConfig,
        get_reflection_output_schema,
        get_reflection_prompt,
        parse_llm_candidate,
    )
    from minicode.reflection_replay import ReplayStructuredGenerationClient
    from minicode.reflection_shadow_metrics import reflection_task_identifier
    from minicode.reflection_synthesis import (
        ReflectionClaimValidator,
        ReflectionValueGate,
    )

    iterations = max(100, min(100_000, int(iterations)))
    evidence = TraceEvidenceExtractor().extract(
        "Preserve parser API",
        [
            {
                "event_id": "event-1",
                "type": "user_constraint",
                "content": "Do not change the public parse API.",
            },
            {"event_id": "event-2", "type": "task_result", "status": "success"},
        ],
    )
    config = ReflectionLLMConfig(prompt_version="calibrated")
    valid_response = _response_text("preserve_parse_api")
    invalid_response = _response_text("Preserve-Parse API")

    def parse_valid() -> None:
        parse_llm_candidate(
            valid_response,
            "Preserve parser API",
            evidence,
            config,
        )

    def parse_invalid() -> None:
        try:
            parse_llm_candidate(
                invalid_response,
                "Preserve parser API",
                evidence,
                config,
            )
        except LLMCandidateParseError:
            pass

    replay_iterations = max(100, iterations // 10)
    response_hash = hashlib.sha256(valid_response.encode("utf-8")).hexdigest()
    task_identifier = reflection_task_identifier("Preserve parser API")
    records = [
        {
            "task_identifier": task_identifier,
            "sanitized_response": valid_response,
            "replay_response_hash": response_hash,
            "usage_source": "unavailable",
        }
        for _ in range(replay_iterations)
    ]
    replay_client = ReplayStructuredGenerationClient(records)
    synthesizer = LLMReflectionSynthesizer(replay_client, config)
    validator = ReflectionClaimValidator()
    value_gate = ReflectionValueGate()

    def replay_pipeline() -> None:
        attempt = synthesizer.attempt("Preserve parser API", evidence)
        if not attempt.success or attempt.candidate is None:
            raise AssertionError("synthetic replay benchmark candidate failed")
        validation = validator.validate(attempt.candidate, evidence)
        value_gate.evaluate(attempt.candidate, validation, evidence)

    baseline_prompt = get_reflection_prompt("baseline")
    calibrated_prompt = get_reflection_prompt("calibrated")
    baseline_schema = json.dumps(
        get_reflection_output_schema("baseline"), separators=(",", ":")
    )
    calibrated_schema = json.dumps(
        get_reflection_output_schema("calibrated"), separators=(",", ":")
    )
    return {
        "schema_version": 1,
        "network_calls": 0,
        "iterations": {
            "parser": iterations,
            "replay_pipeline": replay_iterations,
        },
        "milliseconds_per_operation": {
            "valid_parse": _per_operation_ms(iterations, parse_valid),
            "invalid_parse_with_detail": _per_operation_ms(iterations, parse_invalid),
            "replay_parser_validator_value": _per_operation_ms(
                replay_iterations, replay_pipeline
            ),
        },
        "prompt_schema_size": {
            "baseline_prompt_characters": len(baseline_prompt),
            "calibrated_prompt_characters": len(calibrated_prompt),
            "prompt_character_delta": len(calibrated_prompt) - len(baseline_prompt),
            "baseline_schema_bytes": len(baseline_schema.encode("utf-8")),
            "calibrated_schema_bytes": len(calibrated_schema.encode("utf-8")),
            "schema_byte_delta": len(calibrated_schema.encode("utf-8"))
            - len(baseline_schema.encode("utf-8")),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_benchmark(args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
