#!/usr/bin/env python3
"""Measure bounded TraceEvidenceExtractor latency and peak Python allocations."""

from __future__ import annotations

import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from minicode.reflection_evidence import TraceEvidenceExtractor  # noqa: E402


def _normal_trace(event_count: int) -> list[dict[str, Any]]:
    return [
        {
            "event_id": f"event-{index + 1:06d}",
            "call_id": f"call-{index + 1:06d}",
            "type": "tool_result",
            "tool_name": "read_file",
            "status": "success",
            "files_read": [f"src/module_{index}.py"],
            "output_summary": "read complete",
        }
        for index in range(event_count)
    ]


def _repeated_error_trace(event_count: int) -> list[dict[str, Any]]:
    return [
        {
            "event_id": f"event-{index + 1:06d}",
            "call_id": "same-call",
            "type": "tool_result" if index % 2 == 0 else "error",
            "tool_name": "run_command",
            "status": "error",
            "is_error": True,
            "error_type": "TimeoutError",
            "output_summary": "TimeoutError: registry unavailable",
            "message": "TimeoutError: registry unavailable",
        }
        for index in range(event_count)
    ]


def _deep_cycle_trace() -> list[dict[str, Any]]:
    root: dict[str, Any] = {}
    cursor = root
    for _ in range(2_000):
        child: dict[str, Any] = {}
        cursor["next"] = child
        cursor = child
    cursor["cycle"] = root
    return [
        {
            "event_id": "event-000001",
            "call_id": "call-1",
            "type": "tool_call",
            "tool_name": "inspect_payload",
            "input": root,
        }
    ]


def _measure(trace: list[dict[str, Any]], repeats: int = 7) -> dict[str, Any]:
    extractor = TraceEvidenceExtractor()
    timings: list[float] = []
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = extractor.extract("Synthetic benchmark", trace)
        timings.append((time.perf_counter() - started) * 1_000)

    tracemalloc.start()
    result = extractor.extract("Synthetic benchmark", trace)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert result is not None
    return {
        "input_events": len(trace),
        "median_ms": round(statistics.median(timings), 3),
        "peak_kib": round(peak_bytes / 1024, 1),
        "output": {
            "files_read": len(result.files_read),
            "tools": len(result.tool_calls),
            "errors": len(result.errors),
            "diagnostics": list(result.diagnostics),
        },
    }


def main() -> int:
    scenarios = {
        "normal_100": _normal_trace(100),
        "normal_1000": _normal_trace(1_000),
        "max_events_500": _normal_trace(500),
        "repeated_error_500": _repeated_error_trace(500),
        "deep_cycle": _deep_cycle_trace(),
    }
    report = {
        "benchmark": "TraceEvidenceExtractor",
        "repeats": 7,
        "scenarios": {
            name: _measure(trace) for name, trace in scenarios.items()
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
