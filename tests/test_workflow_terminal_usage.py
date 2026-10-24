"""Outer workflow failures retain only usage returned by completed Workers."""

from pathlib import Path
import threading

import pytest

from minicode.agent_budget import AgentTurnBudget
import minicode.task_deadline as task_deadline
from minicode.tooling import ToolContext, ToolResult, ToolUsageCounts
from minicode.tools import task as task_module
from minicode.workflow_workspace import WorkflowIsolationError
from test_workflow_v2_structured_routing import _request


@pytest.mark.parametrize("failure", ["deadline", "isolation"])
def test_outer_failure_preserves_completed_worker_usage_without_guessing_interrupted_worker(
        tmp_path, monkeypatch, failure):
    (tmp_path / "allowed.txt").write_text("before")
    now = [0.0]
    first_completed = threading.Event()
    workspaces, events = [], []
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)

    class Sink:
        def emit(self, kind, *, step=None, payload=None):
            events.append((kind, payload))

    def worker(data, context):
        assert data["agent_type"] == "explore"
        workspaces.append(Path(context.cwd))
        if data["description"].endswith(":architecture"):
            for _ in range(2):
                lease = context._agent_budget.reserve_model_call(20)
                context._agent_budget.record_model_call(
                    input_tokens=20, output_tokens=5, reservation=lease)
            first_completed.set()
            return ToolResult(ok=True, output="untrusted display: 999 model turns / 999 tool calls",
                              _usage_counts=ToolUsageCounts(2, 3))
        assert first_completed.wait(2), "the earlier Worker must finish first"
        if data["description"].endswith(":tests"):
            # This invocation never returns a ToolResult. Its unreported
            # usage cannot be inferred from role count or diagnostic prose.
            if failure == "isolation":
                raise WorkflowIsolationError("fixture snapshot became unavailable")
            now[0] = 1000.0
            task_deadline.raise_task_deadline(context)
            pytest.fail("the actual owner deadline was not enforced")
        return ToolResult(ok=True, output="No model or tool calls were made.")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(task_deadline.time, "monotonic", lambda: now[0])
    result = task_module.task_tool.run({
        "description": "terminal usage", "prompt": "Apply the bounded change.",
        "agent_type": "workflow", "parallel_explore": True,
        "workflow_request": _request(risk="low", complex=False),
    }, ToolContext(cwd=str(tmp_path), _runtime={"model": "fixture"},
                   _agent_budget=budget, _task_deadline_monotonic=900.0,
                   _task_timeout_seconds=900.0, _event_sink=Sink()))

    assert not result.ok
    expected_code = ("sub_agent_deadline_exceeded" if failure == "deadline"
                     else "workflow_isolation_unavailable")
    assert f"error[{expected_code}]" in result.output
    assert result._usage_counts == ToolUsageCounts(2, 3)
    assert budget.snapshot().used_model_calls == 2
    completed = [payload for kind, payload in events
                 if kind == "subagent.completed" and payload.get("agentType") == "workflow"]
    assert len(completed) == 1
    assert completed[0]["modelTurns"] == 2 and completed[0]["toolCalls"] == 3
    assert workspaces and all(path != tmp_path and not path.exists() for path in workspaces)
    assert (tmp_path / "allowed.txt").read_text() == "before"
