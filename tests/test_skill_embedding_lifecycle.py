from __future__ import annotations

from pathlib import Path

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.capability_registry import CapabilityRegistry
from minicode.embeddings import OpenAICompatibleEmbeddingClient
from minicode.intent_parser import parse_intent
from minicode.skill_router import SkillRouter, build_skill_router
from minicode.skill_semantics import EmbeddingSemanticMatcher, EmbeddingUnavailable


class _RecordingClient:
    def __init__(self, *, fail_documents: bool = False) -> None:
        self._model = "test-model"
        self._endpoint = "https://embedding.invalid/v1/embeddings"
        self.fail_documents = fail_documents
        self.document_calls = 0
        self.document_count = 0
        self.contexts: list[object] = []

    def embed(self, texts, *, call_context=None):  # noqa: ANN001
        self.document_calls += 1
        self.document_count += len(texts)
        self.contexts.append(call_context)
        if self.fail_documents:
            raise EmbeddingUnavailable("document batch unavailable")
        return [[1.0, 0.0] for _ in texts]

    def embed_one(self, _text, *, call_context=None):  # noqa: ANN001
        self.contexts.append(call_context)
        return [1.0, 0.0]


def _skill(index: int = 0) -> dict[str, object]:
    return {
        "name": f"database-{index}",
        "qualified_name": f"data/database-{index}",
        "description": "database query performance tuning",
        "keywords": ["database", "query"],
        "source": "project",
        "content_digest": f"{index + 1:064x}",
    }


def test_invalid_optional_embedding_configuration_degrades_to_alias(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MINICODE_EMBEDDING_API_KEY", "synthetic-key")
    monkeypatch.setenv("MINICODE_EMBEDDING_BASE_URL", "http://non-loopback.invalid/v1")

    router = build_skill_router(tmp_path)
    result = router.route(
        [_skill()],
        parse_intent("优化数据库查询"),
        CapabilityRegistry(),
    )

    assert result.used_fallback is False
    assert [selected.name for selected in result.selected] == ["database-0"]


def test_document_batch_failure_activates_shared_router_cooldown(
    tmp_path: Path,
) -> None:
    first_client = _RecordingClient(fail_documents=True)
    second_client = _RecordingClient(fail_documents=True)
    cache_path = tmp_path / "skill-embeddings.json"
    intent = parse_intent("优化数据库查询")

    SkillRouter(
        embedding_matcher=EmbeddingSemanticMatcher(first_client, cache_path)
    ).route([_skill()], intent, CapabilityRegistry())
    SkillRouter(
        embedding_matcher=EmbeddingSemanticMatcher(second_client, cache_path)
    ).route([_skill()], intent, CapabilityRegistry())

    assert first_client.document_calls == 1
    assert second_client.document_calls == 0


def test_embedding_call_context_reaches_document_and_query_requests(
    tmp_path: Path,
) -> None:
    client = _RecordingClient()
    matcher = EmbeddingSemanticMatcher(client, tmp_path / "cache.json")
    call_context = {"event_sink": object(), "purpose": "skill-routing"}

    matcher.similarities(
        "database query",
        [("a" * 64, "database query tuning")],
        call_context=call_context,
    )

    assert client.contexts == [call_context, call_context]


def test_one_route_bounds_new_skill_vectors(tmp_path: Path) -> None:
    client = _RecordingClient()
    matcher = EmbeddingSemanticMatcher(client, tmp_path / "cache.json")
    skills = [(f"{index + 1:064x}", f"skill document {index}") for index in range(100)]

    scores = matcher.similarities("query", skills)

    assert len(scores) == 100
    assert client.document_count <= 24


def test_embedding_cache_does_not_follow_workspace_state_root_symlink(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    workspace.mkdir()
    external.mkdir()
    (workspace / ".mini-code").symlink_to(external, target_is_directory=True)
    client = _RecordingClient()
    matcher = EmbeddingSemanticMatcher(
        client,
        workspace / ".mini-code" / "skill-embeddings.json",
        cache_anchor=workspace,
    )

    matcher.similarities(
        "database query",
        [("a" * 64, "database query tuning")],
    )

    assert not (external / "skill-embeddings.json").exists()


def test_embedding_budget_rejection_degrades_with_balanced_events(
    monkeypatch,
) -> None:
    class _Sink:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, object]]] = []

        def emit(self, event_type, *, step=None, payload=None):  # noqa: ANN001
            del step
            self.events.append((event_type, dict(payload or {})))

    monkeypatch.setattr(
        "minicode.embeddings.urllib.request.urlopen",
        lambda *_args, **_kwargs: pytest.fail("budget rejection must precede I/O"),
    )
    budget = AgentTurnBudget(max_model_calls=1)
    budget.reserve_model_call(1)
    sink = _Sink()
    client = OpenAICompatibleEmbeddingClient("synthetic-key")

    with pytest.raises(EmbeddingUnavailable, match="budget"):
        client.embed(
            ["database query"],
            call_context={
                "agent_budget": budget,
                "event_sink": sink,
                "purpose": "skill-routing",
            },
        )

    assert [event_type for event_type, _payload in sink.events] == [
        "model.started",
        "model.failed",
    ]
    assert sink.events[-1][1]["failureKind"] == "budget_exhausted"


def test_skill_embedding_preserves_the_last_agent_model_call(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "minicode.embeddings.urllib.request.urlopen",
        lambda *_args, **_kwargs: pytest.fail(
            "preserved Agent call must reject embedding before I/O"
        ),
    )
    budget = AgentTurnBudget(max_model_calls=2)
    semantic_reservation = budget.reserve_model_call(20, preserve_model_calls=1)
    budget.record_model_call(
        input_tokens=20,
        reservation=semantic_reservation,
    )
    client = OpenAICompatibleEmbeddingClient("synthetic-key")

    with pytest.raises(EmbeddingUnavailable, match="budget"):
        client.embed(
            ["database query"],
            call_context={
                "agent_budget": budget,
                "preserve_model_calls": 1,
                "purpose": "skill-routing",
            },
        )

    agent_reservation = budget.reserve_model_call(10)
    assert budget.snapshot().used_model_calls == 2
    assert budget.fail_model_call(agent_reservation) is True
