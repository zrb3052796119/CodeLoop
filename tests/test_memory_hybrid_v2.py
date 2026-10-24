from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.embeddings import (
    OpenAICompatibleEmbeddingClient,
    OpenAICompatibleEmbeddingEncoder,
)
from minicode.memory import MemoryEntry, MemoryManager, MemoryScope
from minicode.memory_hybrid import (
    HYBRID_EVIDENCE_SCHEMA_VERSION,
    HYBRID_PROMOTION_INFERENCE_PROFILE,
    HYBRID_PROMOTION_VERIFIER_RUNTIME,
    HYBRID_PROTOCOL_VERSION,
    HYBRID_QUERY_GATE_VERSION,
    HYBRID_QWEN_ONE_SHOT_MARKER_NAME,
    HYBRID_RESOLVER_PRESSURE_ARTIFACT,
    HYBRID_RESOLVER_PRESSURE_HOLDOUT_SHA256,
    HYBRID_RESOLVER_PRESSURE_MANIFEST_SHA256,
    HybridActivation,
    HybridAdjudication,
    HybridCandidateSignal,
    assess_hybrid_activation,
    build_hybrid_decision_contract,
    build_hybrid_promotion_verifier_runtime,
    evidence_fingerprint,
)
from minicode.memory_hybrid_freeze import build_hybrid_runtime_freeze
from minicode.memory_hybrid_promotion_contract import (
    QWEN_PROMOTION_ACCEPTANCE_CHECK_KEYS,
    QWEN_PROMOTION_BACKGROUND_COUNT,
    QWEN_PROMOTION_CASE_COUNT,
    QWEN_PROMOTION_DATASET_ID,
    QWEN_PROMOTION_HARD_NEGATIVE_COUNT,
    QWEN_PROMOTION_HOLDOUT_SHA256,
    QWEN_PROMOTION_IMPLEMENTATION_FREEZE_SCHEMA_VERSION,
    QWEN_PROMOTION_MANIFEST_SHA256,
    QWEN_PROMOTION_POSITIVE_COUNT,
    QWEN_PROMOTION_REPORT_ARTIFACT,
    QWEN_VERIFIER_BASE_URL,
)
from minicode.memory_hybrid_runtime import (
    HybridProviderInitializationError,
    HybridRuntimeProvider,
    LocalE5Encoder,
    _build_resolver_batch,
    _canonical_request_fields,
    _model_adapter_cache_identity,
    _normalized,
    _parse_audits,
    _parse_decisions,
    _parse_resolutions,
    _proof_segments,
    _resolution_is_approved,
    clear_hybrid_provider_cache,
    create_hybrid_candidate_provider,
)
from minicode.memory_pipeline import MemoryPipeline
from minicode.memory_retrieval import (
    CanonicalMemoryRetriever,
    MemoryRetrievalRequest,
)
from minicode.types import AgentStep, StepDiagnostics


def _completed_classifier_step(content: str, *, usage=None) -> AgentStep:
    return AgentStep(
        type="assistant",
        content=content,
        diagnostics=StepDiagnostics(stopReason="stop", blockTypes=["text"]),
        usage=usage,
    )


def test_promotion_verifier_runtime_profile_is_exact_and_immutable() -> None:
    source = {
        "temperature": 0.9,
        "maxOutputTokens": 1200,
        "modelMaxRetries": 4,
        "modelTimeoutSeconds": 15,
        "transport": "preserved",
    }

    runtime = build_hybrid_promotion_verifier_runtime(source)

    assert runtime == {
        "temperature": 0,
        "maxOutputTokens": 6000,
        "modelMaxRetries": 1,
        "modelTimeoutSeconds": 90,
        "enableThinking": False,
        "subagentProvider": "openai-compatible",
        "subagentBaseUrl": QWEN_VERIFIER_BASE_URL,
        "transport": "preserved",
    }
    assert source["temperature"] == 0.9
    with pytest.raises(TypeError):
        HYBRID_PROMOTION_VERIFIER_RUNTIME["temperature"] = 1


def _manager(workspace: Path) -> MemoryManager:
    manager = MemoryManager(
        project_root=workspace,
        data_root=workspace / "user-memory",
    )
    entry = MemoryEntry(
        id="lexical-memory",
        scope=MemoryScope.PROJECT,
        category="testing",
        content="Invoice retry tests use a bounded failure schedule.",
        tags=["invoice", "retry", "tests"],
        domains=["testing"],
        approval_status="approved",
        lifecycle_status="active",
        safety_status="safe",
        created_at=1_700_000_000.0,
        updated_at=1_700_000_000.0,
        last_accessed=1_700_000_000.0,
    )
    manager.memories[entry.scope].entries.append(entry)
    manager.memories[entry.scope]._rebuild_indices()
    return manager


def test_missing_hybrid_evidence_falls_back_to_identical_lexical_result(
    tmp_path: Path,
) -> None:
    lexical = MemoryPipeline(_manager(tmp_path / "lexical"))
    lexical.initialize(workspace_path=str(tmp_path / "lexical"))
    with patch("minicode.memory_retrieval.time.time", return_value=1_800_000_000.0):
        expected = lexical.read(
            "run invoice retry tests",
            active_domains=["testing"],
        )

    hybrid = MemoryPipeline(_manager(tmp_path / "hybrid"))
    hybrid.initialize(
        workspace_path=str(tmp_path / "hybrid"),
        enable_vector=True,
        hybrid_model_path=tmp_path / "missing-model",
        hybrid_evidence_path=tmp_path / "missing-evidence.json",
    )
    with patch("minicode.memory_retrieval.time.time", return_value=1_800_000_000.0):
        actual = hybrid.read(
            "run invoice retry tests",
            active_domains=["testing"],
        )

    assert actual == expected
    assert hybrid.stats["hybrid_requested"] is True
    assert hybrid.stats["hybrid_active"] is False
    assert hybrid.stats["hybrid_inactive_reason"] == "evidence_missing"


def test_remote_memory_embedding_requires_separate_explicit_authorization(
    tmp_path: Path,
) -> None:
    pipeline = MemoryPipeline(_manager(tmp_path))

    pipeline.initialize(
        workspace_path=str(tmp_path),
        enable_vector=True,
        hybrid_embedding_provider="qwen",
        allow_remote_memory_embedding=False,
        hybrid_evidence_path=tmp_path / "missing-evidence.json",
    )
    assert pipeline.stats["hybrid_requested"] is True
    assert pipeline.stats["hybrid_active"] is False
    assert pipeline.stats["hybrid_inactive_reason"] == "remote_embedding_not_authorized"


def test_pipeline_reports_precise_evidence_verifier_mismatch_and_fallback(
    tmp_path: Path,
) -> None:
    batches: list[tuple[str, ...]] = []
    activation = _cache_activation(batches)
    pipeline = MemoryPipeline(_manager(tmp_path))

    with patch(
        "minicode.memory_hybrid.assess_hybrid_activation",
        return_value=activation,
    ):
        pipeline.initialize(
            model_adapter=SimpleNamespace(model_id="deepseek-v4-pro"),
            workspace_path=str(tmp_path),
            enable_vector=True,
            hybrid_embedding_provider="qwen",
            allow_remote_memory_embedding=True,
        )

    assert pipeline.stats["hybrid_active"] is False
    assert pipeline.stats["hybrid_fallback"] is True
    assert (
        pipeline.stats["hybrid_inactive_reason"]
        == "verifier_transport_identity_mismatch"
    )
    memories = pipeline.read("run invoice retry tests", active_domains=["testing"])
    runtime = pipeline.last_retrieval_result.diagnostics["hybrid_runtime"]
    assert memories == []
    assert pipeline.last_retrieval_result.selected_ids == ()
    assert pipeline.last_retrieval_result.rendered_ids == ()
    assert pipeline.last_retrieval_result.diagnostics["hybrid"]["fallback"] is True
    assert runtime == {
        "requested": True,
        "active": False,
        "fallback": True,
        "reason": "verifier_transport_identity_mismatch",
        "embedding_provider": "qwen",
        "verifier_binding": "configured_match",
        "provider_cache_reused": False,
        "adjudication_cache_hit": False,
    }


def test_active_hybrid_runtime_failure_suppresses_memory_instead_of_lexical_fallback(
    tmp_path: Path,
) -> None:
    class FailingProvider:
        def adjudicate(self, **_kwargs):
            raise RuntimeError("verifier unavailable")

    retriever = CanonicalMemoryRetriever(
        _manager(tmp_path),
        hybrid_provider=FailingProvider(),
    )

    with patch("minicode.memory_retrieval.time.time", return_value=1_800_000_000.0):
        result = retriever.retrieve(
            MemoryRetrievalRequest(query="run invoice retry tests")
        )

    assert result.rendered_ids == ()
    assert result.selected_ids == ()
    assert result.diagnostics["hybrid"]["fallback"] is True
    assert result.diagnostics["hybrid"]["failure_reason"] == "provider_fail_closed"


def test_passing_evidence_connects_hybrid_provider_to_canonical_read(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    semantic = MemoryEntry(
        id="semantic-memory",
        scope=MemoryScope.PROJECT,
        category="architecture",
        content="A durable operation receipt prevents a repeated debit.",
        tags=["operation-receipt", "debit"],
        approval_status="approved",
        lifecycle_status="active",
        safety_status="safe",
        created_at=1_700_000_000.0,
        updated_at=1_700_000_000.0,
        last_accessed=1_700_000_000.0,
    )
    manager.memories[semantic.scope].entries.append(semantic)
    manager.memories[semantic.scope]._rebuild_indices()

    model_path = tmp_path / "model"
    model_path.mkdir()
    model_identity = {
        "model_id": "test/local-e5",
        "model_revision": "frozen",
        "model_fingerprint": "a" * 64,
    }
    (model_path / "model_manifest.json").write_text(
        json.dumps(model_identity),
        encoding="utf-8",
    )
    evidence = {
        "schema_version": HYBRID_EVIDENCE_SCHEMA_VERSION,
        "protocol_version": HYBRID_PROTOCOL_VERSION,
        "synthetic_data": True,
        "embedding_provider": "qwen",
        "inference_profile": dict(HYBRID_PROMOTION_INFERENCE_PROFILE),
        "model": model_identity,
        "acceptance_gate": {"passed": True},
        "production_enablement_allowed": True,
        "dense_top_k": 20,
        "max_union_candidates": 32,
        "max_model_calls_per_task": 8,
        "query_gate_version": HYBRID_QUERY_GATE_VERSION,
        "runtime_implementation_freeze": build_hybrid_runtime_freeze(),
        **build_hybrid_decision_contract(
            "deepseek-chat",
            challenger_minimum_confidence=0.9,
        ),
    }
    evidence["report_fingerprint"] = evidence_fingerprint(evidence)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    class FakeProvider:
        def adjudicate(self, *, request, entries, lexical_accepted_ids):
            assert request.query == "avoid charging a retried purchase twice"
            assert semantic.id not in lexical_accepted_ids
            assert {entry.id for entry in entries} == {
                "lexical-memory",
                semantic.id,
            }
            return HybridAdjudication(
                signals=(
                    HybridCandidateSignal(
                        entry_id=semantic.id,
                        dense_score=0.91,
                        relevance_score=0.97,
                        accepted=True,
                        reason_codes=("semantic_equivalence",),
                    ),
                ),
                diagnostics={"verifier": "fake"},
            )

    pipeline = MemoryPipeline(
        manager,
        hybrid_provider_factory=lambda **_kwargs: FakeProvider(),
    )
    untrusted_activation = assess_hybrid_activation(
        requested=True,
        evidence_path=evidence_path,
        model_path=model_path,
    )
    assert untrusted_activation.active is False
    assert untrusted_activation.reason == "promotion_not_allowlisted"
    with patch(
        "minicode.memory_hybrid.HYBRID_ACCEPTED_PROMOTION_FINGERPRINT",
        evidence["report_fingerprint"],
    ):
        pipeline.initialize(
            workspace_path=str(tmp_path),
            model_adapter=object(),
            enable_vector=True,
            hybrid_model_path=model_path,
            hybrid_evidence_path=evidence_path,
        )

    with patch("minicode.memory_retrieval.time.time", return_value=1_800_000_000.0):
        result = pipeline.read("avoid charging a retried purchase twice")

    assert [item["id"] for item in result] == [semantic.id]
    assert result[0]["score"]["dense_score"] == 0.91
    assert result[0]["score"]["semantic_score"] == 0.97
    assert "hybrid_semantic_admission" in result[0]["reason_codes"]
    assert pipeline.stats["hybrid_active"] is True


def _memory(entry_id: str, *, safety_status: str = "safe") -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        scope=MemoryScope.PROJECT,
        category="architecture",
        content=f"Durable rule for {entry_id}.",
        tags=[entry_id],
        approval_status="approved",
        lifecycle_status="active",
        safety_status=safety_status,
    )


class _FakeEncoder:
    def encode_documents(self, texts):
        return tuple((1.0, 0.0) for _ in texts)

    def encode_queries(self, texts):
        return tuple((1.0, 0.0) for _ in texts)


class _FakeRemoteEmbeddingClient:
    model = "text-embedding-v3"
    endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class _CacheEmbeddingClient:
    model = "text-embedding-v3"
    endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"

    def __init__(self, batches: list[tuple[str, ...]], credential: str) -> None:
        self._batches = batches
        self._api_key = credential

    def embed(self, texts, **_kwargs):
        self._batches.append(tuple(texts))
        return [[1.0, 0.0] for _ in texts]


class _CountingVerifier:
    model_id = "deepseek-chat"

    def __init__(self) -> None:
        self.calls = 0
        self.seen_ids: list[list[str]] = []

    def next(self, messages):
        self.calls += 1
        request = json.loads(messages[1]["content"])
        ids = [row["id"] for row in request["pairs"]]
        self.seen_ids.append(ids)
        if "admission auditor" in messages[0]["content"]:
            return _completed_classifier_step(
                content=json.dumps(
                    {
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
                ),
                usage=None,
            )
        decisions = [
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
        return _completed_classifier_step(
            content=json.dumps({"decisions": decisions}),
            usage=None,
        )


class _CacheVerifier(_CountingVerifier):
    model_id = "qwen3.7-plus"

    def __init__(self, credential: str = "verifier-key") -> None:
        super().__init__()
        self.runtime = {
            "model": "qwen3.7-plus",
            "provider": "custom",
            "customBaseUrl": QWEN_VERIFIER_BASE_URL,
            "customApiKey": credential,
            "maxOutputTokens": HYBRID_PROMOTION_INFERENCE_PROFILE[
                "max_output_tokens"
            ],
            "temperature": HYBRID_PROMOTION_INFERENCE_PROFILE["temperature"],
            "modelMaxRetries": HYBRID_PROMOTION_INFERENCE_PROFILE["max_retries"],
            "modelTimeoutSeconds": HYBRID_PROMOTION_INFERENCE_PROFILE[
                "timeout_seconds"
            ],
            "enableThinking": HYBRID_PROMOTION_INFERENCE_PROFILE[
                "enable_thinking"
            ],
        }


def _remote_runtime_evidence(client) -> dict:
    remote_identity = OpenAICompatibleEmbeddingEncoder(client, provider="qwen").identity
    return {
        "embedding_provider": "qwen",
        "verifier_route": {
            "kind": "dedicated_exact_subagent",
            "provider": "openai-compatible",
            "base_url": QWEN_VERIFIER_BASE_URL,
            "model_id": "qwen3.7-plus",
        },
        "inference_profile": dict(HYBRID_PROMOTION_INFERENCE_PROFILE),
        "model": remote_identity,
        **build_hybrid_decision_contract("qwen3.7-plus"),
        "dense_top_k": 20,
        "max_union_candidates": 32,
        "max_model_calls_per_task": 8,
    }


def _cache_activation(batches: list[tuple[str, ...]]) -> HybridActivation:
    seed = _CacheEmbeddingClient(batches, "evidence-seed")
    evidence = _remote_runtime_evidence(seed)
    batches.clear()
    return HybridActivation(
        requested=True,
        active=True,
        reason="activated",
        evidence=evidence,
        embedding_provider="qwen",
    )


def _cached_provider(
    activation: HybridActivation,
    workspace: Path,
    batches: list[tuple[str, ...]],
    verifier: _CacheVerifier,
    *,
    embedding_credential: str = "embedding-key",
) -> HybridRuntimeProvider:
    provider = create_hybrid_candidate_provider(
        activation=activation,
        model_adapter=verifier,
        workspace_path=workspace,
        embedding_client_factory=lambda _workspace: _CacheEmbeddingClient(
            batches, embedding_credential
        ),
        strict=True,
    )
    assert isinstance(provider, HybridRuntimeProvider)
    return provider


@pytest.fixture
def shared_adjudication_store(monkeypatch):
    class SharedCache:
        available = True

        def __init__(self):
            self.values = {}
            self.reads = []
            self.ttls = []
            self.fail_next_read = False

        def budget(self):
            return nullcontext()

        def get_adjudication(self, key):
            self.reads.append(key)
            if self.fail_next_read:
                self.available = False
                return None
            return deepcopy(self.values.get(key))

        def put_adjudication(self, key, value, ttl_seconds=3600):
            self.values[key] = deepcopy(value)
            self.ttls.append(ttl_seconds)

    store = SharedCache()
    monkeypatch.setenv("MINI_CODE_MEMORY_REDIS_URL", "redis://unused-test-host/0")
    monkeypatch.setattr(
        "minicode.memory_hybrid_runtime.get_memory_hot_store", lambda _root: store
    )
    clear_hybrid_provider_cache()
    yield store
    clear_hybrid_provider_cache()


def _hot_cache_query(entry):
    return {
        "request": SimpleNamespace(
            query=f"apply {entry.id} durable rule",
            current_files=(),
            active_domains=(),
        ),
        "entries": (entry,),
        "lexical_accepted_ids": (entry.id,),
        "authority_revision": "frozen-authority-1",
    }


def test_redis_adjudication_shares_across_independent_provider_caches(
    tmp_path, monkeypatch, shared_adjudication_store
):
    batches = []
    activation = _cache_activation(batches)
    first_verifier = _CacheVerifier()
    first = _cached_provider(activation, tmp_path, batches, first_verifier)
    kwargs = _hot_cache_query(_memory("safe-entry"))
    original = first.adjudicate(**kwargs)
    assert original is not None and first_verifier.calls == 2

    # Model a different process: neither the LRU nor its process salt survives.
    clear_hybrid_provider_cache()
    monkeypatch.setattr("minicode.memory_hybrid_runtime._PROVIDER_CACHE_SALT", b"new")
    second_verifier = _CacheVerifier()
    second = _cached_provider(activation, tmp_path, batches, second_verifier)
    assert second._shared_state is not first._shared_state
    reused = second.adjudicate(**kwargs)
    assert reused is not None and reused.signals == original.signals
    assert reused.candidate_ids == original.candidate_ids
    assert reused.diagnostics["cache_hit"] is True
    assert reused.diagnostics["model_call_count"] == 0
    assert second_verifier.calls == 0
    assert len(set(shared_adjudication_store.reads)) == 1
    assert shared_adjudication_store.ttls == [3600]

    # Redis precedes the warm LRU, and an eviction is backfilled from that LRU.
    shared_adjudication_store.values.clear()
    assert second.adjudicate(**kwargs).diagnostics["cache_hit"] is True
    assert len(shared_adjudication_store.values) == 1
    assert second_verifier.calls == 0


def test_redis_adjudication_uses_frozen_revision_and_recomputes_after_write(
    tmp_path, shared_adjudication_store
):
    batches = []
    activation = _cache_activation(batches)
    verifier = _CacheVerifier()
    provider = _cached_provider(activation, tmp_path, batches, verifier)
    manager = MemoryManager(project_root=tmp_path, data_root=tmp_path / "user-memory")
    manager.add_entry(
        MemoryScope.PROJECT, "testing",
        "Invoice retry tests use a bounded failure schedule.",
        tags=["invoice", "retry", "tests"], domains=["testing"],
    )
    retriever = CanonicalMemoryRetriever(manager, hybrid_provider=provider)
    request = MemoryRetrievalRequest(query="run invoice retry tests")
    first = retriever.retrieve(request)
    original_calls = verifier.calls
    assert original_calls > 0
    second = retriever.retrieve(request)
    assert second.authority_revision == first.authority_revision
    assert verifier.calls == original_calls
    assert len(shared_adjudication_store.values) == 1

    manager.add_entry(
        MemoryScope.PROJECT, "architecture",
        "Database transactions require an explicit rollback boundary.",
        tags=["database", "rollback"],
    )
    third = retriever.retrieve(request)
    assert third.authority_revision != first.authority_revision
    assert verifier.calls > original_calls
    assert len(shared_adjudication_store.values) == 2


def test_redis_adjudication_identity_binds_protocol_query_order_and_config(
    tmp_path, monkeypatch, shared_adjudication_store
):
    batches = []
    activation = _cache_activation(batches)
    provider = _cached_provider(activation, tmp_path, batches, _CacheVerifier())
    kwargs = _hot_cache_query(_memory("safe-entry"))
    key = provider._revision_cache_key(
        kwargs["request"], ("safe-entry", "other-entry"), "authority"
    )
    reversed_key = provider._revision_cache_key(
        kwargs["request"], ("other-entry", "safe-entry"), "authority"
    )
    assert key != reversed_key
    provider._minimum_confidence += 0.01
    assert key != provider._revision_cache_key(
        kwargs["request"], ("safe-entry", "other-entry"), "authority"
    )
    provider._minimum_confidence -= 0.01
    monkeypatch.setattr("minicode.memory_hybrid_runtime.HYBRID_PROMPT_VERSION", "next")
    assert key != provider._revision_cache_key(
        kwargs["request"], ("safe-entry", "other-entry"), "authority"
    )


@pytest.mark.parametrize("changed", ["model", "credential", "route", "temperature", "embedding"])
def test_redis_provider_identity_binds_models_routes_and_credentials(
    tmp_path, shared_adjudication_store, changed
):
    from minicode.memory_hybrid_runtime import _provider_cache_key

    batches = []
    activation = _cache_activation(batches)
    verifier = _CacheVerifier()
    embedding = _CacheEmbeddingClient(batches, "embedding-key")
    kwargs = dict(
        activation=activation, model_adapter=verifier, workspace_path=tmp_path,
        embedding_client=embedding, persistent=True,
    )
    original = _provider_cache_key(**kwargs)
    if changed == "model":
        verifier.runtime["model"] = "different-model"
    elif changed == "credential":
        verifier.runtime["customApiKey"] = "rotated"
    elif changed == "route":
        verifier.runtime["customBaseUrl"] = "https://other.invalid/v1"
    elif changed == "temperature":
        verifier.runtime["temperature"] = 0.5
    else:
        embedding._api_key = "rotated"
    assert original != _provider_cache_key(**kwargs)


def test_redis_failure_preserves_local_cache_and_missing_revision_uses_content_key(
    tmp_path, shared_adjudication_store
):
    batches = []
    activation = _cache_activation(batches)
    verifier = _CacheVerifier()
    provider = _cached_provider(activation, tmp_path, batches, verifier)
    kwargs = _hot_cache_query(_memory("safe-entry"))
    original = provider.adjudicate(**kwargs)
    shared_adjudication_store.fail_next_read = True
    fallback = provider.adjudicate(**kwargs)
    assert fallback is not None and fallback.signals == original.signals
    assert verifier.calls == 2
    calls_after_break = len(shared_adjudication_store.reads)
    provider.adjudicate(**kwargs)
    assert len(shared_adjudication_store.reads) == calls_after_break

    del kwargs["authority_revision"]
    provider.adjudicate(**kwargs)
    assert verifier.calls == 4
    kwargs["entries"][0].content += " Preserve rollback behavior."
    provider.adjudicate(**kwargs)
    assert verifier.calls == 6
    assert len(shared_adjudication_store.reads) == calls_after_break


def test_redis_disabled_keeps_existing_content_cache_identity(tmp_path, monkeypatch):
    monkeypatch.delenv("MINI_CODE_MEMORY_REDIS_URL", raising=False)
    clear_hybrid_provider_cache()
    batches = []
    verifier = _CacheVerifier()
    provider = _cached_provider(_cache_activation(batches), tmp_path, batches, verifier)
    kwargs = _hot_cache_query(_memory("safe-entry"))
    assert provider._hot_store is None
    first = provider.adjudicate(**kwargs)
    kwargs["authority_revision"] = "a-different-revision-with-identical-content"
    second = provider.adjudicate(**kwargs)
    assert second.signals == first.signals
    assert second.diagnostics["cache_hit"] is True
    assert verifier.calls == 2
    clear_hybrid_provider_cache()


@pytest.mark.parametrize("corruption", ["accepted", "score", "unknown_id", "cohort", "duplicate"])
def test_malformed_redis_adjudication_is_a_cache_miss(
    tmp_path, shared_adjudication_store, corruption
):
    batches = []
    activation = _cache_activation(batches)
    first = _cached_provider(activation, tmp_path, batches, _CacheVerifier())
    kwargs = _hot_cache_query(_memory("safe-entry"))
    assert first.adjudicate(**kwargs) is not None
    value = next(iter(shared_adjudication_store.values.values()))
    signal = value["signals"][0]
    if corruption == "accepted":
        signal["accepted"] = "false"
    elif corruption == "score":
        signal["dense_score"] = float("nan")
    elif corruption == "unknown_id":
        signal["entry_id"] = "outside-authority"
    elif corruption == "cohort":
        value["candidate_ids"] = []
    else:
        value["signals"].append(deepcopy(signal))
    clear_hybrid_provider_cache()
    verifier = _CacheVerifier()
    second = _cached_provider(activation, tmp_path, batches, verifier)
    result = second.adjudicate(**kwargs)
    assert result is not None
    assert result.diagnostics["cache_hit"] is False
    assert verifier.calls == 2


def test_cross_turn_cache_reuses_canary_and_documents_but_not_turn_budget(
    tmp_path: Path,
) -> None:
    clear_hybrid_provider_cache()
    batches: list[tuple[str, ...]] = []
    activation = _cache_activation(batches)
    activation.evidence["max_model_calls_per_task"] = 2
    first_verifier = _CacheVerifier()
    first = _cached_provider(activation, tmp_path, batches, first_verifier)
    entry = _memory("safe-entry")

    first_result = first.adjudicate(
        request=SimpleNamespace(
            query="apply safe-entry durable rule",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
    )
    after_first = len(batches)
    second_verifier = _CacheVerifier()
    second = _cached_provider(activation, tmp_path, batches, second_verifier)

    assert first_result is not None
    assert first is not second
    assert first._shared_state is second._shared_state
    assert len(batches) == after_first  # no second canary

    second_result = second.adjudicate(
        request=SimpleNamespace(
            query="enforce safe-entry architecture constraint",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
    )

    assert second_result is not None
    assert first_verifier.calls == 2
    assert second_verifier.calls == 2  # fresh per-turn max_model_calls budget
    assert len(batches) == after_first + 1  # query only; no document re-embed
    assert second_result.diagnostics["provider_cache_reused"] is True


def test_cross_turn_cache_joins_identical_concurrent_query_without_model_rebinding(
    tmp_path: Path,
) -> None:
    clear_hybrid_provider_cache()
    batches: list[tuple[str, ...]] = []
    activation = _cache_activation(batches)
    first_verifier = _CacheVerifier("verifier-key")
    second_verifier = _CacheVerifier("verifier-key")
    first = _cached_provider(activation, tmp_path, batches, first_verifier)
    second = _cached_provider(activation, tmp_path, batches, second_verifier)
    entry = _memory("safe-entry")
    kwargs = {
        "request": SimpleNamespace(
            query="apply safe-entry durable rule",
            current_files=(),
            active_domains=(),
        ),
        "entries": (entry,),
        "lexical_accepted_ids": frozenset({entry.id}),
    }

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda provider: provider.adjudicate(**kwargs), (first, second))
        )

    assert all(result is not None for result in results)
    assert first._model is first_verifier
    assert second._model is second_verifier
    assert first_verifier.calls + second_verifier.calls == 2
    assert sum(bool(result.diagnostics["cache_hit"]) for result in results) == 1


def test_cross_turn_cache_key_detects_embedding_credential_rotation(
    tmp_path: Path,
) -> None:
    clear_hybrid_provider_cache()
    batches: list[tuple[str, ...]] = []
    activation = _cache_activation(batches)
    first = _cached_provider(
        activation,
        tmp_path,
        batches,
        _CacheVerifier(),
        embedding_credential="embedding-key-a",
    )
    second = _cached_provider(
        activation,
        tmp_path,
        batches,
        _CacheVerifier(),
        embedding_credential="embedding-key-b",
    )

    assert first._shared_state is not second._shared_state
    assert len(batches) == 2  # each credential proves its own canary identity


@pytest.mark.parametrize(
    ("runtime_key", "drifted_value"),
    [
        ("maxOutputTokens", 5999),
        ("temperature", 0.1),
        ("modelMaxRetries", 2),
        ("modelTimeoutSeconds", 89),
        ("enableThinking", True),
    ],
)
def test_qwen_provider_factory_rejects_each_verifier_inference_profile_drift(
    tmp_path: Path,
    runtime_key: str,
    drifted_value: object,
) -> None:
    clear_hybrid_provider_cache()
    batches: list[tuple[str, ...]] = []
    activation = _cache_activation(batches)
    verifier = _CacheVerifier()
    verifier.runtime[runtime_key] = drifted_value

    with pytest.raises(HybridProviderInitializationError) as raised:
        create_hybrid_candidate_provider(
            activation=activation,
            model_adapter=verifier,
            workspace_path=tmp_path,
            embedding_client_factory=lambda _workspace: _CacheEmbeddingClient(
                batches, "embedding-key"
            ),
            strict=True,
        )

    assert raised.value.reason == "verifier_inference_profile_mismatch"
    assert batches == []  # Reject before remote embedding identity work.


@pytest.mark.parametrize(
    "runtime_key",
    [
        "maxOutputTokens",
        "temperature",
        "modelMaxRetries",
        "modelTimeoutSeconds",
        "enableThinking",
    ],
)
def test_qwen_provider_factory_rejects_missing_verifier_inference_field(
    tmp_path: Path,
    runtime_key: str,
) -> None:
    batches: list[tuple[str, ...]] = []
    activation = _cache_activation(batches)
    verifier = _CacheVerifier()
    del verifier.runtime[runtime_key]

    provider = create_hybrid_candidate_provider(
        activation=activation,
        model_adapter=verifier,
        workspace_path=tmp_path,
        embedding_client_factory=lambda _workspace: _CacheEmbeddingClient(
            batches, "embedding-key"
        ),
    )

    assert provider is None
    assert batches == []


@pytest.mark.parametrize(
    ("runtime_key", "lookalike_key", "lookalike_value"),
    [
        ("maxOutputTokens", "max_output_tokens", 6000),
        ("modelMaxRetries", "max_retries", 1),
        ("modelTimeoutSeconds", "timeout_seconds", 90),
        ("enableThinking", "enable_thinking", False),
        ("enableThinking", "disableThinking", True),
    ],
)
def test_qwen_provider_factory_rejects_ignored_inference_field_aliases(
    tmp_path: Path,
    runtime_key: str,
    lookalike_key: str,
    lookalike_value: object,
) -> None:
    batches: list[tuple[str, ...]] = []
    activation = _cache_activation(batches)
    verifier = _CacheVerifier()
    del verifier.runtime[runtime_key]
    verifier.runtime[lookalike_key] = lookalike_value

    provider = create_hybrid_candidate_provider(
        activation=activation,
        model_adapter=verifier,
        workspace_path=tmp_path,
        embedding_client_factory=lambda _workspace: _CacheEmbeddingClient(
            batches, "embedding-key"
        ),
    )

    assert provider is None
    assert batches == []


def test_real_openai_adapter_has_rotation_sensitive_hybrid_cache_identity(
    monkeypatch,
) -> None:
    from minicode.model_registry import create_model_adapter

    monkeypatch.delenv("MINI_CODE_MODEL_MODE", raising=False)

    def adapter(credential: str):
        return create_model_adapter(
            "deepseek-chat",
            None,
            {
                "model": "deepseek-chat",
                "provider": "custom",
                "customBaseUrl": "https://api.deepseek.com",
                "customApiKey": credential,
            },
        )

    first = _model_adapter_cache_identity(adapter("key-a"))
    same = _model_adapter_cache_identity(adapter("key-a"))
    rotated = _model_adapter_cache_identity(adapter("key-b"))

    assert first is not None
    assert first == same
    assert first != rotated


def test_hybrid_cache_identity_changes_with_verifier_inference_controls(
    monkeypatch,
) -> None:
    from minicode.model_registry import create_model_adapter

    monkeypatch.delenv("MINI_CODE_MODEL_MODE", raising=False)

    def identity(*, temperature: float, max_tokens: int):
        adapter = create_model_adapter(
            "deepseek-chat",
            None,
            {
                "model": "deepseek-chat",
                "provider": "custom",
                "customBaseUrl": "https://api.deepseek.com",
                "customApiKey": "synthetic-key",
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        )
        return _model_adapter_cache_identity(adapter)

    baseline = identity(temperature=0.0, max_tokens=512)

    assert baseline is not None
    assert baseline != identity(temperature=0.7, max_tokens=512)
    assert baseline != identity(temperature=0.0, max_tokens=1024)


def test_hybrid_cache_identity_is_bound_to_adapter_implementation() -> None:
    runtime = {
        "model": "deepseek-chat",
        "provider": "custom",
        "customBaseUrl": "https://api.deepseek.com",
        "customApiKey": "synthetic-key",
        "temperature": 0.0,
    }

    class FirstVerifier:
        pass

    class SecondVerifier:
        pass

    first = FirstVerifier()
    first.runtime = dict(runtime)
    second = SecondVerifier()
    second.runtime = dict(runtime)

    assert _model_adapter_cache_identity(first) != _model_adapter_cache_identity(second)


def test_hybrid_cache_identity_hashes_route_headers_and_provider_params(
    monkeypatch,
) -> None:
    from minicode.model_registry import create_model_adapter

    monkeypatch.delenv("MINI_CODE_MODEL_MODE", raising=False)

    def custom_identity(header_value: str):
        adapter = create_model_adapter(
            "deepseek-chat",
            None,
            {
                "model": "deepseek-chat",
                "provider": "custom",
                "customBaseUrl": "https://api.deepseek.com",
                "customApiKey": "synthetic-private-key",
                "customApiExtraHeaders": f"X-Tenant:{header_value}",
            },
        )
        return _model_adapter_cache_identity(adapter)

    def openrouter_identity(transforms: str):
        adapter = create_model_adapter(
            "deepseek/deepseek-chat",
            None,
            {
                "model": "deepseek/deepseek-chat",
                "provider": "openrouter",
                "openrouterBaseUrl": "https://openrouter.ai/api",
                "openrouterApiKey": "synthetic-openrouter-key",
                "openrouterTransforms": transforms,
            },
        )
        return _model_adapter_cache_identity(adapter)

    tenant_a = custom_identity("synthetic-tenant-a")
    tenant_b = custom_identity("synthetic-tenant-b")
    transforms_a = openrouter_identity("middle-out")
    transforms_b = openrouter_identity("provider-routing")

    assert tenant_a != tenant_b
    assert transforms_a != transforms_b
    rendered = str((tenant_a, tenant_b, transforms_a, transforms_b))
    for private_value in (
        "synthetic-private-key",
        "synthetic-openrouter-key",
        "synthetic-tenant-a",
        "synthetic-tenant-b",
        "middle-out",
        "provider-routing",
    ):
        assert private_value not in rendered


def test_explicit_adapter_identity_cannot_mask_runtime_config_changes() -> None:
    def adapter(temperature: float):
        return SimpleNamespace(
            hybrid_runtime_cache_identity="stable-adapter-instance",
            runtime={
                "model": "deepseek-chat",
                "provider": "custom",
                "customBaseUrl": "https://api.deepseek.com",
                "customApiKey": "synthetic-key",
                "temperature": temperature,
            },
        )

    assert _model_adapter_cache_identity(adapter(0.0)) != (
        _model_adapter_cache_identity(adapter(0.7))
    )


def test_real_embedding_client_reuses_verified_canary_state_across_turns(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batches: list[tuple[str, ...]] = []

    def fake_embed_batch(self, texts, **_kwargs):
        batches.append(tuple(texts))
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(
        OpenAICompatibleEmbeddingClient,
        "_embed_batch",
        fake_embed_batch,
    )

    def client():
        return OpenAICompatibleEmbeddingClient(
            "embedding-key",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="text-embedding-v3",
        )

    evidence = _remote_runtime_evidence(client())
    batches.clear()
    clear_hybrid_provider_cache()
    activation = HybridActivation(
        requested=True,
        active=True,
        reason="activated",
        evidence=evidence,
        embedding_provider="qwen",
    )
    first = create_hybrid_candidate_provider(
        activation=activation,
        model_adapter=_CacheVerifier(),
        workspace_path=tmp_path,
        embedding_client_factory=lambda _workspace: client(),
        strict=True,
    )
    second = create_hybrid_candidate_provider(
        activation=activation,
        model_adapter=_CacheVerifier(),
        workspace_path=tmp_path,
        embedding_client_factory=lambda _workspace: client(),
        strict=True,
    )

    assert isinstance(first, HybridRuntimeProvider)
    assert isinstance(second, HybridRuntimeProvider)
    assert first._shared_state is second._shared_state
    assert len(batches) == 1


def test_qwen_provider_factory_joins_live_canary_identity_before_use(
    tmp_path: Path,
) -> None:
    client = _FakeRemoteEmbeddingClient()
    evidence = _remote_runtime_evidence(client)
    activation = HybridActivation(
        requested=True,
        active=True,
        reason="activated",
        evidence=evidence,
        embedding_provider="qwen",
    )

    provider = create_hybrid_candidate_provider(
        activation=activation,
        model_adapter=_CacheVerifier(),
        workspace_path=tmp_path,
        embedding_client_factory=lambda _workspace: client,
    )

    assert isinstance(provider, HybridRuntimeProvider)


def test_default_orchestrator_qwen_adapter_matches_frozen_inference_profile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from minicode.model_registry import create_dedicated_model_adapter

    monkeypatch.delenv("MINI_CODE_MODEL_MODE", raising=False)
    client = _FakeRemoteEmbeddingClient()
    activation = HybridActivation(
        requested=True,
        active=True,
        reason="activated",
        evidence=_remote_runtime_evidence(client),
        embedding_provider="qwen",
    )
    runtime = build_hybrid_promotion_verifier_runtime(
        {
            "subagentRoutingEnabled": True,
            "subagentModels": {"default": "qwen3.7-plus"},
            "subagentApiKey": "synthetic-qwen-key",
        }
    )
    verifier = create_dedicated_model_adapter("qwen3.7-plus", None, runtime)

    provider = create_hybrid_candidate_provider(
        activation=activation,
        model_adapter=verifier,
        workspace_path=tmp_path,
        embedding_client_factory=lambda _workspace: client,
        strict=True,
    )

    assert isinstance(provider, HybridRuntimeProvider)
    assert {
        "max_output_tokens": verifier.runtime["maxOutputTokens"],
        "temperature": verifier.runtime["temperature"],
        "max_retries": verifier.runtime["modelMaxRetries"],
        "timeout_seconds": verifier.runtime["modelTimeoutSeconds"],
        "enable_thinking": verifier.runtime["enableThinking"],
    } == dict(HYBRID_PROMOTION_INFERENCE_PROFILE)


def test_qwen_provider_factory_rejects_remote_model_drift(tmp_path: Path) -> None:
    client = _FakeRemoteEmbeddingClient()
    evidence = _remote_runtime_evidence(client)
    evidence["model"] = {
        **evidence["model"],
        "canary_fingerprint": "b" * 64,
    }
    activation = HybridActivation(
        requested=True,
        active=True,
        reason="activated",
        evidence=evidence,
        embedding_provider="qwen",
    )

    provider = create_hybrid_candidate_provider(
        activation=activation,
        model_adapter=_CacheVerifier(),
        workspace_path=tmp_path,
        embedding_client_factory=lambda _workspace: client,
    )

    assert provider is None


def test_qwen_provider_factory_rejects_verifier_transport_drift(
    tmp_path: Path,
) -> None:
    client = _FakeRemoteEmbeddingClient()
    evidence = _remote_runtime_evidence(client)
    activation = HybridActivation(
        requested=True,
        active=True,
        reason="activated",
        evidence=evidence,
        embedding_provider="qwen",
    )
    verifier = _CacheVerifier()
    verifier.runtime["customBaseUrl"] = "https://example.invalid/v1"

    provider = create_hybrid_candidate_provider(
        activation=activation,
        model_adapter=verifier,
        workspace_path=tmp_path,
        embedding_client_factory=lambda _workspace: client,
    )

    assert provider is None


def test_runtime_exact_gate_rejects_unsafe_lexical_candidate_without_llm() -> None:
    safe = _memory("safe-entry")
    suspicious = _memory("suspicious-entry", safety_status="suspicious")
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )
    request = SimpleNamespace(
        query="apply the durable rule",
        current_files=(),
        active_domains=(),
    )

    result = provider.adjudicate(
        request=request,
        entries=(safe, suspicious),
        lexical_accepted_ids=frozenset({safe.id, suspicious.id}),
    )

    assert result is not None
    by_id = {signal.entry_id: signal for signal in result.signals}
    assert by_id[safe.id].accepted is True
    assert by_id[suspicious.id].accepted is False
    assert "hybrid_ineligible_exact_gate" in by_id[suspicious.id].reason_codes
    assert verifier.seen_ids == [[safe.id], [safe.id]]


def test_runtime_caches_identical_snapshot_adjudication() -> None:
    entry = _memory("safe-entry")
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )
    request = SimpleNamespace(
        query="apply the durable rule",
        current_files=("src/rule.py",),
        active_domains=("architecture",),
    )
    kwargs = {
        "request": request,
        "entries": (entry,),
        "lexical_accepted_ids": frozenset({entry.id}),
    }

    first = provider.adjudicate(**kwargs)
    second = provider.adjudicate(**kwargs)

    assert first is not None and second is not None
    assert verifier.calls == 2
    assert second.diagnostics["cache_hit"] is True
    assert second.diagnostics["model_call_count"] == 0


def test_cache_and_query_gate_share_one_bounded_request_projection() -> None:
    entry = _memory("bounded-request")
    verifier = _CountingVerifier()
    captured_pairs: list[dict[str, object]] = []
    original_next = verifier.next

    def capture(messages):
        captured_pairs.append(json.loads(messages[1]["content"])["pairs"][0])
        return original_next(messages)

    verifier.next = capture  # type: ignore[method-assign]
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )
    prefix = ("apply bounded-request durable policy " + "x" * 2_000)[:2_000]
    common_files = tuple(f"src/file-{index:02d}.py" for index in range(16))
    common_domains = tuple(f"domain-{index:02d}" for index in range(16))
    first_request = SimpleNamespace(
        query=prefix + " first ignored suffix",
        current_files=(*common_files, "src/ignored-first.py"),
        active_domains=(*common_domains, "ignored-first"),
    )
    second_request = SimpleNamespace(
        query=prefix + " second different ignored suffix",
        current_files=(*common_files, "src/ignored-second.py"),
        active_domains=(*common_domains, "ignored-second"),
    )

    first = provider.adjudicate(
        request=first_request,
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )
    second = provider.adjudicate(
        request=second_request,
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert first is not None and second is not None
    assert _canonical_request_fields(first_request) == _canonical_request_fields(
        second_request
    )
    assert second.diagnostics["cache_hit"] is True
    assert captured_pairs == []
    assert first.diagnostics["query_gate"] == "input_budget"
    assert first.signals == ()
    assert first.diagnostics["accepted_count"] == 0


def test_runtime_model_call_budget_resets_at_each_budgetless_query() -> None:
    entry = _memory("safe-entry")
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        max_model_calls=2,
    )

    first = provider.adjudicate(
        request=SimpleNamespace(
            query="apply the durable rule",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
    )
    second = provider.adjudicate(
        request=SimpleNamespace(
            query="apply another concrete durable rule",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
    )

    assert first is not None
    assert second is not None
    assert verifier.calls == 4


@pytest.mark.parametrize(
    ("step_type", "stop_reason", "calls", "block_types", "diagnostics_present"),
    (
        ("tool_calls", "stop", [], ["text"], True),
        ("assistant", "length", [], ["text"], True),
        ("assistant", "content_filter", [], ["text"], True),
        ("assistant", None, [], ["text"], True),
        (
            "assistant",
            "stop",
            [{"id": "call-1", "toolName": "read_file", "input": {}}],
            ["text"],
            True,
        ),
        ("assistant", "stop", [], ["tool_calls"], True),
        ("assistant", "stop", [], ["tool_use"], True),
        ("assistant", "stop", [], ["text"], False),
    ),
)
def test_qwen_runtime_rejects_nonterminal_or_tool_classifier_steps(
    step_type: str,
    stop_reason: str | None,
    calls: list[dict[str, object]],
    block_types: list[str],
    diagnostics_present: bool,
) -> None:
    class EnvelopeModel:
        model_id = "qwen3.7-plus"

        def next(self, messages):
            pairs = json.loads(messages[1]["content"])["pairs"]
            content = json.dumps(
                {
                    "decisions": [
                        {
                            "id": pair["id"],
                            "decision": "relevant",
                            "confidence": 0.99,
                            "objectMatch": True,
                            "relationSupported": True,
                            "reasonCode": "constraint",
                        }
                        for pair in pairs
                    ]
                }
            )
            return AgentStep(
                type=step_type,  # type: ignore[arg-type]
                content=content,
                calls=calls,  # type: ignore[arg-type]
                diagnostics=(
                    StepDiagnostics(
                        stopReason=stop_reason,
                        blockTypes=block_types,
                    )
                    if diagnostics_present
                    else None
                ),
            )

    entry = _memory("envelope-entry")
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=EnvelopeModel(),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        embedding_provider="qwen",
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="apply the concrete envelope-entry rule",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
    )

    assert result is None


def test_qwen_runtime_accepts_a_terminal_text_only_classifier_step() -> None:
    entry = _memory("qwen-terminal-entry")
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=_CacheVerifier(),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        embedding_provider="qwen",
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="apply the concrete qwen-terminal-entry rule",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
    )

    assert result is not None
    assert result.signals[0].accepted is True


def test_qwen_runtime_rejects_an_envelope_without_an_explicit_calls_list() -> None:
    class MissingCallsModel:
        def next(self, messages):
            pair = json.loads(messages[1]["content"])["pairs"][0]
            return SimpleNamespace(
                type="assistant",
                content=json.dumps(
                    {
                        "decisions": [
                            {
                                "id": pair["id"],
                                "decision": "relevant",
                                "confidence": 0.99,
                                "objectMatch": True,
                                "relationSupported": True,
                                "reasonCode": "constraint",
                            }
                        ]
                    }
                ),
                diagnostics=StepDiagnostics(
                    stopReason="stop",
                    blockTypes=["text"],
                ),
                usage=None,
            )

    entry = _memory("missing-calls-entry")
    result = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=MissingCallsModel(),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        embedding_provider="qwen",
    ).adjudicate(
        request=SimpleNamespace(
            query="apply the concrete missing-calls-entry rule",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
    )

    assert result is None


def test_invalid_classifier_envelope_emits_failure_not_completion() -> None:
    class NonTerminalModel:
        def next(self, messages):
            pair = json.loads(messages[1]["content"])["pairs"][0]
            return AgentStep(
                type="assistant",
                content=json.dumps(
                    {
                        "decisions": [
                            {
                                "id": pair["id"],
                                "decision": "relevant",
                                "confidence": 0.99,
                                "objectMatch": True,
                                "relationSupported": True,
                                "reasonCode": "constraint",
                            }
                        ]
                    }
                ),
                diagnostics=StepDiagnostics(
                    stopReason="length",
                    blockTypes=["text"],
                ),
            )

    class Sink:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, object]]] = []

        def emit(self, event_type, **kwargs):
            self.events.append((event_type, kwargs.get("payload", {})))

    entry = _memory("event-envelope-entry")
    sink = Sink()
    result = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=NonTerminalModel(),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        embedding_provider="qwen",
    ).adjudicate(
        request=SimpleNamespace(
            query="apply the concrete event-envelope-entry rule",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
        event_sink=sink,
    )

    assert result is None
    event_names = [event_type for event_type, _payload in sink.events]
    assert event_names.count("model.failed") == 1
    assert "model.completed" not in event_names
    failure = next(
        payload for event_type, payload in sink.events if event_type == "model.failed"
    )
    assert failure["failureKind"] == "response_protocol_error"


@pytest.mark.parametrize(
    "mode",
    ("no-json", "duplicate-key", "wrong-id", "malformed-field"),
)
def test_classifier_schema_failure_is_a_protocol_event_not_completion(
    mode: str,
) -> None:
    class InvalidSchemaModel:
        def next(self, messages):
            pair_id = json.loads(messages[1]["content"])["pairs"][0]["id"]
            if mode == "no-json":
                content = "not JSON"
            elif mode == "duplicate-key":
                content = '{"decisions":[],"decisions":[]}'
            else:
                decision = {
                    "id": "wrong-id" if mode == "wrong-id" else pair_id,
                    "decision": "relevant",
                    "confidence": 0.99,
                    "objectMatch": True,
                    "relationSupported": True,
                    "reasonCode": (
                        "different_object"
                        if mode == "malformed-field"
                        else "constraint"
                    ),
                }
                content = json.dumps({"decisions": [decision]})
            return _completed_classifier_step(content=content, usage=None)

    class Sink:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, object]]] = []

        def emit(self, event_type, **kwargs):
            self.events.append((event_type, kwargs.get("payload", {})))

    entry = _memory(f"schema-event-{mode}")
    sink = Sink()
    result = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=InvalidSchemaModel(),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        embedding_provider="qwen",
    ).adjudicate(
        request=SimpleNamespace(
            query=f"apply the concrete schema-event-{mode} rule",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
        event_sink=sink,
    )

    assert result is None
    event_names = [event_type for event_type, _payload in sink.events]
    assert event_names.count("model.failed") == 1
    assert "model.completed" not in event_names
    failure = next(
        payload for event_type, payload in sink.events if event_type == "model.failed"
    )
    assert failure["failureKind"] == "response_protocol_error"


def test_provider_failure_event_is_distinct_and_content_free() -> None:
    class FailingModel:
        def next(self, _messages):
            raise RuntimeError("secret-provider-detail")

    class Sink:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, object]]] = []

        def emit(self, event_type, **kwargs):
            self.events.append((event_type, kwargs.get("payload", {})))

    entry = _memory("provider-failure-event")
    sink = Sink()
    result = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=FailingModel(),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        embedding_provider="qwen",
    ).adjudicate(
        request=SimpleNamespace(
            query="apply the concrete provider failure rule",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
        event_sink=sink,
    )

    assert result is None
    event_names = [event_type for event_type, _payload in sink.events]
    assert event_names.count("model.failed") == 1
    assert "model.completed" not in event_names
    failure = next(
        payload for event_type, payload in sink.events if event_type == "model.failed"
    )
    assert failure["failureKind"] == "provider_error"
    assert "secret-provider-detail" not in json.dumps(sink.events)


@pytest.mark.parametrize(
    ("dense_top_k", "max_candidates"),
    ((19, 32), (20, 31), (True, 32), (20, True)),
)
def test_runtime_provider_rejects_unpromoted_candidate_limits(
    dense_top_k: int,
    max_candidates: int,
) -> None:
    with pytest.raises(ValueError, match="promoted value"):
        HybridRuntimeProvider(
            encoder=_FakeEncoder(),
            model_adapter=_CountingVerifier(),
            dense_top_k=dense_top_k,
            max_candidates=max_candidates,
            minimum_confidence=0.85,
        )


def test_runtime_shared_budget_preserves_the_final_agent_model_call() -> None:
    entry = _memory("safe-entry")
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )
    budget = AgentTurnBudget(max_model_calls=1, max_total_tokens=10_000)

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="apply safe-entry durable rule",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
        agent_budget=budget,
    )

    assert result is None
    assert verifier.calls == 0
    assert budget.snapshot().used_model_calls == 0


@pytest.mark.parametrize(
    "query",
    (
        "Fix this recovery behavior.",
        "Fix the cache.",
        "修复这个恢复行为。",
    ),
)
def test_runtime_query_gate_rejects_underspecified_request_without_llm(
    query: str,
) -> None:
    entry = _memory("safe-entry")
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query=query,
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
    )

    assert result is not None
    assert verifier.calls == 0
    assert result.diagnostics["query_gate"] == "underspecified"
    assert result.diagnostics["provider"] == "local-e5_strict_llm_v3"
    assert result.diagnostics["candidate_count"] == 1
    assert result.diagnostics["lexical_rescue_count"] == 1
    assert result.diagnostics["query_gate_rejected_count"] == 1
    assert result.diagnostics["accepted_count"] == 0
    assert result.diagnostics["candidate_budget_rejected_count"] == 0
    assert len(result.signals) == 1
    assert result.signals[0].accepted is False
    assert result.signals[0].reason_codes == ("hybrid_query_underspecified",)


def test_runtime_query_gate_caps_underspecified_candidates_without_llm() -> None:
    entries = tuple(_memory(f"safe-entry-{index:02d}") for index in range(40))
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="Fix this recovery behavior.",
            current_files=(),
            active_domains=(),
        ),
        entries=entries,
        lexical_accepted_ids=frozenset(entry.id for entry in entries),
    )

    assert result is not None
    assert verifier.calls == 0
    assert len(result.candidate_ids) == 32
    assert len(result.signals) == 32
    assert result.diagnostics["candidate_count"] == 32
    assert result.diagnostics["query_gate_rejected_count"] == 32
    assert result.diagnostics["accepted_count"] == 0
    assert result.diagnostics["candidate_budget_rejected_count"] == 8


def test_runtime_query_gate_allows_concrete_file_context() -> None:
    entry = _memory("safe-entry")
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="Fix this recovery behavior.",
            current_files=("services/runtime/lease.py",),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
    )

    assert result is not None
    assert verifier.calls == 2
    assert result.signals[0].accepted is True


def test_dense_top20_is_not_displaced_by_lexical_rescue_candidates() -> None:
    class TargetFirstEncoder:
        def encode_documents(self, texts):
            return tuple(
                (1.0, 0.0) if "dense-target" in text else (0.0, 1.0) for text in texts
            )

        def encode_queries(self, texts):
            return tuple((1.0, 0.0) for _ in texts)

    lexical = tuple(_memory(f"lexical-{index:02d}") for index in range(40))
    target = _memory("dense-target")
    provider = HybridRuntimeProvider(
        encoder=TargetFirstEncoder(),
        model_adapter=_CountingVerifier(),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="recover the dense target lease",
            current_files=(),
            active_domains=(),
        ),
        entries=(*lexical, target),
        lexical_accepted_ids=frozenset(entry.id for entry in lexical),
    )

    assert result is not None
    assert len(result.candidate_ids) == 32
    assert result.candidate_ids[0] == target.id
    assert target.id in result.candidate_ids[:20]
    assert result.diagnostics["dense_lane_count"] == 20
    assert result.diagnostics["lexical_rescue_count"] == 12
    assert result.diagnostics["accepted_count"] == sum(
        signal.accepted for signal in result.signals
    )
    assert {signal.entry_id for signal in result.signals if signal.accepted} >= {
        target.id
    }


def test_lexical_rescue_order_is_part_of_the_adjudication_cache_identity() -> None:
    class LaneEncoder:
        def encode_documents(self, texts):
            return tuple(
                (1.0, 0.0) if "dense-cache-marker" in text else (-1.0, 0.0)
                for text in texts
            )

        def encode_queries(self, texts):
            return tuple((1.0, 0.0) for _ in texts)

    dense = tuple(_memory(f"cache-dense-{index:02d}") for index in range(20))
    lexical = tuple(_memory(f"cache-lexical-{index:02d}") for index in range(13))
    for entry in dense:
        entry.content = f"dense-cache-marker durable evidence {entry.id}."
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=LaneEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )
    request = SimpleNamespace(
        query="Apply the cache lexical priority contract.",
        current_files=(),
        active_domains=(),
    )

    first = provider.adjudicate(
        request=request,
        entries=(*dense, *lexical),
        lexical_accepted_ids=tuple(entry.id for entry in lexical),
    )
    second = provider.adjudicate(
        request=request,
        entries=(*dense, *lexical),
        lexical_accepted_ids=tuple(entry.id for entry in reversed(lexical)),
    )

    assert first is not None and second is not None
    assert first.diagnostics["cache_hit"] is False
    assert second.diagnostics["cache_hit"] is False
    assert first.candidate_ids[20:] == tuple(entry.id for entry in lexical[:12])
    assert second.candidate_ids[20:] == tuple(
        entry.id for entry in tuple(reversed(lexical))[:12]
    )
    assert first.candidate_ids != second.candidate_ids


def test_adjudication_cache_preserves_structured_request_identity() -> None:
    entry = _memory("structured-request-cache")
    packed = SimpleNamespace(
        query="Review the selected files.",
        current_files=("src/a.py | src/b.py",),
        active_domains=("backend | security",),
    )
    separate = SimpleNamespace(
        query="Review the selected files.",
        current_files=("src/a.py", "src/b.py"),
        active_domains=("backend", "security"),
    )

    packed_key = HybridRuntimeProvider._cache_key(packed, (entry,), ())
    separate_key = HybridRuntimeProvider._cache_key(separate, (entry,), ())

    assert packed_key != separate_key


def test_adjudication_cache_preserves_structured_entry_identity() -> None:
    request = SimpleNamespace(
        query="Review the selected memory.",
        current_files=(),
        active_domains=(),
    )
    packed = _memory("structured-entry-cache")
    packed.tags = ["checkout | retry"]
    packed.domains = ["backend | security"]
    separate = _memory("structured-entry-cache")
    separate.tags = ["checkout", "retry"]
    separate.domains = ["backend", "security"]

    packed_key = HybridRuntimeProvider._cache_key(request, (packed,), ())
    separate_key = HybridRuntimeProvider._cache_key(request, (separate,), ())

    assert packed_key != separate_key


def test_real_hybrid_to_canonical_chain_preserves_recall_lane_and_denies_hard_negative(
    tmp_path: Path,
) -> None:
    from scripts.memory_hybrid_qwen_v2_evaluator import (
        _canonical_hybrid_report_fields,
    )

    class LaneEncoder:
        def encode_documents(self, texts):
            vectors = []
            for text in texts:
                if "dense-lane-marker" in text:
                    vectors.append((1.0, 0.0))
                elif "lexical-rescue-marker" in text:
                    vectors.append((0.5, 0.8660254037844386))
                else:
                    vectors.append((-1.0, 0.0))
            return tuple(vectors)

        def encode_queries(self, texts):
            return tuple((1.0, 0.0) for _ in texts)

    hard_negative_id = "z-dense-07"

    class SelectiveChallenger(_CountingVerifier):
        def next(self, messages):
            if "admission auditor" not in messages[0]["content"]:
                return super().next(messages)
            self.calls += 1
            pairs = json.loads(messages[1]["content"])["pairs"]
            ids = [pair["id"] for pair in pairs]
            self.seen_ids.append(ids)
            return _completed_classifier_step(
                json.dumps(
                    {
                        "audits": [
                            {
                                "id": entry_id,
                                "admit": entry_id != hard_negative_id,
                                "confidence": 0.99,
                                "reasonCode": (
                                    "different_object"
                                    if entry_id == hard_negative_id
                                    else "no_disqualifier"
                                ),
                            }
                            for entry_id in ids
                        ]
                    }
                )
            )

    dense = tuple(_memory(f"z-dense-{index:02d}") for index in range(20))
    lexical = tuple(_memory(f"a-lexical-{index:02d}") for index in range(16))
    background = tuple(_memory(f"m-background-{index:02d}") for index in range(8))
    for entry in dense:
        entry.content = (
            f"dense-lane-marker durable coordinator evidence unique {entry.id}."
        )
    for entry in lexical:
        entry.content = (
            "lexical-rescue-marker apply exact lexical rescue policy to "
            f"checkout coordinator unique {entry.id}."
        )
    for entry in background:
        entry.content = f"background-marker unrelated archive evidence {entry.id}."

    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    entries = (*dense, *lexical, *background)
    manager.memories[MemoryScope.PROJECT].entries.extend(entries)
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()
    verifier = SelectiveChallenger()
    provider = HybridRuntimeProvider(
        encoder=LaneEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        resolver_minimum_confidence=None,
        embedding_provider="qwen",
    )
    request = MemoryRetrievalRequest(
        query="Apply exact lexical rescue policy to checkout coordinator.",
        max_memories=32,
        max_total_tokens=12_000,
        max_tokens_per_memory=200,
    )

    result = CanonicalMemoryRetriever(
        manager,
        hybrid_provider=provider,
    ).retrieve(request)

    dense_ids = tuple(entry.id for entry in dense)
    lexical_ids = tuple(entry.id for entry in lexical)
    expected_candidates = (*dense_ids, *lexical_ids[:12])
    assert len(entries) == 44
    assert result.candidate_ids == expected_candidates
    assert result.candidate_count == 32
    provider_details = result.diagnostics["hybrid"]["provider"]
    assert provider_details["dense_lane_count"] == 20
    assert provider_details["lexical_rescue_count"] == 12
    assert provider_details["candidate_count"] == 32
    assert provider_details["candidate_budget_rejected_count"] == 4
    assert provider_details["resolver_attempted_count"] == 0
    assert provider_details["challenger_accepted_count"] == 31
    assert provider_details["semantic_accepted_count"] == 31
    assert provider_details["accepted_count"] == 31
    assert verifier.calls == 4
    fallback, projected = _canonical_hybrid_report_fields(
        result.diagnostics["hybrid"],
        observed_candidate_count=len(result.candidate_ids),
        observed_admitted_count=len(result.admitted_ids),
    )
    assert fallback is False
    assert projected["candidate_count"] == 32
    assert projected["accepted_count"] == 31

    assert hard_negative_id in result.candidate_ids
    assert hard_negative_id not in result.admitted_ids
    assert hard_negative_id not in result.selected_ids
    assert hard_negative_id not in result.rendered_ids
    rejected = next(
        memory for memory in result.candidates if memory.entry_id == hard_negative_id
    )
    assert "challenge_different_object" in rejected.reason_codes
    assert "hybrid_semantic_rejection" in rejected.reason_codes
    assert result.admitted_ids == tuple(
        entry_id for entry_id in expected_candidates if entry_id != hard_negative_id
    )
    assert result.admitted_count == 31


@pytest.mark.parametrize(
    "target_metadata",
    (
        {"path": "services/payments/retry.py"},
        {"paths": ["services/payments/retry.py"]},
        {"files": ["services/payments/retry.py"]},
        {"file_paths": ["services/payments/retry.py"]},
        {"renamed_from": "services/payments/retry.py"},
        {"renamed_to": "services/payments/retry.py"},
    ),
    ids=("path", "paths", "files", "file-paths", "renamed-from", "renamed-to"),
)
@pytest.mark.parametrize(
    ("decoy_path", "expected_admitted_count"),
    (("retry.py", 32), ("legacy/services/payments/retry.py", 21)),
    ids=("basename-decoys", "longer-path-decoys"),
)
def test_exact_file_evidence_wins_the_bounded_lexical_rescue_lane(
    tmp_path: Path,
    target_metadata: dict[str, object],
    decoy_path: str,
    expected_admitted_count: int,
) -> None:
    class RescueEncoder:
        def encode_documents(self, texts):
            vectors = []
            for text in texts:
                if "protected-dense-marker" in text:
                    vectors.append((1.0, 0.0))
                elif "lexical-decoy-marker" in text:
                    vectors.append((0.5, 0.8660254037844386))
                else:
                    vectors.append((-1.0, 0.0))
            return tuple(vectors)

        def encode_queries(self, texts):
            return tuple((1.0, 0.0) for _ in texts)

    dense = tuple(_memory(f"dense-{index:02d}") for index in range(20))
    # The rescue budget has exactly twelve slots.  Basename mentions are useful
    # recall evidence, but they must not fill every slot ahead of one entry whose
    # structured authority metadata names the complete current file path.
    decoys = tuple(_memory(f"decoy-{index:02d}") for index in range(12))
    target = _memory("target-exact-file")
    for entry in dense:
        entry.content = f"protected-dense-marker unrelated evidence {entry.id}."
    for entry in decoys:
        entry.content = (
            f"lexical-decoy-marker {decoy_path} "
            "repair payment retry handler policy "
            f"variant {entry.id}."
        )
    target.content = "The payment retry handler policy is owned by this component."
    target.metadata = target_metadata
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.extend((*dense, *decoys, target))
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()
    provider = HybridRuntimeProvider(
        encoder=RescueEncoder(),
        model_adapter=_CountingVerifier(),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        resolver_minimum_confidence=None,
        embedding_provider="qwen",
    )

    result = CanonicalMemoryRetriever(
        manager,
        hybrid_provider=provider,
    ).retrieve(
        MemoryRetrievalRequest(
            query="Repair the payment retry handler policy.",
            current_files=("services/payments/retry.py",),
            max_memories=32,
            max_total_tokens=12_000,
            max_tokens_per_memory=200,
        )
    )

    assert len(result.candidate_ids) == 32
    assert result.candidate_ids[:20] == tuple(entry.id for entry in dense)
    assert result.candidate_ids[20] == target.id
    assert target.id in result.admitted_ids
    target_candidate = next(
        memory for memory in result.candidates if memory.entry_id == target.id
    )
    assert "full_path_match" in target_candidate.reason_codes
    assert target_candidate.path_recall_priority == 7
    assert result.diagnostics["hybrid"]["provider"]["lexical_rescue_count"] == 12
    assert result.diagnostics["hybrid"]["provider"][
        "candidate_budget_rejected_count"
    ] == 1

    runtime = result.with_detail_level("runtime")
    assert runtime.candidate_ids == ()
    assert runtime.admitted_ids == ()
    assert runtime.counts == result.counts
    assert runtime.candidate_count == 32
    assert runtime.admitted_count == expected_admitted_count
    assert runtime.diagnostics["candidate_count"] == 32
    assert runtime.diagnostics["admitted_count"] == expected_admitted_count


@pytest.mark.parametrize(
    ("query", "current_files"),
    (
        (
            "Repair the payment retry handler policy.",
            ("services/payments/retry.py",),
        ),
        ("Review `services/payments/retry.py`.", ()),
    ),
    ids=("current-file", "query-path"),
)
def test_structured_full_path_is_recall_only_without_hybrid_authority(
    tmp_path: Path,
    query: str,
    current_files: tuple[str, ...],
) -> None:
    target = _memory("path-recall-only")
    target.content = "Unrelated component ownership note."
    target.tags = []
    target.domains = []
    target.metadata = {"path": "services/payments/retry.py"}
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.append(target)
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()

    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(
            query=query,
            current_files=current_files,
        )
    )

    candidate = next(
        memory for memory in result.candidates if memory.entry_id == target.id
    )
    assert candidate.score.file_score == 0.0
    assert "full_path_match" not in candidate.reason_codes
    assert target.id not in result.admitted_ids
    assert target.id not in result.selected_ids
    assert target.id not in result.rendered_ids


def test_full_path_named_in_query_gets_protected_lexical_recall(
    tmp_path: Path,
) -> None:
    class RescueEncoder:
        def encode_documents(self, texts):
            return tuple(
                (1.0, 0.0) if "protected-dense-marker" in text else (-1.0, 0.0)
                for text in texts
            )

        def encode_queries(self, texts):
            return tuple((1.0, 0.0) for _ in texts)

    dense = tuple(_memory(f"query-path-dense-{index:02d}") for index in range(20))
    decoys = tuple(_memory(f"query-path-decoy-{index:02d}") for index in range(12))
    target = _memory("query-path-target")
    for entry in dense:
        entry.content = f"protected-dense-marker unrelated evidence {entry.id}."
    for entry in decoys:
        entry.content = (
            "Review payment retry module notes for this lexical decoy "
            f"{entry.id}."
        )
    target.content = "Authoritative ownership note for the selected component."
    target.metadata = {"path": "services/payments/retry.py"}

    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.extend((*dense, *decoys, target))
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()
    provider = HybridRuntimeProvider(
        encoder=RescueEncoder(),
        model_adapter=_CountingVerifier(),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        resolver_minimum_confidence=None,
        embedding_provider="qwen",
    )

    result = CanonicalMemoryRetriever(manager, hybrid_provider=provider).retrieve(
        MemoryRetrievalRequest(
            query="Review `services/payments/retry.py`.",
            max_memories=32,
            max_total_tokens=12_000,
            max_tokens_per_memory=200,
        )
    )

    assert result.candidate_ids[:20] == tuple(entry.id for entry in dense)
    assert result.candidate_ids[20] == target.id
    target_candidate = next(
        memory for memory in result.candidates if memory.entry_id == target.id
    )
    assert "full_path_match" in target_candidate.reason_codes
    assert target_candidate.path_recall_priority == 8


@pytest.mark.parametrize(
    "decoy_kind",
    ("casefold-structured", "exact-text"),
)
def test_strong_path_evidence_cannot_be_crowded_out_by_weaker_path_matches(
    tmp_path: Path,
    decoy_kind: str,
) -> None:
    class RescueEncoder:
        def encode_documents(self, texts):
            return tuple(
                (1.0, 0.0) if "protected-dense-marker" in text else (-1.0, 0.0)
                for text in texts
            )

        def encode_queries(self, texts):
            return tuple((1.0, 0.0) for _ in texts)

    dense = tuple(_memory(f"tier-dense-{index:02d}") for index in range(20))
    decoys = tuple(_memory(f"tier-decoy-{index:02d}") for index in range(12))
    target = _memory("tier-structured-exact-target")
    for entry in dense:
        entry.content = f"protected-dense-marker unrelated evidence {entry.id}."
    for entry in decoys:
        if decoy_kind == "casefold-structured":
            entry.content = f"Unrelated case variant {entry.id}."
            entry.metadata = {"path": "Services/Payments/Retry.py"}
        else:
            entry.content = (
                f"Unrelated text mention services/payments/retry.py {entry.id}."
            )
    target.content = "Authoritative ownership note for the selected component."
    target.metadata = {"path": "services/payments/retry.py"}

    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.extend((*dense, *decoys, target))
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()
    result = CanonicalMemoryRetriever(
        manager,
        hybrid_provider=HybridRuntimeProvider(
            encoder=RescueEncoder(),
            model_adapter=_CountingVerifier(),
            dense_top_k=20,
            max_candidates=32,
            minimum_confidence=0.85,
            resolver_minimum_confidence=None,
            embedding_provider="qwen",
        ),
    ).retrieve(
        MemoryRetrievalRequest(
            query="Review `services/payments/retry.py`.",
            max_memories=32,
            max_total_tokens=12_000,
            max_tokens_per_memory=200,
        )
    )

    assert result.candidate_ids[:20] == tuple(entry.id for entry in dense)
    assert result.candidate_ids[20] == target.id
    assert len({entry.id for entry in decoys} & set(result.candidate_ids)) == 11


def test_recall_ignores_current_files_outside_the_model_visible_projection(
    tmp_path: Path,
) -> None:
    class CaptureProvider:
        def adjudicate(self, **kwargs):
            self.lexical_ids = tuple(kwargs["lexical_accepted_ids"])

    visible = _memory("visible-current-file")
    hidden = _memory("hidden-current-file")
    visible.content = "Visible path authority."
    hidden.content = "Hidden path authority."
    visible.metadata = {"path": "src/module-00.py"}
    hidden.metadata = {"path": "src/module-16.py"}
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    manager.memories[MemoryScope.PROJECT].entries.extend((visible, hidden))
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()
    provider = CaptureProvider()

    result = CanonicalMemoryRetriever(manager, hybrid_provider=provider).retrieve(
        MemoryRetrievalRequest(
            query="Review the selected module ownership policy.",
            current_files=tuple(f"src/module-{index:02d}.py" for index in range(17)),
        )
    )

    by_id = {memory.entry_id: memory for memory in result.candidates}
    assert "full_path_match" in by_id[visible.id].reason_codes
    assert by_id[visible.id].path_recall_priority == 7
    assert "full_path_match" not in by_id[hidden.id].reason_codes
    assert by_id[hidden.id].path_recall_priority == 0


def test_candidate_cohort_is_distinct_from_admission_and_rendering(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    rejected = _memory("dense-hard-negative")
    accepted = _memory("dense-relevant")
    manager.memories[MemoryScope.PROJECT].entries.extend((rejected, accepted))
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()

    class Provider:
        def adjudicate(self, **_kwargs):
            return HybridAdjudication(
                signals=(
                    HybridCandidateSignal(
                        entry_id=rejected.id,
                        dense_score=0.99,
                        relevance_score=0.99,
                        accepted=False,
                        reason_codes=("semantic_different_object",),
                    ),
                    HybridCandidateSignal(
                        entry_id=accepted.id,
                        dense_score=0.90,
                        relevance_score=0.95,
                        accepted=True,
                        reason_codes=("semantic_recovery",),
                    ),
                ),
                diagnostics={"provider": "scripted_strict_gate"},
                candidate_ids=(rejected.id, accepted.id),
            )

    request = MemoryRetrievalRequest(
        query="recover the relevant dense lease",
        max_memories=5,
        max_total_tokens=1200,
    )
    result = CanonicalMemoryRetriever(manager, hybrid_provider=Provider()).retrieve(
        request
    )
    runtime = CanonicalMemoryRetriever(
        manager,
        hybrid_provider=Provider(),
        detail_level="runtime",
    ).retrieve(request)

    assert result.candidate_ids == (rejected.id, accepted.id)
    assert result.admitted_ids == (accepted.id,)
    assert result.selected_ids == (accepted.id,)
    assert result.rendered_ids == (accepted.id,)
    assert rejected.id not in result.selected_ids
    assert runtime.candidate_ids == ()
    assert runtime.admitted_ids == ()
    assert runtime.counts is not None
    assert runtime.counts.candidate_count == 2
    assert runtime.counts.admitted_count == 1
    assert runtime.counts.selected_count == 1
    assert runtime.counts.rendered_count == 1
    assert runtime.diagnostics["admitted_count"] == 1


def test_raw_admission_is_visible_before_duplicate_consolidation(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    first = _memory("duplicate-admission-a")
    second = _memory("duplicate-admission-b")
    first.content = second.content = "Use the same durable recovery rule."
    manager.memories[MemoryScope.PROJECT].entries.extend((first, second))
    manager.memories[MemoryScope.PROJECT]._rebuild_indices()

    class Provider:
        def adjudicate(self, **_kwargs):
            return HybridAdjudication(
                signals=tuple(
                    HybridCandidateSignal(
                        entry_id=entry.id,
                        dense_score=0.99,
                        relevance_score=0.99,
                        accepted=True,
                        reason_codes=("semantic_recovery",),
                    )
                    for entry in (first, second)
                ),
                diagnostics={"provider": "scripted_strict_gate"},
                candidate_ids=(first.id, second.id),
            )

    result = CanonicalMemoryRetriever(manager, hybrid_provider=Provider()).retrieve(
        MemoryRetrievalRequest(
            query="Apply the durable recovery rule to the retry worker.",
            max_memories=5,
            max_total_tokens=1200,
        )
    )

    assert result.candidate_ids == (first.id, second.id)
    assert result.admitted_ids == (first.id, second.id)
    assert len(result.selected_ids) == 1
    assert len(result.rendered_ids) == 1


def test_hybrid_adjudication_rejects_admission_outside_candidate_cohort() -> None:
    with pytest.raises(ValueError, match="accepted hybrid signals"):
        HybridAdjudication(
            signals=(
                HybridCandidateSignal(
                    entry_id="declared-candidate",
                    dense_score=0.9,
                    relevance_score=0.0,
                    accepted=False,
                    reason_codes=("semantic_unrelated",),
                ),
                HybridCandidateSignal(
                    entry_id="outside-candidate",
                    dense_score=0.8,
                    relevance_score=0.99,
                    accepted=True,
                    reason_codes=("semantic_constraint",),
                ),
            ),
            diagnostics={},
            candidate_ids=("declared-candidate",),
        )


def test_hybrid_adjudication_preserves_an_explicit_empty_candidate_cohort() -> None:
    with pytest.raises(ValueError, match="accepted hybrid signals"):
        HybridAdjudication(
            signals=(
                HybridCandidateSignal(
                    entry_id="must-not-be-recovered",
                    dense_score=0.9,
                    relevance_score=0.99,
                    accepted=True,
                    reason_codes=("semantic_constraint",),
                ),
            ),
            diagnostics={},
            candidate_ids=(),
        )


def test_structured_path_gate_rejects_conflict_and_allows_proven_rename() -> None:
    conflicting = _memory("conflicting-path")
    conflicting.content = (
        "tools/release/config.py disables retries during artifact signing."
    )
    conflicting.metadata = {"path": "tools/release/config.py"}
    renamed = _memory("proven-rename")
    renamed.content = (
        "Migration cfg-19 moved retry ownership from legacy/api/settings.py "
        "to services/api/config.py."
    )
    renamed.metadata = {
        "renamed_from": "legacy/api/settings.py",
        "renamed_to": "services/api/config.py",
        "migration_id": "cfg-19",
    }
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query=(
                "Apply the retry handling rename from legacy/api/settings.py "
                "to services/api/config.py."
            ),
            current_files=("services/api/config.py",),
            active_domains=(),
        ),
        entries=(conflicting, renamed),
        lexical_accepted_ids=frozenset({conflicting.id, renamed.id}),
    )

    assert result is not None
    by_id = {signal.entry_id: signal for signal in result.signals}
    assert result.candidate_ids == (conflicting.id, renamed.id)
    assert by_id[conflicting.id].accepted is False
    assert "deterministic_path_conflict" in by_id[conflicting.id].reason_codes
    assert by_id[renamed.id].accepted is True
    assert "deterministic_proven_rename" in by_id[renamed.id].reason_codes
    assert verifier.calls == 0


def test_proven_rename_cannot_override_an_action_polarity_veto() -> None:
    entry = _memory("rename-with-opposite-operation")
    entry.content = (
        "Migration cfg-19 moved the enable retries rule from "
        "legacy/api/settings.py to services/api/config.py."
    )
    entry.metadata = {
        "renamed_from": "legacy/api/settings.py",
        "renamed_to": "services/api/config.py",
        "migration_id": "cfg-19",
    }
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="Disable retries in services/api/config.py.",
            current_files=("services/api/config.py",),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert result.signals[0].reason_codes == (
        "deterministic_action_polarity_conflict",
    )
    assert result.diagnostics["deterministic_accepted_count"] == 0
    assert result.diagnostics["deterministic_rejected_count"] == 1
    assert result.diagnostics["model_call_count"] == 0
    assert verifier.calls == 0


def test_case_only_path_difference_cannot_be_deterministic_rename_proof() -> None:
    entry = _memory("case-distinct-rename")
    entry.content = (
        "Migration cfg-20 moved retry ownership to services/api/config.py."
    )
    entry.metadata = {
        "renamed_from": "legacy/api/settings.py",
        "renamed_to": "services/api/config.py",
        "migration_id": "cfg-20",
    }
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="Change retry ownership in services/API/Config.py.",
            current_files=("services/API/Config.py",),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert verifier.calls == 2


def test_unproven_rename_is_deferred_to_semantic_adjudication() -> None:
    entry = _memory("unproven-rename")
    entry.content = "Ownership moved to engine/generation_lease.py."
    entry.metadata = {"renamed_to": "engine/generation_lease.py"}
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="Update generation ownership in engine/generation_lease.py.",
            current_files=("engine/generation_lease.py",),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert verifier.calls == 2


def test_proven_rename_without_matching_operation_is_still_deferred() -> None:
    entry = _memory("unrelated-proven-rename")
    entry.content = "Migration cfg-19 moved retry ownership to services/api/config.py."
    entry.metadata = {
        "renamed_from": "legacy/api/settings.py",
        "renamed_to": "services/api/config.py",
        "migration_id": "cfg-19",
    }
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="Format comments in services/api/config.py.",
            current_files=("services/api/config.py",),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert verifier.calls == 2


def test_generic_rename_verb_at_destination_cannot_bypass_semantic_gate() -> None:
    entry = _memory("generic-rename-destination")
    entry.content = (
        "Migration cfg-19 moved retry ownership to services/api/config.py."
    )
    entry.metadata = {
        "renamed_from": "legacy/api/settings.py",
        "renamed_to": "services/api/config.py",
        "migration_id": "cfg-19",
    }
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="Rename comments in services/api/config.py.",
            current_files=("services/api/config.py",),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "deterministic_proven_rename" not in result.signals[0].reason_codes
    assert verifier.calls == 2


def test_explicit_source_and_destination_can_use_migration_proof() -> None:
    entry = _memory("explicit-rename-path-pair")
    entry.content = (
        "Migration cfg-19 moved ownership from legacy/api/settings.py "
        "to services/api/config.py."
    )
    entry.metadata = {
        "renamed_from": "legacy/api/settings.py",
        "renamed_to": "services/api/config.py",
        "migration_id": "cfg-19",
    }
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query=(
                "Apply the rename from legacy/api/settings.py to "
                "services/api/config.py."
            ),
            current_files=("services/api/config.py",),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "deterministic_proven_rename" in result.signals[0].reason_codes
    assert verifier.calls == 0


@pytest.mark.parametrize(
    ("query", "content", "reason"),
    (
        (
            "Publish success only after the result transaction commits.",
            "Publish success before committing the result transaction.",
            "deterministic_operation_order_conflict",
        ),
        (
            "Keep the newest two checkpoints after recovery.",
            "Do not keep the newest two checkpoints after recovery.",
            "deterministic_negation_conflict",
        ),
        (
            "默认只监听本机地址。",
            "默认向所有网络接口公开监听端口。",
            "deterministic_network_exposure_conflict",
        ),
        (
            "可逆选择无需再次询问。",
            "可逆选择仍然必须逐项确认。",
            "deterministic_confirmation_policy_conflict",
        ),
        (
            "Enable retries for the invoice worker.",
            "Disable retries for the invoice worker.",
            "deterministic_action_polarity_conflict",
        ),
        (
            "允许上传服务写入临时目录。",
            "禁止上传服务写入临时目录。",
            "deterministic_action_polarity_conflict",
        ),
        (
            "提交事务之后发布成功。",
            "提交事务之前发布成功。",
            "deterministic_operation_order_conflict",
        ),
        (
            "保留最新两个检查点。",
            "不要保留最新两个检查点。",
            "deterministic_negation_conflict",
        ),
    ),
)
def test_deterministic_applicability_conflicts_bypass_the_model(
    query: str,
    content: str,
    reason: str,
) -> None:
    entry = _memory("conflicting-rule")
    entry.content = content
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query=query,
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert reason in result.signals[0].reason_codes
    assert verifier.calls == 0


def test_order_words_on_different_objects_are_deferred_to_the_model() -> None:
    entry = _memory("different-operation")
    entry.content = "Refresh the cache before retrying a worker lease."
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="Run invoice cleanup after publishing the receipt.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert verifier.calls == 2


def test_removed_version_configuration_is_rejected_without_model_call() -> None:
    entry = _memory("obsolete-setting")
    entry.content = (
        "Worker runtime v1 uses max_idle_jobs; this setting was removed in runtime v3."
    )
    entry.metadata = {"component": "worker-runtime", "version": "1"}
    verifier = _CountingVerifier()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="Keep idle workers warm between traffic bursts.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_obsolete_configuration" in (result.signals[0].reason_codes)
    assert verifier.calls == 0


def test_challenger_vetoes_preliminary_opposite_order_match() -> None:
    class ContradictionChallenger(_CountingVerifier):
        def next(self, messages):
            if "admission auditor" not in messages[0]["content"]:
                return super().next(messages)
            self.calls += 1
            request = json.loads(messages[1]["content"])
            ids = [row["id"] for row in request["pairs"]]
            self.seen_ids.append(ids)
            return _completed_classifier_step(
                content=json.dumps(
                    {
                        "audits": [
                            {
                                "id": entry_id,
                                "admit": False,
                                "confidence": 0.99,
                                "reasonCode": "contradictory_order",
                            }
                            for entry_id in ids
                        ]
                    }
                ),
                usage=None,
            )

    entry = _memory("opposite-order")
    verifier = ContradictionChallenger()
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=verifier,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="publish only after commit",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "challenge_contradictory_order" in result.signals[0].reason_codes


@pytest.mark.parametrize(
    "fatal_reason",
    (
        "different_object",
        "different_actor",
        "unsupported_relation",
        "unrelated",
        "query_underspecified",
    ),
)
def test_challenger_requires_positive_admission_for_every_fatal_reason(
    fatal_reason: str,
) -> None:
    class ConservativeChallenger(_CountingVerifier):
        def next(self, messages):
            if "admission auditor" not in messages[0]["content"]:
                return super().next(messages)
            self.calls += 1
            request = json.loads(messages[1]["content"])
            ids = [row["id"] for row in request["pairs"]]
            self.seen_ids.append(ids)
            return _completed_classifier_step(
                content=json.dumps(
                    {
                        "audits": [
                            {
                                "id": entry_id,
                                "admit": False,
                                "confidence": 0.99,
                                "reasonCode": fatal_reason,
                            }
                            for entry_id in ids
                        ]
                    }
                ),
                usage=None,
            )

    entry = _memory("verified-recovery")
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=ConservativeChallenger(),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="resume archive extraction after restart",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert f"challenge_{fatal_reason}" in result.signals[0].reason_codes


def test_challenger_low_confidence_admission_fails_closed() -> None:
    class LowConfidenceChallenger(_CountingVerifier):
        def next(self, messages):
            if "admission auditor" not in messages[0]["content"]:
                return super().next(messages)
            self.calls += 1
            request = json.loads(messages[1]["content"])
            ids = [row["id"] for row in request["pairs"]]
            return _completed_classifier_step(
                content=json.dumps(
                    {
                        "audits": [
                            {
                                "id": entry_id,
                                "admit": True,
                                "confidence": 0.79,
                                "reasonCode": "no_disqualifier",
                            }
                            for entry_id in ids
                        ]
                    }
                ),
                usage=None,
            )

    entry = _memory("uncertain-admission")
    provider = HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=LowConfidenceChallenger(),
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        challenger_minimum_confidence=0.8,
    )

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="apply the uncertain admission contract",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "challenge_below_confidence" in result.signals[0].reason_codes


def test_verifier_parser_rejects_duplicate_or_missing_ids() -> None:
    row = {
        "id": "a",
        "decision": "relevant",
        "confidence": 0.99,
        "objectMatch": True,
        "relationSupported": True,
        "reasonCode": "constraint",
    }
    text = json.dumps({"decisions": [row, row]})

    with pytest.raises(ValueError, match="IDs mismatch"):
        _parse_decisions(text, ["a", "b"])


def test_activation_rejects_evidence_without_frozen_verifier_contract(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    model_identity = {
        "model_id": "test/local-e5",
        "model_revision": "frozen",
        "model_fingerprint": "a" * 64,
    }
    (model_path / "model_manifest.json").write_text(
        json.dumps(model_identity), encoding="utf-8"
    )
    evidence = {
        "schema_version": HYBRID_EVIDENCE_SCHEMA_VERSION,
        "protocol_version": HYBRID_PROTOCOL_VERSION,
        "inference_profile": dict(HYBRID_PROMOTION_INFERENCE_PROFILE),
        "model": model_identity,
        "acceptance_gate": {"passed": True},
        "production_enablement_allowed": True,
        "runtime_implementation_freeze": build_hybrid_runtime_freeze(),
    }
    evidence["report_fingerprint"] = evidence_fingerprint(evidence)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    activation = assess_hybrid_activation(
        requested=True,
        evidence_path=evidence_path,
        model_path=model_path,
    )

    assert activation.active is False
    assert activation.reason == "verifier_evidence_missing"


def _qwen_activation_evidence() -> dict[str, object]:
    freeze_body = {
        "schema_version": QWEN_PROMOTION_IMPLEMENTATION_FREEZE_SCHEMA_VERSION,
        "file_count": 1,
        "files": [{"path": "synthetic/evaluator.py", "sha256": "a" * 64}],
    }
    implementation_freeze = {
        **freeze_body,
        "aggregate_sha256": hashlib.sha256(
            json.dumps(
                freeze_body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }
    remote_identity = {
        "provider": "qwen",
        "model_id": "text-embedding-v3",
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
        "dimension": 1024,
        "representation_version": "memory-structured-v1",
        "canary_version": "embedding-canary-v1",
        "canary_fingerprint": "b" * 64,
    }
    evidence = {
        "schema_version": HYBRID_EVIDENCE_SCHEMA_VERSION,
        "protocol_version": HYBRID_PROTOCOL_VERSION,
        "synthetic_data": True,
        "embedding_provider": "qwen",
        "inference_profile": dict(HYBRID_PROMOTION_INFERENCE_PROFILE),
        "model": remote_identity,
        "acceptance_gate": {
            "passed": True,
            "checks": {key: True for key in QWEN_PROMOTION_ACCEPTANCE_CHECK_KEYS},
        },
        "production_enablement_allowed": True,
        "dense_top_k": 20,
        "max_union_candidates": 32,
        "max_model_calls_per_task": 8,
        "query_gate_version": HYBRID_QUERY_GATE_VERSION,
        "runtime_implementation_freeze": build_hybrid_runtime_freeze(),
        "implementation_freeze": implementation_freeze,
        "promotion_report": {
            "artifact": QWEN_PROMOTION_REPORT_ARTIFACT,
            "report_fingerprint": "d" * 64,
        },
        "holdout": {
            "version": QWEN_PROMOTION_DATASET_ID,
            "file_sha256": QWEN_PROMOTION_HOLDOUT_SHA256,
            "manifest_sha256": QWEN_PROMOTION_MANIFEST_SHA256,
            "background_count": QWEN_PROMOTION_BACKGROUND_COUNT,
            "case_count": QWEN_PROMOTION_CASE_COUNT,
            "positive_count": QWEN_PROMOTION_POSITIVE_COUNT,
            "hard_negative_count": QWEN_PROMOTION_HARD_NEGATIVE_COUNT,
        },
        "one_shot_claim": {
            "schema_version": 1,
            "dataset_id": QWEN_PROMOTION_DATASET_ID,
            "holdout_sha256": QWEN_PROMOTION_HOLDOUT_SHA256,
            "output_artifact": QWEN_PROMOTION_REPORT_ARTIFACT,
            "marker_path": HYBRID_QWEN_ONE_SHOT_MARKER_NAME,
            "claimed_at_utc": "2026-09-03T00:00:00Z",
        },
        "resolver_pressure_receipt": {
            "artifact": HYBRID_RESOLVER_PRESSURE_ARTIFACT,
            "report_fingerprint": "c" * 64,
            "holdout_sha256": HYBRID_RESOLVER_PRESSURE_HOLDOUT_SHA256,
            "manifest_sha256": HYBRID_RESOLVER_PRESSURE_MANIFEST_SHA256,
            "counts": {
                "case_count": 60,
                "raw_relevant_count": 0,
                "approved_count": 0,
            },
            "approved_one_sided_exact_95_upper": (1.0 - 0.05 ** (1.0 / 60.0)),
        },
        "verifier_route": {
            "kind": "dedicated_exact_subagent",
            "provider": "openai-compatible",
            "base_url": QWEN_VERIFIER_BASE_URL,
            "model_id": "qwen3.7-plus",
        },
        **build_hybrid_decision_contract("qwen3.7-plus"),
    }
    evidence["report_fingerprint"] = evidence_fingerprint(evidence)
    return evidence


@pytest.fixture(autouse=True)
def _allow_test_pressure_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind the synthetic Qwen activation fixture to test-only release authority."""

    monkeypatch.setattr(
        "minicode.memory_hybrid.HYBRID_ACCEPTED_RESOLVER_PRESSURE_REPORT_FINGERPRINT",
        "c" * 64,
    )


def test_qwen_activation_requires_its_own_allowlisted_remote_identity(
    tmp_path: Path,
) -> None:
    evidence = _qwen_activation_evidence()
    evidence_path = tmp_path / "qwen-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    without_qwen_authority = assess_hybrid_activation(
        requested=True,
        evidence_path=evidence_path,
        model_path=None,
        embedding_provider="qwen",
        allow_remote_embedding=True,
    )
    with patch(
        "minicode.memory_hybrid.HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT",
        evidence["report_fingerprint"],
    ):
        authorized = assess_hybrid_activation(
            requested=True,
            evidence_path=evidence_path,
            model_path=None,
            embedding_provider="qwen",
            allow_remote_embedding=True,
        )

    assert without_qwen_authority.active is False
    assert without_qwen_authority.reason == "promotion_not_allowlisted"
    assert authorized.active is True
    assert authorized.embedding_provider == "qwen"
    assert authorized.model_path is None

    stale_gate = {**evidence, "query_gate_version": "concrete-object-v1"}
    stale_gate["report_fingerprint"] = evidence_fingerprint(stale_gate)
    evidence_path.write_text(json.dumps(stale_gate), encoding="utf-8")
    with patch(
        "minicode.memory_hybrid.HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT",
        stale_gate["report_fingerprint"],
    ):
        stale_activation = assess_hybrid_activation(
            requested=True,
            evidence_path=evidence_path,
            model_path=None,
            embedding_provider="qwen",
            allow_remote_embedding=True,
        )

    assert stale_activation.active is False
    assert stale_activation.reason == "candidate_evidence_invalid"


def test_qwen_activation_requires_pressure_release_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _qwen_activation_evidence()
    evidence_path = tmp_path / "qwen-unreleased-pressure.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    monkeypatch.setattr(
        "minicode.memory_hybrid.HYBRID_ACCEPTED_RESOLVER_PRESSURE_REPORT_FINGERPRINT",
        "",
    )
    monkeypatch.setattr(
        "minicode.memory_hybrid.HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT",
        evidence["report_fingerprint"],
    )

    activation = assess_hybrid_activation(
        requested=True,
        evidence_path=evidence_path,
        model_path=None,
        embedding_provider="qwen",
        allow_remote_embedding=True,
    )

    assert activation.active is False
    assert activation.reason == "resolver_pressure_evidence_invalid"


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    (
        ("evaluation_gate", "acceptance_gate_failed"),
        ("gate_extra", "acceptance_gate_failed"),
        ("freeze_extra", "implementation_freeze_invalid"),
        ("model_extra", "remote_model_evidence_invalid"),
    ),
)
def test_qwen_production_activation_rejects_nonproduction_nested_schema(
    tmp_path: Path,
    mutation: str,
    expected_reason: str,
) -> None:
    evidence = _qwen_activation_evidence()
    if mutation == "evaluation_gate":
        evidence["acceptance_gate"] = {
            "passed": True,
            "evaluation_bootstrap": True,
        }
        evidence["production_enablement_allowed"] = False
    elif mutation == "gate_extra":
        gate = evidence["acceptance_gate"]
        assert isinstance(gate, dict)
        gate["evaluation_bootstrap"] = True
    elif mutation == "freeze_extra":
        freeze = evidence["implementation_freeze"]
        assert isinstance(freeze, dict)
        freeze["note"] = "not part of the closed certificate"
    else:
        model = evidence["model"]
        assert isinstance(model, dict)
        model["alternate_endpoint"] = "https://example.invalid"
    evidence["report_fingerprint"] = evidence_fingerprint(evidence)
    evidence_path = tmp_path / f"qwen-nested-{mutation}.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with patch(
        "minicode.memory_hybrid.HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT",
        evidence["report_fingerprint"],
    ):
        activation = assess_hybrid_activation(
            requested=True,
            evidence_path=evidence_path,
            model_path=None,
            embedding_provider="qwen",
            allow_remote_embedding=True,
        )

    assert activation.active is False
    assert activation.reason == expected_reason


@pytest.mark.parametrize("mutation", ("holdout", "artifact"))
def test_qwen_activation_rejects_self_bound_promotion_identity(
    tmp_path: Path,
    mutation: str,
) -> None:
    evidence = _qwen_activation_evidence()
    claim = evidence["one_shot_claim"]
    holdout = evidence["holdout"]
    promotion_report = evidence["promotion_report"]
    assert isinstance(claim, dict)
    assert isinstance(holdout, dict)
    assert isinstance(promotion_report, dict)
    if mutation == "holdout":
        holdout["file_sha256"] = "f" * 64
        claim["holdout_sha256"] = "f" * 64
    else:
        promotion_report["artifact"] = "artifacts/alternate-promotion.json"
        claim["output_artifact"] = "artifacts/alternate-promotion.json"
    evidence["report_fingerprint"] = evidence_fingerprint(evidence)
    evidence_path = tmp_path / f"qwen-self-bound-{mutation}.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with patch(
        "minicode.memory_hybrid.HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT",
        evidence["report_fingerprint"],
    ):
        activation = assess_hybrid_activation(
            requested=True,
            evidence_path=evidence_path,
            model_path=None,
            embedding_provider="qwen",
            allow_remote_embedding=True,
        )

    assert activation.active is False
    assert activation.reason == "one_shot_evidence_invalid"


def test_qwen_activation_rejects_non_finite_json(tmp_path: Path) -> None:
    evidence = _qwen_activation_evidence()
    encoded = json.dumps(evidence).replace(
        '"max_model_calls_per_task": 8',
        '"max_model_calls_per_task": NaN',
    )
    evidence_path = tmp_path / "qwen-non-finite.json"
    evidence_path.write_text(encoded, encoding="utf-8")

    activation = assess_hybrid_activation(
        requested=True,
        evidence_path=evidence_path,
        model_path=None,
        embedding_provider="qwen",
        allow_remote_embedding=True,
    )

    assert activation.active is False
    assert activation.reason == "evidence_malformed"


def test_qwen_activation_rejects_duplicate_json_keys_before_allowlisting(
    tmp_path: Path,
) -> None:
    evidence = _qwen_activation_evidence()
    encoded = json.dumps(evidence)
    evidence_path = tmp_path / "qwen-duplicate-key-evidence.json"
    evidence_path.write_text(
        '{"schema_version":"3.0","schema_version":"3.0",' + encoded[1:],
        encoding="utf-8",
    )

    activation = assess_hybrid_activation(
        requested=True,
        evidence_path=evidence_path,
        model_path=None,
        embedding_provider="qwen",
        allow_remote_embedding=True,
    )

    assert activation.active is False
    assert activation.reason == "evidence_malformed"


def test_qwen_activation_rejects_a_rebound_one_shot_claim(tmp_path: Path) -> None:
    evidence = _qwen_activation_evidence()
    claim = evidence["one_shot_claim"]
    assert isinstance(claim, dict)
    claim["output_artifact"] = "artifacts/replayed-report.json"
    evidence["report_fingerprint"] = evidence_fingerprint(evidence)
    evidence_path = tmp_path / "qwen-replayed-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with patch(
        "minicode.memory_hybrid.HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT",
        evidence["report_fingerprint"],
    ):
        activation = assess_hybrid_activation(
            requested=True,
            evidence_path=evidence_path,
            model_path=None,
            embedding_provider="qwen",
            allow_remote_embedding=True,
        )

    assert activation.active is False
    assert activation.reason == "one_shot_evidence_invalid"


@pytest.mark.parametrize(
    "mutation",
    ("missing", "counts", "holdout_hash", "manifest_hash", "artifact"),
)
def test_qwen_activation_rejects_invalid_resolver_pressure_receipt(
    tmp_path: Path,
    mutation: str,
) -> None:
    evidence = _qwen_activation_evidence()
    if mutation == "missing":
        evidence.pop("resolver_pressure_receipt")
    elif mutation == "counts":
        receipt = evidence["resolver_pressure_receipt"]
        assert isinstance(receipt, dict)
        counts = receipt["counts"]
        assert isinstance(counts, dict)
        counts["approved_count"] = 1
    else:
        receipt = evidence["resolver_pressure_receipt"]
        assert isinstance(receipt, dict)
        if mutation == "holdout_hash":
            receipt["holdout_sha256"] = "f" * 64
        elif mutation == "manifest_hash":
            receipt["manifest_sha256"] = "f" * 64
        else:
            receipt["artifact"] = "artifacts/forged-pressure.json"
    evidence["report_fingerprint"] = evidence_fingerprint(evidence)
    evidence_path = tmp_path / f"qwen-{mutation}-pressure-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with patch(
        "minicode.memory_hybrid.HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT",
        evidence["report_fingerprint"],
    ):
        activation = assess_hybrid_activation(
            requested=True,
            evidence_path=evidence_path,
            model_path=None,
            embedding_provider="qwen",
            allow_remote_embedding=True,
        )

    assert activation.active is False
    assert activation.reason == (
        "evidence_schema_mismatch"
        if mutation == "missing"
        else "resolver_pressure_evidence_invalid"
    )


def test_qwen_activation_rejects_runtime_implementation_drift(
    tmp_path: Path,
) -> None:
    evidence = _qwen_activation_evidence()
    runtime_freeze = evidence["runtime_implementation_freeze"]
    assert isinstance(runtime_freeze, dict)
    runtime_freeze["aggregate_sha256"] = "f" * 64
    evidence["report_fingerprint"] = evidence_fingerprint(evidence)
    evidence_path = tmp_path / "qwen-runtime-drift-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with patch(
        "minicode.memory_hybrid.HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT",
        evidence["report_fingerprint"],
    ):
        activation = assess_hybrid_activation(
            requested=True,
            evidence_path=evidence_path,
            model_path=None,
            embedding_provider="qwen",
            allow_remote_embedding=True,
        )

    assert activation.active is False
    assert activation.reason == "runtime_implementation_drift"


def test_local_encoder_rejects_symlinked_model_root_before_loading_runtime(
    tmp_path: Path,
) -> None:
    real_root = tmp_path / "real-model"
    (real_root / "onnx").mkdir(parents=True)
    for relative, value in {
        "config.json": b"{}",
        "tokenizer.json": b"{}",
        "onnx/model_quantized.onnx": b"not-a-model",
    }.items():
        path = real_root / relative
        path.write_bytes(value)
    files = {
        relative: hashlib.sha256((real_root / relative).read_bytes()).hexdigest()
        for relative in (
            "config.json",
            "tokenizer.json",
            "onnx/model_quantized.onnx",
        )
    }
    manifest = {
        "model_id": "test/local-e5",
        "model_revision": "frozen",
        "files": files,
    }
    manifest["model_fingerprint"] = hashlib.sha256(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    (real_root / "model_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    linked_root = tmp_path / "linked-model"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(ValueError, match="real local directory"):
        LocalE5Encoder(linked_root, manifest)


class _ScriptedDisagreementResolver:
    """Drive the verifier, challenger, and resolver through their public seam."""

    model_id = "deepseek-chat"

    def __init__(
        self,
        *,
        primary_reason: str = "semantic_equivalence",
        challenger_reason: str = "different_object",
        challenger_confidence: float = 0.99,
        resolver_confidence: float = 0.99,
        support_kind: str = "semantic_equivalence",
        query_evidence: str,
        memory_evidence: str,
        malformed_challenger: bool = False,
        resolver_mutation: str = "",
    ) -> None:
        self.primary_reason = primary_reason
        self.challenger_reason = challenger_reason
        self.challenger_confidence = challenger_confidence
        self.resolver_confidence = resolver_confidence
        self.support_kind = support_kind
        self.query_evidence = query_evidence
        self.memory_evidence = memory_evidence
        self.malformed_challenger = malformed_challenger
        self.resolver_mutation = resolver_mutation
        self.call_kinds: list[str] = []
        self.resolver_pair_ids: list[tuple[str, ...]] = []

    def next(self, messages):
        system = messages[0]["content"]
        request = json.loads(messages[1]["content"])
        pairs = request["pairs"]
        if "disagreement resolver" in system:
            self.call_kinds.append("resolver")
            self.resolver_pair_ids.append(tuple(pair["id"] for pair in pairs))
            resolutions = []
            for pair in pairs:
                query_evidence_id = next(
                    segment["id"]
                    for segment in pair["queryEvidenceSegments"]
                    if self.query_evidence in segment["text"]
                )
                memory_evidence_id = next(
                    segment["id"]
                    for segment in pair["memoryEvidenceSegments"]
                    if self.memory_evidence in segment["text"]
                )
                row = {
                    "id": pair["id"],
                    "decision": "relevant",
                    "confidence": self.resolver_confidence,
                    "queryGrounded": True,
                    "objectMatch": True,
                    "relationSupported": True,
                    "supportKind": self.support_kind,
                    "disqualifier": "none",
                    "queryEvidenceId": query_evidence_id,
                    "memoryEvidenceId": memory_evidence_id,
                }
                if self.resolver_mutation == "query_substring":
                    row["queryEvidenceId"] = "q999"
                elif self.resolver_mutation == "memory_substring":
                    row["memoryEvidenceId"] = "m999"
                elif self.resolver_mutation == "id":
                    row["id"] = "unexpected-memory-id"
                elif self.resolver_mutation == "schema":
                    row.pop("supportKind")
                resolutions.append(row)
            return _completed_classifier_step(
                content=json.dumps({"resolutions": resolutions}),
                usage=None,
            )
        if "admission auditor" in system:
            self.call_kinds.append("challenger")
            if self.malformed_challenger:
                return _completed_classifier_step(
                    content=json.dumps(
                        {
                            "audits": [
                                {
                                    "id": pair["id"],
                                    "admit": "false",
                                    "confidence": self.challenger_confidence,
                                    "reasonCode": self.challenger_reason,
                                }
                                for pair in pairs
                            ]
                        }
                    ),
                    usage=None,
                )
            return _completed_classifier_step(
                content=json.dumps(
                    {
                        "audits": [
                            {
                                "id": pair["id"],
                                "admit": False,
                                "confidence": self.challenger_confidence,
                                "reasonCode": self.challenger_reason,
                            }
                            for pair in pairs
                        ]
                    }
                ),
                usage=None,
            )
        self.call_kinds.append("verifier")
        return _completed_classifier_step(
            content=json.dumps(
                {
                    "decisions": [
                        {
                            "id": pair["id"],
                            "decision": "relevant",
                            "confidence": 0.99,
                            "objectMatch": True,
                            "relationSupported": True,
                            "reasonCode": self.primary_reason,
                        }
                        for pair in pairs
                    ]
                }
            ),
            usage=None,
        )


def _proof_resolver_provider(
    model: _ScriptedDisagreementResolver,
) -> HybridRuntimeProvider:
    return HybridRuntimeProvider(
        encoder=_FakeEncoder(),
        model_adapter=model,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        challenger_minimum_confidence=0.8,
        resolver_minimum_confidence=0.9,
    )


def test_resolver_recovers_exact_file_grounded_disagreement() -> None:
    entry = _memory("file-grounded-retry")
    entry.content = (
        "downloads/chunk_window.py starts its retry window at the server receipt "
        "timestamp, not the local request timestamp."
    )
    entry.metadata = {"path": "downloads/chunk_window.py"}
    model = _ScriptedDisagreementResolver(
        challenger_reason="query_underspecified",
        support_kind="exact_file_context",
        query_evidence="Fix this retry timeout.",
        memory_evidence="downloads/chunk_window.py starts its retry window",
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="Fix this retry timeout.",
            current_files=("downloads/chunk_window.py",),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "resolver_proven_relevant" in result.signals[0].reason_codes
    assert result.diagnostics["resolver_disagreement_count"] == 1
    assert result.diagnostics["resolver_accepted_count"] == 1
    assert result.diagnostics["challenger_accepted_count"] == 0
    assert result.diagnostics["semantic_accepted_count"] == 1
    assert result.diagnostics["accepted_count"] == 1
    assert model.call_kinds == ["verifier", "challenger", "resolver"]


def test_resolver_recovers_cross_language_equivalence_disagreement() -> None:
    entry = _memory("cross-language-checkout")
    entry.content = (
        "The payment-authentication state returns to the pending cart checkout "
        "step after authentication."
    )
    entry.metadata = {
        "relation": "cross_language_equivalence",
        "alias_from": "支付认证结束后回到原来的购物车结算步骤",
        "alias_to": (
            "the payment-authentication state returns to the pending cart "
            "checkout step after authentication"
        ),
    }
    model = _ScriptedDisagreementResolver(
        challenger_reason="different_object",
        support_kind="cross_language_equivalence",
        query_evidence="支付认证结束后回到原来的购物车结算步骤",
        memory_evidence="payment-authentication state returns to the pending cart checkout",
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="支付认证结束后回到原来的购物车结算步骤。",
            current_files=(),
            active_domains=("security",),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "resolver_proven_relevant" in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier", "challenger", "resolver"]


@pytest.mark.parametrize(
    ("primary_reason", "expected_rejection"),
    (
        ("cause", "semantic_unverified_cause"),
        ("recovery", "semantic_unverified_recovery"),
    ),
)
def test_unverified_causal_vote_cannot_pass_via_challenger_consent(
    primary_reason: str,
    expected_rejection: str,
) -> None:
    class UnverifiedCausalModel:
        model_id = "deepseek-chat"

        def __init__(self) -> None:
            self.call_kinds: list[str] = []
            self.seen_authority: list[tuple[bool, bool]] = []

        def next(self, messages):
            pairs = json.loads(messages[1]["content"])["pairs"]
            if "admission auditor" in messages[0]["content"]:
                self.call_kinds.append("challenger")
                return _completed_classifier_step(
                    content=json.dumps(
                        {
                            "audits": [
                                {
                                    "id": pair["id"],
                                    "admit": True,
                                    "confidence": 0.99,
                                    "reasonCode": "no_disqualifier",
                                }
                                for pair in pairs
                            ]
                        }
                    ),
                    usage=None,
                )
            self.call_kinds.append("verifier")
            self.seen_authority.extend(
                (
                    pair["memory"]["verifiedCauseAuthority"],
                    pair["memory"]["verifiedRecoveryAuthority"],
                )
                for pair in pairs
            )
            return _completed_classifier_step(
                content=json.dumps(
                    {
                        "decisions": [
                            {
                                "id": pair["id"],
                                "decision": "relevant",
                                "confidence": 0.99,
                                "objectMatch": True,
                                "relationSupported": True,
                                "reasonCode": primary_reason,
                            }
                            for pair in pairs
                        ]
                    }
                ),
                usage=None,
            )

    entry = _memory(f"unverified-{primary_reason}")
    entry.content = "Checkout worker retry resets its failed lease."
    entry.provenance = {}
    model = UnverifiedCausalModel()

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="Recover the checkout worker after its failed lease.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert expected_rejection in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier"]
    assert model.seen_authority == [(False, False)]


@pytest.mark.parametrize(
    ("primary_reason", "provenance", "expected_accepted", "authorities"),
    (
        (
            "cause",
            {"claim_type": "root_cause", "evidence_kind": "verified_root_cause"},
            True,
            (True, False),
        ),
        (
            "cause",
            {"claim_type": "recovery", "evidence_kind": "verified_recovery"},
            False,
            (False, True),
        ),
        (
            "recovery",
            {"claim_type": "recovery", "evidence_kind": "verified_recovery"},
            True,
            (False, True),
        ),
        (
            "recovery",
            {"claim_type": "root_cause", "evidence_kind": "verified_root_cause"},
            False,
            (True, False),
        ),
        (
            "recovery",
            {
                "claim_type": "approach",
                "evidence_kind": "verified_recovery",
                "durable_signals": ["verified_solution"],
            },
            False,
            (False, False),
        ),
        (
            "cause",
            {"durable_signals": ["confirmed_error_recovery_verified"]},
            False,
            (False, False),
        ),
        (
            "cause",
            {
                "claim_type": "root_cause",
                "durable_signals": ["confirmed_error_recovery_verified"],
            },
            True,
            (True, False),
        ),
        (
            "cause",
            {
                "claim_type": "root_cause",
                "evidence_kind": "verified_recovery",
                "durable_signals": ["confirmed_error_recovery_verified"],
            },
            False,
            (False, False),
        ),
        (
            "recovery",
            {
                "claim_type": "recovery",
                "evidence_kind": "verified_root_cause",
                "durable_signals": ["verified_solution"],
            },
            False,
            (False, False),
        ),
        (
            "recovery",
            {
                "claim_type": "recovery",
                "evidence_kind": "unknown",
                "durable_signals": ["verified_solution"],
            },
            False,
            (False, False),
        ),
    ),
)
def test_causal_consent_requires_matching_typed_authority(
    primary_reason: str,
    provenance: dict[str, object],
    expected_accepted: bool,
    authorities: tuple[bool, bool],
) -> None:
    class TypedCausalModel:
        model_id = "deepseek-chat"

        def __init__(self) -> None:
            self.call_kinds: list[str] = []
            self.seen_authorities: list[tuple[bool, bool]] = []

        def next(self, messages):
            pairs = json.loads(messages[1]["content"])["pairs"]
            if "admission auditor" in messages[0]["content"]:
                self.call_kinds.append("challenger")
                return _completed_classifier_step(
                    content=json.dumps(
                        {
                            "audits": [
                                {
                                    "id": pair["id"],
                                    "admit": True,
                                    "confidence": 0.99,
                                    "reasonCode": "no_disqualifier",
                                }
                                for pair in pairs
                            ]
                        }
                    ),
                    usage=None,
                )
            self.call_kinds.append("verifier")
            self.seen_authorities.extend(
                (
                    pair["memory"]["verifiedCauseAuthority"],
                    pair["memory"]["verifiedRecoveryAuthority"],
                )
                for pair in pairs
            )
            return _completed_classifier_step(
                content=json.dumps(
                    {
                        "decisions": [
                            {
                                "id": pair["id"],
                                "decision": "relevant",
                                "confidence": 0.99,
                                "objectMatch": True,
                                "relationSupported": True,
                                "reasonCode": primary_reason,
                            }
                            for pair in pairs
                        ]
                    }
                ),
                usage=None,
            )

    entry = _memory(f"typed-{primary_reason}")
    entry.content = "Checkout worker lease handling is authoritative."
    entry.provenance = provenance
    model = TypedCausalModel()

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="Recover the checkout worker after its failed lease.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is expected_accepted
    assert model.seen_authorities == [authorities]
    assert model.call_kinds == (
        ["verifier", "challenger"] if expected_accepted else ["verifier"]
    )


@pytest.mark.parametrize(
    "support_kind",
    ("semantic_equivalence", "cross_language_equivalence"),
)
def test_resolver_cannot_override_different_object_without_bound_evidence(
    support_kind: str,
) -> None:
    entry = _memory("unrelated-resolver-claim")
    entry.content = "Delete the production database replicas."
    model = _ScriptedDisagreementResolver(
        challenger_reason="different_object",
        support_kind=support_kind,
        query_evidence="Format UI header comments",
        memory_evidence="Delete the production database replicas",
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="Format UI header comments.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier", "challenger", "resolver"]


def test_cross_language_resolver_rejects_a_narrow_alias_over_unrelated_actions() -> None:
    entry = _memory("narrow-cross-language-alias")
    entry.content = "Delete payment database replicas."
    entry.metadata = {
        "relation": "cross_language_equivalence",
        "alias_from": "支付",
        "alias_to": "payment",
    }
    model = _ScriptedDisagreementResolver(
        primary_reason="alias",
        challenger_reason="different_object",
        support_kind="cross_language_equivalence",
        query_evidence="支付页面需要格式化标题",
        memory_evidence="Delete payment database replicas",
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="支付页面需要格式化标题。",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


@pytest.mark.parametrize(
    ("query", "content"),
    (
        (
            "Checkout database cache must remain durable.",
            "Checkout database cache is never durable.",
        ),
        (
            "Investigate checkout database latency.",
            "Checkout database replica retention policy.",
        ),
    ),
)
def test_semantic_resolver_requires_an_exact_cited_clause(
    query: str,
    content: str,
) -> None:
    entry = _memory("unsupported-semantic-resolution")
    entry.content = content
    model = _ScriptedDisagreementResolver(
        challenger_reason="unsupported_relation",
        support_kind="semantic_equivalence",
        query_evidence=query.rstrip("."),
        memory_evidence=content.rstrip("."),
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query=query,
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


@pytest.mark.parametrize(
    ("provenance", "expected_accepted"),
    (
        ({}, False),
        ({"approval_basis": "not_verified_solution"}, False),
        ({"approval_basis": "verified_solution"}, False),
        (
            {"claim_type": "recovery", "approval_basis": "verified_solution"},
            True,
        ),
        ({"evidence_kind": "verified_recovery"}, True),
        ({"evidence_kind": "verified_root_cause"}, False),
        ({"durable_signals": ["confirmed_error_recovery_verified"]}, False),
    ),
)
def test_resolver_requires_authority_provenance_for_causal_recovery(
    provenance: dict[str, object],
    expected_accepted: bool,
) -> None:
    entry = _memory("process-exit-recovery")
    entry.content = (
        "Suite cleanup shuts down the metrics HTTP exporter before joining its "
        "background thread."
    )
    entry.provenance = provenance
    model = _ScriptedDisagreementResolver(
        primary_reason="recovery",
        challenger_reason="unsupported_relation",
        support_kind="verified_recovery",
        query_evidence="metrics HTTP exporter process remains alive after all checks",
        memory_evidence="shuts down the metrics HTTP exporter",
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query=(
                "The integration command says done but the metrics HTTP exporter "
                "process remains alive after all checks."
            ),
            current_files=(),
            active_domains=("testing",),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is expected_accepted
    if expected_accepted:
        assert result.diagnostics["resolver_disagreement_count"] == 1
        assert result.diagnostics["resolver_accepted_count"] == 1
        assert "resolver_proven_relevant" in result.signals[0].reason_codes
    else:
        assert result.diagnostics["resolver_disagreement_count"] == 0
        assert result.diagnostics["resolver_accepted_count"] == 0
        assert "semantic_unverified_recovery" in result.signals[0].reason_codes


def test_verified_root_cause_disagreement_remains_fail_closed_without_typed_resolver_support(
) -> None:
    entry = _memory("verified-root-cause-disagreement")
    entry.content = (
        "Checkout worker fails because stale lease ownership blocks renewal."
    )
    entry.provenance = {
        "claim_type": "root_cause",
        "evidence_kind": "verified_root_cause",
    }
    model = _ScriptedDisagreementResolver(
        primary_reason="cause",
        challenger_reason="unsupported_relation",
        support_kind="verified_recovery",
        query_evidence="Checkout worker fails after lease renewal",
        memory_evidence="Checkout worker fails because stale lease ownership",
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="Checkout worker fails after lease renewal.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes
    assert model.call_kinds == ["verifier", "challenger", "resolver"]


def test_verified_recovery_resolver_still_requires_a_shared_concrete_object() -> None:
    entry = _memory("unrelated-verified-recovery")
    entry.content = (
        "The thumbnail image worker recovers a timeout by resetting its render "
        "queue."
    )
    entry.provenance = {"evidence_kind": "verified_recovery"}
    model = _ScriptedDisagreementResolver(
        primary_reason="recovery",
        challenger_reason="different_object",
        support_kind="verified_recovery",
        query_evidence="checkout database crashed after commit",
        memory_evidence="thumbnail image worker recovers a timeout",
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="The checkout database crashed after commit.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


@pytest.mark.parametrize(
    ("support_kind", "primary_reason", "memory_evidence"),
    (
        ("verified_recovery", "recovery", "Database recovery resets"),
        ("explicit_constraint", "constraint", "db/config.py"),
        ("explicit_configuration", "configuration", "db/config.py"),
    ),
)
def test_resolver_relation_cannot_be_grounded_only_by_unrelated_file_metadata(
    support_kind: str,
    primary_reason: str,
    memory_evidence: str,
) -> None:
    entry = _memory(f"unrelated-{support_kind}")
    entry.content = "Database recovery resets the replica checkpoint."
    entry.metadata = {"path": "db/config.py", "config_key": "replica_mode"}
    entry.provenance = {"evidence_kind": "verified_recovery"}
    model = _ScriptedDisagreementResolver(
        primary_reason=primary_reason,
        challenger_reason="different_object",
        support_kind=support_kind,
        query_evidence="ui/components/header.tsx",
        memory_evidence=memory_evidence,
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="Format interface comments only.",
            current_files=("ui/components/header.tsx",),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


@pytest.mark.parametrize(
    ("support_kind", "primary_reason"),
    (
        ("verified_recovery", "recovery"),
        ("explicit_constraint", "constraint"),
        ("explicit_configuration", "configuration"),
    ),
)
def test_resolver_typed_relation_requires_cited_task_relation_evidence(
    support_kind: str,
    primary_reason: str,
) -> None:
    entry = _memory(f"unrelated-task-{support_kind}")
    entry.content = "Database recovery resets the replica checkpoint."
    entry.provenance = {"evidence_kind": "verified_recovery"}
    model = _ScriptedDisagreementResolver(
        primary_reason=primary_reason,
        challenger_reason="different_object",
        support_kind=support_kind,
        query_evidence="Format interface comments only",
        memory_evidence="Database recovery resets",
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="Format interface comments only.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


@pytest.mark.parametrize(
    ("support_kind", "primary_reason", "text"),
    (
        (
            "explicit_constraint",
            "constraint",
            "Checkout uploads must keep the retry receipt.",
        ),
        (
            "explicit_configuration",
            "configuration",
            "Enable checkout retry backoff.",
        ),
    ),
)
def test_resolver_typed_relation_accepts_bound_task_content_evidence(
    support_kind: str,
    primary_reason: str,
    text: str,
) -> None:
    entry = _memory(f"bound-task-{support_kind}")
    entry.content = text
    model = _ScriptedDisagreementResolver(
        primary_reason=primary_reason,
        challenger_reason="different_object",
        support_kind=support_kind,
        query_evidence=text.rstrip("."),
        memory_evidence=text.rstrip("."),
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query=text,
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "resolver_proven_relevant" in result.signals[0].reason_codes


@pytest.mark.parametrize(
    ("support_kind", "primary_reason", "query", "content"),
    (
        (
            "explicit_constraint",
            "constraint",
            "Checkout uploads must encrypt receipts.",
            "Checkout database must delete replicas.",
        ),
        (
            "explicit_configuration",
            "configuration",
            "Enable checkout image cache.",
            "Enable checkout database deletion.",
        ),
    ),
)
def test_resolver_typed_relation_cannot_bind_on_one_topic_word(
    support_kind: str,
    primary_reason: str,
    query: str,
    content: str,
) -> None:
    entry = _memory(f"topic-only-{support_kind}")
    entry.content = content
    model = _ScriptedDisagreementResolver(
        primary_reason=primary_reason,
        challenger_reason="different_object",
        support_kind=support_kind,
        query_evidence=query.rstrip("."),
        memory_evidence=content.rstrip("."),
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query=query,
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


@pytest.mark.parametrize(
    ("category", "metadata"),
    (
        ("anti-preference-example", {}),
        ("architecture", {"kind": "not_preference"}),
    ),
)
def test_resolver_preference_authority_rejects_substring_spoofs(
    category: str,
    metadata: dict[str, str],
) -> None:
    entry = _memory("spoofed-preference")
    entry.category = category
    entry.content = "Use concise checkout diagnostics."
    entry.metadata = metadata
    model = _ScriptedDisagreementResolver(
        primary_reason="preference",
        challenger_reason="different_object",
        support_kind="established_preference",
        query_evidence="Use concise checkout diagnostics",
        memory_evidence="Use concise checkout diagnostics",
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="Use concise checkout diagnostics.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


def test_resolver_preference_authority_accepts_exact_category() -> None:
    entry = _memory("established-preference")
    entry.category = "preference"
    entry.content = "Use concise checkout diagnostics."
    model = _ScriptedDisagreementResolver(
        primary_reason="preference",
        challenger_reason="different_object",
        support_kind="established_preference",
        query_evidence="Use concise checkout diagnostics",
        memory_evidence="Use concise checkout diagnostics",
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="Use concise checkout diagnostics.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=(entry.id,),
    )

    assert result is not None
    assert result.signals[0].accepted is True
    assert "resolver_proven_relevant" in result.signals[0].reason_codes


def test_resolver_preference_authority_still_requires_the_same_object() -> None:
    entry = _memory("unrelated-established-preference")
    entry.category = "preference"
    entry.content = "Use verbose database migration logs."
    model = _ScriptedDisagreementResolver(
        primary_reason="preference",
        challenger_reason="different_object",
        support_kind="established_preference",
        query_evidence="Use concise checkout diagnostics",
        memory_evidence="Use verbose database migration logs",
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="Use concise checkout diagnostics.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "resolver_proven_relevant" not in result.signals[0].reason_codes
    assert "resolver_support_not_authorized" in result.signals[0].reason_codes


def test_adjudication_cache_binds_resolver_authority_provenance() -> None:
    entry = _memory("process-exit-cache-authority")
    entry.content = (
        "Suite cleanup shuts down the metrics HTTP exporter before joining its "
        "background thread."
    )
    entry.provenance = {"evidence_kind": "verified_recovery"}
    model = _ScriptedDisagreementResolver(
        primary_reason="recovery",
        challenger_reason="unsupported_relation",
        support_kind="verified_recovery",
        query_evidence="metrics HTTP exporter process remains alive after all checks",
        memory_evidence="shuts down the metrics HTTP exporter",
    )
    provider = _proof_resolver_provider(model)
    request = SimpleNamespace(
        query=(
            "The integration command says done but the metrics HTTP exporter "
            "process remains alive after all checks."
        ),
        current_files=(),
        active_domains=("testing",),
    )

    authorized = provider.adjudicate(
        request=request,
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )
    entry.provenance = {}
    revoked = provider.adjudicate(
        request=request,
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert authorized is not None and authorized.signals[0].accepted is True
    assert revoked is not None and revoked.signals[0].accepted is False
    assert revoked.diagnostics["cache_hit"] is False
    assert model.call_kinds == [
        "verifier",
        "challenger",
        "resolver",
        "verifier",
    ]


def test_deterministic_veto_cannot_reach_resolver() -> None:
    entry = _memory("ordered-publication")
    entry.content = "Publish success before committing the result transaction."
    model = _ScriptedDisagreementResolver(
        challenger_reason="contradictory_order",
        support_kind="semantic_equivalence",
        query_evidence="after the result transaction commits",
        memory_evidence="before committing the result transaction",
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="Publish success only after the result transaction commits.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "deterministic_operation_order_conflict" in result.signals[0].reason_codes
    assert result.diagnostics["resolver_disagreement_count"] == 0
    assert model.call_kinds == []


def test_low_confidence_challenger_veto_cannot_reach_resolver() -> None:
    entry = _memory("low-confidence-veto")
    model = _ScriptedDisagreementResolver(
        challenger_reason="different_object",
        challenger_confidence=0.79,
        support_kind="semantic_equivalence",
        query_evidence="low confidence veto",
        memory_evidence="low-confidence-veto",
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="Apply the low confidence veto rule.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert "challenge_below_confidence" in result.signals[0].reason_codes
    assert result.diagnostics["resolver_disagreement_count"] == 0
    assert model.call_kinds == ["verifier", "challenger"]


def test_malformed_challenger_fails_closed_without_calling_resolver() -> None:
    entry = _memory("malformed-challenger")
    model = _ScriptedDisagreementResolver(
        challenger_reason="different_object",
        support_kind="semantic_equivalence",
        query_evidence="malformed challenger",
        memory_evidence="malformed-challenger",
        malformed_challenger=True,
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="Apply the malformed challenger rule.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is None
    assert model.call_kinds == ["verifier", "challenger"]


@pytest.mark.parametrize(
    "resolver_mutation",
    ("query_substring", "memory_substring", "id", "schema"),
)
def test_invalid_resolver_proof_fails_closed(
    resolver_mutation: str,
) -> None:
    entry = _memory("strict-resolver-proof")
    model = _ScriptedDisagreementResolver(
        challenger_reason="different_object",
        support_kind="semantic_equivalence",
        query_evidence="strict resolver proof",
        memory_evidence="strict-resolver-proof",
        resolver_mutation=resolver_mutation,
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="Apply the strict resolver proof rule.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is None
    assert model.call_kinds == ["verifier", "challenger", "resolver"]


def test_low_confidence_resolver_proof_fails_closed() -> None:
    entry = _memory("low-confidence-resolver")
    model = _ScriptedDisagreementResolver(
        challenger_reason="different_object",
        resolver_confidence=0.89,
        support_kind="semantic_equivalence",
        query_evidence="low confidence resolver",
        memory_evidence="low-confidence-resolver",
    )

    result = _proof_resolver_provider(model).adjudicate(
        request=SimpleNamespace(
            query="Apply the low confidence resolver rule.",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset(),
    )

    assert result is not None
    assert result.signals[0].accepted is False
    assert result.diagnostics["resolver_disagreement_count"] == 1
    assert result.diagnostics["resolver_accepted_count"] == 0
    assert model.call_kinds == ["verifier", "challenger", "resolver"]


def _relevant_decision(reason: str = "semantic_equivalence") -> dict[str, object]:
    return {
        "decision": "relevant",
        "confidence": 0.99,
        "objectMatch": True,
        "relationSupported": True,
        "reasonCode": reason,
    }


def _contested_audit(reason: str = "different_object") -> dict[str, object]:
    return {
        "admit": False,
        "confidence": 0.99,
        "reasonCode": reason,
    }


def _relevant_resolution_row(pair: dict[str, object]) -> dict[str, object]:
    query_segments = pair["queryEvidenceSegments"]
    memory_segments = pair["memoryEvidenceSegments"]
    assert isinstance(query_segments, list)
    assert isinstance(memory_segments, list)
    return {
        "id": pair["id"],
        "decision": "relevant",
        "confidence": 0.99,
        "queryGrounded": True,
        "objectMatch": True,
        "relationSupported": True,
        "supportKind": "semantic_equivalence",
        "disqualifier": "none",
        "queryEvidenceId": query_segments[0]["id"],
        "memoryEvidenceId": memory_segments[0]["id"],
    }


def test_resolver_segment_ids_are_globally_unique_and_pair_bound() -> None:
    request = SimpleNamespace(
        query="Apply the checkout retry ownership rule.",
        current_files=(),
        active_domains=("testing",),
    )
    entries = (_memory("pair-alpha"), _memory("pair-beta"))
    decisions = {entry.id: _relevant_decision() for entry in entries}
    audits = {entry.id: _contested_audit() for entry in entries}

    pairs, expected = _build_resolver_batch(
        [(request, entry) for entry in entries],
        decisions=decisions,
        audits=audits,
    )

    evidence_ids_by_pair: dict[str, set[str]] = {}
    all_evidence_ids: list[str] = []
    for pair in pairs:
        pair_id = str(pair["id"])
        evidence_ids = {
            segment["id"]
            for side in ("queryEvidenceSegments", "memoryEvidenceSegments")
            for segment in pair[side]
        }
        evidence_ids_by_pair[pair_id] = evidence_ids
        all_evidence_ids.extend(evidence_ids)
        expected_ids = {
            *expected[pair_id]["query_segments"],
            *expected[pair_id]["memory_segments"],
        }
        assert evidence_ids == expected_ids
        assert expected[pair_id]["contested_reason"] == "different_object"
    assert len(all_evidence_ids) == len(set(all_evidence_ids))
    assert evidence_ids_by_pair[entries[0].id].isdisjoint(
        evidence_ids_by_pair[entries[1].id]
    )

    resolutions = [_relevant_resolution_row(pair) for pair in pairs]
    parsed = _parse_resolutions(
        json.dumps({"resolutions": resolutions}),
        expected=expected,
    )
    assert {row["contestedReason"] for row in parsed} == {"different_object"}

    echoed = [dict(row) for row in resolutions]
    echoed[0]["contestedReason"] = "different_object"
    with pytest.raises(ValueError, match="resolution is malformed"):
        _parse_resolutions(
            json.dumps({"resolutions": echoed}),
            expected=expected,
        )

    resolutions[0]["queryEvidenceId"] = resolutions[1]["queryEvidenceId"]
    with pytest.raises(ValueError, match="proof segment ID is invalid"):
        _parse_resolutions(
            json.dumps({"resolutions": resolutions}),
            expected=expected,
        )


@pytest.mark.parametrize(
    (
        "case_name",
        "support_kind",
        "primary_reason",
        "query",
        "current_files",
        "metadata",
        "category",
        "provenance",
        "query_kind",
        "memory_kind",
    ),
    (
        (
            "recovery_without_authority_provenance",
            "verified_recovery",
            "recovery",
            "Recover the checkout worker after a crash.",
            (),
            {},
            "architecture",
            {},
            "query.task",
            "memory.content",
        ),
        (
            "exact_file_without_shared_path",
            "exact_file_context",
            "semantic_equivalence",
            "Fix the current checkout worker.",
            ("src/current_checkout.py",),
            {"path": "src/other_checkout.py"},
            "architecture",
            {},
            "query.current_files",
            "memory.metadata",
        ),
        (
            "rename_without_migration_authority",
            "verified_rename",
            "rename",
            "Use the renamed checkout worker.",
            ("src/new_checkout.py",),
            {
                "renamed_from": "src/old_checkout.py",
                "renamed_to": "src/new_checkout.py",
            },
            "architecture",
            {},
            "query.current_files",
            "memory.metadata",
        ),
        (
            "preference_without_user_authority",
            "established_preference",
            "preference",
            "Format checkout diagnostics consistently.",
            (),
            {},
            "architecture",
            {},
            "query.task",
            "memory.content",
        ),
        (
            "support_kind_with_wrong_segment_kinds",
            "semantic_equivalence",
            "semantic_equivalence",
            "Apply the checkout ownership rule.",
            ("src/checkout.py",),
            {"path": "src/checkout.py"},
            "architecture",
            {},
            "query.current_files",
            "memory.metadata",
        ),
    ),
)
def test_resolver_support_kind_requires_matching_authority(
    case_name: str,
    support_kind: str,
    primary_reason: str,
    query: str,
    current_files: tuple[str, ...],
    metadata: dict[str, object],
    category: str,
    provenance: dict[str, object],
    query_kind: str,
    memory_kind: str,
) -> None:
    entry = _memory(f"authority-{case_name}")
    entry.category = category
    entry.metadata = metadata
    entry.provenance = provenance
    request = SimpleNamespace(
        query=query,
        current_files=current_files,
        active_domains=(),
    )
    resolution = {
        "decision": "relevant",
        "confidence": 0.99,
        "queryGrounded": True,
        "objectMatch": True,
        "relationSupported": True,
        "supportKind": support_kind,
        "disqualifier": "none",
        "querySegmentKind": query_kind,
        "memorySegmentKind": memory_kind,
    }

    assert not _resolution_is_approved(
        resolution,
        decision=_relevant_decision(primary_reason),
        entry=entry,
        request=request,
        minimum_confidence=0.9,
    ), case_name


def test_primary_parser_rejects_duplicate_json_keys() -> None:
    duplicate = (
        '{"decisions":[{"id":"entry","decision":"irrelevant",'
        '"decision":"relevant","confidence":0.99,"objectMatch":true,'
        '"relationSupported":true,"reasonCode":"semantic_equivalence"}]}'
    )

    with pytest.raises(ValueError, match="duplicate key"):
        _parse_decisions(duplicate, ["entry"])


@pytest.mark.parametrize(
    ("decision", "reason", "object_match", "relation_supported"),
    (
        ("relevant", "opposite", True, True),
        ("relevant", "different_object", True, True),
        ("relevant", "constraint", False, True),
        ("irrelevant", "constraint", False, False),
        ("irrelevant", "unrelated", True, True),
    ),
)
def test_primary_parser_rejects_semantically_inconsistent_rows(
    decision: str,
    reason: str,
    object_match: bool,
    relation_supported: bool,
) -> None:
    payload = {
        "decisions": [
            {
                "id": "entry",
                "decision": decision,
                "confidence": 0.99,
                "objectMatch": object_match,
                "relationSupported": relation_supported,
                "reasonCode": reason,
            }
        ]
    }

    with pytest.raises(ValueError, match="decision is malformed"):
        _parse_decisions(json.dumps(payload), ["entry"])


def test_challenger_parser_rejects_duplicate_json_keys() -> None:
    duplicate = (
        '{"audits":[{"id":"entry","admit":false,"admit":true,'
        '"confidence":0.99,"reasonCode":"no_disqualifier"}]}'
    )

    with pytest.raises(ValueError, match="duplicate key"):
        _parse_audits(duplicate, ["entry"])


def test_challenger_parser_rejects_negative_vote_without_a_disqualifier() -> None:
    payload = {
        "audits": [
            {
                "id": "entry",
                "admit": False,
                "confidence": 0.99,
                "reasonCode": "no_disqualifier",
            }
        ]
    }

    with pytest.raises(ValueError, match="audit is malformed"):
        _parse_audits(json.dumps(payload), ["entry"])


@pytest.mark.parametrize(
    "reason",
    ("contradictory_order", "contradictory_polarity", "path_conflict"),
)
def test_resolver_batch_rejects_non_overridable_veto_before_model(reason: str) -> None:
    entry = _memory("non-overridable-veto")
    request = SimpleNamespace(
        query="Apply the concrete release transaction rule.",
        current_files=(),
        active_domains=(),
    )
    decisions = {
        entry.id: {
            "id": entry.id,
            "decision": "relevant",
            "confidence": 0.99,
            "objectMatch": True,
            "relationSupported": True,
            "reasonCode": "constraint",
        }
    }
    audits = {
        entry.id: {
            "id": entry.id,
            "admit": False,
            "confidence": 0.99,
            "reasonCode": reason,
        }
    }

    with pytest.raises(ValueError, match="ineligible disagreement"):
        _build_resolver_batch(
            [(request, entry)],
            decisions=decisions,
            audits=audits,
        )


def test_resolver_parser_rejects_duplicate_json_keys() -> None:
    duplicate = '{"resolutions":[{"id":"entry","id":"other"}]}'

    with pytest.raises(ValueError, match="duplicate key"):
        _parse_resolutions(duplicate, expected={"entry": {}})


@pytest.mark.parametrize(
    ("line_length", "expected_segment_count"),
    ((479, 1), (480, 1), (481, 2)),
)
def test_proof_segment_span_roundtrip_at_chunk_boundaries(
    line_length: int,
    expected_segment_count: int,
) -> None:
    document = "task: " + ("x" * (line_length - len("task: ")))

    segments = _proof_segments(document, "boundary-q", side="query")

    assert len(segments) == expected_segment_count
    assert "".join(segment["text"] for segment in segments) == document
    for segment in segments:
        assert document[segment["start"] : segment["end"]] == segment["text"]


def test_proof_segments_render_default_ignorables_and_bidi_controls_visibly() -> None:
    raw = "checkout\u200bworker\u202eowner"
    normalized = _normalized(raw, 256)
    document = f"task: {normalized}"

    segments = _proof_segments(document, "unicode-q", side="query")

    rendered = "".join(segment["text"] for segment in segments)
    assert "<U+200B>" in rendered
    assert "<U+202E>" in rendered
    assert "\u200b" not in rendered
    assert "\u202e" not in rendered
    for segment in segments:
        assert document[segment["start"] : segment["end"]] == segment["text"]


def _assert_resolver_input_budget_rejected_before_model_call(
    *,
    request: SimpleNamespace,
    entries: list[MemoryEntry],
) -> None:
    model = _CountingVerifier()
    provider = _proof_resolver_provider(model)
    decisions = {entry.id: _relevant_decision() for entry in entries}
    audits = {entry.id: _contested_audit() for entry in entries}

    resolutions, usage = provider._resolve_disagreements(
        request=request,
        entries=entries,
        decisions=decisions,
        audits=audits,
    )

    assert model.calls == 0
    assert usage["model_call_count"] == 0
    assert usage["input_rejected_count"] == len(entries)
    assert set(resolutions) == {entry.id for entry in entries}
    assert all(
        row["runtimeReason"] == "resolver_input_budget_rejected"
        and row["decision"] == "irrelevant"
        for row in resolutions.values()
    )


def test_resolver_segment_budget_rejects_before_model_call() -> None:
    request = SimpleNamespace(
        query="q" * 2_000,
        current_files=tuple(f"src/{'f' * 240}{index}.py" for index in range(16)),
        active_domains=tuple(f"domain-{'d' * 240}{index}" for index in range(16)),
    )

    _assert_resolver_input_budget_rejected_before_model_call(
        request=request,
        entries=[_memory("segment-budget")],
    )


def test_resolver_per_pair_budget_rejects_before_model_call() -> None:
    entry = _memory("pair-budget")
    entry.content = "m" * 4_000
    entry.tags = [("t" * 250) + str(index) for index in range(8)]
    request = SimpleNamespace(
        query="q" * 2_000,
        current_files=tuple(f"src/{'f' * 240}{index}.py" for index in range(16)),
        active_domains=(),
    )

    _assert_resolver_input_budget_rejected_before_model_call(
        request=request,
        entries=[entry],
    )


def test_resolver_batch_budget_splits_without_poisoning_candidates() -> None:
    entries = [_memory(f"batch-budget-{index}") for index in range(20)]
    for index, entry in enumerate(entries):
        entry.content = ("m" * 3_990) + str(index)
        entry.tags = []
    request = SimpleNamespace(
        query="q" * 2_000,
        current_files=(),
        active_domains=(),
    )

    model = _ScriptedDisagreementResolver(
        query_evidence="qqqq",
        memory_evidence="mmmm",
    )
    provider = _proof_resolver_provider(model)
    decisions = {entry.id: _relevant_decision() for entry in entries}
    audits = {entry.id: _contested_audit() for entry in entries}

    resolutions, usage = provider._resolve_disagreements(
        request=request,
        entries=entries,
        decisions=decisions,
        audits=audits,
    )

    assert set(resolutions) == {entry.id for entry in entries}
    assert usage["input_rejected_count"] == 0
    assert usage["model_call_count"] == len(model.call_kinds)
    assert usage["model_call_count"] > 1
    assert set(model.call_kinds) == {"resolver"}


def test_oversized_resolver_candidate_cannot_poison_valid_neighbour() -> None:
    oversized = _memory("oversized-resolver-input")
    oversized.content = "Apply the checkout ownership retry rule. " + ("m" * 4_000)
    oversized.tags = [("tag" * 84) + str(index) for index in range(16)]
    valid = _memory("valid-resolver-neighbour")
    valid.content = "Apply the checkout ownership retry rule."
    model = _ScriptedDisagreementResolver(
        query_evidence="Apply the checkout ownership retry rule",
        memory_evidence="Apply the checkout ownership retry rule",
    )
    provider = _proof_resolver_provider(model)

    result = provider.adjudicate(
        request=SimpleNamespace(
            query="Apply the checkout ownership retry rule.",
            current_files=(),
            active_domains=(),
        ),
        entries=(oversized, valid),
        lexical_accepted_ids=(oversized.id, valid.id),
    )

    assert result is not None
    by_id = {signal.entry_id: signal for signal in result.signals}
    assert by_id[oversized.id].accepted is False
    assert by_id[oversized.id].reason_codes == ("hybrid_memory_input_budget",)
    assert by_id[valid.id].accepted is True
    assert "resolver_proven_relevant" in by_id[valid.id].reason_codes
    assert result.diagnostics["memory_input_budget_rejected_count"] == 1
    assert result.diagnostics["resolver_attempted_count"] == 1
    assert result.diagnostics["resolver_input_rejected_count"] == 0
    assert result.diagnostics["resolver_disagreement_count"] == 1
    assert result.diagnostics["resolver_accepted_count"] == 1
    assert result.diagnostics["resolver_model_call_count"] == 1
    assert result.diagnostics["model_call_count"] == 3
    assert result.diagnostics["accepted_count"] == 1
    assert model.call_kinds == ["verifier", "challenger", "resolver"]
    assert model.resolver_pair_ids == [(valid.id,)]
