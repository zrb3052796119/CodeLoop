"""Actual call-scoped tool schemas and admission must agree at the HTTP boundary.

All HTTP entry points are replaced with local byte responses. No network runs.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.anthropic_adapter import AnthropicModelAdapter
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.model_call_control import call_model_next
from minicode.model_registry import create_model_adapter
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult


MESSAGES = [
    {"role": "system", "content": "Use the authorized tools when necessary."},
    {"role": "user", "content": "Return a concise conclusion."},
]
RESPONSE_SCHEMA = {
    "name": "conclusion", "strict": True,
    "schema": {"type": "object", "additionalProperties": False,
               "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
}
INJECTED_TOOLS = [{"type": "function", "function": {
    "name": "runtime_catalog", "description": "Runtime catalog entry. " * 1000,
    "parameters": {"type": "object", "properties": {"entry": {"type": "string"}}},
}}]


class _Response:
    status = 200

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = json.dumps(body).encode("utf-8")
        self._offset = 0

    def read1(self, amount: int = -1) -> bytes:
        end = len(self._body) if amount < 0 else self._offset + amount
        chunk = self._body[self._offset:end]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        pass


def _registry() -> ToolRegistry:
    return ToolRegistry([ToolDefinition(
        name="inspect", description="Inspect the public entry.", input_schema={"type": "object"},
        validator=lambda value: value,
        run=lambda _value, _context: ToolResult(ok=True, output="public entry"),
    )])


def _wire_schema_cost(body: dict[str, Any]) -> int:
    schemas = body.get("tools", [])
    return estimate_tokens(json.dumps(schemas)) if schemas else 0


@pytest.fixture(autouse=True)
def _local_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("minicode.cybernetic_supervisor.SUPERVISOR_STATE_PATH",
                        tmp_path / "state" / "cybernetic_supervisor.json")
    monkeypatch.delenv("MINI_CODE_MODEL_MODE", raising=False)
    monkeypatch.delenv("MINICODE_PROMPT_CACHE", raising=False)

    def forbidden_network(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("This request-shape test must never enter the network transport.")

    monkeypatch.setattr("urllib.request.urlopen", forbidden_network)


def _capture_http(monkeypatch: pytest.MonkeyPatch, provider: str) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def fake_open(request: Any, *, timeout: float) -> _Response:
        assert timeout > 0
        body = json.loads(request.data.decode("utf-8"))
        captured.append(body)
        input_tokens = sum(estimate_message_tokens(message) for message in body.get("messages", []))
        input_tokens += _wire_schema_cost(body)
        if provider == "openai":
            return _Response({
                "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": input_tokens, "completion_tokens": 4},
            })
        return _Response({
            "stop_reason": "end_turn", "content": [{"type": "text", "text": "<final>ok</final>"}],
            "usage": {"input_tokens": input_tokens, "output_tokens": 4},
        })

    monkeypatch.setattr(f"minicode.{provider}_adapter.open_verified_url", fake_open)
    return captured


def _estimate_and_call(model: Any, registry: ToolRegistry, options: dict[str, Any],
                       captured: list[dict[str, Any]], *, calibrated: bool) -> tuple[int, dict[str, Any]]:
    # This helper is introduced by the proposal. Its presence is required for
    # consistency guards; a missing import on frozen code is never a red proof.
    from minicode.model_call_control import estimate_budgeted_request_input

    local_messages = sum(estimate_message_tokens(message) for message in MESSAGES)
    minimum = 100000 if calibrated else local_messages
    budget = AgentTurnBudget(max_total_tokens=1000000, max_model_calls=1)
    original_runtime = deepcopy(model.runtime)
    estimate = estimate_budgeted_request_input(
        model, MESSAGES, agent_budget=budget, estimated_input=minimum,
        optional_kwargs=options, default_tools=registry,
    )
    assert not captured, "Estimation must not make an HTTP request."
    result = call_model_next(model, MESSAGES, optional_kwargs=options)
    assert result.type == "assistant"
    assert len(captured) == 1
    body = captured[0]
    expected = max(minimum, local_messages + _wire_schema_cost(body))
    assert estimate == expected, (estimate, expected, body.get("tools"))
    assert model.runtime == original_runtime
    return estimate, body


@pytest.mark.parametrize("calibrated", [False, True])
@pytest.mark.parametrize("case", [
    "ordinary", "runtime_inject", "runtime_clear", "empty_override",
    "runtime_inject_with_empty_override", "disable_tools", "json_object", "response_schema",
])
def test_openai_estimate_matches_final_wire_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, calibrated: bool,
) -> None:
    registry = _registry()
    captured = _capture_http(monkeypatch, "openai")
    runtime_params: dict[str, Any] = {}
    if case in {"runtime_inject", "runtime_inject_with_empty_override", "disable_tools",
                "json_object", "response_schema"}:
        runtime_params = {"tools": deepcopy(INJECTED_TOOLS), "tool_choice": "auto", "parallel_tool_calls": True}
    elif case == "runtime_clear":
        runtime_params = {"tools": []}
    options: dict[str, Any] = {"max_retries_override": 0, "max_output_tokens_override": 256}
    if case in {"empty_override", "runtime_inject_with_empty_override"}:
        options["tools_override"] = ToolRegistry([])
    if case == "disable_tools":
        options["disable_tools"] = True
    if case == "json_object":
        # This is the same public provider-factory route used by the existing
        # semantic provider-control tests; no capability is forged on the model.
        model = create_model_adapter("glm-5.3-flash", registry, {
            "provider": "custom",
            "customBaseUrl": "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions",
            "customApiKey": "synthetic", "modelMaxRetries": 0,
            "_openrouter_params": runtime_params,
        })
        assert model.supports_json_object
        options["json_object_override"] = True
    elif case == "response_schema":
        model = create_model_adapter("gpt-4o-mini", registry, {
            "provider": "openai", "openaiBaseUrl": "https://api.openai.com/v1",
            "openaiApiKey": "synthetic", "modelMaxRetries": 0,
            "_openrouter_params": runtime_params,
        })
        assert model.supports_native_json_schema
        options["response_schema_override"] = RESPONSE_SCHEMA
    else:
        model = OpenAIModelAdapter({
            "model": "local-scripted", "openaiBaseUrl": "https://provider.invalid/v1",
            "openaiApiKey": "synthetic", "modelMaxRetries": 0,
            "_openrouter_params": runtime_params,
        }, registry)

    _estimate, body = _estimate_and_call(model, registry, options, captured, calibrated=calibrated)
    if case == "runtime_inject":
        assert body["tools"] == INJECTED_TOOLS
    elif case in {"runtime_clear", "empty_override", "runtime_inject_with_empty_override"}:
        assert body.get("tools", []) == []
        if case == "runtime_inject_with_empty_override":
            assert all(key not in body for key in ("tool_choice", "parallel_tool_calls"))
    elif case in {"disable_tools", "json_object", "response_schema"}:
        assert all(key not in body for key in ("tools", "tool_choice", "parallel_tool_calls"))
    if case == "json_object":
        assert body["response_format"] == {"type": "json_object"}
    elif case == "response_schema":
        assert body["response_format"] == {"type": "json_schema", "json_schema": RESPONSE_SCHEMA}


@pytest.mark.parametrize("calibrated", [False, True])
@pytest.mark.parametrize("case", [
    "cache_default", "cache_disabled", "disable_tools", "empty_override", "unsupported_json_object",
])
def test_anthropic_estimate_matches_final_wire_tool_cache_markers(
    monkeypatch: pytest.MonkeyPatch, case: str, calibrated: bool,
) -> None:
    registry = _registry()
    captured = _capture_http(monkeypatch, "anthropic")
    runtime = {"model": "claude-local", "baseUrl": "https://provider.invalid", "apiKey": "synthetic",
               "modelMaxRetries": 0}
    if case == "cache_disabled":
        runtime["promptCache"] = False
    model = AnthropicModelAdapter(runtime, registry)
    options: dict[str, Any] = {"max_retries_override": 0, "max_output_tokens_override": 256}
    if case == "disable_tools":
        options["disable_tools"] = True
    elif case == "empty_override":
        options["tools_override"] = ToolRegistry([])
    elif case == "unsupported_json_object":
        # call_model_next filters this OpenAI-only option out of Anthropic's
        # signature. Estimation must apply the same filtering before pricing.
        options["json_object_override"] = True
    unmarked = deepcopy(model._get_serialized_tools())
    _estimate, body = _estimate_and_call(model, registry, options, captured, calibrated=calibrated)
    if case in {"cache_default", "unsupported_json_object"}:
        assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    elif case == "cache_disabled":
        assert all("cache_control" not in tool for tool in body["tools"])
    else:
        assert not body.get("tools")
    assert model._get_serialized_tools() == unmarked, "Pricing and cache markers must not mutate the base schema cache."


def test_agent_loop_uses_current_registry_instead_of_runtime_injected_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry()
    captured = _capture_http(monkeypatch, "openai")
    limit = estimate_tokens(json.dumps(INJECTED_TOOLS)) - 1
    budget = AgentTurnBudget(max_total_tokens=limit, max_model_calls=1)
    model = OpenAIModelAdapter({
        "model": "local-scripted", "openaiBaseUrl": "https://provider.invalid/v1",
        "openaiApiKey": "synthetic", "modelMaxRetries": 0, "maxOutputTokens": 256,
        "_openrouter_params": {"tools": deepcopy(INJECTED_TOOLS)},
    }, registry)
    result = run_agent_turn(
        model=model, tools=registry, messages=[{"role": "user", "content": "Conclude from the evidence."}],
        cwd=str(tmp_path), runtime=model.runtime, agent_budget=budget, max_steps=1,
        enable_work_chain=False, enable_bounded_completion=False, enable_implementation_phases=False,
    )
    assert len(captured) == 1
    assert {tool["function"]["name"] for tool in captured[0]["tools"]} == {"inspect"}
    assert budget.snapshot().used_model_calls == 1
    assert not any("turn_budget_exceeded" in message.get("content", "") for message in result)


def test_current_registry_schema_is_rejected_before_real_adapter_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    function = INJECTED_TOOLS[0]["function"]
    registry = ToolRegistry([ToolDefinition(
        name=function["name"], description=function["description"],
        input_schema=function["parameters"], validator=lambda value: value,
        run=lambda _value, _context: ToolResult(ok=True, output="entry"),
    )])
    captured = _capture_http(monkeypatch, "openai")
    model = OpenAIModelAdapter({
        "model": "local-scripted", "openaiBaseUrl": "https://provider.invalid/v1",
        "openaiApiKey": "synthetic", "modelMaxRetries": 0, "maxOutputTokens": 256,
        "_openrouter_params": {"tools": []},
    }, _registry())
    limit = estimate_tokens(json.dumps(model.request_tool_schemas(tools_override=registry))) - 1
    budget = AgentTurnBudget(max_total_tokens=limit, max_model_calls=1)
    result = run_agent_turn(
        model=model, tools=registry, messages=[{"role": "user", "content": "Conclude from the evidence."}],
        cwd=str(tmp_path), runtime=model.runtime, agent_budget=budget, max_steps=1,
        enable_work_chain=False, enable_bounded_completion=False, enable_implementation_phases=False,
    )
    assert not captured, "The actual current registry alone exceeds the token budget."
    assert budget.snapshot().used_model_calls == 0
    assert budget.snapshot().used_total_tokens == 0
    assert budget.snapshot().reserved_total_tokens == 0
    assert any("turn_budget_exceeded" in message.get("content", "") for message in result)
