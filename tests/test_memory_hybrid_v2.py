from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import json

import pytest

from minicode.memory import MemoryEntry, MemoryManager, MemoryScope
from minicode.memory_hybrid import (
    HYBRID_EVIDENCE_SCHEMA_VERSION,
    HYBRID_CHALLENGER_PROMPT_VERSION,
    HYBRID_CHALLENGER_SYSTEM_PROMPT,
    HYBRID_CHALLENGER_MODE,
    HYBRID_CHALLENGER_VETO_REASONS,
    HYBRID_PROTOCOL_VERSION,
    HybridAdjudication,
    HybridCandidateSignal,
    HYBRID_PROMPT_VERSION,
    HYBRID_QUERY_GATE_VERSION,
    HYBRID_SYSTEM_PROMPT,
    HybridActivation,
    evidence_fingerprint,
    assess_hybrid_activation,
)
from minicode.memory_hybrid_runtime import (
    HybridRuntimeProvider,
    LocalE5Encoder,
    create_hybrid_candidate_provider,
    _parse_decisions,
)
from minicode.embeddings import OpenAICompatibleEmbeddingEncoder
from minicode.memory_pipeline import MemoryPipeline
from minicode.memory_retrieval import (
    CanonicalMemoryRetriever,
    MemoryRetrievalRequest,
)


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
    assert (
        pipeline.stats["hybrid_inactive_reason"]
        == "remote_embedding_not_authorized"
    )


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
        "model": model_identity,
        "acceptance_gate": {"passed": True},
        "production_enablement_allowed": True,
        "dense_top_k": 20,
        "max_union_candidates": 32,
        "max_model_calls_per_task": 8,
        "query_gate_version": HYBRID_QUERY_GATE_VERSION,
        "verifier": {
            "prompt_version": HYBRID_PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(
                HYBRID_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "model_id": "deepseek-chat",
            "minimum_confidence": 0.85,
        },
        "challenger": {
            "prompt_version": HYBRID_CHALLENGER_PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(
                HYBRID_CHALLENGER_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "model_id": "deepseek-chat",
            "minimum_confidence": 0.9,
            "mode": HYBRID_CHALLENGER_MODE,
            "veto_reason_codes": sorted(HYBRID_CHALLENGER_VETO_REASONS),
        },
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
            return SimpleNamespace(
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
        return SimpleNamespace(
            content=json.dumps({"decisions": decisions}),
            usage=None,
        )


def _remote_runtime_evidence(client) -> dict:
    remote_identity = OpenAICompatibleEmbeddingEncoder(
        client, provider="qwen"
    ).identity
    return {
        "model": remote_identity,
        "verifier": {
            "prompt_version": HYBRID_PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(
                HYBRID_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "model_id": "deepseek-chat",
            "minimum_confidence": 0.85,
        },
        "challenger": {
            "prompt_version": HYBRID_CHALLENGER_PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(
                HYBRID_CHALLENGER_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "model_id": "deepseek-chat",
            "minimum_confidence": 0.8,
            "mode": HYBRID_CHALLENGER_MODE,
            "veto_reason_codes": sorted(HYBRID_CHALLENGER_VETO_REASONS),
        },
        "dense_top_k": 20,
        "max_union_candidates": 32,
        "max_model_calls_per_task": 8,
    }


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
        model_adapter=_CountingVerifier(),
        workspace_path=tmp_path,
        embedding_client_factory=lambda _workspace: client,
    )

    assert isinstance(provider, HybridRuntimeProvider)


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
        model_adapter=_CountingVerifier(),
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


def test_runtime_model_call_budget_fails_closed_on_new_query() -> None:
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
    exhausted = provider.adjudicate(
        request=SimpleNamespace(
            query="apply another concrete durable rule",
            current_files=(),
            active_domains=(),
        ),
        entries=(entry,),
        lexical_accepted_ids=frozenset({entry.id}),
    )

    assert first is not None
    assert exhausted is None
    assert verifier.calls == 2


@pytest.mark.parametrize(
    "query",
    (
        "Fix this recovery behavior.",
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
    assert len(result.signals) == 1
    assert result.signals[0].accepted is False
    assert result.signals[0].reason_codes == ("hybrid_query_underspecified",)


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


def test_challenger_vetoes_preliminary_opposite_order_match() -> None:
    class ContradictionChallenger(_CountingVerifier):
        def next(self, messages):
            if "admission auditor" not in messages[0]["content"]:
                return super().next(messages)
            self.calls += 1
            request = json.loads(messages[1]["content"])
            ids = [row["id"] for row in request["pairs"]]
            self.seen_ids.append(ids)
            return SimpleNamespace(
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


def test_challenger_non_conflict_diagnostic_cannot_revoke_admission() -> None:
    class ConservativeChallenger(_CountingVerifier):
        def next(self, messages):
            if "admission auditor" not in messages[0]["content"]:
                return super().next(messages)
            self.calls += 1
            request = json.loads(messages[1]["content"])
            ids = [row["id"] for row in request["pairs"]]
            self.seen_ids.append(ids)
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "audits": [
                            {
                                "id": entry_id,
                                "admit": False,
                                "confidence": 0.99,
                                "reasonCode": "unsupported_relation",
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
    assert result.signals[0].accepted is True
    assert "challenge_unsupported_relation" in result.signals[0].reason_codes


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
        "model": model_identity,
        "acceptance_gate": {"passed": True},
        "production_enablement_allowed": True,
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


def test_qwen_activation_requires_its_own_allowlisted_remote_identity(
    tmp_path: Path,
) -> None:
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
        "model": remote_identity,
        "acceptance_gate": {"passed": True},
        "production_enablement_allowed": True,
        "dense_top_k": 20,
        "max_union_candidates": 32,
        "max_model_calls_per_task": 8,
        "query_gate_version": HYBRID_QUERY_GATE_VERSION,
        "verifier": {
            "prompt_version": HYBRID_PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(
                HYBRID_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "model_id": "deepseek-chat",
            "minimum_confidence": 0.85,
        },
        "challenger": {
            "prompt_version": HYBRID_CHALLENGER_PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(
                HYBRID_CHALLENGER_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "model_id": "deepseek-chat",
            "minimum_confidence": 0.8,
            "mode": HYBRID_CHALLENGER_MODE,
            "veto_reason_codes": sorted(HYBRID_CHALLENGER_VETO_REASONS),
        },
    }
    evidence["report_fingerprint"] = evidence_fingerprint(evidence)
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


def test_local_encoder_rejects_symlinked_model_root_before_loading_runtime(
    tmp_path: Path,
) -> None:
    real_root = tmp_path / "real-model"
    (real_root / "onnx").mkdir(parents=True)
    for relative, value in {
        "config.json": b'{}',
        "tokenizer.json": b'{}',
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
