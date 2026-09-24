from __future__ import annotations

import json
import time
import urllib.error
from typing import Any, ClassVar

import pytest

from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.model_registry import build_provider_config, create_model_adapter
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken


_MODEL = "glm-5.3-flash"
_ENDPOINT = "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
_MESSAGES = [{"role": "user", "content": "Classify this instruction as JSON."}]
_SCHEMA = {
    "name": "classification",
    "strict": True,
    "schema": {"type": "object"},
}


class _Response:
    status = 200

    def __init__(self, *, content: str = '{"intents":[]}', on_read=None) -> None:
        self.body = json.dumps(
            {
                "choices": [
                    {"message": {"content": content}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 7},
            }
        ).encode("utf-8")
        self.offset = 0
        self.on_read = on_read
        self.closed = False

    def read1(self, amount: int) -> bytes:
        if self.on_read:
            self.on_read()
        chunk = self.body[self.offset : self.offset + amount]
        self.offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class _Tool:
    name = "run_command"
    description = "Run a command"
    input_schema: ClassVar[dict[str, str]] = {"type": "object"}


class _Tools:
    def list(self):
        return [_Tool()]


@pytest.fixture(autouse=True)
def _real_factory(monkeypatch):
    monkeypatch.delenv("MINI_CODE_MODEL_MODE", raising=False)


def _adapter(**runtime: Any) -> OpenAIModelAdapter:
    return create_model_adapter(
        _MODEL,
        _Tools(),
        {
            "provider": "custom",
            "customBaseUrl": _ENDPOINT,
            "customApiKey": "synthetic",
            "maxOutputTokens": 8000,
            "temperature": 0.9,
            "modelMaxRetries": 4,
            "modelTimeoutSeconds": 120,
            **runtime,
        },
    )


@pytest.mark.parametrize("effort", ["low", "high", "max"])
def test_glm_json_and_reasoning_controls_reach_wire_with_existing_limits(
    monkeypatch, effort
) -> None:
    captured = []

    def fake_open(request, *, timeout):
        captured.append((request.full_url, json.loads(request.data), timeout))
        return _Response()

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", fake_open)
    adapter = _adapter(
        _openrouter_params={
            "max_tokens": 9999,
            "temperature": 1,
            "tools": [{"type": "function"}],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        }
    )
    result = adapter.next(
        _MESSAGES,
        json_object_override=True,
        reasoning_effort_override=effort,
        max_output_tokens_override=512,
        max_response_bytes_override=4096,
        temperature_override=0,
        max_retries_override=0,
        deadline_monotonic=time.monotonic() + 2,
    )

    url, body, timeout = captured[0]
    assert len(captured) == 1
    assert url == _ENDPOINT
    assert body["model"] == _MODEL
    assert body["response_format"] == {"type": "json_object"}
    assert body["reasoning_effort"] == effort
    assert body["max_tokens"] == 512
    assert body["temperature"] == 0
    assert all(key not in body for key in ("tools", "tool_choice", "parallel_tool_calls"))
    assert 0 < timeout <= 2
    assert adapter.supports_json_object is True
    assert adapter.supports_native_json_schema is False
    assert adapter.supported_reasoning_efforts == ("low", "high", "max")
    assert result.content == '{"intents":[]}'
    assert result.diagnostics.stopReason == "stop"
    assert result.usage.output_tokens == 7


def test_glm_defaults_do_not_inject_controls_or_persist_per_call_overrides(monkeypatch):
    bodies = []

    def fake_open(request, *, timeout):
        bodies.append(json.loads(request.data))
        return _Response()

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", fake_open)
    adapter = _adapter()
    original_runtime = dict(adapter.runtime)
    adapter.next(_MESSAGES)
    adapter.next(_MESSAGES, json_object_override=True, reasoning_effort_override="low")
    adapter.next(_MESSAGES)

    assert bodies[0] == bodies[2]
    assert "tools" in bodies[0]
    assert "response_format" not in bodies[0]
    assert "reasoning_effort" not in bodies[0]
    assert bodies[0]["max_tokens"] == 8000
    assert bodies[0]["temperature"] == 0.9
    assert adapter.runtime == original_runtime


@pytest.mark.parametrize("provider", ["custom", "openai"])
@pytest.mark.parametrize("path", ["/api/paas/v4", "/api/coding/paas/v4"])
def test_only_exact_official_completion_routes_advertise_controls(provider, path):
    field = "customBaseUrl" if provider == "custom" else "openaiBaseUrl"
    config = build_provider_config(
        _MODEL,
        {"provider": provider, field: f"https://open.bigmodel.cn{path}/chat/completions/"},
    )
    assert config.supports_json_object is True
    assert config.supports_native_json_schema is False
    assert config.supported_reasoning_efforts == ("low", "high", "max")


@pytest.mark.parametrize(
    ("provider", "model", "endpoint"),
    [
        ("custom", _MODEL, "https://proxy.invalid/v1"),
        ("custom", _MODEL, "https://open.bigmodel.cn.proxy.invalid/api/coding/paas/v4/chat/completions"),
        ("custom", _MODEL, "https://open.bigmodel.cn:444/api/coding/paas/v4/chat/completions"),
        ("custom", _MODEL, "https://open.bigmodel.cn/v1"),
        ("custom", _MODEL, "https://open.bigmodel.cn/api/coding/paas/v4"),
        ("custom", "glm-5.3", _ENDPOINT),
        ("custom", "glm-5.3-flash-preview", _ENDPOINT),
        ("custom", "glm-4.7-flash", _ENDPOINT),
        ("custom", "deepseek-chat", _ENDPOINT),
        ("openrouter", _MODEL, "https://openrouter.ai/api"),
        ("openrouter", _MODEL, _ENDPOINT),
        ("openai", "gpt-4o-mini", "https://api.openai.com/v1"),
    ],
)
def test_capability_isolation_overwrites_untrusted_runtime_claims(
    monkeypatch, provider, model, endpoint
):
    def forbidden_open(*args, **kwargs):
        raise AssertionError("unsupported controls must fail before transport")

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", forbidden_open)
    field = {
        "custom": "customBaseUrl",
        "openai": "openaiBaseUrl",
        "openrouter": "openrouterBaseUrl",
    }[provider]
    adapter = create_model_adapter(
        model,
        None,
        {
            "provider": provider,
            field: endpoint,
            "_supportsJsonObject": True,
            "_supportedReasoningEfforts": ("low", "high", "max"),
            "_resolvedOpenAIProvider": "custom",
        },
    )
    assert adapter.supports_json_object is False
    assert adapter.supported_reasoning_efforts == ()
    with pytest.raises(RuntimeError, match="json_object_unsupported"):
        adapter.next(_MESSAGES, json_object_override=True)
    with pytest.raises(RuntimeError, match="reasoning_effort_unsupported"):
        adapter.next(_MESSAGES, reasoning_effort_override="low")


@pytest.mark.parametrize(
    "overrides",
    [
        {"json_object_override": "true"},
        {"json_object_override": 1},
        {"json_object_override": None},
        {"reasoning_effort_override": "medium"},
        {"reasoning_effort_override": "none"},
        {"reasoning_effort_override": "LOW"},
        {"reasoning_effort_override": True},
        {"reasoning_effort_override": []},
        {"json_object_override": True, "response_schema_override": _SCHEMA},
    ],
)
def test_invalid_semantic_call_controls_fail_before_transport(monkeypatch, overrides):
    def forbidden_open(*args, **kwargs):
        raise AssertionError("invalid controls must fail before transport")

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", forbidden_open)
    with pytest.raises(ValueError):
        _adapter().next(_MESSAGES, **overrides)


def test_json_object_does_not_grant_native_schema_support(monkeypatch):
    def forbidden_open(*args, **kwargs):
        raise AssertionError("unsupported schema must fail before transport")

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", forbidden_open)
    with pytest.raises(RuntimeError, match="native_json_schema_unsupported"):
        _adapter().next(_MESSAGES, response_schema_override=_SCHEMA)


@pytest.mark.parametrize("control", [{"json_object_override": True}, {"reasoning_effort_override": "low"}])
def test_route_mutation_cannot_reuse_old_capabilities(monkeypatch, control):
    def forbidden_open(*args, **kwargs):
        raise AssertionError("changed route must not inherit old capabilities")

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", forbidden_open)
    adapter = _adapter()
    adapter.runtime["openaiBaseUrl"] = "https://proxy.invalid/v1"
    with pytest.raises(RuntimeError, match="unsupported"):
        adapter.next(_MESSAGES, **control)


def test_semantic_controls_preserve_response_byte_limit(monkeypatch):
    response = _Response(content="x" * 8192)
    monkeypatch.setattr(
        "minicode.openai_adapter.open_verified_url",
        lambda request, *, timeout: response,
    )
    with pytest.raises(RuntimeError, match="response.*limit"):
        _adapter().next(
            _MESSAGES,
            json_object_override=True,
            reasoning_effort_override="low",
            max_response_bytes_override=128,
        )
    assert response.closed is True


def test_semantic_controls_preserve_zero_retry_limit(monkeypatch):
    calls = []

    def failing_open(request, *, timeout):
        calls.append(json.loads(request.data))
        raise urllib.error.URLError("synthetic transport failure")

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", failing_open)
    with pytest.raises(urllib.error.URLError):
        _adapter().next(
            _MESSAGES,
            json_object_override=True,
            reasoning_effort_override="low",
            max_retries_override=0,
        )
    assert len(calls) == 1


@pytest.mark.parametrize("control", ["deadline", "cancellation"])
def test_semantic_controls_preserve_pre_transport_cancellation_and_deadline(
    monkeypatch, control
):
    def forbidden_open(*args, **kwargs):
        raise AssertionError("cancelled or expired calls must not reach transport")

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", forbidden_open)
    token = TurnCancellationToken("turn_" + "1" * 32)
    kwargs = {}
    if control == "cancellation":
        token.request()
        kwargs["cancellation_token"] = token
        error = TurnCancellationRequested
    else:
        kwargs["deadline_monotonic"] = time.monotonic() - 1
        error = ModelCallDeadlineExceeded
    with pytest.raises(error):
        _adapter().next(
            _MESSAGES,
            json_object_override=True,
            reasoning_effort_override="low",
            **kwargs,
        )


def test_semantic_controls_preserve_cancellation_during_body_read(monkeypatch):
    token = TurnCancellationToken("turn_" + "1" * 32)
    response = _Response(on_read=token.request)
    monkeypatch.setattr(
        "minicode.openai_adapter.open_verified_url",
        lambda request, *, timeout: response,
    )
    with pytest.raises(TurnCancellationRequested):
        _adapter().next(
            _MESSAGES,
            json_object_override=True,
            reasoning_effort_override="low",
            cancellation_token=token,
        )
    assert response.closed is True
