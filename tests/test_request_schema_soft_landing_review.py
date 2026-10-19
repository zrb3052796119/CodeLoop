"""Public request-boundary regressions supplementing the frozen seven tests.

Run from the chosen production root:
  PYTHONPATH=.:tests python3 -m pytest -p conftest --noconftest -q \
      -o cache_dir=/tmp/request-schema-pytest-cache /tmp/test_request_schema_soft_landing_review.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.types import AgentStep, ModelUsage


def _messages_price(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def _schemas_price(tools: ToolRegistry) -> int:
    schemas = [{"type": "function", "function": {"name": tool.name,
        "description": tool.description, "parameters": tool.input_schema}}
        for tool in tools.list()]
    return estimate_tokens(json.dumps(schemas)) if schemas else 0


def _catalog() -> ToolRegistry:
    return ToolRegistry([ToolDefinition(
        name="inspect_catalog", description="Inspect an authorized catalog entry. " * 1000,
        input_schema={"type": "object", "properties": {"entry": {"type": "string"}},
                      "required": ["entry"]},
        validator=lambda value: value,
        run=lambda _value, _context: ToolResult(ok=True, output="catalog entry inspected"),
    )])


@pytest.fixture(autouse=True)
def _isolated_supervisor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("minicode.cybernetic_supervisor.SUPERVISOR_STATE_PATH",
                        tmp_path / "state" / "cybernetic_supervisor.json")


def test_soft_landing_rejects_schema_cost_before_second_model_call(tmp_path: Path) -> None:
    tools = _catalog()
    schema_cost = _schemas_price(tools)
    limit = schema_cost + 512
    budget = AgentTurnBudget(max_total_tokens=limit, max_model_calls=2)
    bills: list[int] = []

    class Model:
        runtime = {"maxOutputTokens": 256}

        def next(self, messages: list[dict[str, Any]], **options: Any) -> AgentStep:
            # Neither phase nor completion policy disables tools in this
            # existing soft-landing route: the same catalog reaches the model.
            assert not options.get("disable_tools")
            actual_input = _messages_price(messages) + schema_cost
            if not bills:
                actions = [{"id": "inspection", "toolName": "inspect_catalog",
                            "input": {"entry": "public"}}]
                output = estimate_tokens(json.dumps(actions))
                assert output <= self.runtime["maxOutputTokens"]
                bills.append(actual_input + output)
                return AgentStep(type="tool_calls", calls=actions,
                    usage=ModelUsage(input_tokens=actual_input, output_tokens=output, source="estimated"))
            text = "Inspection ended with unresolved work."
            output = estimate_tokens(text)
            assert output <= options.get("max_output_tokens_override", self.runtime["maxOutputTokens"])
            bills.append(actual_input + output)
            return AgentStep(type="assistant", kind="final", content=text,
                usage=ModelUsage(input_tokens=actual_input, output_tokens=output, source="estimated"))

    result = run_agent_turn(
        model=Model(), tools=tools,
        messages=[{"role": "user", "content": "Inspect the public catalog entry."}],
        cwd=str(tmp_path), runtime={"model": "local-scripted", "maxOutputTokens": 256},
        agent_budget=budget, max_steps=1, max_steps_soft_landing=True,
        enable_work_chain=False, enable_bounded_completion=False,
        enable_implementation_phases=False,
    )
    assert bills and bills[0] < limit
    remaining_after_first = limit - bills[0]
    closing_messages_cost = _messages_price(result[:-1])
    assert closing_messages_cost < remaining_after_first < closing_messages_cost + schema_cost
    assert len(bills) == 1, "The soft-landing schema does not fit, so a second model request must not start."
    snapshot = budget.snapshot()
    assert snapshot.used_model_calls == 1
    assert snapshot.used_total_tokens == bills[0] < limit
    assert snapshot.reserved_total_tokens == 0
    assert result[-1]["content"] == "Reached the maximum tool step limit for this turn."


def test_explicit_adapter_tools_none_keeps_messages_only_admission(tmp_path: Path) -> None:
    tools = _catalog()
    budget = AgentTurnBudget(max_total_tokens=1024, max_model_calls=1)
    assert _schemas_price(tools) > 1024
    inputs: list[tuple[int, int]] = []

    class Model(OpenAIModelAdapter):
        def next(self, messages: list[dict[str, Any]], **options: Any) -> AgentStep:
            assert options.get("tools_override") is None
            assert self._get_serialized_tools() == []
            sent = _messages_price(messages)
            inputs.append((sent, budget.snapshot().reserved_total_tokens))
            return AgentStep(type="assistant", kind="final", content="Done.",
                usage=ModelUsage(input_tokens=sent, output_tokens=1, source="estimated"))

    model = Model(runtime={"model": "local-scripted", "maxOutputTokens": 256}, tools=None)
    result = run_agent_turn(
        model=model, tools=tools,
        messages=[{"role": "user", "content": "Conclude from the available evidence."}],
        cwd=str(tmp_path), runtime=model.runtime, agent_budget=budget, max_steps=1,
        enable_work_chain=False, enable_bounded_completion=False,
        enable_implementation_phases=False,
    )
    assert len(inputs) == 1, "An adapter with tools=None sends no schema, even with a nonempty execution registry."
    actual_input, reserved_input = inputs[0]
    assert actual_input == reserved_input
    assert result[-1]["content"] == "Done."
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().used_total_tokens == actual_input + 1
    assert budget.snapshot().reserved_total_tokens == 0


def test_observed_input_calibration_does_not_count_schema_twice(tmp_path: Path) -> None:
    from test_reviewer_result_submission import _bound, _rejection

    _assignment, store, channel, submit, _kwargs = _bound()
    tools = ToolRegistry([*_catalog().list(), submit])
    schema_cost = _schemas_price(tools)
    provider_framing = 400
    budget = AgentTurnBudget(max_total_tokens=100000, max_model_calls=3)
    observations: list[tuple[int, int, int]] = []

    class Model:
        runtime = {"maxOutputTokens": 256}

        def next(self, messages: list[dict[str, Any]], **options: Any) -> AgentStep:
            assert options.get("tools_override") is None, "Both ordinary native-worker requests fit."
            assert not options.get("disable_tools")
            actual_input = _messages_price(messages) + schema_cost + provider_framing
            actions = ([{"id": "inspection", "toolName": "inspect_catalog",
                         "input": {"entry": "public"}}] if not observations else
                       [{"id": "review", "toolName": "review_submit", "input": _rejection()}])
            output = estimate_tokens(json.dumps(actions))
            assert output <= self.runtime["maxOutputTokens"]
            observations.append((actual_input, budget.snapshot().reserved_total_tokens, output))
            # A deterministic provider-usage fixture exercises the host's
            # existing observed-input calibration; no provider is contacted.
            return AgentStep(type="tool_calls", calls=actions,
                usage=ModelUsage(input_tokens=actual_input, output_tokens=output, source="provider"))

    try:
        run_agent_turn(
            model=Model(), tools=tools,
            messages=[{"role": "user", "content": "Review the public catalog entry."}],
            cwd=str(tmp_path), runtime={"model": "local-scripted", "maxOutputTokens": 256},
            agent_budget=budget, max_steps=2, enable_work_chain=False,
            enable_bounded_completion=False, enable_implementation_phases=False,
            host_native_result_tool=submit, subagent_mailbox=channel,
        )
        assert len(observations) == 2
        second_input, second_reservation, _output = observations[1]
        assert second_reservation == second_input, (
            "Observed input already contains schemas and framing; take max with the raw request estimate.",
            second_reservation, second_input, schema_cost,
        )
        assert store.read(channel.write_key) is not None
        assert budget.snapshot().used_total_tokens == sum(actual + output for actual, _reserved, output in observations)
        assert budget.snapshot().used_model_calls == 2
        assert budget.snapshot().reserved_total_tokens == 0
    finally:
        channel.close()
