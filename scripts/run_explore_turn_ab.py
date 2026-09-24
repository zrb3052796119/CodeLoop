#!/usr/bin/env python3
"""Explore turn-count A/B harness.

Runs the frozen exploration cases through the real task tool under
different ``MINI_CODE_SUBAGENT_EXPLORE_MAX_TURNS`` values and compares
success / fallback / partial rates plus per-run cost. ``--scripted`` uses a
deterministic scripted explorer (no network, validates the pipeline);
``--live`` uses the real model configuration from ``load_runtime_config``
and SPENDS API QUOTA.

Usage:
    python scripts/run_explore_turn_ab.py --turns 8,12 --live
    python scripts/run_explore_turn_ab.py --turns 2,3 --scripted --limit 2
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from minicode.agent_budget import AgentTurnBudget
from minicode.subagent_sessions import SubagentSessionStore
from minicode.tooling import ToolContext
from minicode.tools.task import task_tool

_HEADER_RE = re.compile(r"Model turns: (\d+) \(tool calls: (\d+)\)")
_PARTIAL_RE = re.compile(r"Result: PARTIAL")
_CONTRACT_RE = re.compile(r'"contractStatus":\s*"(reported|fallback|derived)"')

_DEFAULT_CASES = Path("tests/fixtures/explore_turn_ab_cases.json")


class _ScriptedExplorer:
    """A three-turn explorer: tree → grep → read, then a final answer.

    Deterministically needs exactly 3 model turns, so a turn cap of 2 must
    soft-land partial while 3 succeeds — the pipeline's accounting check.
    """

    def __init__(self) -> None:
        self.calls = 0

    def next(self, messages, on_stream_chunk=None):  # noqa: ANN001
        from minicode.types import AgentStep

        self.calls += 1
        if self.calls == 1:
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "1",
                        "toolName": "file_tree",
                        "input": {"path": "."},
                    }
                ],
            )
        if self.calls == 2:
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "2",
                        "toolName": "grep_files",
                        "input": {"pattern": "def ", "path": "."},
                    }
                ],
            )
        return AgentStep(
            type="assistant",
            content="<final> Survey complete: the entry points and their modules were identified.",
        )


def _parse_result(output: str) -> dict[str, Any]:
    header = _HEADER_RE.search(output)
    contract = _CONTRACT_RE.search(output)
    if header is None:
        raise RuntimeError("task result header missing turn counts")
    return {
        "ok": True,
        "model_turns": int(header.group(1)),
        "tool_calls": int(header.group(2)),
        "partial": _PARTIAL_RE.search(output) is not None,
        "contractStatus": contract.group(1) if contract else "unavailable",
    }


def _run_case(
    *,
    prompt: str,
    turns: int,
    cwd: str,
    runtime: dict,
    scripted: bool,
) -> dict[str, Any]:
    original_adapter = None
    if scripted:
        import minicode.subagent_model_routing as routing_module

        original_adapter = routing_module.create_subagent_model_adapter
        model = _ScriptedExplorer()
        routing_module.create_subagent_model_adapter = (
            lambda runtime_, agent_type, *, tools: model
        )
    env_name = "MINI_CODE_SUBAGENT_EXPLORE_MAX_TURNS"
    previous = os.environ.get(env_name)
    os.environ[env_name] = str(turns)
    try:
        from minicode.subagent_mailbox import SubagentMailbox

        budget = AgentTurnBudget.from_runtime(runtime)
        context = ToolContext(
            cwd=cwd,
            _runtime=runtime,
            _agent_budget=budget,
            _subagent_mailbox=SubagentMailbox(),
            _subagent_sessions=SubagentSessionStore(),
        )
        result = task_tool.run(
            {
                "description": "explore case",
                "prompt": prompt,
                "agent_type": "explore",
            },
            context,
        )
    finally:
        if previous is None:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = previous
        if original_adapter is not None:
            routing_module.create_subagent_model_adapter = original_adapter
    if not result.ok:
        return {
            "ok": False,
            "model_turns": None,
            "tool_calls": None,
            "partial": False,
            "contractStatus": "unavailable",
            "error": result.output[:300],
        }
    row = _parse_result(result.output)
    row["error"] = None
    return row


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    successes = sum(1 for row in rows if row["ok"])
    fallbacks = sum(1 for row in rows if row["contractStatus"] == "fallback")
    partials = sum(1 for row in rows if row["partial"])

    def _mean(key: str) -> float | None:
        values = [row[key] for row in rows if row[key] is not None]
        return round(sum(values) / len(values), 4) if values else None

    return {
        "caseCount": count,
        "successRate": round(successes / count, 4) if count else None,
        "fallbackRate": round(fallbacks / count, 4) if count else None,
        "partialRate": round(partials / count, 4) if count else None,
        "meanModelTurns": _mean("model_turns"),
        "meanToolCalls": _mean("tool_calls"),
    }


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", default="8,12", help="comma-separated turn caps")
    parser.add_argument("--cases", type=Path, default=_DEFAULT_CASES)
    parser.add_argument("--limit", type=int, default=None, help="first N cases only")
    parser.add_argument("--repeats", type=int, default=1)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--scripted", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    document = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = document["cases"][: args.limit] if args.limit else document["cases"]
    turns_values = [int(value) for value in args.turns.split(",")]

    if args.live:
        from minicode.config import load_runtime_config

        runtime = load_runtime_config(os.getcwd())
        mode_label = "live"
    else:
        runtime = {"model": "scripted"}
        mode_label = "scripted"

    configs: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        (repo / "sample.py").write_text(
            "def entry():\n    return 1\n", encoding="utf-8"
        )
        for turns in turns_values:
            rows = []
            for repeat in range(args.repeats):
                for case in cases:
                    row = _run_case(
                        prompt=case["prompt"],
                        turns=turns,
                        cwd=str(repo),
                        runtime=runtime,
                        scripted=not args.live,
                    )
                    row["caseId"] = case["id"]
                    row["repeat"] = repeat
                    rows.append(row)
            configs[str(turns)] = {"cases": rows, "summary": _summarize(rows)}

    report = {
        "schemaVersion": 1,
        "mode": mode_label,
        "turnsCompared": turns_values,
        "caseFile": str(args.cases),
        "repeats": args.repeats,
        "configs": configs,
        "notes": (
            "successRate counts task-tool ok; partialRate counts soft-landed "
            "results; fallbackRate counts contractStatus=fallback. live mode "
            "spends API quota."
        ),
    }
    out_dir = args.out or Path(f"artifacts/explore-turn-ab-{mode_label}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return report


if __name__ == "__main__":
    main()
