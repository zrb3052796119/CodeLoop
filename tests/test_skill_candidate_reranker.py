from __future__ import annotations

import json
import time
import urllib.error
from dataclasses import dataclass
from typing import Any

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.skill_candidate_reranker import (
    BudgetedRemoteSkillReranker,
    SkillCandidateRerankerConfig,
    SkillRerankCandidate,
    SkillRerankRequest,
    build_skill_candidate_reranker,
)
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
        del step
        self.events.append(_Event(event_type, dict(payload or {})))


class _Model:
    supports_native_json_schema = True
    catalog_model_key = "openai/gpt-4o-mini"

    def __init__(self, result: AgentStep | BaseException) -> None:
        self.result = result
        self.calls = 0
        self.messages: list[dict[str, Any]] = []
        self.kwargs: dict[str, Any] = {}

    def next(
        self,
        messages,
        *,
        cancellation_token=None,
        deadline_monotonic=None,
        disable_tools=False,
        isolated_reasoning_state=False,
        max_output_tokens_override=None,
        max_response_bytes_override=None,
        max_retries_override=None,
        response_schema_override=None,
        temperature_override=None,
    ):
        self.calls += 1
        self.messages = list(messages)
        self.kwargs = {
            "cancellation_token": cancellation_token,
            "deadline_monotonic": deadline_monotonic,
            "disable_tools": disable_tools,
            "isolated_reasoning_state": isolated_reasoning_state,
            "max_output_tokens_override": max_output_tokens_override,
            "max_response_bytes_override": max_response_bytes_override,
            "max_retries_override": max_retries_override,
            "response_schema_override": response_schema_override,
            "temperature_override": temperature_override,
        }
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class _UnsafeModel:
    supports_native_json_schema = True

    def __init__(self) -> None:
        self.calls = 0

    def next(self, messages):
        del messages
        self.calls += 1
        return _response()


def _response(
    *,
    decision: str = "select",
    ids: list[str] | None = None,
    confidence: float = 0.9,
    reason_codes: list[str] | None = None,
    extra: dict[str, object] | None = None,
) -> AgentStep:
    document: dict[str, object] = {
        "schemaVersion": 1,
        "decision": decision,
        "orderedCandidateIds": ids if ids is not None else ["candidate-a"],
        "confidence": confidence,
        "reasonCodes": reason_codes if reason_codes is not None else ["direct_match"],
    }
    if extra:
        document.update(extra)
    return AgentStep(
        type="assistant",
        content=json.dumps(document, allow_nan=True),
        usage=ModelUsage(
            input_tokens=31,
            output_tokens=9,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            source="provider",
        ),
    )


def _request(*, top_k: int = 2) -> SkillRerankRequest:
    return SkillRerankRequest(
        query="Review the router and explain the selected flow",
        intent_type="review",
        action_type="analyze",
        candidates=(
            SkillRerankCandidate(
                "candidate-a",
                "Reviews code quality",
                4.2,
                (0,),
            ),
            SkillRerankCandidate(
                "candidate-b",
                "Explains implementation flow",
                3.8,
                (1,),
            ),
        ),
        top_k=top_k,
    )


def _config(**overrides: object) -> SkillCandidateRerankerConfig:
    values: dict[str, object] = {
        "enabled": True,
        "timeout_seconds": 1.5,
        "min_confidence": 0.8,
        "max_candidates": 8,
        "max_input_chars": 6000,
        "max_output_bytes": 2048,
        "max_output_tokens": 128,
        "reserve_model_calls": 1,
    }
    values.update(overrides)
    return SkillCandidateRerankerConfig(**values)


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


def test_success_uses_native_schema_shared_controls_and_content_free_events() -> None:
    secret = "private-query-canary"
    request = SkillRerankRequest(
        query=f"Review the router {secret}",
        intent_type="review",
        action_type="analyze",
        candidates=_request().candidates,
        top_k=2,
    )
    model = _Model(_response(ids=["candidate-b", "candidate-a"]))
    sink = _Sink()
    budget = AgentTurnBudget(max_model_calls=3, max_total_tokens=10_000)

    decision = BudgetedRemoteSkillReranker(model, config=_config()).rerank(
        request,
        call_context=_controls(budget=budget, sink=sink),
    )

    assert decision.status == "accepted"
    assert decision.ordered_candidate_ids == ("candidate-b", "candidate-a")
    assert model.calls == 1
    assert model.kwargs["disable_tools"] is True
    assert model.kwargs["isolated_reasoning_state"] is True
    assert model.kwargs["max_output_tokens_override"] == 128
    assert 2048 < model.kwargs["max_response_bytes_override"] <= 128 * 1024
    assert model.kwargs["max_retries_override"] == 0
    assert model.kwargs["temperature_override"] == 0.0
    schema = model.kwargs["response_schema_override"]
    assert schema["strict"] is True
    candidate_schema = schema["schema"]["properties"]["orderedCandidateIds"]
    assert candidate_schema["items"]["enum"] == ["candidate-a", "candidate-b"]
    assert candidate_schema["maxItems"] == 2
    assert secret in str(model.messages)
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.completed",
        "model.costed",
    ]
    assert secret not in str(sink.events)
    assert "Reviews code quality" not in str(sink.events)
    assert {event.payload["purpose"] for event in sink.events} == {"skill-routing"}
    assert sink.events[1].payload["rerankerStatus"] == "accepted"
    assert sink.events[1].payload["nativeSchemaEnforced"] is True
    snapshot = budget.snapshot()
    assert snapshot.used_model_calls == 1
    assert snapshot.reserved_total_tokens == 0
    assert snapshot.used_total_tokens == 40


def test_missing_controls_or_native_schema_capability_never_calls_model() -> None:
    controlled = _Model(_response())
    missing = BudgetedRemoteSkillReranker(controlled, config=_config()).rerank(
        _request(),
        call_context={"agent_budget": AgentTurnBudget(max_model_calls=3)},
    )
    unsupported = _Model(_response())
    unsupported.supports_native_json_schema = False
    unsupported_decision = BudgetedRemoteSkillReranker(
        unsupported,
        config=_config(),
    ).rerank(_request(), call_context=_controls())

    assert missing.status == "control_unavailable"
    assert unsupported_decision.status == "unsupported_model"
    assert controlled.calls == 0
    assert unsupported.calls == 0


def test_inactive_observer_or_incomplete_adapter_controls_never_call_model() -> None:
    class _InactiveSink:
        observation_active = False

        def emit(self, event_type, *, step=None, payload=None) -> None:
            del event_type, step, payload

    model = _Model(_response())
    inactive = BudgetedRemoteSkillReranker(model, config=_config()).rerank(
        _request(),
        call_context={
            "agent_budget": AgentTurnBudget(max_model_calls=3),
            "event_sink": _InactiveSink(),
        },
    )
    unsafe = _UnsafeModel()
    unsafe_decision = BudgetedRemoteSkillReranker(
        unsafe,
        config=_config(),
    ).rerank(_request(), call_context=_controls())

    assert inactive.status == "control_unavailable"
    assert unsafe_decision.status == "unsupported_model"
    assert model.calls == 0
    assert unsafe.calls == 0


def test_reranker_preserves_the_last_agent_model_call() -> None:
    model = _Model(_response())
    budget = AgentTurnBudget(max_model_calls=1)

    decision = BudgetedRemoteSkillReranker(model, config=_config()).rerank(
        _request(),
        call_context=_controls(budget=budget),
    )

    assert decision.status == "budget_exhausted"
    assert model.calls == 0
    assert budget.snapshot().used_model_calls == 0


@pytest.mark.parametrize(
    ("step", "expected_status"),
    [
        (AgentStep(type="assistant", content="not-json"), "invalid_response"),
        (_response(extra={"authority": "required"}), "invalid_response"),
        (_response(ids=["candidate-unknown"]), "invalid_response"),
        (_response(ids=["candidate-a", "candidate-a"]), "invalid_response"),
        (_response(ids=["candidate-a", "candidate-b"], confidence=0.79), "low_confidence"),
        (_response(decision="abstain", ids=["candidate-a"]), "invalid_response"),
        (_response(decision="select", ids=[]), "invalid_response"),
        (_response(reason_codes=["invented_reason"]), "invalid_response"),
        (_response(confidence=float("nan")), "invalid_response"),
        (
            AgentStep(
                type="tool_calls",
                calls=[{"id": "x", "toolName": "run_command", "input": {}}],
            ),
            "tool_call_rejected",
        ),
    ],
)
def test_untrusted_responses_fail_closed(
    step: AgentStep,
    expected_status: str,
) -> None:
    decision = BudgetedRemoteSkillReranker(
        _Model(step),
        config=_config(),
    ).rerank(_request(), call_context=_controls())

    assert decision.status == expected_status
    assert decision.ordered_candidate_ids == ()


def test_duplicate_json_key_and_over_top_k_fail_closed() -> None:
    duplicate = AgentStep(
        type="assistant",
        content=(
            '{"schemaVersion":1,"decision":"select",'
            '"orderedCandidateIds":["candidate-a"],'
            '"orderedCandidateIds":["candidate-b"],"confidence":0.9,'
            '"reasonCodes":["direct_match"]}'
        ),
    )
    duplicate_decision = BudgetedRemoteSkillReranker(
        _Model(duplicate), config=_config()
    ).rerank(_request(), call_context=_controls())
    over_k_decision = BudgetedRemoteSkillReranker(
        _Model(_response(ids=["candidate-a", "candidate-b"])),
        config=_config(),
    ).rerank(_request(top_k=1), call_context=_controls())

    assert duplicate_decision.status == "invalid_response"
    assert over_k_decision.status == "invalid_response"


def test_response_byte_limit_is_checked_after_native_schema_generation() -> None:
    oversized = _response()
    oversized.content = oversized.content + (" " * 2048)

    decision = BudgetedRemoteSkillReranker(
        _Model(oversized),
        config=_config(max_output_bytes=256),
    ).rerank(_request(), call_context=_controls())

    assert decision.status == "invalid_response"
    assert decision.ordered_candidate_ids == ()


def test_explicit_abstention_is_distinct_from_low_confidence() -> None:
    abstained = BudgetedRemoteSkillReranker(
        _Model(
            _response(
                decision="abstain",
                ids=[],
                confidence=0.4,
                reason_codes=["insufficient_evidence"],
            )
        ),
        config=_config(),
    ).rerank(_request(), call_context=_controls())

    assert abstained.status == "abstained"
    assert abstained.confidence == 0.4
    assert abstained.ordered_candidate_ids == ()


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (urllib.error.URLError("private endpoint"), "network"),
        (RuntimeError("private provider output"), "provider_error"),
        (TimeoutError("slow provider"), "timeout"),
    ],
)
def test_provider_failures_return_content_free_decisions_and_settle_budget(
    error: BaseException,
    status: str,
) -> None:
    model = _Model(error)
    sink = _Sink()
    budget = AgentTurnBudget(max_model_calls=3, max_total_tokens=10_000)

    decision = BudgetedRemoteSkillReranker(model, config=_config()).rerank(
        _request(),
        call_context=_controls(budget=budget, sink=sink),
    )

    assert decision.status == status
    assert budget.snapshot().reserved_total_tokens == 0
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.failed",
    ]
    assert set(sink.events[-1].payload) <= {
        "operationId",
        "failureKind",
        "durationMs",
        "purpose",
    }
    assert "private" not in str(sink.events)


def test_parent_deadline_and_cancellation_remain_control_flow() -> None:
    expired_model = _Model(_response())
    with pytest.raises(ModelCallDeadlineExceeded):
        BudgetedRemoteSkillReranker(expired_model, config=_config()).rerank(
            _request(),
            call_context=_controls(deadline_monotonic=time.monotonic() - 1),
        )
    assert expired_model.calls == 0

    token = TurnCancellationToken("turn_" + "a" * 32)
    token.request()
    cancelled_model = _Model(_response())
    with pytest.raises(TurnCancellationRequested):
        BudgetedRemoteSkillReranker(cancelled_model, config=_config()).rerank(
            _request(),
            call_context=_controls(cancellation_token=token),
        )
    assert cancelled_model.calls == 0


def test_public_contracts_validate_candidate_authority_and_operation_bounds() -> None:
    with pytest.raises(ValueError, match="unique"):
        SkillRerankRequest(
            query="q",
            intent_type="review",
            action_type="analyze",
            candidates=(
                SkillRerankCandidate("same", "a", 1.0),
                SkillRerankCandidate("same", "b", 0.5),
            ),
            top_k=1,
        )
    with pytest.raises(ValueError, match="operation_indexes"):
        SkillRerankCandidate("candidate", "description", 1.0, (16,))


def test_config_and_builder_are_opt_in_and_native_schema_only() -> None:
    defaults = SkillCandidateRerankerConfig.from_runtime({})
    assert defaults == SkillCandidateRerankerConfig()
    model = _Model(_response())
    assert build_skill_candidate_reranker(model, runtime={}) is None

    enabled = build_skill_candidate_reranker(
        model,
        runtime={
            "skillCandidateReranker": {
                "enabled": True,
                "timeoutSeconds": 2.0,
                "minConfidence": 0.82,
                "maxCandidates": 6,
                "maxInputChars": 5000,
                "maxOutputBytes": 1024,
                "maxOutputTokens": 96,
                "reserveModelCalls": 2,
            }
        },
    )
    assert isinstance(enabled, BudgetedRemoteSkillReranker)

    model.supports_native_json_schema = False
    assert (
        build_skill_candidate_reranker(
            model,
            runtime={"skillCandidateReranker": {"enabled": True}},
        )
        is None
    )


def test_candidate_limit_and_input_bound_fail_before_provider_call() -> None:
    model = _Model(_response())
    candidates = tuple(
        SkillRerankCandidate(f"candidate-{index}", "d", float(index))
        for index in range(3)
    )
    limited = BudgetedRemoteSkillReranker(
        model,
        config=_config(max_candidates=2),
    ).rerank(
        SkillRerankRequest("q", "review", "analyze", candidates, 1),
        call_context=_controls(),
    )

    assert limited.status == "candidate_limit_exceeded"
    assert model.calls == 0
