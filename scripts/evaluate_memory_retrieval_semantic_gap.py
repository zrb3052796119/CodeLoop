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
V2_CANDIDATE_PATH = (
    PROJECT_ROOT / "artifacts" / "memory-retrieval-semantic-gap-v2-candidate.json"
)
ACCEPTED_V2_PATH = (
    PROJECT_ROOT / "artifacts" / "memory-retrieval-semantic-gap-baseline-v2.json"
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
    parser.add_argument(
        "--write-v2-candidate",
        action="store_true",
        help="Explicitly refresh the protected v2 behavior-candidate artifact.",
    )
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
    args = parse_args()
    protected_paths = {
        ACCEPTED_GOLD_PATH.resolve(),
        ACCEPTED_V2_PATH.resolve(),
        V2_CANDIDATE_PATH.resolve(),
    }
    if args.output.resolve() in protected_paths and not (
        args.write_v2_candidate
        and args.output.resolve() == V2_CANDIDATE_PATH.resolve()
    ):
        raise ValueError(
            "The protected semantic artifact is immutable without the explicit "
            "--write-v2-candidate workflow."
        )
    if args.write_v2_candidate and args.output.resolve() != V2_CANDIDATE_PATH.resolve():
        raise ValueError("--write-v2-candidate may only target the v2 candidate path.")
    from scripts.memory_retrieval_semantic_gap_evaluator import (
        _safe_report_scan,
        evaluate_fingerprint,
        evaluate_semantic_gap,
        finalize_v2_candidate_status,
        semantic_behavior_v2_view,
        write_reports,
    )

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
    finalize_v2_candidate_status(report)
    if ACCEPTED_V2_PATH.is_file() and args.output.resolve() != ACCEPTED_V2_PATH.resolve():
        accepted_v2 = json.loads(ACCEPTED_V2_PATH.read_text(encoding="utf-8"))
        matches_v2 = semantic_behavior_v2_view(report) == semantic_behavior_v2_view(
            accepted_v2
        )
        report["accepted_v2_comparison"] = {
            "artifact": ACCEPTED_V2_PATH.name,
            "matches": matches_v2,
        }
        report["evaluation_passed"] = report["evaluation_passed"] and matches_v2
    if args.write_v2_candidate and not report["acceptance"]["behavior_candidate"][
        "passed"
    ]:
        raise ValueError("The v2 behavior candidate gates did not pass.")
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
                "behavior_candidate_passed": report["acceptance"][
                    "behavior_candidate"
                ]["passed"],
                "source_certification_passed": report["acceptance"][
                    "source_certification"
                ]["passed"],
                "accepted": report["acceptance"]["accepted"],
                "accepted_v2_matches": report.get("accepted_v2_comparison", {}).get(
                    "matches"
                ),
                "confirmed_gaps": report["semantic_gap_adjudication"]["confirmed_count"],
                "phase3b_gate": report["phase3b_entry_gate"]["passed"],
                "remote_calls": report["remote_call_count"],
                "output": str(args.output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if args.write_v2_candidate:
        return 0 if report["acceptance"]["behavior_candidate"]["passed"] else 1
    return 0 if report["evaluation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
