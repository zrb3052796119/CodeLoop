from __future__ import annotations

import json
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import _model_next
from minicode.agent_reflection import ReflectionEngine
from minicode.config import safe_runtime_summary
from minicode.context_compactor import LLMSummaryGenerator
from minicode.embeddings import OpenAICompatibleEmbeddingClient
from minicode.memory import MemoryEntry, MemoryScope
from minicode.memory_curator_agent import MemoryCuratorAgent
from minicode.memory_hybrid_runtime import HybridRuntimeProvider
from minicode.model_call_control import (
    ModelCallDeadlineExceeded,
    bounded_request_timeout,
)
from minicode.reflection_llm import (
    LLMReflectionSynthesizer,
    ReflectionLLMConfig,
    StructuredGenerationResponse,
)
from minicode.turn_cancellation import TurnCancellationToken
from minicode.types import AgentStep, ModelUsage
from scripts.check_release_reproducibility import classify_porcelain


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, event_type: str, *, step=None, payload=None) -> None:
        self.events.append((event_type, dict(payload or {})))


def test_default_turn_budget_has_finite_token_call_and_cost_limits() -> None:
    budget = AgentTurnBudget.from_runtime({})

    assert budget.max_total_tokens == 1_000_000
    assert budget.max_model_calls == 80
    assert str(budget.max_cost_usd) == "5.00"


def test_request_timeout_is_bounded_by_remaining_agent_deadline() -> None:
    deadline = time.monotonic() + 0.25

    timeout = bounded_request_timeout(
        120.0,
        deadline_monotonic=deadline,
        cancellation_token=None,
    )

    assert 0 < timeout <= 0.25
    with pytest.raises(ModelCallDeadlineExceeded):
        bounded_request_timeout(
            120.0,
            deadline_monotonic=time.monotonic() - 1,
            cancellation_token=None,
        )


def test_model_next_forwards_control_only_to_supporting_adapter() -> None:
    token = TurnCancellationToken("turn_" + "a" * 32)
    deadline = time.monotonic() + 10
    captured: dict[str, object] = {}

    class ControlledModel:
        def next(
            self,
            _messages,
            *,
            on_stream_chunk=None,
            cancellation_token=None,
            deadline_monotonic=None,
        ):
            captured["token"] = cancellation_token
            captured["deadline"] = deadline_monotonic
            return AgentStep(type="assistant", content="done")

    result = _model_next(
        ControlledModel(),
        [{"role": "user", "content": "probe"}],
        on_stream_chunk=None,
        store=None,
        cancellation_token=token,
        deadline_monotonic=deadline,
    )

    assert result.content == "done"
    assert captured == {"token": token, "deadline": deadline}


def test_openai_adapter_bounds_socket_and_retry_backoff_by_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.error

    import minicode.openai_adapter as adapter_module
    from minicode.openai_adapter import OpenAIModelAdapter

    observed_timeouts: list[float] = []

    def fail_request(_request, *, timeout):
        observed_timeouts.append(timeout)
        raise urllib.error.URLError("synthetic outage")

    monkeypatch.setattr(adapter_module, "open_verified_url", fail_request)
    monkeypatch.setattr(adapter_module, "calculate_backoff", lambda *_args, **_kwargs: 1.0)
    adapter = OpenAIModelAdapter(
        {
            "model": "deepseek-chat",
            "openaiApiKey": "synthetic",
            "openaiBaseUrl": "https://example.invalid/v1",
            "_isolatedOpenAIConfig": True,
            "modelMaxRetries": 4,
            "modelTimeoutSeconds": 120,
        },
        tools=None,
    )

    with pytest.raises(ModelCallDeadlineExceeded):
        adapter.next(
            [{"role": "user", "content": "probe"}],
            deadline_monotonic=time.monotonic() + 0.01,
        )

    assert len(observed_timeouts) == 1
    assert 0 < observed_timeouts[0] <= 0.01


def test_provider_adapter_checks_cancel_before_transmitting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import minicode.openai_adapter as adapter_module
    from minicode.openai_adapter import OpenAIModelAdapter
    from minicode.turn_cancellation import TurnCancellationRequested

    transmitted = False

    def should_not_transmit(_request, *, timeout):
        nonlocal transmitted
        transmitted = True
        raise AssertionError("request should not be transmitted")

    monkeypatch.setattr(adapter_module, "open_verified_url", should_not_transmit)
    token = TurnCancellationToken("turn_" + "b" * 32)
    token.request()
    adapter = OpenAIModelAdapter(
        {
            "model": "deepseek-chat",
            "openaiApiKey": "synthetic",
            "openaiBaseUrl": "https://example.invalid/v1",
            "_isolatedOpenAIConfig": True,
            "modelMaxRetries": 0,
        },
        tools=None,
    )

    with pytest.raises(TurnCancellationRequested):
        adapter.next(
            [{"role": "user", "content": "probe"}],
            cancellation_token=token,
        )

    assert transmitted is False


def test_compaction_model_call_is_budgeted_and_journaled() -> None:
    sink = _RecordingSink()
    budget = AgentTurnBudget(max_model_calls=4, max_total_tokens=10_000)

    class SummaryModel:
        model_id = "deepseek-chat"

        def next(self, _messages, **_kwargs):
            return AgentStep(
                type="assistant",
                content="bounded context summary " * 4,
                usage=ModelUsage(
                    input_tokens=20,
                    output_tokens=8,
                    source="provider",
                ),
            )

    summary = LLMSummaryGenerator(
        SummaryModel(),
        agent_budget=budget,
        event_sink=sink,
    ).summarize([{"role": "user", "content": "x" * 500}])

    assert summary
    assert [event_type for event_type, _ in sink.events] == [
        "model.started",
        "model.completed",
        "model.costed",
    ]
    operation_ids = {payload["operationId"] for _, payload in sink.events}
    assert len(operation_ids) == 1
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().used_total_tokens == 28
    assert Decimal(budget.snapshot().used_cost_usd) > 0


def test_reflection_model_call_inherits_controls_budget_and_observation() -> None:
    sink = _RecordingSink()
    budget = AgentTurnBudget(max_model_calls=4, max_total_tokens=10_000)
    token = TurnCancellationToken("turn_" + "c" * 32)
    deadline = time.monotonic() + 10
    captured: dict[str, object] = {}
    response_text = json.dumps(
        {
            "task_summary": "Preserve parser API",
            "outcome": "success",
            "claims": [
                {
                    "claim_type": "constraint",
                    "semantic_key": "preserve_parser_api",
                    "statement": "Project constraint: preserve the public parser API.",
                    "evidence_ids": ["event-1"],
                    "epistemic_status": "confirmed",
                    "applies_when": "When changing parser interfaces.",
                    "limitations": [],
                    "verification_ids": [],
                    "related_error_ids": [],
                    "related_recovery_ids": [],
                }
            ],
        }
    )

    class ReflectionClient:
        model_id = "deepseek-chat"

        def generate_json(
            self,
            _messages,
            *,
            timeout_seconds,
            max_output_tokens,
            cancellation_token=None,
            deadline_monotonic=None,
        ):
            captured.update(
                timeout=timeout_seconds,
                output=max_output_tokens,
                token=cancellation_token,
                deadline=deadline_monotonic,
            )
            return StructuredGenerationResponse(
                text=response_text,
                input_tokens=30,
                output_tokens=7,
                usage_source="provider",
            )

    config = ReflectionLLMConfig(
        mode="llm",
        model="deepseek-chat",
        selection_strategy="replace",
    )
    engine = ReflectionEngine(
        llm_config=config,
        llm_synthesizer=LLMReflectionSynthesizer(
            ReflectionClient(), config
        ),
    )

    result = engine.reflect(
        "Preserve parser API",
        [
            {
                "event_id": "event-1",
                "type": "user_constraint",
                "content": "Preserve the public parser API.",
            },
            {"event_id": "event-2", "type": "task_result", "status": "success"},
        ],
        agent_budget=budget,
        event_sink=sink,
        cancellation_token=token,
        deadline_monotonic=deadline,
    )

    assert result.synthesis_source == "llm_replace"
    assert captured["token"] is token
    assert captured["deadline"] == deadline
    assert [event for event, _ in sink.events] == [
        "model.started",
        "model.completed",
        "model.costed",
    ]
    assert {payload["purpose"] for _, payload in sink.events} == {
        "memory_reflection"
    }
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().used_total_tokens == 37


def test_curator_model_call_inherits_controls_budget_and_observation() -> None:
    sink = _RecordingSink()
    budget = AgentTurnBudget(max_model_calls=4, max_total_tokens=10_000)
    token = TurnCancellationToken("turn_" + "d" * 32)
    deadline = time.monotonic() + 10
    captured: dict[str, object] = {}

    class CuratorModel:
        model_id = "deepseek-chat"

        def next(
            self,
            _messages,
            *,
            cancellation_token=None,
            deadline_monotonic=None,
        ):
            captured["token"] = cancellation_token
            captured["deadline"] = deadline_monotonic
            return AgentStep(
                type="assistant",
                content="Shared architecture insight across all related entries.",
                usage=ModelUsage(
                    input_tokens=20,
                    output_tokens=6,
                    source="provider",
                ),
            )

    curator = MemoryCuratorAgent(model_adapter=CuratorModel())
    entries = [
        SimpleNamespace(
            id=f"entry-{index}",
            content="Use a stable repository boundary for shared services.",
            domains=["backend"],
        )
        for index in range(3)
    ]

    insight = curator._synthesize_insight(
        entries,
        agent_budget=budget,
        event_sink=sink,
        cancellation_token=token,
        deadline_monotonic=deadline,
    )

    assert insight
    assert captured == {"token": token, "deadline": deadline}
    assert [event for event, _ in sink.events] == [
        "model.started",
        "model.completed",
        "model.costed",
    ]
    assert {payload["purpose"] for _, payload in sink.events} == {
        "memory_curation"
    }
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().used_total_tokens == 26


def test_hybrid_verifier_uses_shared_budget_and_model_observation() -> None:
    class Encoder:
        def encode_documents(self, texts):
            return tuple((1.0, 0.0) for _ in texts)

        def encode_queries(self, texts):
            return tuple((1.0, 0.0) for _ in texts)

    class Verifier:
        model_id = "deepseek-chat"

        def next(self, messages, **_kwargs):
            request = json.loads(messages[1]["content"])
            ids = [pair["id"] for pair in request["pairs"]]
            if "admission auditor" in messages[0]["content"]:
                payload = {
                    "audits": [
                        {
                            "id": entry_id,
                            "admit": True,
                            "confidence": 0.99,
                            "reasonCode": "no_disqualifier",
                        }
                        for entry_id in ids
                    ]
                }
            else:
                payload = {
                    "decisions": [
                        {
                            "id": entry_id,
                            "decision": "relevant",
                            "confidence": 0.99,
                            "objectMatch": True,
                            "relationSupported": True,
                            "reasonCode": "constraint",
                        }
                        for entry_id in ids
                    ]
                }
            return AgentStep(
                type="assistant",
                content=json.dumps(payload),
                usage=ModelUsage(
                    input_tokens=12,
                    output_tokens=4,
                    source="provider",
                ),
            )

    entry = MemoryEntry(
        id="safe-entry",
        scope=MemoryScope.PROJECT,
        category="architecture",
        content="Durable recovery constraint for services/runtime/lease.py.",
        tags=["lease"],
        approval_status="approved",
        lifecycle_status="active",
        safety_status="safe",
    )
    provider = HybridRuntimeProvider(
        encoder=Encoder(),
        model_adapter=Verifier(),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )
    budget = AgentTurnBudget(max_model_calls=4, max_total_tokens=10_000)
    sink = _RecordingSink()

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="Fix services/runtime/lease.py recovery.",
            current_files=("services/runtime/lease.py",),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
        agent_budget=budget,
        event_sink=sink,
    )

    assert result is not None and result.signals[0].accepted is True
    assert budget.snapshot().used_model_calls == 2
    assert budget.snapshot().used_total_tokens == 32
    assert Decimal(budget.snapshot().used_cost_usd) > 0
    assert [event for event, _ in sink.events].count("model.completed") == 2


def test_remote_embedding_is_budgeted_and_journaled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "data": [{"embedding": [1.0, 0.0]}],
                    "usage": {"prompt_tokens": 7, "total_tokens": 7},
                }
            ).encode()

    observed_timeout = None

    def urlopen(_request, *, timeout):
        nonlocal observed_timeout
        observed_timeout = timeout
        return Response()

    monkeypatch.setattr("minicode.embeddings.urllib.request.urlopen", urlopen)
    budget = AgentTurnBudget(max_model_calls=2, max_total_tokens=100)
    sink = _RecordingSink()
    client = OpenAICompatibleEmbeddingClient(
        "synthetic",
        base_url="https://example.invalid/v1",
        model="text-embedding-v3",
        timeout=30,
    )

    vectors = client.embed(
        ["hello"],
        call_context={
            "agent_budget": budget,
            "event_sink": sink,
            "deadline_monotonic": time.monotonic() + 1,
            "purpose": "memory_hybrid_embedding_query",
        },
    )

    assert vectors == [[1.0, 0.0]]
    assert observed_timeout is not None and 0 < observed_timeout <= 1
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().used_total_tokens == 7
    assert Decimal(budget.snapshot().used_cost_usd) > 0
    assert [event for event, _ in sink.events] == [
        "model.started",
        "model.completed",
        "model.costed",
    ]


def test_safe_runtime_summary_never_projects_credentials() -> None:
    summary = safe_runtime_summary(
        {
            "model": "deepseek-chat",
            "baseUrl": "https://example.invalid/v1",
            "apiKey": "primary-secret",
            "subagentApiKey": "child-secret",
            "subagentBaseUrl": "https://child.example.invalid/v1",
            "subagentModels": {"default": "qwen3.6-flash"},
            "agentTurnBudget": {
                "maxTokens": None,
                "maxModelCalls": None,
                "maxCostUsd": None,
            },
        }
    )

    serialized = json.dumps(summary, sort_keys=True)
    assert "primary-secret" not in serialized
    assert "child-secret" not in serialized
    assert summary["credentials"] == {
        "primaryConfigured": True,
        "subagentConfigured": True,
    }
    assert summary["effectiveTurnBudget"] == {
        "maxTokens": 1_000_000,
        "maxModelCalls": 80,
        "maxCostUsd": "5.00",
    }


def test_release_status_classification_exposes_counts_not_paths() -> None:
    status = classify_porcelain(
        " M minicode/secret-name.py\n?? private/generated-token.txt\n"
    )

    assert status == {
        "changedTotal": 2,
        "trackedOrStaged": 1,
        "untracked": 1,
    }
    assert "secret-name" not in json.dumps(status)
