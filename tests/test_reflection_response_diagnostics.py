"""Provider response diagnostics survive reflection without reasoning text."""
from __future__ import annotations

from dataclasses import asdict
import json

import pytest

from minicode.anthropic_adapter import AnthropicModelAdapter
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.recovery_lesson import RecoveryLessonAgent
from minicode.reflection_evidence import TraceEvidenceExtractor
from minicode.reflection_llm import (
    LLMReflectionSynthesizer,
    ModelAdapterStructuredGenerationClient,
    ReflectionLLMConfig,
    StructuredGenerationResponse,
)
from scripts import evaluate_recovery_lesson_shadow as shadow


PRIVATE_REASONING = "private deliberation must never enter reflection artifacts"
TASK = "Preserve parser API"


class _HTTPResponse:
    status = 200

    def __init__(self, payload):
        self.data = json.dumps(payload).encode()

    def read(self, size=-1):
        if size < 0:
            size = len(self.data)
        result, self.data = self.data[:size], self.data[size:]
        return result

    def close(self):
        pass


def _openai_client(monkeypatch, *, text="", stop_reason=None, reasoning=None):
    payload = {
        "choices": [{"message": {"content": text, "reasoning_content": reasoning},
                     "finish_reason": stop_reason}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 1200},
    }
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: _HTTPResponse(payload))
    return ModelAdapterStructuredGenerationClient(OpenAIModelAdapter({
        "model": "diagnostic-test", "openaiBaseUrl": "https://example.invalid/v1",
        "openaiApiKey": "test", "modelMaxRetries": 0,
    }, None))


def _evidence():
    return TraceEvidenceExtractor().extract(TASK, [
        {"event_id": "constraint", "type": "user_constraint",
         "content": "Do not change the public parse API."},
        {"event_id": "done", "type": "task_result", "status": "success"},
    ])


def _valid_json():
    return json.dumps({"task_summary": TASK, "outcome": "success", "claims": [{
        "claim_type": "constraint", "semantic_key": "preserve_parse_api",
        "statement": "Project constraint: Do not change the public parse API.",
        "evidence_ids": ["constraint"], "epistemic_status": "confirmed",
        "applies_when": "When modifying parser interfaces.", "limitations": [],
        "verification_ids": [], "related_error_ids": [], "related_recovery_ids": [],
    }]})


def test_real_openai_adapter_preserves_only_safe_response_diagnostics(monkeypatch):
    client = _openai_client(monkeypatch, stop_reason="length", reasoning=PRIVATE_REASONING)
    response = client.generate_json([{"role": "user", "content": "json"}],
                                    timeout_seconds=2, max_output_tokens=1200)

    assert response.stop_reason == "length"
    assert response.reasoning_present is True
    assert response.output_tokens == 1200
    assert response.usage_source == "provider"
    assert PRIVATE_REASONING not in json.dumps(asdict(response))


@pytest.mark.parametrize("stop_reason,expected", [
    ("length", "output_limit_reached"), ("max_tokens", "output_limit_reached"),
    ("stop", "empty_response"), (None, "empty_response"),
])
def test_empty_provider_response_distinguishes_output_limit(monkeypatch, stop_reason, expected):
    client = _openai_client(monkeypatch, stop_reason=stop_reason, reasoning=PRIVATE_REASONING)
    attempt = LLMReflectionSynthesizer(client, ReflectionLLMConfig()).attempt(TASK, _evidence())

    assert attempt.success is False
    assert attempt.candidate is None
    assert attempt.failure_code == expected
    assert attempt.stop_reason == stop_reason
    assert attempt.reasoning_present is True
    assert attempt.model_called is True
    assert attempt.output_tokens == 1200
    assert PRIVATE_REASONING not in json.dumps(asdict(attempt))


@pytest.mark.parametrize("stop_reason", ["length", "max_tokens", "stop"])
def test_valid_json_is_not_admitted_when_provider_reports_output_limit(monkeypatch, stop_reason):
    client = _openai_client(monkeypatch, text=_valid_json(), stop_reason=stop_reason, reasoning="")
    attempt = LLMReflectionSynthesizer(client, ReflectionLLMConfig()).attempt(TASK, _evidence())

    if stop_reason == "stop":
        assert attempt.success is True
        assert attempt.candidate.claims[0].semantic_key == "preserve_parse_api"
    else:
        assert attempt.success is False
        assert attempt.failure_code == "output_limit_reached"
        assert attempt.candidate is None
    assert attempt.reasoning_present is False


@pytest.mark.parametrize("stop_reason", [None, PRIVATE_REASONING, {"private": PRIVATE_REASONING}])
def test_unknown_provider_diagnostics_remain_unknown_and_never_copy_raw_values(monkeypatch, stop_reason):
    client = _openai_client(monkeypatch, stop_reason=stop_reason)
    response = client.generate_json([{"role": "user", "content": "json"}],
                                    timeout_seconds=2, max_output_tokens=1200)

    assert response.stop_reason is None
    assert response.reasoning_present is None
    assert PRIVATE_REASONING not in json.dumps(asdict(response))


def test_real_anthropic_adapter_reports_thinking_presence_without_copying_blocks(monkeypatch):
    payload = {"stop_reason": "max_tokens", "content": [
        {"type": "thinking", "thinking": PRIVATE_REASONING, "signature": "private-signature"},
    ], "usage": {"input_tokens": 40, "output_tokens": 1200}}
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: _HTTPResponse(payload))
    adapter = AnthropicModelAdapter({
        "model": "claude-test", "baseUrl": "https://example.invalid",
        "authToken": "test", "modelMaxRetries": 0,
    }, None)
    response = ModelAdapterStructuredGenerationClient(adapter).generate_json(
        [{"role": "user", "content": "json"}], timeout_seconds=2, max_output_tokens=1200,
    )

    assert response.stop_reason == "max_tokens"
    assert response.reasoning_present is True
    assert PRIVATE_REASONING not in json.dumps(asdict(response))
    assert adapter._thinking_blocks == []


def test_episode_and_synthetic_capture_retain_diagnostics_but_not_reasoning(monkeypatch):
    manifest, cases = shadow.load_dataset(shadow.DEFAULT_DATASET)
    case = next(item for item in cases if item["expected_episode_count"] == 1)
    client = _openai_client(monkeypatch, stop_reason="length", reasoning=PRIVATE_REASONING)
    config = ReflectionLLMConfig(mode="llm_shadow")
    evidence = TraceEvidenceExtractor().extract(case["task_description"], case["trace"])
    result = RecoveryLessonAgent(client, config).attempt_lessons(case["task_description"], evidence)
    outcome = result.to_dict()["outcomes"][0]

    assert outcome["stop_reason"] == "length"
    assert outcome["reasoning_present"] is True
    assert outcome["failure_code"] == "output_limit_reached"
    assert result.lessons == []
    assert PRIVATE_REASONING not in json.dumps(result.to_dict())

    report = shadow.evaluate_cases([case], manifest=manifest, config=config,
                                   execute=True, client=client, max_calls=1)
    captured = report["cases"][0]["episodes"][0]["outcome"]
    assert captured["stop_reason"] == "length"
    assert captured["reasoning_present"] is True
    assert captured["physical_call"]["stop_reason"] == "length"
    assert captured["physical_call"]["reasoning_present"] is True
    assert report["summary"]["output_limit_reached_count"] == 1
    assert report["summary"]["empty_response_count"] == 0
    assert report["summary"]["pipeline_completed"] is False
    assert PRIVATE_REASONING not in json.dumps(report)


def test_scripted_response_defaults_do_not_fabricate_provider_diagnostics():
    response = StructuredGenerationResponse(text="{}")
    assert response.stop_reason is None
    assert response.reasoning_present is None
