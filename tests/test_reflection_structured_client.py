from __future__ import annotations

import json

from minicode.anthropic_adapter import AnthropicModelAdapter
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.reflection_llm import (
    ModelAdapterStructuredGenerationClient,
    ReflectionLLMConfig,
    create_structured_generation_client,
)
from minicode.types import AgentStep


class DummyResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status = 200

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class StreamingResponse:
    def __init__(self, events: list[dict]) -> None:
        self.status = 200
        self._lines = [
            f"data: {json.dumps(event)}\n".encode("utf-8") for event in events
        ] + [b"data: [DONE]\n"]

    def __iter__(self):
        return iter(self._lines)


def test_openai_adapter_omits_tools_for_tool_free_calls(monkeypatch) -> None:
    captured: dict = {}

    def fake_urlopen(request, timeout=60):
        captured.update(json.loads(request.data.decode("utf-8")))
        captured["timeout"] = timeout
        return DummyResponse(
            {
                "choices": [
                    {
                        "message": {"content": "{}"},
                        "finish_reason": "stop",
                    }
                ]
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = OpenAIModelAdapter(
        {
            "model": "gpt-test",
            "openaiBaseUrl": "http://127.0.0.1:8000",
            "openaiApiKey": "test",
            "modelTimeoutSeconds": 7.0,
            "modelMaxRetries": 0,
            "temperature": 0,
        },
        None,
    )

    adapter.next([{"role": "user", "content": "json"}])

    assert "tools" not in captured
    assert captured["temperature"] == 0
    assert captured["timeout"] == 7.0


def test_anthropic_adapter_omits_tools_for_tool_free_calls(monkeypatch) -> None:
    captured: dict = {}

    def fake_urlopen(request, timeout=60):
        captured.update(json.loads(request.data.decode("utf-8")))
        captured["timeout"] = timeout
        return DummyResponse(
            {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "{}"}],
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = AnthropicModelAdapter(
        {
            "model": "claude-test",
            "baseUrl": "http://127.0.0.1:8000",
            "authToken": "test",
            "modelTimeoutSeconds": 6.0,
            "modelMaxRetries": 0,
            "temperature": 0,
        },
        None,
    )

    adapter.next([{"role": "user", "content": "json"}])

    assert "tools" not in captured
    assert captured["temperature"] == 0
    assert captured["timeout"] == 6.0


def test_openai_compatible_provider_usage_reaches_structured_client(monkeypatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=60: DummyResponse(
            {
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 41,
                    "completion_tokens": 7,
                    "prompt_tokens_details": {"cached_tokens": 11},
                },
            }
        ),
    )
    adapter = OpenAIModelAdapter(
        {
            "model": "deepseek-chat",
            "openaiBaseUrl": "https://api.deepseek.com",
            "openaiApiKey": "test",
            "modelMaxRetries": 0,
        },
        None,
    )

    response = ModelAdapterStructuredGenerationClient(adapter).generate_json(
        [{"role": "user", "content": "json"}],
        timeout_seconds=5,
        max_output_tokens=100,
    )

    assert response.input_tokens == 41
    assert response.output_tokens == 7
    assert response.cache_read_tokens == 11
    assert response.usage_source == "provider"


def test_anthropic_provider_usage_and_cache_reach_structured_client(monkeypatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=60: DummyResponse(
            {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "{}"}],
                "usage": {
                    "input_tokens": 31,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 13,
                    "cache_creation_input_tokens": 3,
                },
            }
        ),
    )
    adapter = AnthropicModelAdapter(
        {
            "model": "claude-test",
            "baseUrl": "https://api.anthropic.com",
            "authToken": "test",
            "modelMaxRetries": 0,
        },
        None,
    )

    response = ModelAdapterStructuredGenerationClient(adapter).generate_json(
        [{"role": "user", "content": "json"}],
        timeout_seconds=5,
        max_output_tokens=100,
    )

    assert response.input_tokens == 31
    assert response.output_tokens == 5
    assert response.cache_read_tokens == 13
    assert response.cache_creation_tokens == 3
    assert response.usage_source == "provider"


def test_missing_provider_usage_is_explicitly_estimated(monkeypatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=60: DummyResponse(
            {
                "choices": [
                    {"message": {"content": '{"result":true}'}, "finish_reason": "stop"}
                ]
            }
        ),
    )
    adapter = OpenAIModelAdapter(
        {
            "model": "gpt-test",
            "openaiBaseUrl": "http://127.0.0.1:8000",
            "openaiApiKey": "test",
            "modelMaxRetries": 0,
        },
        None,
    )

    response = ModelAdapterStructuredGenerationClient(adapter).generate_json(
        [{"role": "user", "content": "json"}],
        timeout_seconds=5,
        max_output_tokens=100,
    )

    assert response.usage_source == "estimated"
    assert response.input_tokens is not None
    assert response.output_tokens is not None


def test_structured_reflection_cost_is_calculated_once_without_store_mutation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=60: DummyResponse(
            {
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
        ),
    )
    calls: list[dict] = []

    def fake_cost(**kwargs):
        calls.append(kwargs)
        return 0.001

    monkeypatch.setattr("minicode.cost_tracker.calculate_cost", fake_cost)
    adapter = OpenAIModelAdapter(
        {
            "model": "deepseek-chat",
            "openaiBaseUrl": "https://api.deepseek.com",
            "openaiApiKey": "test",
            "modelMaxRetries": 0,
        },
        None,
    )

    response = ModelAdapterStructuredGenerationClient(adapter).generate_json(
        [{"role": "user", "content": "json"}],
        timeout_seconds=5,
        max_output_tokens=100,
    )

    assert response.estimated_cost_usd == 0.001
    assert len(calls) == 1
    assert calls[0]["input_tokens"] == 10


def test_openai_streaming_usage_is_preserved(monkeypatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=60: StreamingResponse(
            [
                {"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]},
                {"choices": [], "usage": {"prompt_tokens": 17, "completion_tokens": 2}},
            ]
        ),
    )
    adapter = OpenAIModelAdapter(
        {
            "model": "gpt-test",
            "openaiBaseUrl": "http://127.0.0.1:8000",
            "openaiApiKey": "test",
            "modelMaxRetries": 0,
        },
        None,
    )

    step = adapter.next([{"role": "user", "content": "json"}], lambda chunk: None)

    assert step.usage is not None
    assert step.usage.source == "provider"
    assert (step.usage.input_tokens, step.usage.output_tokens) == (17, 2)


def test_anthropic_streaming_usage_is_preserved(monkeypatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=60: StreamingResponse(
            [
                {
                    "type": "message_start",
                    "message": {
                        "usage": {
                            "input_tokens": 19,
                            "cache_read_input_tokens": 4,
                            "cache_creation_input_tokens": 2,
                        }
                    },
                },
                {
                    "type": "content_block_start",
                    "content_block": {"type": "text"},
                },
                {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "ok"},
                },
                {"type": "message_delta", "delta": {}, "usage": {"output_tokens": 3}},
            ]
        ),
    )
    adapter = AnthropicModelAdapter(
        {
            "model": "claude-test",
            "baseUrl": "https://api.anthropic.com",
            "authToken": "test",
            "modelMaxRetries": 0,
        },
        None,
    )

    step = adapter.next([{"role": "user", "content": "json"}], lambda chunk: None)

    assert step.usage is not None
    assert step.usage.source == "provider"
    assert (step.usage.input_tokens, step.usage.output_tokens) == (19, 3)
    assert (step.usage.cache_read_tokens, step.usage.cache_creation_tokens) == (4, 2)


class StatefulAdapter:
    def __init__(self) -> None:
        self.runtime = {"model": "local-test"}
        self._thinking_blocks = [{"type": "thinking", "content": "preserve"}]
        self.calls = 0

    def next(self, messages):
        self.calls += 1
        assert self._thinking_blocks == []
        self._thinking_blocks.append({"type": "thinking", "content": "temporary"})
        return AgentStep(type="assistant", content='{"ok":true}')


def test_structured_client_restores_provider_thinking_state() -> None:
    adapter = StatefulAdapter()
    client = ModelAdapterStructuredGenerationClient(adapter)

    response = client.generate_json(
        [{"role": "user", "content": "json"}],
        timeout_seconds=5,
        max_output_tokens=100,
    )

    assert response.text == '{"ok":true}'
    assert adapter.calls == 1
    assert adapter._thinking_blocks == [
        {"type": "thinking", "content": "preserve"}
    ]


def test_factory_uses_registry_with_no_tools_and_reflection_limits(monkeypatch) -> None:
    captured: dict = {}

    def fake_create_model_adapter(model, tools, runtime):
        captured.update({"model": model, "tools": tools, "runtime": runtime})
        return StatefulAdapter()

    monkeypatch.setattr(
        "minicode.model_registry.create_model_adapter",
        fake_create_model_adapter,
    )
    config = ReflectionLLMConfig(
        mode="llm_shadow",
        model="local-reflection",
        timeout_seconds=9,
        max_output_tokens=777,
    )

    result = create_structured_generation_client(
        {
            "model": "main-agent",
            "customBaseUrl": "http://127.0.0.1:9000",
            "customApiKey": "test",
        },
        config,
    )

    assert result.client is not None
    assert result.unavailable_reason is None
    assert captured["tools"] is None
    assert captured["runtime"]["maxOutputTokens"] == 777
    assert captured["runtime"]["modelTimeoutSeconds"] == 9
    assert captured["runtime"]["modelMaxRetries"] == 0
    assert captured["runtime"]["temperature"] == 0


def test_factory_does_not_instantiate_remote_client_without_opt_in(monkeypatch) -> None:
    called = False

    def fake_create_model_adapter(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not instantiate")

    monkeypatch.setattr(
        "minicode.model_registry.create_model_adapter",
        fake_create_model_adapter,
    )
    result = create_structured_generation_client(
        {
            "model": "gpt-4o",
            "openaiBaseUrl": "https://api.openai.com",
            "openaiApiKey": "test",
        },
        ReflectionLLMConfig(mode="llm", model="gpt-4o"),
    )

    assert result.client is None
    assert result.unavailable_reason == "remote_model_not_allowed"
    assert called is False
