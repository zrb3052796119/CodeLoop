#!/usr/bin/env python3
"""Generate the offline Retrieval Phase 2A artifact and comparison reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FROZEN_ACCEPTED_OUTPUTS = frozenset(
    {
        (PROJECT_ROOT / "artifacts" / "memory-retrieval-phase2a.json").resolve(),
        (PROJECT_ROOT / "docs" / "memory-retrieval-phase2a.md").resolve(),
        (
            PROJECT_ROOT / "docs" / "memory-retrieval-phase2a-comparison.md"
        ).resolve(),
    }
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "tests" / "fixtures" / "memory_retrieval_golden",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "memory-retrieval-baseline.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT
        / "artifacts"
        / "memory-retrieval-phase2a-evaluation.json",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=PROJECT_ROOT / "docs" / "memory-retrieval-phase2a-evaluation.md",
    )
    parser.add_argument(
        "--comparison",
        type=Path,
        default=PROJECT_ROOT
        / "docs"
        / "memory-retrieval-phase2a-evaluation-comparison.md",
    )
    parser.add_argument("--deterministic-core-output", type=Path, default=None)
    parser.add_argument(
        "--enforce-wall-clock-performance",
        action="store_true",
        help="Fail when the measured canonical P95 exceeds the unchanged 5.0 ms limit.",
    )
    return parser.parse_args(argv)


def _validate_output_paths(args: argparse.Namespace) -> None:
    destinations = {
        "--output": args.output,
        "--markdown": args.markdown,
        "--comparison": args.comparison,
        "--deterministic-core-output": args.deterministic_core_output,
    }
    for option, path in destinations.items():
        if path is not None and path.resolve() in FROZEN_ACCEPTED_OUTPUTS:
            raise ValueError(
                f"{option} refuses frozen accepted Phase 2A path: {path}"
            )


def main(argv: list[str] | None = None) -> int:
    from scripts.memory_retrieval_phase2a_evaluator import (
        deterministic_phase2a_view,
        evaluate_phase2a_dataset,
        phase2a_exit_code,
        write_phase2a_reports,
    )

    args = parse_args(argv)
    try:
        _validate_output_paths(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    enforcement_mode = (
        "strict" if args.enforce_wall_clock_performance else "advisory"
    )
    report = evaluate_phase2a_dataset(
        args.dataset,
        project_root=PROJECT_ROOT,
        baseline_path=args.baseline,
        enforcement_mode=enforcement_mode,
    )
    write_phase2a_reports(
        report,
        json_path=args.output,
        markdown_path=args.markdown,
        comparison_path=args.comparison,
    )
    if args.deterministic_core_output is not None:
        args.deterministic_core_output.parent.mkdir(parents=True, exist_ok=True)
        args.deterministic_core_output.write_text(
            json.dumps(
                deterministic_phase2a_view(report),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    exit_code = phase2a_exit_code(report)
    print(
        f"Phase2A cases={report['dataset_case_count']} arms={len(report['arms'])} "
        f"mode={report['enforcementMode']} "
        f"canonical_p95_ms={report['latency']['canonical_retrieval']['p95_ms']:.6f} "
        f"deterministic_passed={report['deterministicAcceptancePassed']} "
        f"strict_passed={report['strictPassed']} "
        f"passed={report['acceptancePassed']} "
        f"remote_calls={report['remote_call_count']} output={args.output}"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
