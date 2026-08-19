"""Run MiniCode's deterministic Tier 0 quality promotion gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__:
    from .agent_quality_evaluator import evaluate_gate, evaluate_quality_suite
else:
    sys.path.insert(0, str(PROJECT_ROOT))
    from agent_quality_evaluator import evaluate_gate, evaluate_quality_suite


DEFAULT_FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "agent_quality"
DEFAULT_CONTRACT = PROJECT_ROOT / "artifacts" / "agent-quality-contract.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate deterministic Skill-routing and context-compaction "
            "quality without remote model calls."
        )
    )
    parser.add_argument(
        "--profile",
        choices=("current", "a"),
        default="current",
        help="current prevents regression; a checks the promotion target",
    )
    parser.add_argument(
        "--fixture-root",
        type=Path,
        default=DEFAULT_FIXTURE_ROOT,
    )
    parser.add_argument(
        "--contract",
        type=Path,
        default=DEFAULT_CONTRACT,
    )
    parser.add_argument(
        "--north-star-results",
        type=Path,
        default=None,
        help=(
            "evaluate a fresh evidence file; defaults to the recorded baseline "
            "results in the fixture root"
        ),
    )
    parser.add_argument(
        "--include-cases",
        action="store_true",
        help="include per-case diagnostics in the JSON report",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = evaluate_quality_suite(
            args.fixture_root,
            include_cases=args.include_cases,
            north_star_results_path=args.north_star_results,
        )
        gate = evaluate_gate(report, args.contract, profile=args.profile)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    output = dict(report)
    output["gate"] = gate
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
