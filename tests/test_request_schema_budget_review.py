"""Behavioral admission regressions using local, cap-respecting model fixtures.

Run from the production root, with its test helpers on PYTHONPATH:
  PYTHONPATH=.:tests python3 -m pytest -p conftest --noconftest -q \
      -o cache_dir=/tmp/request-schema-pytest-cache /tmp/test_request_schema_budget_review.py

The repository's conftest plugin isolates user state before minicode imports.
No provider, network, runner, or experiment artifacts are used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.types import AgentStep, ModelUsage
from test_reviewer_result_submission import _bound, _rejection


def _message_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def _wire_tools(tools: ToolRegistry) -> list[dict[str, Any]]:
    # Independent wire-shape oracle matching the local fixture's request.
    return [{"type": "function", "function": {
        "name": tool.name, "description": tool.description,
        "parameters": tool.input_schema,
    }} for tool in tools.list()]


def _schema_tokens(tools: ToolRegistry) -> int:
    schemas = _wire_tools(tools)
    return estimate_tokens(json.dumps(schemas)) if schemas else 0


def _catalog_tool() -> ToolDefinition:
    return ToolDefinition(
        name="inspect_catalog",
        description="Inspect an authorized catalog entry. " * 1000,
        input_schema={"type": "object", "properties": {
            "entry": {"type": "string"}}, "required": ["entry"]},
        validator=lambda value: value,
        run=lambda _value, _context: ToolResult(ok=True, output="catalog entry"),
    )


@pytest.fixture(autouse=True)
def _isolated_supervisor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "minicode.cybernetic_supervisor.SUPERVISOR_STATE_PATH",
        tmp_path / "state" / "cybernetic_supervisor.json",
    )


def _run(model: Any, tools: ToolRegistry, budget: AgentTurnBudget,
         tmp_path: Path, **options: Any) -> list[dict[str, Any]]:
    return run_agent_turn(
        model=model, tools=tools,
        messages=[{"role": "user", "content": "Conclude from the available evidence."}],
        cwd=str(tmp_path), runtime={"model": "local-scripted", "maxOutputTokens": 8192},
        agent_budget=budget, max_steps=1,
        enable_work_chain=False, enable_bounded_completion=False,
        enable_implementation_phases=False, **options,
    )


def test_first_request_rejects_schema_cost_before_calling_model(tmp_path: Path) -> None:
    tools = ToolRegistry([_catalog_tool()])
    limit = _schema_tokens(tools) - 1
    budget = AgentTurnBudget(max_total_tokens=limit, max_model_calls=1)
    calls: list[int] = []

    class Model:
        def next(self, messages: list[dict[str, Any]], **_options: Any) -> AgentStep:
            sent = _message_tokens(messages)
            calls.append(sent)
            assert sent < limit < sent + _schema_tokens(tools)
            return AgentStep(type="assistant", kind="final", content="Done.",
                usage=ModelUsage(input_tokens=sent + _schema_tokens(tools),
                                 output_tokens=estimate_tokens("Done."), source="estimated"))

    result = _run(Model(), tools, budget, tmp_path)
    assert not calls, "Messages fit, but the actual tool schemas must prevent this request."
    assert budget.snapshot().used_model_calls == 0
    assert budget.snapshot().used_total_tokens == 0
    assert budget.snapshot().reserved_total_tokens == 0
    assert any("turn_budget_exceeded" in message.get("content", "") for message in result)


def test_first_native_closure_rejects_unaffordable_result_schema(tmp_path: Path) -> None:
    _assignment, store, channel, submit, _kwargs = _bound()
    native_tools = ToolRegistry([submit])
    tools = ToolRegistry([_catalog_tool(), submit])
    limit = _schema_tokens(native_tools) - 1
    budget = AgentTurnBudget(max_total_tokens=limit, max_model_calls=1)
    calls: list[int] = []

    class Model:
        def next(self, messages: list[dict[str, Any]], *, tools_override: ToolRegistry,
                 max_output_tokens_override: int, **_options: Any) -> AgentStep:
            assert tools_override.list_all() == ["review_submit"]
            sent = _message_tokens(messages)
            calls.append(sent)
            assert sent < limit < sent + _schema_tokens(tools_override)
            actions = [{"id": "native-result", "toolName": "review_submit", "input": _rejection()}]
            output = estimate_tokens(json.dumps(actions))
            assert output <= max_output_tokens_override
            return AgentStep(type="tool_calls", calls=actions,
                usage=ModelUsage(input_tokens=sent + _schema_tokens(tools_override),
                                 output_tokens=output, source="estimated"))

    try:
        _run(Model(), tools, budget, tmp_path,
             host_native_result_tool=submit, subagent_mailbox=channel)
        assert not calls, "The first native-only input must include its real result schema."
        assert store.read(channel.write_key) is None
        assert budget.snapshot().used_model_calls == 0
        assert budget.snapshot().used_total_tokens == 0
        assert budget.snapshot().reserved_total_tokens == 0
    finally:
        channel.close()


def test_fitting_native_closure_caps_output_after_actual_selected_schema(tmp_path: Path) -> None:
    _assignment, store, channel, submit, _kwargs = _bound()
    native_tools = ToolRegistry([submit])
    tools = ToolRegistry([_catalog_tool(), submit])
    limit = _schema_tokens(native_tools) + 1024
    assert _schema_tokens(tools) > limit, "An unselected inspection schema would prevent the fitting closure."
    budget = AgentTurnBudget(max_total_tokens=limit, max_model_calls=1)
    bills: list[int] = []
    admissions: list[tuple[int, int, int]] = []

    class Model:
        def next(self, messages: list[dict[str, Any]], *, tools_override: ToolRegistry,
                 max_output_tokens_override: int, **_options: Any) -> AgentStep:
            assert tools_override.list_all() == ["review_submit"]
            actual_input = _message_tokens(messages) + _schema_tokens(tools_override)
            remaining = limit - budget.snapshot().used_total_tokens
            admissions.append((actual_input, max_output_tokens_override, remaining))
            actions = [{"id": "native-result", "toolName": "review_submit", "input": _rejection()}]
            output = estimate_tokens(json.dumps(actions))
            assert output <= max_output_tokens_override
            bills.append(actual_input + output)
            return AgentStep(type="tool_calls", calls=actions,
                usage=ModelUsage(input_tokens=actual_input, output_tokens=output, source="estimated"))

    try:
        _run(Model(), tools, budget, tmp_path,
             host_native_result_tool=submit, subagent_mailbox=channel)
        assert len(bills) == 1, "The native-only schema fits even though the original catalog does not."
        actual_input, output_cap, remaining = admissions[0]
        assert 0 < output_cap <= remaining - actual_input, (
            "Output must fit after the actual selected native schema is priced.",
            output_cap, remaining, actual_input,
        )
        assert store.read(channel.write_key) is not None
        snapshot = budget.snapshot()
        assert snapshot.used_model_calls == 1
        assert snapshot.used_total_tokens == sum(bills) < limit
        assert snapshot.reserved_total_tokens == 0
    finally:
        channel.close()


def test_no_tools_reserves_only_messages_without_empty_schema_charge(tmp_path: Path) -> None:
    tools = ToolRegistry([])
    budget = AgentTurnBudget(max_total_tokens=10000, max_model_calls=1)
    inputs: list[int] = []

    class Model:
        def next(self, messages: list[dict[str, Any]], **_options: Any) -> AgentStep:
            sent = _message_tokens(messages)
            assert budget.snapshot().reserved_total_tokens == sent
            inputs.append(sent)
            return AgentStep(type="assistant", kind="final", content="Done.",
                usage=ModelUsage(input_tokens=sent, output_tokens=1, source="estimated"))

    _run(Model(), tools, budget, tmp_path)
    assert len(inputs) == 1
    assert budget.snapshot().used_total_tokens == inputs[0] + 1
    assert budget.snapshot().reserved_total_tokens == 0


def test_phase_request_prices_only_its_actual_tool_projection(tmp_path: Path) -> None:
    from minicode.tools.edit_file import edit_file_tool
    from test_executor_result_submission import _bound as bound_executor, _submission

    _assignment, _order, store, channel, submit = bound_executor()
    tools = ToolRegistry([_catalog_tool(), edit_file_tool, submit])
    budget = AgentTurnBudget(max_total_tokens=100000, max_model_calls=6)
    observations: list[tuple[int, int, int]] = []

    class Model:
        def next(self, messages: list[dict[str, Any]], *, tools_override: ToolRegistry,
                 max_output_tokens_override: int, **_options: Any) -> AgentStep:
            names = [tool.name for tool in tools_override.list()]
            assert "inspect_catalog" not in names
            assert "executor_submit" in names
            actual_input = _message_tokens(messages) + _schema_tokens(tools_override)
            actions = [{"id": "executor-result", "toolName": "executor_submit", "input": _submission()}]
            output = estimate_tokens(json.dumps(actions))
            assert output <= max_output_tokens_override
            observations.append((actual_input, budget.snapshot().reserved_total_tokens, output))
            return AgentStep(type="tool_calls", calls=actions,
                usage=ModelUsage(input_tokens=actual_input, output_tokens=output, source="estimated"))

    try:
        run_agent_turn(
            model=Model(), tools=tools,
            messages=[{"role": "user", "content": "Complete the assigned implementation."}],
            cwd=str(tmp_path), runtime={"model": "local-scripted", "maxOutputTokens": 256},
            agent_budget=budget, max_steps=6, enable_work_chain=False,
            enable_bounded_completion=False, enable_implementation_phases=True,
            bounded_implementation_phases=True, implementation_task=True,
            implementation_analysis_complete=True, subagent_mailbox=channel,
        )
        assert len(observations) == 1
        actual_input, reserved_input, output = observations[0]
        assert reserved_input == actual_input, "Price the selected phase schemas, without the hidden catalog."
        assert store.read(channel.write_key) is not None
        assert budget.snapshot().used_model_calls == 1
        assert budget.snapshot().used_total_tokens == actual_input + output
        assert budget.snapshot().reserved_total_tokens == 0
    finally:
        channel.close()


def test_custom_adapter_schema_serialization_is_used_for_first_admission(tmp_path: Path) -> None:
    tool = ToolDefinition(name="inspect", description="Inspect.", input_schema={"type": "object"},
        validator=lambda value: value, run=lambda _value, _context: ToolResult(ok=True, output="entry"))
    tools = ToolRegistry([tool])
    provider_schema = [{"name": "inspect", "description": "Inspect.", "input_schema": {
        "type": "object", "properties": {"entry": {"type": "string",
        "description": "Provider-expanded catalog entry. " * 1000}}}}]
    schema_cost = estimate_tokens(json.dumps(provider_schema))
    budget = AgentTurnBudget(max_total_tokens=schema_cost - 1, max_model_calls=1)
    serialized: list[list[str]] = []
    calls: list[int] = []

    class Model:
        def _get_serialized_tools(self, tools_override: ToolRegistry | None = None) -> list[dict[str, Any]]:
            selected = tools if tools_override is None else tools_override
            serialized.append(selected.list_all())
            return provider_schema

        def next(self, messages: list[dict[str, Any]], **_options: Any) -> AgentStep:
            actual_input = _message_tokens(messages) + schema_cost
            calls.append(actual_input)
            return AgentStep(type="assistant", kind="final", content="Done.",
                usage=ModelUsage(input_tokens=actual_input, output_tokens=1, source="estimated"))

    _run(Model(), tools, budget, tmp_path)
    assert not calls, "The provider's expanded schema, rather than a generic shape, must be priced."
    assert serialized and all(names == ["inspect"] for names in serialized)
    assert budget.snapshot().used_model_calls == 0
    assert budget.snapshot().used_total_tokens == 0


def test_unlimited_total_tokens_does_not_add_schema_serialization(tmp_path: Path) -> None:
    tools = ToolRegistry([_catalog_tool()])
    budget = AgentTurnBudget(max_model_calls=1)
    calls: list[int] = []

    class Model:
        def _get_serialized_tools(self, tools_override: ToolRegistry | None = None) -> list[dict[str, Any]]:
            pytest.fail("Unlimited token admission must not introduce a serializer call.")

        def next(self, messages: list[dict[str, Any]], **_options: Any) -> AgentStep:
            actual_input = _message_tokens(messages) + _schema_tokens(tools)
            calls.append(actual_input)
            return AgentStep(type="assistant", kind="final", content="Done.",
                usage=ModelUsage(input_tokens=actual_input, output_tokens=1, source="estimated"))

    result = _run(Model(), tools, budget, tmp_path)
    assert len(calls) == 1
    assert result[-1]["content"] == "Done."
    assert budget.snapshot().limit_total_tokens is None
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().used_total_tokens == calls[0] + 1
    assert budget.snapshot().reserved_total_tokens == 0
