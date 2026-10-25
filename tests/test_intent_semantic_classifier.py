from __future__ import annotations

import json
import logging
import time
import urllib.error
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.anthropic_adapter import AnthropicModelAdapter
from minicode.intent_parser import (
    ActionType,
    IntentContext,
    IntentTurn,
    IntentType,
    parse_intent,
)
from minicode.intent_semantic_calibration import (
    INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION,
    CalibrationObservation,
    fit_confidence_calibration,
)
from minicode.intent_semantic_classifier import (
    BudgetedSemanticIntentClassifier,
    IntentSemanticClassifierConfig,
    build_semantic_intent_adapter,
)
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.turn_cancellation import (
    TurnCancellationRequested,
    TurnCancellationToken,
)
from minicode.types import AgentStep, ModelUsage


@dataclass
class _Event:
    event_type: str
    payload: dict[str, object]


class _Sink:
    observation_active = True

    def __init__(self) -> None:
        self.events: list[_Event] = []

    def emit(self, event_type: str, *, step=None, payload=None) -> None:
        self.events.append(_Event(event_type, dict(payload or {})))


class _RawProviderResponse:
    status = 200

    def __init__(self, body: bytes) -> None:
        self._body = body
        self._offset = 0
        self.closed = False

    def read1(self, amount: int = -1) -> bytes:
        if self._offset >= len(self._body):
            return b""
        end = len(self._body) if amount < 0 else self._offset + amount
        chunk = self._body[self._offset : end]
        self._offset += len(chunk)
        return chunk

    def read(self, _amount: int = -1) -> bytes:
        raise AssertionError("provider adapters must prefer bounded read1")

    def close(self) -> None:
        self.closed = True


class _Model:
    def __init__(self, result: AgentStep | BaseException) -> None:
        self.result = result
        self.calls = 0
        self.messages: list[dict[str, Any]] = []
        self.kwargs: dict[str, Any] = {}
        self.catalog_model_key = "openai/gpt-4o-mini"

    def next(
        self,
        messages,
        *,
        disable_tools=False,
        max_output_tokens_override=None,
        max_response_bytes_override=None,
        temperature_override=None,
        max_retries_override=None,
        isolated_reasoning_state=False,
        cancellation_token=None,
        deadline_monotonic=None,
    ):
        self.calls += 1
        self.messages = list(messages)
        self.kwargs = {
            "disable_tools": disable_tools,
            "max_output_tokens_override": max_output_tokens_override,
            "max_response_bytes_override": max_response_bytes_override,
            "temperature_override": temperature_override,
            "max_retries_override": max_retries_override,
            "isolated_reasoning_state": isolated_reasoning_state,
            "cancellation_token": cancellation_token,
            "deadline_monotonic": deadline_monotonic,
        }
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class _UnsafeModelWithoutCallControls:
    def __init__(self) -> None:
        self.calls = 0

    def next(self, messages):
        self.calls += 1
        return _semantic_step("refactor", "update", 0.99)


class _ControlRaceModel(_Model):
    def __init__(
        self,
        result: BaseException,
        *,
        cancellation_token: TurnCancellationToken | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        super().__init__(result)
        self._cancellation_token = cancellation_token
        self._delay_seconds = delay_seconds

    def next(self, messages, **kwargs):
        self.calls += 1
        if self._cancellation_token is not None:
            self._cancellation_token.request()
        if self._delay_seconds:
            time.sleep(self._delay_seconds)
        raise self.result


def _semantic_step(
    intent: str,
    action: str,
    confidence: float,
    *,
    extra_operation: dict[str, object] | None = None,
) -> AgentStep:
    operation: dict[str, object] = {
        "intent": intent,
        "action": action,
        "confidence": confidence,
    }
    if extra_operation:
        operation.update(extra_operation)
    return AgentStep(
        type="assistant",
        content=json.dumps(
            {"schemaVersion": 1, "operations": [operation]},
            allow_nan=True,
        ),
        usage=ModelUsage(
            input_tokens=28,
            output_tokens=12,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            source="provider",
        ),
    )


def _config(**overrides: object) -> IntentSemanticClassifierConfig:
    values: dict[str, object] = {
        "enabled": True,
        "timeout_seconds": 1.5,
        "min_confidence": 0.72,
        "max_input_chars": 4000,
        "max_output_bytes": 4096,
        "max_output_tokens": 256,
        "reserve_model_calls": 1,
        "calibration_path": "",
        "allow_unreviewed_calibration": False,
    }
    values.update(overrides)
    return IntentSemanticClassifierConfig(**values)


def _controls(
    *,
    budget: AgentTurnBudget | None = None,
    sink: _Sink | None = None,
    cancellation_token: TurnCancellationToken | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, object]:
    return {
        "agent_budget": budget or AgentTurnBudget(max_model_calls=3),
        "event_sink": sink or _Sink(),
        "cancellation_token": cancellation_token,
        "deadline_monotonic": deadline_monotonic,
    }


def test_success_is_bounded_metered_observed_and_privacy_safe() -> None:
    sentinel = "customer-secret-must-stay-private"
    model = _Model(_semantic_step("refactor", "update", 0.99))
    sink = _Sink()
    budget = AgentTurnBudget(max_model_calls=3, max_total_tokens=10_000)
    adapter = BudgetedSemanticIntentClassifier(model, config=_config())

    intent = parse_intent(
        f"Could you make the auth flow less brittle? {sentinel}",
        context=IntentContext(
            recent_turns=(
                IntentTurn(role="user", content="We were discussing auth."),
                IntentTurn(role="assistant", content="I inspected the flow."),
            )
        ),
        semantic_adapter=adapter,
        call_context=_controls(budget=budget, sink=sink),
    )

    assert model.calls == 1
    assert model.kwargs["disable_tools"] is True
    assert model.kwargs["max_output_tokens_override"] == 256
    assert model.kwargs["max_response_bytes_override"] > 4096
    assert model.kwargs["max_response_bytes_override"] <= 128 * 1024
    assert model.kwargs["temperature_override"] == 0.0
    assert model.kwargs["max_retries_override"] == 0
    assert model.kwargs["isolated_reasoning_state"] is True
    assert intent.intent_type is IntentType.REFACTOR
    assert intent.action_type is ActionType.UPDATE
    assert intent.diagnostics.semantic_status == "accepted"
    assert intent.primary_operation is not None
    assert intent.primary_operation.clause == intent.raw_input
    assert intent.primary_operation.confidence <= 0.85
    assert sentinel in str(model.messages)
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.completed",
        "model.costed",
    ]
    assert sentinel not in str(sink.events)
    assert "auth flow" not in str(sink.events)
    assert all(
        event.payload.get("purpose") == "intent_classification" for event in sink.events
    )
    assert sink.events[1].payload["semanticStatus"] == "accepted"
    assert sink.events[1].payload["calibrationProductionFit"] is False
    snapshot = budget.snapshot()
    assert snapshot.used_model_calls == 1
    assert snapshot.reserved_total_tokens == 0
    assert snapshot.used_total_tokens == 40


def test_system_prompt_is_deanchored_and_contract_is_versioned() -> None:
    model = _Model(_semantic_step("question", "read", 0.9))
    adapter = BudgetedSemanticIntentClassifier(model, config=_config())

    intent = parse_intent(
        "Could you clarify the current situation?",
        semantic_adapter=adapter,
        call_context=_controls(),
    )

    assert intent.intent_type is IntentType.QUESTION
    system_prompt = str(model.messages[0]["content"])
    assert '"intent":"refactor"' not in system_prompt
    assert '"action":"update"' not in system_prompt
    assert "each object contains exactly intent, action, and" in system_prompt
    assert "Do not copy a label merely because it appears first" in system_prompt
    assert INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION == (
        "intent-semantic-classifier-v3"
    )


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_small_content_cap_is_independent_from_bounded_provider_envelope(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    semantic_content = json.dumps(
        {
            "schemaVersion": 1,
            "operations": [
                {"intent": "refactor", "action": "update", "confidence": 0.99}
            ],
        }
    )
    assert len(semantic_content.encode()) < 256
    if provider == "openai":
        payload = {
            "id": "response-id",
            "model": "gpt-test",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": semantic_content},
                }
            ],
            "usage": {"prompt_tokens": 28, "completion_tokens": 12},
            "provider_metadata": "m" * 4096,
        }
        model = OpenAIModelAdapter(
            {
                "model": "gpt-test",
                "openaiBaseUrl": "https://openai.invalid/v1",
                "openaiApiKey": "test",
                "modelMaxRetries": 0,
            },
            None,
        )
    else:
        payload = {
            "id": "response-id",
            "model": "claude-test",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": semantic_content}],
            "usage": {"input_tokens": 28, "output_tokens": 12},
            "provider_metadata": "m" * 4096,
        }
        model = AnthropicModelAdapter(
            {
                "model": "claude-test",
                "baseUrl": "https://anthropic.invalid",
                "authToken": "test",
                "modelMaxRetries": 0,
            },
            None,
        )
    body = json.dumps(payload).encode()
    assert len(body) > 256
    response = _RawProviderResponse(body)
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )

    intent = parse_intent(
        "Could you make this flow less brittle?",
        semantic_adapter=BudgetedSemanticIntentClassifier(
            model,
            config=_config(max_output_bytes=256),
        ),
        call_context=_controls(),
    )

    assert intent.intent_type is IntentType.REFACTOR
    assert intent.diagnostics.semantic_status == "accepted"
    assert response.closed is True


def test_semantic_success_preserves_supplied_context_for_skill_routing() -> None:
    model = _Model(_semantic_step("refactor", "update", 0.99))

    intent = parse_intent(
        "Could you make this flow less brittle?",
        context=IntentContext(
            recent_turns=(
                IntentTurn(role="user", content="We are working in auth.py."),
            )
        ),
        semantic_adapter=BudgetedSemanticIntentClassifier(model, config=_config()),
        call_context=_controls(),
    )

    assert intent.context_used is True
    assert intent.diagnostics.context_turns_used == 1
    assert intent.entities["files"] == ["auth.py"]
    assert "auth.py" in intent.semantic_query


def test_rules_and_negation_keep_the_classifier_at_zero_calls() -> None:
    model = _Model(_semantic_step("refactor", "update", 0.99))
    sink = _Sink()
    budget = AgentTurnBudget(max_model_calls=3)
    adapter = BudgetedSemanticIntentClassifier(model, config=_config())
    call_context = _controls(budget=budget, sink=sink)

    direct = parse_intent(
        "Refactor the authentication module",
        semantic_adapter=adapter,
        call_context=call_context,
    )
    negated = parse_intent(
        "Do not make the auth flow less brittle",
        semantic_adapter=adapter,
        call_context=call_context,
    )

    assert direct.diagnostics.semantic_status == "skipped"
    assert negated.diagnostics.semantic_status == "skipped"
    assert model.calls == 0
    assert sink.events == []
    assert budget.snapshot().used_model_calls == 0


def test_model_can_explicitly_abstain_without_fabricating_an_operation() -> None:
    model = _Model(
        AgentStep(
            type="assistant",
            content='{"schemaVersion":1,"operations":[]}',
            usage=ModelUsage(input_tokens=10, output_tokens=5),
        )
    )
    sink = _Sink()

    intent = parse_intent(
        "Could you maybe deal with that somehow?",
        semantic_adapter=BudgetedSemanticIntentClassifier(model, config=_config()),
        call_context=_controls(sink=sink),
    )

    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.operations == ()
    assert intent.diagnostics.semantic_status == "abstained"
    assert sink.events[1].payload["semanticStatus"] == "abstained"


@pytest.mark.parametrize(
    "step",
    [
        AgentStep(type="assistant", content="not-json"),
        AgentStep(
            type="assistant",
            content='{"schemaVersion":1,"operations":[],"authority":"required"}',
        ),
        _semantic_step(
            "refactor", "update", 0.99, extra_operation={"authority": "explicit"}
        ),
        _semantic_step("refactor", "read", 0.99),
        _semantic_step("code", "delete", 0.99),
        _semantic_step("test", "execute", 0.99),
        _semantic_step("refactor", "update", float("nan")),
        AgentStep(
            type="assistant",
            content=json.dumps(
                {
                    "schemaVersion": 1,
                    "operations": [
                        {"intent": "refactor", "action": "update", "confidence": 0.99}
                    ]
                    * 5,
                }
            ),
        ),
        AgentStep(
            type="tool_calls",
            calls=[{"id": "x", "toolName": "bash", "input": {}}],
        ),
    ],
)
def test_untrusted_or_authority_shaped_outputs_fail_closed(step: AgentStep) -> None:
    model = _Model(step)
    sink = _Sink()
    adapter = BudgetedSemanticIntentClassifier(model, config=_config())

    intent = parse_intent(
        "Could you make this flow less brittle?",
        semantic_adapter=adapter,
        call_context=_controls(sink=sink),
    )

    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.operations == ()
    assert intent.diagnostics.semantic_status in {
        "invalid_response",
        "tool_call_rejected",
    }
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.completed",
        "model.costed",
    ]


def test_compound_response_is_all_or_nothing() -> None:
    step = AgentStep(
        type="assistant",
        content=json.dumps(
            {
                "schemaVersion": 1,
                "operations": [
                    {"intent": "review", "action": "analyze", "confidence": 0.99},
                    {"intent": "code", "action": "delete", "confidence": 0.99},
                ],
            }
        ),
    )
    adapter = BudgetedSemanticIntentClassifier(_Model(step), config=_config())

    intent = parse_intent(
        "Please improve this in the best way you see fit",
        semantic_adapter=adapter,
        call_context=_controls(),
    )

    assert intent.operations == ()
    assert intent.diagnostics.semantic_status == "invalid_response"


def test_low_calibrated_confidence_abstains() -> None:
    model = _Model(_semantic_step("refactor", "update", 0.51))
    adapter = BudgetedSemanticIntentClassifier(model, config=_config())

    intent = parse_intent(
        "Could you make this flow less brittle?",
        semantic_adapter=adapter,
        call_context=_controls(),
    )

    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.diagnostics.semantic_status == "low_confidence"


def test_compound_low_confidence_telemetry_is_order_independent() -> None:
    operations = [
        {"intent": "review", "action": "analyze", "confidence": 0.75},
        {"intent": "refactor", "action": "update", "confidence": 0.51},
    ]
    observed: list[tuple[str, float, float]] = []
    for ordered in (operations, list(reversed(operations))):
        model = _Model(
            AgentStep(
                type="assistant",
                content=json.dumps({"schemaVersion": 1, "operations": ordered}),
            )
        )
        sink = _Sink()
        intent = parse_intent(
            "Please improve this in the best way you see fit",
            semantic_adapter=BudgetedSemanticIntentClassifier(model, config=_config()),
            call_context=_controls(sink=sink),
        )
        completed = sink.events[1].payload
        observed.append(
            (
                intent.diagnostics.semantic_status,
                completed["rawConfidenceMinimum"],
                completed["calibratedConfidenceMinimum"],
            )
        )

    assert observed == [
        ("low_confidence", 0.51, 0.45),
        ("low_confidence", 0.51, 0.45),
    ]


def test_missing_controls_and_unsafe_model_interface_do_not_call_provider() -> None:
    controlled_model = _Model(_semantic_step("refactor", "update", 0.99))
    adapter = BudgetedSemanticIntentClassifier(controlled_model, config=_config())

    missing = parse_intent(
        "Could you make this flow less brittle?",
        semantic_adapter=adapter,
        call_context={"agent_budget": AgentTurnBudget(max_model_calls=3)},
    )
    unsafe_model = _UnsafeModelWithoutCallControls()
    unsafe = parse_intent(
        "Could you make this flow less brittle?",
        semantic_adapter=BudgetedSemanticIntentClassifier(
            unsafe_model, config=_config()
        ),
        call_context=_controls(),
    )

    assert missing.diagnostics.semantic_status == "control_unavailable"
    assert unsafe.diagnostics.semantic_status == "control_unavailable"
    assert controlled_model.calls == 0
    assert unsafe_model.calls == 0


def test_callable_but_inactive_observer_keeps_classifier_at_zero_calls() -> None:
    class _InactiveSink:
        observation_active = False

        def emit(self, _event_type, *, step=None, payload=None) -> None:
            del step, payload

    model = _Model(_semantic_step("refactor", "update", 0.99))

    intent = parse_intent(
        "Could you make this flow less brittle?",
        semantic_adapter=BudgetedSemanticIntentClassifier(model, config=_config()),
        call_context={
            "agent_budget": AgentTurnBudget(max_model_calls=3),
            "event_sink": _InactiveSink(),
        },
    )

    assert intent.diagnostics.semantic_status == "control_unavailable"
    assert model.calls == 0


def test_classifier_preserves_last_model_call_for_agent_execution() -> None:
    model = _Model(_semantic_step("refactor", "update", 0.99))
    budget = AgentTurnBudget(max_model_calls=1)
    adapter = BudgetedSemanticIntentClassifier(model, config=_config())

    intent = parse_intent(
        "Could you make this flow less brittle?",
        semantic_adapter=adapter,
        call_context=_controls(budget=budget),
    )

    assert intent.diagnostics.semantic_status == "budget_exhausted"
    assert model.calls == 0
    assert budget.snapshot().used_model_calls == 0


def test_preserved_call_is_admitted_for_the_agent_after_one_classification() -> None:
    model = _Model(_semantic_step("refactor", "update", 0.99))
    budget = AgentTurnBudget(max_model_calls=2)
    adapter = BudgetedSemanticIntentClassifier(model, config=_config())

    first = parse_intent(
        "Could you make this flow less brittle?",
        semantic_adapter=adapter,
        call_context=_controls(budget=budget),
    )
    second = parse_intent(
        "Could you make another flow less brittle?",
        semantic_adapter=adapter,
        call_context=_controls(budget=budget),
    )
    agent_reservation = budget.reserve_model_call(10)

    assert first.diagnostics.semantic_status == "accepted"
    assert second.diagnostics.semantic_status == "budget_exhausted"
    assert model.calls == 1
    assert budget.snapshot().used_model_calls == 2
    assert budget.fail_model_call(agent_reservation) is True


def test_local_timeout_falls_back_and_releases_token_reservation() -> None:
    model = _Model(ModelCallDeadlineExceeded("local timeout secret"))
    sink = _Sink()
    budget = AgentTurnBudget(max_model_calls=3, max_total_tokens=10_000)
    adapter = BudgetedSemanticIntentClassifier(model, config=_config())

    intent = parse_intent(
        "Could you make this flow less brittle?",
        semantic_adapter=adapter,
        call_context=_controls(budget=budget, sink=sink),
    )

    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.diagnostics.semantic_status == "timeout"
    assert budget.snapshot().reserved_total_tokens == 0
    assert budget.snapshot().used_input_tokens > 0
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.failed",
    ]
    assert sink.events[1].payload["failureKind"] == "timeout"
    assert set(sink.events[1].payload) <= {
        "operationId",
        "failureKind",
        "durationMs",
        "purpose",
    }
    assert "secret" not in str(sink.events)


def test_url_error_is_projected_as_content_free_network_failure() -> None:
    model = _Model(urllib.error.URLError("private endpoint and prompt secret"))
    sink = _Sink()
    budget = AgentTurnBudget(max_model_calls=3, max_total_tokens=10_000)

    intent = parse_intent(
        "Could you make this flow less brittle?",
        semantic_adapter=BudgetedSemanticIntentClassifier(model, config=_config()),
        call_context=_controls(budget=budget, sink=sink),
    )

    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.diagnostics.semantic_status == "network"
    assert budget.snapshot().reserved_total_tokens == 0
    assert budget.snapshot().used_input_tokens > 0
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.failed",
    ]
    assert sink.events[1].payload["failureKind"] == "network"
    assert set(sink.events[1].payload) <= {
        "operationId",
        "failureKind",
        "durationMs",
        "purpose",
    }
    assert "secret" not in str(sink.events)


def test_expired_parent_deadline_and_cancellation_remain_control_flow() -> None:
    deadline_model = _Model(_semantic_step("refactor", "update", 0.99))
    adapter = BudgetedSemanticIntentClassifier(deadline_model, config=_config())

    with pytest.raises(ModelCallDeadlineExceeded):
        parse_intent(
            "Could you make this flow less brittle?",
            semantic_adapter=adapter,
            call_context=_controls(deadline_monotonic=time.monotonic() - 1),
        )
    assert deadline_model.calls == 0

    token = TurnCancellationToken("turn_" + "a" * 32)
    token.request()
    cancelled_model = _Model(_semantic_step("refactor", "update", 0.99))
    with pytest.raises(TurnCancellationRequested):
        parse_intent(
            "Could you make this flow less brittle?",
            semantic_adapter=BudgetedSemanticIntentClassifier(
                cancelled_model, config=_config()
            ),
            call_context=_controls(cancellation_token=token),
        )
    assert cancelled_model.calls == 0


@pytest.mark.parametrize(
    "provider_error",
    [ConnectionError("private provider failure"), TimeoutError("private timeout")],
)
def test_provider_exception_does_not_mask_concurrent_cancellation(
    provider_error: Exception,
) -> None:
    token = TurnCancellationToken("turn_" + "b" * 32)
    sink = _Sink()
    budget = AgentTurnBudget(max_model_calls=3, max_total_tokens=10_000)
    model = _ControlRaceModel(
        provider_error,
        cancellation_token=token,
    )

    with pytest.raises(TurnCancellationRequested):
        parse_intent(
            "Could you make this flow less brittle?",
            semantic_adapter=BudgetedSemanticIntentClassifier(model, config=_config()),
            call_context=_controls(
                budget=budget,
                sink=sink,
                cancellation_token=token,
            ),
        )

    assert budget.snapshot().reserved_total_tokens == 0
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.failed",
    ]
    assert sink.events[-1].payload["failureKind"] == "interrupted"


@pytest.mark.parametrize(
    "provider_error",
    [ConnectionError("private provider failure"), TimeoutError("private timeout")],
)
def test_provider_exception_does_not_mask_elapsed_parent_deadline(
    provider_error: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import minicode.intent_semantic_classifier as classifier_module
    import minicode.model_call_control as control_module

    # Preparation time must not consume the deadline before this test reaches
    # the provider. Advance only after the real call boundary has been entered;
    # leave the shared stdlib clock (and other tests) untouched.
    clock = SimpleNamespace(now=1000.0)
    local_time = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr(classifier_module, "time", local_time)
    monkeypatch.setattr(control_module, "time", local_time)
    parent_deadline = clock.now + 0.005
    sink = _Sink()
    budget = AgentTurnBudget(max_model_calls=3, max_total_tokens=10_000)
    provider_entries: list[dict[str, object]] = []

    class DeadlineRaceModel(_Model):
        def next(self, messages, **kwargs):
            provider_entries.append({
                "now": clock.now,
                "deadline": kwargs.get("deadline_monotonic"),
                "events": [event.event_type for event in sink.events],
                "reserved_tokens": budget.snapshot().reserved_total_tokens,
            })
            clock.now += 0.02
            return super().next(messages, **kwargs)

    model = DeadlineRaceModel(provider_error)
    with pytest.raises(ModelCallDeadlineExceeded) as raised:
        parse_intent(
            "Could you make this flow less brittle?",
            semantic_adapter=BudgetedSemanticIntentClassifier(model, config=_config()),
            call_context=_controls(
                budget=budget,
                sink=sink,
                deadline_monotonic=parent_deadline,
            ),
        )

    # Assertions stay outside Model.next: the classifier treats ordinary
    # provider exceptions as advisory failures and could swallow an assertion.
    assert type(raised.value) is ModelCallDeadlineExceeded
    assert model.calls == 1
    assert len(provider_entries) == 1
    entry = provider_entries[0]
    assert entry["now"] == 1000.0 < parent_deadline < clock.now
    assert entry["deadline"] == parent_deadline
    assert entry["events"] == ["model.started"]
    assert entry["reserved_tokens"] > 0
    snapshot = budget.snapshot()
    assert snapshot.used_model_calls == 1
    assert snapshot.reserved_total_tokens == 0
    assert snapshot.used_input_tokens == entry["reserved_tokens"]
    assert snapshot.used_total_tokens == snapshot.used_input_tokens
    assert snapshot.used_output_tokens == 0
    assert snapshot.usage_uncertain is True
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.failed",
    ]
    assert sink.events[-1].payload["operationId"] == sink.events[0].payload["operationId"]
    assert sink.events[-1].payload["failureKind"] == "timeout"
    assert "private" not in str(sink.events)


def test_timeout_honors_parent_bounded_deadline_before_clock_expiry() -> None:
    sink = _Sink()
    model = _Model(TimeoutError("provider reported its bounded timeout"))

    with pytest.raises(ModelCallDeadlineExceeded):
        parse_intent(
            "Could you make this flow less brittle?",
            semantic_adapter=BudgetedSemanticIntentClassifier(
                model, config=_config(timeout_seconds=1.5)
            ),
            call_context=_controls(
                sink=sink,
                deadline_monotonic=time.monotonic() + 0.5,
            ),
        )

    assert sink.events[-1].event_type == "model.failed"
    assert sink.events[-1].payload["failureKind"] == "timeout"


def test_lone_surrogate_provider_output_is_invalid_with_balanced_events() -> None:
    model = _Model(
        AgentStep(
            type="assistant",
            content=(
                '{"schemaVersion":1,"operations":['
                '{"intent":"\ud800","action":"update","confidence":0.99}]}'
            ),
            usage=ModelUsage(input_tokens=10, output_tokens=5),
        )
    )
    sink = _Sink()

    intent = parse_intent(
        "Could you make this flow less brittle?",
        semantic_adapter=BudgetedSemanticIntentClassifier(model, config=_config()),
        call_context=_controls(sink=sink),
    )

    assert intent.intent_type is IntentType.UNKNOWN
    assert intent.diagnostics.semantic_status == "invalid_response"
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.completed",
        "model.costed",
    ]
    assert sink.events[1].payload["semanticStatus"] == "invalid_response"


@pytest.mark.parametrize("control_exit", [KeyboardInterrupt(), SystemExit(2)])
def test_process_control_exit_settles_budget_and_emits_terminal_event(
    control_exit: BaseException,
) -> None:
    sink = _Sink()
    budget = AgentTurnBudget(max_model_calls=3, max_total_tokens=10_000)

    with pytest.raises(type(control_exit)):
        parse_intent(
            "Could you make this flow less brittle?",
            semantic_adapter=BudgetedSemanticIntentClassifier(
                _Model(control_exit), config=_config()
            ),
            call_context=_controls(budget=budget, sink=sink),
        )

    assert budget.snapshot().reserved_total_tokens == 0
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.failed",
    ]
    assert sink.events[-1].payload["failureKind"] == "interrupted"


def test_builder_is_opt_in_and_rejects_invalid_configuration() -> None:
    model = _Model(_semantic_step("refactor", "update", 0.99))

    assert build_semantic_intent_adapter(model, runtime={}) is None
    adapter = build_semantic_intent_adapter(
        model,
        runtime={"intentSemanticClassifier": {"enabled": True}},
    )
    assert isinstance(adapter, BudgetedSemanticIntentClassifier)

    with pytest.raises(ValueError):
        IntentSemanticClassifierConfig.from_runtime(
            {
                "intentSemanticClassifier": {
                    "enabled": True,
                    "timeoutSeconds": float("nan"),
                }
            }
        )
    assert (
        IntentSemanticClassifierConfig.from_runtime(
            {
                "intentSemanticClassifier": {
                    "allowUnreviewedCalibration": "true",
                }
            }
        ).allow_unreviewed_calibration
        is True
    )


def _write_external_calibration(
    path,
    *,
    model_key: str = "openai/gpt-4o-mini",
    contract_version: str = INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION,
) -> None:
    fit_confidence_calibration(
        [
            CalibrationObservation(0.2, False),
            CalibrationObservation(0.9, True),
        ],
        version="external-test-v1",
        model_key=model_key,
        classifier_contract_version=contract_version,
        bin_count=2,
    ).write(path)


def test_external_calibration_is_default_denied_and_explicitly_opted_in(
    tmp_path,
) -> None:
    artifact = tmp_path / "calibration.json"
    _write_external_calibration(artifact)
    model = _Model(_semantic_step("refactor", "update", 0.99))

    denied = build_semantic_intent_adapter(
        model,
        runtime={
            "intentSemanticClassifier": {
                "enabled": True,
                "calibrationPath": str(artifact),
            }
        },
    )
    allowed = build_semantic_intent_adapter(
        model,
        runtime={
            "intentSemanticClassifier": {
                "enabled": True,
                "calibrationPath": str(artifact),
                "allowUnreviewedCalibration": True,
            }
        },
    )

    assert denied is None
    assert isinstance(allowed, BudgetedSemanticIntentClassifier)


def test_external_calibration_uses_adapter_runtime_model_when_catalog_key_absent(
    tmp_path,
) -> None:
    artifact = tmp_path / "calibration.json"
    _write_external_calibration(artifact, model_key="runtime/model-v1")
    model = _Model(_semantic_step("refactor", "update", 0.99))
    del model.catalog_model_key
    model.runtime = {"model": "runtime/model-v1"}

    matched = build_semantic_intent_adapter(
        model,
        runtime={
            "intentSemanticClassifier": {
                "enabled": True,
                "calibrationPath": str(artifact),
                "allowUnreviewedCalibration": True,
            }
        },
    )
    del model.runtime
    missing_identity = build_semantic_intent_adapter(
        model,
        runtime={
            "intentSemanticClassifier": {
                "enabled": True,
                "calibrationPath": str(artifact),
                "allowUnreviewedCalibration": True,
            }
        },
    )

    assert isinstance(matched, BudgetedSemanticIntentClassifier)
    assert missing_identity is None


@pytest.mark.parametrize(
    ("artifact_model_key", "contract_version"),
    [
        ("anthropic/claude-test", INTENT_SEMANTIC_CLASSIFIER_CONTRACT_VERSION),
        ("openai/gpt-4o-mini", "intent-semantic-classifier-v1"),
    ],
)
def test_builder_rejects_external_calibration_for_other_model_or_contract(
    tmp_path,
    artifact_model_key: str,
    contract_version: str,
) -> None:
    artifact = tmp_path / "calibration.json"
    _write_external_calibration(
        artifact,
        model_key=artifact_model_key,
        contract_version=contract_version,
    )

    adapter = build_semantic_intent_adapter(
        _Model(_semantic_step("refactor", "update", 0.99)),
        runtime={
            "intentSemanticClassifier": {
                "enabled": True,
                "calibrationPath": str(artifact),
                "allowUnreviewedCalibration": True,
            }
        },
    )

    assert adapter is None


def test_invalid_external_calibration_disables_fallback_without_path_leak(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    artifact = tmp_path / "customer-secret-calibration.json"
    artifact.write_text('{"schemaVersion": 1, "prompt": "secret"}', encoding="utf-8")
    caplog.set_level(
        logging.WARNING,
        logger="minicode.intent_semantic_classifier",
    )

    adapter = build_semantic_intent_adapter(
        _Model(_semantic_step("refactor", "update", 0.99)),
        runtime={
            "intentSemanticClassifier": {
                "enabled": True,
                "calibrationPath": str(artifact),
            }
        },
    )

    assert adapter is None
    assert "calibration is unavailable" in caplog.text
    assert "customer-secret" not in caplog.text
    assert '"prompt"' not in caplog.text
