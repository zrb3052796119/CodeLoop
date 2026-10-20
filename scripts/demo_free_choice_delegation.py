#!/usr/bin/env python3
"""Free-choice delegation bakeoff. No model call and no API key.

Live runs happen on Dari's machine. This script checks the conditions that
comparison needs, then prints the protocol. It does not call a model.

Protocol for the live arm:
- Do not pass ImplementationSpec. A host-owned spec never asks the parent
  to choose.
- Do not prepend a forced "call task(workflow)" instruction.
- Leave delegationWriteGate unset. The default is off. The lexical decision
  is a hint in the system prompt, and the parent may edit.
- Turn subagent routing on and point the child models at plan and general.
- Pick a task that really spans several modules, for example implementing
  hold and release in stock.py together with test_public.py, or a real
  bug that touches two packages. Record whether the parent's first mutating
  call is task(agent_type='workflow', write_paths, verification_commands)
  or a direct edit_file/write_file.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from minicode.agent_intelligence import (  # noqa: E402
    ErrorCategory,
    NudgeGenerator,
    RecoveryStrategy,
    ClassifiedError,
)
from minicode.delegation_policy import (  # noqa: E402
    delegation_prompt_note,
    parent_delegation_gate,
)
from minicode.prompt import build_system_prompt  # noqa: E402
from minicode.tools.task import (  # noqa: E402
    build_task_tool_for_project,
    task_tool,
)

MULTI = "Implement hold and release in stock.py and test_public.py."


def evaluate() -> list[str]:
    """Return labels for bakeoff conditions that are not met."""

    failures: list[str] = []
    gate = parent_delegation_gate(
        MULTI,
        routing_enabled=True,
        implementation_owned=False,
        agent_depth=0,
    )
    if gate.pending or not gate.handoff:
        failures.append("default-gate")
    note = delegation_prompt_note(
        MULTI, routing_enabled=True, implementation_owned=False,
    )
    if not note.startswith("Hint:") or "stay blocked" in note:
        failures.append("soft-hint")
    guide = build_system_prompt("/tmp", [], {"skills": []})
    if "few non-sensitive" in guide or "just to avoid work" in guide:
        failures.append("old-edit-directly-guidance")
    if "task(agent_type='workflow', write_paths, verification_commands)" not in guide:
        failures.append("workflow-guidance")
    parent = build_task_tool_for_project("/tmp")
    hidden = parent.input_schema["properties"]
    if "workflow_request" in hidden or "parallel_explore" in hidden:
        failures.append("parent-schema")
    if "workflow_request" not in task_tool.input_schema["properties"]:
        failures.append("worker-schema")
    nudge = NudgeGenerator.generate(
        ClassifiedError(
            category=ErrorCategory.PERMISSION,
            strategy=RecoveryStrategy.REQUEST_PERMISSION,
            confidence=1.0,
            context={"tool_name": "write_file"},
        )
    )
    if 'agent_type="workflow" is an authorized route' in nudge:
        failures.append("permission-fallback")
    return failures


def main() -> int:
    print("free-choice delegation bakeoff (offline, no model call)")
    failures = evaluate()
    if failures:
        print("failed: " + ", ".join(failures))
        return 1
    print("gate default: off (delegationWriteGate)")
    print("hint: lexical decision is advisory; the parent may edit")
    print("live arm: no ImplementationSpec, no forced prompt, routing on")
    print("task: a change that spans several modules")
    print("record: first mutating call is task(workflow) or a direct edit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
