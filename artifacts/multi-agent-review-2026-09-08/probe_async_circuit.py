"""Offline probe: asynchronous delegation bypasses the read-only circuit.

Run from the repository root with:
    python artifacts/multi-agent-review-2026-09-08/probe_async_circuit.py

Only worker bodies are replaced; actual task dispatch, lifecycle, mailbox,
and terminal polling are exercised. No model or network calls are made.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from minicode.subagent_lifecycle import AsyncSubagentLifecycle
from minicode.subagent_mailbox import SubagentMailbox
from minicode.tooling import ToolContext, ToolResult
from minicode.tools import task


def main() -> None:
    mailbox = SubagentMailbox()
    lifecycle = AsyncSubagentLifecycle()
    context = ToolContext(
        _subagent_mailbox=mailbox,
        _subagent_lifecycle=lifecycle,
    )
    base = {"agent_type": "explore", "description": "inspect", "prompt": "inspect code"}
    failure = ToolResult(ok=False, output="simulated worker failure")
    try:
        with (
            patch.object(task, "_SYNC_RUN", return_value=failure),
            patch.object(task, "_run", return_value=failure),
        ):
            for _ in range(2):
                task._dispatch(base, context)
            blocked_sync = task._dispatch(base, context)
            assert "delegation_circuit_open" in blocked_sync.output
            admitted_async = task._dispatch({**base, "action": "spawn"}, context)
            assert admitted_async.ok
            subagent_id = json.loads(admitted_async.output)["subagentId"]
            terminal = lifecycle.poll(subagent_id, wait_seconds=1)
            assert terminal["status"] == "failed"
            assert task._delegation_failure_count(mailbox) == 2
            print(json.dumps({
                "two_sync_failures_open_circuit": True,
                "third_sync_rejected": not blocked_sync.ok,
                "spawn_admitted_despite_open_circuit": admitted_async.ok,
                "spawn_terminal_status": terminal["status"],
                "failure_count_after_failed_spawn": task._delegation_failure_count(mailbox),
            }, indent=2))
    finally:
        lifecycle.shutdown()


if __name__ == "__main__":
    main()
