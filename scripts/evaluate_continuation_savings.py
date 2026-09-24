#!/usr/bin/env python3
"""Deterministic continuation-savings benchmark (no network, no API keys).

Scenario: an explore sub-agent maps a small fixture repository (three tool
calls plus a final turn), then the same follow-up question is asked twice —
once by continuing the stored conversation (``continue_subagent_id``), once
by a fresh explore run that must redo the same search. Model turns and tool
calls come from the real task-tool result headers, so the numbers reflect
the production accounting path, not a simulation of it.

Usage: python scripts/evaluate_continuation_savings.py [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from minicode.subagent_sessions import SubagentSessionStore
from minicode.tooling import ToolContext
from minicode.tools.task import task_tool

_HEADER_RE = re.compile(
    r"Model turns: (\d+) \(tool calls: (\d+)\)"
)
_CONTINUE_RE = re.compile(r"continue_subagent_id='(sub_[0-9a-f]{32})'")


class _ScriptedModel:
    """Deterministic explorer: fixed steps, no provider."""

    def __init__(self, steps: list[Any]) -> None:
        self._steps = steps
        self.calls = 0

    def next(self, messages, on_stream_chunk=None):  # noqa: ANN001
        step = self._steps[min(self.calls, len(self._steps) - 1)]
        self.calls += 1
        return step


def _tool_step(index: int, name: str, payload: dict) -> Any:
    from minicode.types import AgentStep

    return AgentStep(
        type="tool_calls",
        calls=[{"id": str(index), "toolName": name, "input": payload}],
    )


def _final_step(text: str) -> Any:
    from minicode.types import AgentStep

    return AgentStep(type="assistant", content=text)


# Round 1 / fresh control: a real survey needs a tree pass, two greps, a
# read, then the summary. The follow-up via continuation needs none of
# that — the answer is already in the stored conversation.
SURVEY_STEPS = [
    _tool_step(1, "file_tree", {"path": "."}),
    _tool_step(2, "grep_files", {"pattern": "invoice", "path": "."}),
    _tool_step(3, "read_file", {"path": "billing/invoices.py"}),
    _final_step(
        "<final> billing/invoices.py owns invoice creation; "
        "create_invoice() is the entry point and totals flow through "
        "sum_line_items()."
    ),
]
FOLLOWUP_DIRECT = [
    _final_step(
        "<final> create_invoice() in billing/invoices.py issues the invoice."
    ),
]


def build_fixture_repo(root: Path) -> Path:
    (root / "billing").mkdir(parents=True, exist_ok=True)
    (root / "billing" / "invoices.py").write_text(
        "def create_invoice(order):\n"
        "    \"\"\"Issue an invoice for one order.\"\"\"\n"
        "    return {'id': 1, 'total': sum_line_items(order)}\n"
        "\n"
        "def sum_line_items(order):\n"
        "    return sum(item['price'] for item in order['items'])\n",
        encoding="utf-8",
    )
    (root / "billing" / "ledger.py").write_text(
        "LEDGER = []\n", encoding="utf-8"
    )
    (root / "README.md").write_text(
        "# fixture repo for the continuation-savings benchmark\n",
        encoding="utf-8",
    )
    return root


def _parse_header(output: str) -> dict[str, int]:
    match = _HEADER_RE.search(output)
    if match is None:
        raise RuntimeError("task result header missing turn counts")
    return {
        "model_turns": int(match.group(1)),
        "tool_calls": int(match.group(2)),
    }


def _run_task(model_steps: list, context: ToolContext, payload: dict) -> tuple[dict, str]:
    import minicode.subagent_model_routing as routing_module

    model = _ScriptedModel(model_steps)
    original = routing_module.create_subagent_model_adapter
    routing_module.create_subagent_model_adapter = (
        lambda runtime, agent_type, *, tools: model
    )
    try:
        result = task_tool.run(payload, context)
    finally:
        routing_module.create_subagent_model_adapter = original
    if not result.ok:
        raise RuntimeError(f"task failed: {result.output[:500]}")
    return _parse_header(result.output), result.output


def run_scenario(context: ToolContext) -> dict[str, Any]:
    """Run initial explore → continuation follow-up → fresh follow-up."""
    initial, initial_output = _run_task(
        SURVEY_STEPS,
        context,
        {
            "description": "map billing module",
            "prompt": "Map the billing module: which files and functions exist?",
            "agent_type": "explore",
        },
    )
    # The header advertises the subagent id for continuation.
    match = _CONTINUE_RE.search(initial_output)
    if match is None:
        raise RuntimeError("no continuation id advertised in result header")
    subagent_id = match.group(1)

    followup_prompt = "Which function issues the invoice?"
    continuation, _ = _run_task(
        FOLLOWUP_DIRECT,
        context,
        {
            "description": "invoice follow-up",
            "prompt": followup_prompt,
            "agent_type": "explore",
            "continue_subagent_id": subagent_id,
        },
    )
    continuation["is_continuation"] = True
    continuation["continued_from"] = subagent_id

    fresh, _ = _run_task(
        SURVEY_STEPS,
        context,
        {
            "description": "invoice lookup",
            "prompt": followup_prompt,
            "agent_type": "explore",
        },
    )
    fresh["is_continuation"] = False
    initial["is_continuation"] = False
    return {"initial": initial, "continuation": continuation, "fresh": fresh}


def main(out_root: Path | None = None) -> dict[str, Any]:
    out_root = Path(out_root) if out_root else Path(
        "artifacts/continuation-savings-2026-09-24"
    )
    out_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        repo = build_fixture_repo(Path(tmp) / "repo")
        context = ToolContext(
            cwd=str(repo),
            _runtime={"model": "scripted"},
            _subagent_sessions=SubagentSessionStore(),
        )
        report: dict[str, Any] = {
            "schemaVersion": 1,
            "mode": "offline-deterministic",
            "notes": (
                "model_turns/tool_calls parsed from real task-tool result "
                "headers; tokens are not applicable with the scripted model"
            ),
        }
        report.update(run_scenario(context))
    report["savingsRatio"] = {
        key: round(
            1.0
            - report["continuation"][key] / max(1, report["fresh"][key]),
            4,
        )
        for key in ("model_turns", "tool_calls")
    }
    (out_root / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None)
    main(parser.parse_args().out)
