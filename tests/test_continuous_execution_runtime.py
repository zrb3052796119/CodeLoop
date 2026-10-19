"""Actual default loops do not acquire hidden turn/time/assignment caps."""
from types import SimpleNamespace

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.multi_agent_contracts import parse_workflow_request
from minicode.subagent_types import resolve_max_turns
from minicode.task_deadline import task_timeout_seconds
from minicode.tooling import ToolContext, ToolDefinition, ToolRegistry, ToolResult
from minicode.types import AgentStep
from minicode.workflow_runner import _build_worker_assignment
from test_workflow_v2_structured_routing import _request


@pytest.fixture(autouse=True)
def explicit_configuration_only(monkeypatch):
    monkeypatch.delenv("MINICODE_TASK_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr("minicode.subagent_types._read_max_turns_override", lambda name: None)


def test_default_loop_can_finish_after_old_fifty_step_boundary(tmp_path):
    executed = []

    def echo(inputs, context):
        executed.append(inputs["index"])
        return ToolResult(ok=True, output=f"Observed {inputs['index']}")

    class Model:
        calls = 0

        def next(self, messages, on_stream_chunk=None):
            self.calls += 1
            if self.calls <= 55:
                return AgentStep(type="tool_calls", calls=[{
                    "id": f"call-{self.calls}", "toolName": "echo",
                    "input": {"index": self.calls},
                }])
            return AgentStep(type="assistant", content="All 55 observations delivered.")

    model = Model()
    budget = AgentTurnBudget()
    result = run_agent_turn(
        model=model, tools=ToolRegistry([ToolDefinition(
            name="echo", description="Read a numbered observation",
            input_schema={"type": "object"}, validator=lambda value: value, run=echo,
        )]), cwd=str(tmp_path), enable_work_chain=False, agent_budget=budget,
        messages=[{"role": "user", "content": "Collect the observations."}],
    )
    assert executed == list(range(1, 56))
    assert model.calls == budget.snapshot().used_model_calls == 56
    assert result[-1]["content"] == "All 55 observations delivered."
    assert not result[-1].get("_partial")


def test_unconfigured_task_and_subagent_have_no_deadline_or_turn_cap():
    assert task_timeout_seconds(ToolContext(cwd=".")) is None
    assert all(resolve_max_turns(role) is None for role in ("explore", "plan", "general"))


@pytest.mark.parametrize("role", ["executor", "analyst", "reviewer"])
def test_unlimited_assignment_keeps_null_limits_instead_of_defaulting(role):
    request = parse_workflow_request(_request(), legacy_task_id="task_continuous")
    assignment = _build_worker_assignment(
        request, SimpleNamespace(workflow_id="wf_continuous", assignment_id="asg_continuous"),
        context=ToolContext(cwd="."), agent_budget=AgentTurnBudget(), role=role,
        mode={"executor": "implement", "analyst": "prepare_change", "reviewer": "change_review"}[role],
        objective="Update allowed.txt.",
    )
    assert assignment.budget.to_dict() == {
        "deadlineAt": None, "maxModelCalls": None,
        "maxInputTokens": None, "maxOutputTokens": None,
    }
