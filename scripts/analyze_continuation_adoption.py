#!/usr/bin/env python3
"""Measure continuation adoption over one (or many) Run journal(s).

Reads the same sidecar layout SubagentRunJournal writes
(``<run_dir>/subagent-runs/*.jsonl``) and reports, for read-only runs
(explore/plan and project profiles): total runs, runs carrying the
``continuedFromSubagentId`` marker, the adoption rate, and mean model turns
for continuation vs non-continuation runs. All inputs are content-free
journal records; nothing here touches prompts or findings.

Usage:
    python scripts/analyze_continuation_adoption.py <run_dir> [<run_dir> ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from minicode.subagent_journal import SubagentRunJournal
from minicode.subagent_profiles import AGENT_NAME_RE

_WRITE_TYPES = frozenset({"general", "workflow"})


def _is_read_only_type(agent_type: str) -> bool:
    return (
        agent_type not in _WRITE_TYPES
        and AGENT_NAME_RE.fullmatch(agent_type) is not None
    )


def analyze(run_dir: Path) -> dict:
    journal = SubagentRunJournal(Path(run_dir))
    summaries = journal.list_runs()
    read_only = [s for s in summaries if _is_read_only_type(s.agent_type)]
    continuations = [s for s in read_only if s.continued_from is not None]
    fresh = [s for s in read_only if s.continued_from is None]

    def _mean(values: list[int]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    return {
        "schemaVersion": 1,
        "runDir": str(run_dir),
        "totalRuns": len(read_only),
        "continuationRuns": len(continuations),
        "adoptionRate": (
            round(len(continuations) / len(read_only), 4) if read_only else None
        ),
        "meanModelTurns": {
            "continuation": _mean([s.model_turns for s in continuations]),
            "nonContinuation": _mean([s.model_turns for s in fresh]),
        },
        "meanToolCalls": {
            "continuation": _mean([s.tool_calls for s in continuations]),
            "nonContinuation": _mean([s.tool_calls for s in fresh]),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    args = parser.parse_args(argv)
    reports = [analyze(run_dir) for run_dir in args.run_dirs]
    totals = {
        "schemaVersion": 1,
        "runs": reports,
        "aggregate": {
            "totalRuns": sum(r["totalRuns"] for r in reports),
            "continuationRuns": sum(r["continuationRuns"] for r in reports),
            "adoptionRate": None,
        },
    }
    total = totals["aggregate"]["totalRuns"]
    if total:
        totals["aggregate"]["adoptionRate"] = round(
            totals["aggregate"]["continuationRuns"] / total, 4
        )
    json.dump(totals, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
