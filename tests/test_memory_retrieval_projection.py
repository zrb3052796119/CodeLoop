from __future__ import annotations

import pickle
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from minicode.cybernetic_orchestrator import CyberneticOrchestrator
from minicode.memory import MemoryEntry, MemoryScope, _approval_hash_for_entry
from minicode.memory_hybrid import HybridAdjudication, HybridCandidateSignal
from minicode.memory_pipeline import MemoryPipeline
from minicode.memory_retrieval import (
    CanonicalMemoryRetriever,
    MemoryRetrievalCounts,
    MemoryRetrievalRequest,
    MemoryRetrievalResult,
    RetrievalDetailLevel,
    RetrievalScore,
    RetrievedMemory,
)
from minicode.run_events import project_memory_result_events

_SUPPRESSED_SENTINEL = "suppressed-password-SHOULD-NOT-SURVIVE"
_UNRELATED_METADATA_SENTINEL = "unrelated-private-metadata-MUST-NOT-SURVIVE"


class _Manager:
    def __init__(self, entries: list[MemoryEntry]) -> None:
        self.memories = {
            MemoryScope.PROJECT: SimpleNamespace(entries=entries),
        }
        self.retrieved_ids: list[str] = []
        self.injected_ids: list[str] = []
        self.feedback_calls: list[tuple[tuple[str, ...], bool]] = []

    def search(self, *_args, **_kwargs) -> list[MemoryEntry]:
        return []

    @contextmanager
    def retrieval_snapshot(self):
        yield "snapshot-revision-1"

    def prompt_authority_revision(self) -> str:
        return "authority-revision-1"

    def record_retrievals(self, entry_ids: list[str]) -> None:
        self.retrieved_ids.extend(entry_ids)

    def record_injections(self, entry_ids: list[str]) -> None:
        self.injected_ids.extend(entry_ids)

    def record_feedback(
        self,
        entry_ids: list[str],
        success: bool,
        **_kwargs,
    ) -> None:
        self.feedback_calls.append((tuple(entry_ids), success))


def _entry(entry_id: str, content: str, *, tags: list[str]) -> MemoryEntry:
    entry = MemoryEntry(
        id=entry_id,
        scope=MemoryScope.PROJECT,
        category="recovery",
        content=content,
        tags=tags,
        approval_status="approved",
        lifecycle_status="active",
        safety_status="safe",
        created_at=1_700_000_000.0,
        updated_at=1_700_000_000.0,
        last_accessed=1_700_000_000.0,
    )
    entry.approval_content_hash = _approval_hash_for_entry(entry)
    return entry


def _manager(size: int) -> _Manager:
    entries = [
        _entry(
            "selected-memory",
            "Frostbyte recovery verifies the bounded circuit receipt.",
            tags=["frostbyte", "circuit", "receipt"],
        )
    ]
    entries.extend(
        _entry(
            f"{_SUPPRESSED_SENTINEL}-{index:04d}",
            f"{_SUPPRESSED_SENTINEL} unrelated archive record {index}.",
            tags=[f"archive-{index}"],
        )
        for index in range(1, size)
    )
    return _Manager(entries)


def _request(query: str = "frostbyte circuit receipt") -> MemoryRetrievalRequest:
    return MemoryRetrievalRequest(
        query=query,
        max_memories=5,
        max_total_tokens=800,
        max_tokens_per_memory=160,
        min_relevance=0.0,
    )


def _retrieve_pair(size: int) -> tuple[MemoryRetrievalResult, MemoryRetrievalResult]:
    manager = _manager(size)
    with patch("minicode.memory_retrieval.time.time", return_value=1_800_000_000.0):
        full = CanonicalMemoryRetriever(manager).retrieve(_request())
        runtime = CanonicalMemoryRetriever(
            manager,
            detail_level=RetrievalDetailLevel.RUNTIME,
        ).retrieve(_request())
    return full, runtime


def _rendered_runtime_semantics(
    memories: tuple[object, ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            memory.entry_id,
            memory.scope,
            memory.category,
            memory.content,
            memory.rank,
            memory.token_count,
            memory.truncated,
            memory.source,
            memory.score.lexical_score,
            memory.score.dense_score,
            memory.score.semantic_score,
            memory.score.final_score,
            memory.reason_codes,
        )
        for memory in memories
    )


def _metadata_heavy_result(metadata_count: int) -> MemoryRetrievalResult:
    reason_codes = tuple(
        f"provider_reason_{index:05d}" for index in range(metadata_count)
    )
    metadata_values = tuple(
        f"{_UNRELATED_METADATA_SENTINEL}-{index:05d}" for index in range(metadata_count)
    )
    score = RetrievalScore(
        lexical_score=0.8,
        semantic_score=0.9,
        final_score=0.95,
        matched_terms=metadata_values,
        reason_codes=reason_codes,
    )
    rendered = tuple(
        RetrievedMemory(
            entry_id=f"rendered-{index}",
            scope="project",
            category="recovery",
            content=f"Bounded recovery instruction {index}.",
            score=score,
            rank=index + 1,
            token_count=8,
            truncated=False,
            source="verified_recovery",
            reason_codes=reason_codes,
            tags=metadata_values,
            domains=metadata_values,
            authority_signals=metadata_values,
            relations=tuple(("related_to", value) for value in metadata_values),
        )
        for index in range(3)
    )
    rendered_ids = tuple(memory.entry_id for memory in rendered)
    prompt = "\n".join(
        f"{memory.rank}. [{memory.category}] {memory.content}" for memory in rendered
    )
    return MemoryRetrievalResult(
        candidate_ids=rendered_ids,
        selected_ids=rendered_ids,
        rendered_ids=rendered_ids,
        candidates=rendered,
        selected=rendered,
        rendered=rendered,
        prompt_text=prompt,
        no_match=False,
        no_match_reason="",
        controller_decision={"mode": "standard", "max_memories": 3},
        total_tokens=24,
        query_hash="bounded-query",
        diagnostics={
            "hybrid": {
                "active": True,
                "fallback": False,
                "provider": {
                    "provider_cache_reused": True,
                    "cache_hit": False,
                    "private_payload": list(metadata_values),
                },
            }
        },
        authority_revision="authority-revision-1",
        counts=MemoryRetrievalCounts(
            candidate_count=3,
            admitted_count=3,
            selected_count=3,
            rendered_count=3,
            suppressed_count=0,
        ),
    )


def test_runtime_rendered_projection_is_bounded_by_prompt_not_entry_metadata() -> None:
    # The exact IDs/category/content and prompt budget are intentionally held
    # constant. This proves independence from ranking/provider metadata; it
    # does not claim an absolute bound for an authority ID of arbitrary length.
    small_full = _metadata_heavy_result(1)
    capped_full = _metadata_heavy_result(16)
    large_full = _metadata_heavy_result(2_000)

    small_runtime = small_full.with_detail_level(RetrievalDetailLevel.RUNTIME)
    capped_runtime = capped_full.with_detail_level(RetrievalDetailLevel.RUNTIME)
    large_runtime = large_full.with_detail_level(RetrievalDetailLevel.RUNTIME)

    assert large_full.rendered[0].tags == large_full.rendered[0].domains
    assert len(large_full.rendered[0].score.matched_terms) == 2_000
    assert len(large_full.diagnostics["hybrid"]["provider"]["private_payload"]) == 2_000
    assert _UNRELATED_METADATA_SENTINEL.encode() in pickle.dumps(
        large_full,
        protocol=5,
    )

    assert large_runtime.rendered_ids == large_full.rendered_ids
    assert large_runtime.prompt_text == large_full.prompt_text
    assert large_runtime.counts == large_full.counts
    assert [
        (
            memory.entry_id,
            memory.scope,
            memory.category,
            memory.content,
            memory.rank,
            memory.token_count,
            memory.truncated,
            memory.source,
            memory.score.final_score,
        )
        for memory in large_runtime.rendered
    ] == [
        (
            memory.entry_id,
            memory.scope,
            memory.category,
            memory.content,
            memory.rank,
            memory.token_count,
            memory.truncated,
            memory.source,
            memory.score.final_score,
        )
        for memory in large_full.rendered
    ]
    assert all(not hasattr(memory, "tags") for memory in large_runtime.rendered)
    assert all(memory.score.matched_terms == () for memory in large_runtime.rendered)
    assert all(len(memory.reason_codes) <= 16 for memory in large_runtime.rendered)

    small_runtime_bytes = pickle.dumps(small_runtime, protocol=5)
    capped_runtime_bytes = pickle.dumps(capped_runtime, protocol=5)
    large_runtime_bytes = pickle.dumps(large_runtime, protocol=5)
    assert len(large_runtime_bytes) <= len(small_runtime_bytes) + 1_024
    assert large_runtime_bytes == capped_runtime_bytes
    assert len(large_runtime_bytes) < 16_000
    assert _UNRELATED_METADATA_SENTINEL.encode() not in large_runtime_bytes


@pytest.mark.parametrize("size", [600, 2000])
def test_runtime_projection_is_semantically_equivalent_and_hides_corpus(
    size: int,
) -> None:
    full, runtime = _retrieve_pair(size)

    assert full.detail_level is RetrievalDetailLevel.FULL
    assert runtime.detail_level is RetrievalDetailLevel.RUNTIME
    assert full.counts == runtime.counts
    assert runtime.counts is not None
    assert runtime.counts.candidate_count == size
    assert runtime.counts.admitted_count == full.counts.admitted_count
    assert runtime.counts.selected_count == 1
    assert runtime.counts.rendered_count == 1
    assert runtime.counts.suppressed_count == size - 1
    assert runtime.candidate_ids == ()
    assert runtime.suppressed_ids == ()
    assert runtime.candidates == ()
    assert runtime.selected == ()
    assert runtime.selected_ids == full.selected_ids
    assert runtime.rendered_ids == full.rendered_ids
    assert _rendered_runtime_semantics(runtime.rendered) == _rendered_runtime_semantics(
        full.rendered
    )
    assert all(not hasattr(memory, "tags") for memory in runtime.rendered)
    assert all(memory.score.matched_terms == () for memory in runtime.rendered)
    assert runtime.prompt_text == full.prompt_text
    assert runtime.no_match == full.no_match
    assert runtime.no_match_reason == full.no_match_reason
    assert runtime.controller_decision == full.controller_decision
    assert runtime.total_tokens == full.total_tokens
    assert runtime.query_hash == full.query_hash
    assert runtime.authority_revision == full.authority_revision
    assert runtime.authority_revision == "authority-revision-1"
    assert _SUPPRESSED_SENTINEL.encode() not in pickle.dumps(runtime, protocol=5)


def test_runtime_projection_size_does_not_scale_with_corpus() -> None:
    full_600, runtime_600 = _retrieve_pair(600)
    full_2000, runtime_2000 = _retrieve_pair(2000)

    full_600_size = len(pickle.dumps(full_600, protocol=5))
    full_2000_size = len(pickle.dumps(full_2000, protocol=5))
    runtime_600_size = len(pickle.dumps(runtime_600, protocol=5))
    runtime_2000_size = len(pickle.dumps(runtime_2000, protocol=5))

    assert full_2000_size > full_600_size * 2
    assert runtime_2000_size <= runtime_600_size + 256
    assert runtime_2000_size < full_2000_size // 10


def test_full_default_and_runtime_no_match_are_contract_compatible() -> None:
    manager = _manager(2)
    with patch("minicode.memory_retrieval.time.time", return_value=1_800_000_000.0):
        default_full = CanonicalMemoryRetriever(manager).retrieve(_request())
        explicit_full = CanonicalMemoryRetriever(
            manager,
            detail_level=RetrievalDetailLevel.FULL,
        ).retrieve(_request())
        no_match_full = CanonicalMemoryRetriever(manager).retrieve(_request("the"))
        no_match_runtime = CanonicalMemoryRetriever(
            manager,
            detail_level=RetrievalDetailLevel.RUNTIME,
        ).retrieve(_request("the"))

    assert default_full == explicit_full
    assert no_match_full.counts == no_match_runtime.counts
    assert no_match_full.prompt_text == no_match_runtime.prompt_text == ""
    assert no_match_full.selected_ids == no_match_runtime.selected_ids == ()
    assert no_match_full.rendered_ids == no_match_runtime.rendered_ids == ()
    assert no_match_full.no_match_reason == no_match_runtime.no_match_reason


def test_runtime_hybrid_diagnostics_are_strict_and_bounded() -> None:
    class Provider:
        def adjudicate(self, **_kwargs) -> HybridAdjudication:
            return HybridAdjudication(
                signals=(
                    HybridCandidateSignal(
                        entry_id="selected-memory",
                        dense_score=0.9,
                        relevance_score=0.95,
                        accepted=True,
                        reason_codes=("semantic_equivalence",),
                    ),
                ),
                diagnostics={
                    "provider_cache_reused": True,
                    "cache_hit": True,
                    "secret": _SUPPRESSED_SENTINEL * 2_000,
                    "candidate_payloads": [_SUPPRESSED_SENTINEL] * 2_000,
                },
            )

    manager = _manager(600)
    with patch("minicode.memory_retrieval.time.time", return_value=1_800_000_000.0):
        full = CanonicalMemoryRetriever(
            manager,
            hybrid_provider=Provider(),
        ).retrieve(_request())
        runtime = CanonicalMemoryRetriever(
            manager,
            hybrid_provider=Provider(),
            detail_level=RetrievalDetailLevel.RUNTIME,
        ).retrieve(_request())

    assert full.diagnostics["hybrid"]["provider"]["secret"].startswith(
        _SUPPRESSED_SENTINEL
    )
    assert runtime.prompt_text == full.prompt_text
    assert runtime.selected_ids == full.selected_ids
    assert runtime.rendered_ids == full.rendered_ids
    assert runtime.counts == full.counts
    assert runtime.diagnostics["hybrid"] == {
        "active": True,
        "fallback": False,
        "candidate_count": 1,
        "accepted_count": 1,
        "provider": {
            "provider_cache_reused": True,
            "cache_hit": True,
        },
    }
    serialized = pickle.dumps(runtime, protocol=5)
    assert _SUPPRESSED_SENTINEL.encode() not in serialized
    assert len(serialized) < 8_000


def test_render_transforms_keep_exact_counts_and_ignore_external_ids() -> None:
    retriever = CanonicalMemoryRetriever(
        _manager(600),
        detail_level=RetrievalDetailLevel.RUNTIME,
    )
    with patch("minicode.memory_retrieval.time.time", return_value=1_800_000_000.0):
        result = retriever.retrieve(_request())
    assert result.counts is not None

    external = f"external-{_SUPPRESSED_SENTINEL}"
    suppressed = retriever.suppress_rendered_ids(
        result,
        [result.rendered_ids[0], external],
        reason="already_materialized_this_turn",
    )

    assert suppressed.counts is not None
    assert suppressed.counts.rendered_count == 0
    assert suppressed.counts.suppressed_count == result.counts.suppressed_count + 1
    assert suppressed.diagnostics["already_materialized_count"] == 1
    assert external not in repr(suppressed)
    assert (
        retriever.suppress_rendered_ids(
            suppressed,
            [external],
            reason="already_materialized_this_turn",
        )
        is suppressed
    )

    failed = result.without_rendered("render_failed")
    assert failed.counts is not None
    assert failed.counts.rendered_count == 0
    assert failed.counts.suppressed_count == result.counts.suppressed_count + 1
    assert failed.diagnostics["render_failure_reason"] == "render_failed"

    full, _runtime = _retrieve_pair(2)
    full_suppressed = retriever.suppress_rendered_ids(
        full,
        [full.rendered_ids[0], external],
        reason="already_materialized_this_turn",
    )
    assert full_suppressed.diagnostics["already_materialized_ids"] == [
        full.rendered_ids[0]
    ]
    assert external not in repr(full_suppressed.diagnostics)


def test_runtime_projection_supports_pipeline_read_receipt_and_feedback() -> None:
    manager = _manager(3)
    pipeline = MemoryPipeline(
        manager,
        retrieval_detail_level=RetrievalDetailLevel.RUNTIME,
    )
    pipeline.initialize()

    read = pipeline.read(
        "frostbyte circuit receipt",
        max_results=3,
        _record_retrieval=False,
    )
    assert read[0]["id"] == "selected-memory"
    assert read[0]["scope"] == "project"
    assert read[0]["source"] == "project_memory"
    assert read[0]["relevance"] == read[0]["score"]["final_score"]

    receipt = pipeline.prepare_compaction_context(
        "frostbyte circuit receipt",
    )
    assert receipt.rendered_ids == ("selected-memory",)
    assert receipt.rendered_proofs[0][0] == "selected-memory"
    assert pipeline.commit_prompt_receipt(receipt) == ("selected-memory",)

    pipeline.feedback(True)
    assert manager.retrieved_ids == ["selected-memory"]
    assert manager.injected_ids == ["selected-memory"]
    assert manager.feedback_calls == [(("selected-memory",), True)]


def test_events_prefer_typed_counts_and_keep_legacy_fallback() -> None:
    _full, runtime = _retrieve_pair(600)

    retrieved, rendered = project_memory_result_events(runtime)
    assert retrieved["candidateCount"] == 600
    assert retrieved["selectedCount"] == 1
    assert retrieved["suppressedCount"] == 599
    assert rendered["renderedCount"] == 1

    legacy = SimpleNamespace(
        candidate_ids=("a", "b"),
        selected_ids=("a",),
        suppressed_ids=("b",),
        rendered_ids=("a",),
        no_match=False,
        no_match_reason="",
        controller_decision={"mode": "standard"},
        total_tokens=4,
        diagnostics={},
    )
    legacy_retrieved, legacy_rendered = project_memory_result_events(legacy)
    assert legacy_retrieved["candidateCount"] == 2
    assert legacy_retrieved["suppressedCount"] == 1
    assert legacy_rendered["renderedCount"] == 1


def test_pipeline_default_is_full_and_orchestrator_requests_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct = MemoryPipeline()
    assert direct._retrieval_detail_level is RetrievalDetailLevel.FULL

    captured: dict[str, object] = {}

    class ProbePipeline:
        def __init__(self, _manager, **kwargs) -> None:
            captured.update(kwargs)

        def initialize(self, **_kwargs) -> None:
            captured["initialized"] = True

    monkeypatch.setattr("minicode.memory_pipeline.MemoryPipeline", ProbePipeline)
    orchestrator = CyberneticOrchestrator()
    orchestrator.memory_ctrl = object()
    orchestrator.wire_memory(object())

    assert captured["retrieval_detail_level"] is RetrievalDetailLevel.RUNTIME
    assert captured["initialized"] is True
