"""Behavioral checks for bounded provider-aware intent delivery."""

from __future__ import annotations

import json
import time

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.intent_parser import IntentContext, SemanticIntentRequest
from minicode.intent_semantic_classifier import (
    BudgetedSemanticIntentClassifier,
    IntentSemanticClassifierConfig,
)
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.types import AgentStep, ModelUsage, StepDiagnostics


class CapturingModel:
    catalog_model_key = "glm-5.3-flash"
    supports_json_object = True
    supported_reasoning_efforts = ("low", "high", "max")

    def __init__(self, *, stop_reason: str | None = "stop") -> None:
        self.kwargs: dict[str, object] = {}
        self.stop_reason = stop_reason

    def next(self, messages, **kwargs):
        self.kwargs = kwargs
        return AgentStep(
            type="assistant",
            content=json.dumps({
                "schemaVersion": 1,
                "operations": [{"intent": "search", "action": "read", "confidence": 0.95}],
            }),
            diagnostics=StepDiagnostics(stopReason=self.stop_reason),
            usage=ModelUsage(input_tokens=30, output_tokens=40, source="provider"),
        )


class Sink:
    observation_active = True

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, event_type, *, step=None, payload=None) -> None:
        self.events.append((event_type, dict(payload or {})))


def classify(model, *, settings=None, sink=None):
    config = IntentSemanticClassifierConfig.from_runtime({
        "intentSemanticClassifier": {"enabled": True, **(settings or {})},
    })
    classifier = BudgetedSemanticIntentClassifier(model, config=config)
    budget = AgentTurnBudget(max_model_calls=2, max_total_tokens=5000)
    sink = sink or Sink()
    result = classifier.infer(
        SemanticIntentRequest("Locate the diagram from our design notes.", IntentContext(), ()),
        call_context={
            "agent_budget": budget,
            "event_sink": sink,
            "deadline_monotonic": time.monotonic() + 5,
        },
    )
    return result, classifier, budget, sink


def test_supported_delivery_requests_json_and_low_reasoning_with_existing_limits():
    model = CapturingModel()
    result, classifier, budget, _sink = classify(model)

    assert [(p.intent_type.value, p.action_type.value) for p in result] == [("search", "read")]
    assert classifier.last_status == "accepted"
    assert model.kwargs["json_object_override"] is True
    assert model.kwargs["reasoning_effort_override"] == "low"
    assert model.kwargs["max_output_tokens_override"] == 512
    assert model.kwargs["disable_tools"] is True
    assert model.kwargs["max_retries_override"] == 0
    assert model.kwargs["isolated_reasoning_state"] is True
    assert model.kwargs["deadline_monotonic"] > time.monotonic()
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().reserved_total_tokens == 0


def test_provider_without_capabilities_receives_no_provider_specific_arguments():
    model = CapturingModel()
    model.supports_json_object = False
    model.supported_reasoning_efforts = ()

    result, _classifier, _budget, _sink = classify(model)

    assert result
    assert "json_object_override" not in model.kwargs
    assert "reasoning_effort_override" not in model.kwargs


def test_operator_can_use_provider_defaults_and_disable_json_override():
    model = CapturingModel()
    result, _classifier, _budget, _sink = classify(
        model, settings={"jsonMode": False, "reasoningEffort": "default", "maxOutputTokens": 768}
    )
    assert result
    assert "json_object_override" not in model.kwargs
    assert "reasoning_effort_override" not in model.kwargs
    assert model.kwargs["max_output_tokens_override"] == 768


@pytest.mark.parametrize("effort", ["high", "max"])
def test_selected_supported_reasoning_effort_reaches_model(effort):
    model = CapturingModel()
    classify(model, settings={"reasoningEffort": effort})
    assert model.kwargs["reasoning_effort_override"] == effort


@pytest.mark.parametrize("reason", ["length", "max_tokens"])
def test_token_limited_complete_json_is_not_accepted_as_complete_intent(reason):
    model = CapturingModel(stop_reason=reason)
    result, classifier, budget, sink = classify(model)
    assert result == ()
    assert classifier.last_status == "invalid_response"
    completed = next(payload for event, payload in sink.events if event == "model.completed")
    assert completed["semanticStopReason"] == reason
    assert completed["semanticOutputLimitReached"] is True
    assert budget.snapshot().used_total_tokens == 70


def test_delivery_diagnostics_are_content_free_and_durable(tmp_path):
    journal = RunJournal(tmp_path, data_dir=tmp_path / "journal")
    secret_reason = "private provider diagnostic /sensitive/path"
    with observe_run(
        workspace=tmp_path, source="unknown", title="intent delivery",
        journal_factory=lambda workspace: journal,
    ) as observation:
        assert observation.observation_active
        result, _, _, _ = classify(CapturingModel(stop_reason=secret_reason), sink=observation)
        assert result
        run_id = observation.run_id
    page = journal.list_events(run_id, limit=50)
    completed = next(event.payload for event in page.items if event.type == "model.completed")
    assert completed["semanticStopReason"] == "other"
    assert completed["semanticJsonMode"] is True
    assert completed["semanticReasoningEffort"] == "low"
    assert completed["semanticContentChars"] > 0
    assert secret_reason not in json.dumps(completed)
    assert "Locate the diagram" not in json.dumps(completed)


@pytest.mark.parametrize("settings", [
    {"jsonMode": []}, {"reasoningEffort": "unlimited"}, {"reasoningEffort": 2},
])
def test_invalid_delivery_settings_fail_before_model_call(settings):
    model = CapturingModel()
    with pytest.raises((TypeError, ValueError)):
        classify(model, settings=settings)
    assert model.kwargs == {}
