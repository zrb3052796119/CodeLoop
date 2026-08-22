"""Read-only release check joining Git cleanliness to the frozen A gate."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from scripts.agent_quality_evaluator import evaluate_gate, evaluate_quality_suite


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def classify_porcelain(output: str) -> dict[str, int]:
    """Return bounded status counts without persisting file paths."""
    records = [line for line in output.splitlines() if line]
    untracked = sum(line.startswith("??") for line in records)
    return {
        "changedTotal": len(records),
        "trackedOrStaged": len(records) - untracked,
        "untracked": untracked,
    }


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return completed.stdout.strip()


def check_release(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    root = root.resolve()
    git_head = _git(root, "rev-parse", "HEAD")
    counts = classify_porcelain(
        _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    )
    fixture_root = root / "tests" / "fixtures" / "agent_quality"
    contract_path = root / "artifacts" / "agent-quality-contract.json"
    quality = evaluate_quality_suite(fixture_root)
    gate = evaluate_gate(quality, contract_path, profile="current")
    clean = counts["changedTotal"] == 0
    return {
        "schemaVersion": 1,
        "head": git_head,
        "git": {**counts, "clean": clean},
        "qualityGate": {
            "profile": "current",
            "passed": bool(gate["passed"]),
            "failedChecks": list(gate["failedChecks"]),
        },
        "reproducibleRelease": clean and bool(gate["passed"]),
        "reason": (
            "ready"
            if clean and gate["passed"]
            else "dirty_worktree"
            if not clean
            else "quality_gate_failed"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check whether this exact Git checkout passes the frozen A gate."
    )
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args(argv)
    try:
        report = check_release(args.root)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report = {
            "schemaVersion": 1,
            "reproducibleRelease": False,
            "reason": "check_failed",
            "errorType": type(error).__name__,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["reproducibleRelease"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
