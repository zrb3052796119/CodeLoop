#!/usr/bin/env python3
"""Run the frozen Retrieval Phase 3A semantic-gap evaluation."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCEPTED_GOLD_PATH = (
    PROJECT_ROOT / "artifacts" / "memory-retrieval-semantic-gap-baseline.json"
)
DEFAULT_GENERATED_REPORT_PATH = (
    PROJECT_ROOT / "artifacts" / "memory-retrieval-semantic-gap-evaluation.json"
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "tests" / "fixtures" / "memory_retrieval_semantic_gap",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_GENERATED_REPORT_PATH,
    )
    parser.add_argument(
        "--baseline-markdown",
        type=Path,
        default=PROJECT_ROOT / "docs" / "memory-retrieval-semantic-gap-baseline.md",
    )
    parser.add_argument(
        "--analysis-markdown",
        type=Path,
        default=PROJECT_ROOT / "docs" / "memory-retrieval-semantic-gap-analysis.md",
    )
    parser.add_argument(
        "--performance-markdown",
        type=Path,
        default=PROJECT_ROOT / "docs" / "memory-retrieval-semantic-gap-performance.md",
    )
    parser.add_argument(
        "--formal-root",
        type=Path,
        default=Path.home() / ".mini-code",
    )
    parser.add_argument(
        "--stage-start-snapshot",
        type=Path,
        default=Path("/tmp/minicode-phase3a-formal-tree-start.json"),
    )
    parser.add_argument("--fingerprint", action="store_true")
    parser.add_argument("--skip-hashseed-check", action="store_true")
    return parser.parse_args()


def _hashseed_fingerprints(dataset: Path) -> dict[str, object]:
    fingerprints: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="minicode-phase3a-hashseed-") as temporary:
        for seed in ("1", "777"):
            environment = os.environ.copy()
            environment["PYTHONHASHSEED"] = seed
            environment["HOME"] = str(Path(temporary) / f"home-{seed}")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--dataset",
                    str(dataset),
                    "--fingerprint",
                    "--skip-hashseed-check",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
                cwd=PROJECT_ROOT,
            )
            fingerprints[seed] = completed.stdout.strip().splitlines()[-1]
    return {
        "pythonhashseed_fingerprints": fingerprints,
        "matches": len(set(fingerprints.values())) == 1,
    }


def main() -> int:
    from scripts.memory_retrieval_semantic_gap_evaluator import (
        _safe_report_scan,
        evaluate_fingerprint,
        evaluate_semantic_gap,
        write_reports,
    )

    args = parse_args()
    if args.output.resolve() == ACCEPTED_GOLD_PATH.resolve():
        raise ValueError("The accepted semantic baseline is immutable.")
    if args.fingerprint:
        print(evaluate_fingerprint(args.dataset))
        return 0
    stage_snapshot = args.stage_start_snapshot if args.stage_start_snapshot.is_file() else None
    report = evaluate_semantic_gap(
        project_root=PROJECT_ROOT,
        dataset_root=args.dataset,
        formal_root=args.formal_root,
        stage_start_snapshot_path=stage_snapshot,
    )
    if args.skip_hashseed_check:
        report["determinism"] = {
            "pythonhashseed_fingerprints": {},
            "matches": None,
            "skipped": True,
        }
    else:
        report["determinism"] = _hashseed_fingerprints(args.dataset)
        report["evaluation_passed"] = (
            report["evaluation_passed"] and report["determinism"]["matches"]
        )
    report["artifact_security_scan"] = _safe_report_scan(report)
    report["evaluation_passed"] = (
        report["evaluation_passed"] and report["artifact_security_scan"]["passed"]
    )
    write_reports(
        report,
        json_path=args.output,
        baseline_markdown_path=args.baseline_markdown,
        analysis_markdown_path=args.analysis_markdown,
        performance_markdown_path=args.performance_markdown,
    )
    print(
        json.dumps(
            {
                "cases": report["dataset"]["case_count"],
                "evaluation_passed": report["evaluation_passed"],
                "confirmed_gaps": report["semantic_gap_adjudication"]["confirmed_count"],
                "phase3b_gate": report["phase3b_entry_gate"]["passed"],
                "remote_calls": report["remote_call_count"],
                "output": str(args.output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["evaluation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
