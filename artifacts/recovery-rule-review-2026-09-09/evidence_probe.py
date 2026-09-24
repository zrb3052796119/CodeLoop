"""Read-only recovery probes using current tool validators and trace emitter.

Run from any directory with Python 3. Synthetic ToolResult values are fed into
the real runtime trace emitter; no commands, edits, or memory writes execute.
The only output file is evidence_probe.json alongside this script.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from minicode.agent_loop import _append_tool_trace_events, _append_trace_event
from minicode.agent_reflection import ReflectionEngine
from minicode.tooling import ToolResult
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.run_command import run_command_tool

TOOLS = {"run_command": run_command_tool, "edit_file": edit_file_tool}


def evaluate(name, steps, expected):
    trace = []
    for index, (tool, payload, ok, output) in enumerate(steps, start=1):
        normalized = TOOLS[tool].validator(payload)
        call = {"id": f"call-{index}", "toolName": tool, "input": normalized}
        _append_tool_trace_events(trace, call, ToolResult(ok=ok, output=output), index)
    _append_trace_event(trace, {"type": "task_result", "status": "success"})
    reflection = ReflectionEngine().reflect("Review and verify the project", trace)
    evidence = reflection.task_evidence
    return {
        "name": name,
        "expected": expected,
        "trace": trace,
        "recoveries": [asdict(item) for item in evidence.recoveries],
        "verification": [asdict(item) for item in evidence.verification],
        "persistable_claims": [asdict(item) for item in reflection.structured_claims],
        "value_accepted": reflection.value_decision.accepted,
    }


def main():
    cases = [
        evaluate(
            "different_args_targets_plus_unrelated_edit",
            [
                ("run_command", {"command": "pytest", "args": ["tests/test_auth.py", "-q"]}, False, "AssertionError: bad auth token"),
                ("edit_file", {"path": "docs/README.md", "old": "Foo", "new": "Bar"}, True, "updated"),
                ("run_command", {"command": "pytest", "args": ["tests/test_billing.py", "-q"]}, True, "3 passed"),
            ],
            "No recovery: billing was tested after a documentation edit; auth was never retested.",
        ),
        evaluate(
            "same_basename_different_project_no_edit",
            [
                ("run_command", {"command": "pytest", "args": ["backend/tests/test_config.py", "-q"]}, False, "AssertionError: bad backend config"),
                ("run_command", {"command": "python", "args": ["-m", "pytest", "frontend/tests/test_config.py", "-q"]}, True, "3 passed"),
            ],
            "No recovery: backend and frontend test files are distinct targets.",
        ),
        evaluate(
            "masked_fixture_error_with_mixed_passed_output",
            [
                ("run_command", {"command": "pytest", "args": ["tests/test_auth.py", "--bad-option"]}, False, "UsageError: unrecognized arguments: --bad-option"),
                ("run_command", {"command": "bash", "args": ["-lc", "pytest tests/test_auth.py -q | tail -20"]}, True, "1 passed, 1 error in 0.15s"),
            ],
            "Verification must not be passed: the pipeline's zero status masks a fixture error.",
        ),
        evaluate(
            "timeout_budget_change_loses_corrective_input",
            [
                ("run_command", {"command": "python", "args": ["scripts/report.py"], "timeout": 1}, False, "Command timed out after 1 seconds (process group terminated)."),
                ("run_command", {"command": "python", "args": ["scripts/report.py"], "timeout": 120}, True, "report created"),
            ],
            "Keep as a candidate for semantic assessment, not causal proof: timeout is a real changed input but absent from the invocation projection, and transient failures are blanket-excluded.",
        ),
    ]
    target = Path(__file__).with_suffix(".json")
    target.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps([
        {"name": case["name"], "recoveries": len(case["recoveries"]),
         "persistable_claims": [item["claim_type"] for item in case["persistable_claims"]],
         "verification": [(item["call_id"], item["result"]) for item in case["verification"]]}
        for case in cases
    ], indent=2))


if __name__ == "__main__":
    main()
