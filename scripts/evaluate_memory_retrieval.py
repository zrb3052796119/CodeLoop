#!/usr/bin/env python3
"""Generate MiniCode's offline synthetic memory-retrieval baseline."""

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
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "tests" / "fixtures" / "memory_retrieval_golden",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "memory-retrieval-baseline.json",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=PROJECT_ROOT / "docs" / "memory-retrieval-baseline.md",
    )
    parser.add_argument(
        "--deterministic-core-output",
        type=Path,
        default=None,
        help="Optional timing-free report used for repeatability comparison.",
    )
    return parser.parse_args()


def main() -> int:
    from scripts.memory_retrieval_evaluator import (
        deterministic_report_view,
        evaluate_dataset,
        write_json_report,
        write_markdown_report,
    )

    args = parse_args()
    report = evaluate_dataset(args.dataset, project_root=PROJECT_ROOT)
    write_json_report(report, args.output)
    write_markdown_report(report, args.markdown)
    if args.deterministic_core_output is not None:
        core = deterministic_report_view(report)
        args.deterministic_core_output.parent.mkdir(parents=True, exist_ok=True)
        args.deterministic_core_output.write_text(
            json.dumps(core, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(
        f"Evaluated {report['dataset_case_count']} synthetic cases across four arms; "
        f"JSON={args.output}; Markdown={args.markdown}; remote_calls={report['remote_call_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
