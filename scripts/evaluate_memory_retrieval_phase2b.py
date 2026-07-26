#!/usr/bin/env python3
"""Generate Retrieval Phase 2B offline accuracy and performance reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--holdout",
        type=Path,
        default=PROJECT_ROOT / "tests" / "fixtures" / "memory_retrieval_phase2b_holdout.json",
    )
    parser.add_argument(
        "--phase1-dataset",
        type=Path,
        default=PROJECT_ROOT / "tests" / "fixtures" / "memory_retrieval_golden",
    )
    parser.add_argument(
        "--phase2a-baseline",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "memory-retrieval-baseline.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "memory-retrieval-phase2b.json",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=PROJECT_ROOT / "docs" / "memory-retrieval-phase2b.md",
    )
    parser.add_argument(
        "--comparison",
        type=Path,
        default=PROJECT_ROOT / "docs" / "memory-retrieval-phase2b-comparison.md",
    )
    parser.add_argument(
        "--performance",
        type=Path,
        default=PROJECT_ROOT / "docs" / "memory-retrieval-phase2b-performance.md",
    )
    parser.add_argument("--deterministic-core-output", type=Path, default=None)
    parser.add_argument(
        "--enforce-wall-clock-performance",
        action="store_true",
        help=(
            "Treat the real wall-clock gates as strict CLI exit criteria. "
            "The default shared-environment mode records them as advisory observations."
        ),
    )
    return parser.parse_args(argv)


def main() -> int:
    from scripts.memory_retrieval_phase2b_evaluator import (
        deterministic_phase2b_view,
        evaluate_phase2b,
        phase2b_exit_code,
        write_phase2b_reports,
    )

    args = parse_args()
    enforcement_mode = (
        "strict" if args.enforce_wall_clock_performance else "advisory"
    )
    report = evaluate_phase2b(
        project_root=PROJECT_ROOT,
        holdout_path=args.holdout,
        phase1_dataset_root=args.phase1_dataset,
        phase2a_baseline_path=args.phase2a_baseline,
        enforcement_mode=enforcement_mode,
    )
    write_phase2b_reports(
        report,
        json_path=args.output,
        markdown_path=args.markdown,
        comparison_path=args.comparison,
        performance_path=args.performance,
    )
    if args.deterministic_core_output is not None:
        args.deterministic_core_output.parent.mkdir(parents=True, exist_ok=True)
        args.deterministic_core_output.write_text(
            json.dumps(
                deterministic_phase2b_view(report),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    print(
        f"Phase2B cases={report['holdout']['case_count']} "
        f"passed={report['acceptance_passed']} remote_calls={report['remote_call_count']} "
        f"wall_clock_mode={report['performance']['enforcementMode']} "
        f"wall_clock_strict_passed={report['performance']['strictPassed']} "
        f"output={args.output}"
    )
    return phase2b_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
