#!/usr/bin/env python3
"""Evaluate the current ReflectionEngine against the golden trace dataset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, default=None)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--comparison", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    from reflection_evaluator import (
        evaluate_dataset,
        write_comparison_report,
        write_json_report,
        write_markdown_report,
    )

    args = parse_args()
    report = evaluate_dataset(args.dataset)
    write_json_report(report, args.output)
    if args.markdown is not None:
        write_markdown_report(report, args.markdown)
    if args.comparison is not None:
        if args.baseline is None:
            raise ValueError("--comparison requires --baseline")
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        shared_case_ids = {
            case["case_id"]
            for case in baseline.get("cases", [])
            if isinstance(case, dict) and case.get("case_id")
        }
        current_shared = evaluate_dataset(args.dataset, shared_case_ids)
        write_comparison_report(baseline, current_shared, report, args.comparison)
    print(
        f"Evaluated {report['case_count']} cases; "
        f"JSON: {args.output}"
        + (f"; Markdown: {args.markdown}" if args.markdown is not None else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
