"""The final request is usable for handoff after growing test feedback."""

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.implementation_phases import ImplementationPhaseController
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult


def _tool(name):
    return ToolDefinition(name=name, description=name, input_schema={"type": "object"},
                          validator=lambda values: values,
                          run=lambda values, context: ToolResult(ok=True, output="unused"))


@pytest.mark.parametrize("native", [False, True])
def test_last_request_cannot_be_spent_on_test_or_a_mixed_test_submit_batch(native):
    budget = AgentTurnBudget(max_model_calls=4, max_total_tokens=500000)
    policy = ImplementationPhaseController(bounded=True, snapshot=budget.snapshot(), max_steps=4,
        now=0, deadline=600, analysis_complete=True, allow_test_completion=True)
    registry = ToolRegistry([_tool("executor_test"), _tool("run_command")]
                            + ([_tool("executor_submit")] if native else []))
    # One implementation request followed by the existing three closing requests.
    policy.request(budget.snapshot(), estimated_input=1000, now=0)
    policy.tool_view(registry)
    policy.admitted()
    for _ in range(2):
        policy.request(budget.snapshot(), estimated_input=1000, now=480)
        assert "executor_test" in {tool.name for tool in policy.tool_view(registry).list()}
        policy.admitted()
        assert not policy.admit_batch([{"id": "test", "toolName": "executor_test", "input": {}}])
    request = policy.request(budget.snapshot(), estimated_input=1000, now=480)
    names = {tool.name for tool in policy.tool_view(registry).list()}
    assert names == ({"executor_submit"} if native else set())
    assert request.handoff_only
    policy.admitted()
    rejected = policy.admit_batch([
        {"id": "test", "toolName": "executor_test", "input": {}},
        {"id": "command", "toolName": "run_command", "input": {"command": "python", "args": ["-m", "pytest"]}},
        {"id": "submit", "toolName": "executor_submit", "input": {}},
    ])
    assert set(rejected) == ({"test", "command"} if native else {"test", "command", "submit"})
    assert policy.exhausted


def test_single_closing_instruction_never_requires_an_unavailable_submit_tool():
    budget = AgentTurnBudget(max_model_calls=4)
    policy = ImplementationPhaseController(bounded=True, snapshot=budget.snapshot(), max_steps=4,
        now=0, deadline=600, analysis_complete=True, allow_test_completion=True)
    policy.tool_view(ToolRegistry([_tool("executor_test"), _tool("run_command")]))
    request = policy.request(budget.snapshot(), estimated_input=1000, now=480)
    assert "executor_submit" not in request.message
    assert "final response" in request.message


def test_observed_input_growth_is_reserved_before_an_optional_test_is_admitted():
    # The first result grew input by 10k tokens. The remaining two tests have
    # similarly sized output, so one more optional request would strand submit.
    budget = AgentTurnBudget(max_model_calls=20, max_total_tokens=500000)
    policy = ImplementationPhaseController(bounded=True, snapshot=budget.snapshot(), max_steps=20,
        now=0, deadline=600, analysis_complete=True, allow_test_completion=True)
    policy.request(budget.snapshot(), estimated_input=10000, now=0)
    policy.admitted()
    budget.record_model_call(input_tokens=10000, output_tokens=10000)
    # Leave 85k tokens: not enough for three closing inputs of 20k, 30k, 40k.
    # Dropping one optional test leaves enough for a real test then handoff.
    budget.record_model_call(input_tokens=395000)
    request = policy.request(budget.snapshot(), estimated_input=20000, now=480)
    assert request.preserve_calls == 1, "two more test rounds would consume the result request"
    assert request.preserve_tokens >= 30000 + 2 * 4096
    reservation = budget.reserve_model_call(20000,
        preserve_model_calls=request.preserve_calls,
        preserve_total_tokens=request.preserve_tokens)
    policy.admitted()
    budget.record_model_call(input_tokens=20000, output_tokens=4096, reservation=reservation)
    handoff = policy.request(budget.snapshot(), estimated_input=30000, now=480)
    assert handoff.handoff_only
    assert handoff.preserve_calls == 0
    reservation = budget.reserve_model_call(30000, preserve_total_tokens=handoff.preserve_tokens)
    policy.admitted()
    budget.record_model_call(input_tokens=30000, output_tokens=2048, reservation=reservation)
    assert policy.exhausted
    assert budget.snapshot().used_total_tokens <= 500000
