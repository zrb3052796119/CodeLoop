"""Provider controls must reach the real adapter without changing Agent defaults."""
from __future__ import annotations

import json
import urllib.error

import pytest

from minicode.model_registry import create_model_adapter
from minicode.reflection_llm import ModelAdapterStructuredGenerationClient
from minicode.types import AgentStep


@pytest.fixture(autouse=True)
def real_provider_factory(monkeypatch):
    monkeypatch.delenv("MINI_CODE_MODEL_MODE", raising=False)


class Response:
    status = 200

    def __init__(self):
        self.body = json.dumps({
            "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 2},
        }).encode()
        self.offset = 0

    def read1(self, amount):
        chunk = self.body[self.offset:self.offset + amount]
        self.offset += len(chunk)
        return chunk

    def close(self):
        pass


def adapter(*, model="glm-5.3-flash", url="https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"):
    return create_model_adapter(model, None, {
        "provider": "custom", "customBaseUrl": url, "customApiKey": "synthetic",
        "maxOutputTokens": 9000, "modelMaxRetries": 4, "modelTimeoutSeconds": 90,
    })


def test_reflection_uses_supported_low_reasoning_json_controls_only_for_its_call(monkeypatch):
    bodies = []
    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", lambda req, **kw: (bodies.append(json.loads(req.data)), Response())[1])
    model = adapter()
    runtime_before = dict(model.runtime)
    messages = [{"role": "user", "content": "Return JSON."}]
    model.next(messages)
    client = ModelAdapterStructuredGenerationClient(model)
    client.generate_json(messages, timeout_seconds=15, max_output_tokens=1200)
    model.next(messages)

    assert bodies[0] == bodies[2]
    assert "reasoning_effort" not in bodies[0]
    assert "response_format" not in bodies[0]
    assert bodies[1]["reasoning_effort"] == "low"
    assert bodies[1]["response_format"] == {"type": "json_object"}
    assert bodies[1]["max_tokens"] == 1200
    assert "enable_thinking" not in bodies[1]
    assert "tools" not in bodies[1]
    assert model.runtime == runtime_before


@pytest.mark.parametrize("model,url", [
    ("glm-5.3-flash", "https://example.invalid/v1"),
    ("glm-4", "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"),
])
def test_compatible_syntax_does_not_enable_unadvertised_controls(monkeypatch, model, url):
    bodies = []
    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", lambda req, **kw: (bodies.append(json.loads(req.data)), Response())[1])
    client = ModelAdapterStructuredGenerationClient(adapter(model=model, url=url))
    client.generate_json([], timeout_seconds=5, max_output_tokens=500)
    assert "reasoning_effort" not in bodies[0]
    assert "response_format" not in bodies[0]
    assert bodies[0]["max_tokens"] == 500


def test_generation_call_does_not_inherit_parent_retry_multiplier(monkeypatch):
    calls = []

    def unavailable(req, **kwargs):
        calls.append(req)
        raise urllib.error.URLError("synthetic unreachable provider")

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", unavailable)
    with pytest.raises(urllib.error.URLError):
        ModelAdapterStructuredGenerationClient(adapter()).generate_json(
            [], timeout_seconds=5, max_output_tokens=1200,
        )
    assert len(calls) == 1


def test_legacy_adapter_without_optional_controls_still_works():
    class LegacyAdapter:
        def next(self, messages):
            return AgentStep(type="assistant", content="{}")

    response = ModelAdapterStructuredGenerationClient(LegacyAdapter()).generate_json(
        [], timeout_seconds=5, max_output_tokens=1200,
    )
    assert response.text == "{}"


def test_model_is_given_the_exact_host_metadata_contract_and_parser_stays_strict():
    from minicode.reflection_evidence import TaskEvidence, sanitize_evidence_text
    from minicode.reflection_llm import (
        LLMReflectionSynthesizer, ReflectionLLMConfig, StructuredGenerationResponse,
        get_reflection_output_schema,
    )

    task = "Investigate " + "authentication behavior " * 14
    evidence = TaskEvidence(outcome="success")
    requests = []

    class RewritingClient:
        def generate_json(self, messages, **kwargs):
            requests.append(messages)
            return StructuredGenerationResponse(text=json.dumps({
                "task_summary": "A plausible rewritten task title", "outcome": "success", "claims": [],
            }))

    attempt = LLMReflectionSynthesizer(RewritingClient(), ReflectionLLMConfig()).attempt(task, evidence)
    payload = json.loads(requests[0][1]["content"])
    schema = payload["required_output_schema"]
    assert schema["properties"]["task_summary"]["const"] == sanitize_evidence_text(task, 200)
    assert schema["properties"]["outcome"]["const"] == "success"
    assert "Copy task_summary and outcome verbatim" in requests[0][0]["content"]
    assert attempt.failure_code == "task_summary_mismatch"
    assert "const" not in get_reflection_output_schema("calibrated_compact")["properties"]["task_summary"]
