from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

import minicode.memory as memory_module
import minicode.memory_retrieval as retrieval_module
from minicode.memory import MemoryApprovalPolicy, MemoryManager, MemoryScope
from minicode.memory_hybrid import HybridAdjudication, HybridCandidateSignal
from minicode.memory_retrieval import (
    CanonicalMemoryRetriever,
    MemoryRetrievalRequest,
)


def _manager(tmp_path: Path) -> tuple[MemoryManager, str]:
    manager = MemoryManager(
        project_root=tmp_path / "workspace",
        data_root=tmp_path / "memory-home",
    )
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "failure-recovery",
        "PREPARED_CORPUS_MARKER checkout rounding recovery",
        tags=["checkout", "rounding"],
        approval_policy=MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED,
    )
    assert entry is not None
    return manager, entry.id


def _request() -> MemoryRetrievalRequest:
    return MemoryRetrievalRequest(
        query="checkout rounding recovery",
        max_total_tokens=800,
        max_tokens_per_memory=160,
    )


def test_canonical_retrieval_does_not_call_live_manager_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, entry_id = _manager(tmp_path)

    def forbidden_search(*_args: object, **_kwargs: object) -> list[object]:
        raise AssertionError("canonical retrieval must score its immutable snapshot")

    monkeypatch.setattr(manager, "search", forbidden_search)

    result = CanonicalMemoryRetriever(manager).retrieve(_request())

    assert result.rendered_ids == (entry_id,)


def test_unchanged_authority_reuses_prepared_entry_features(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, entry_id = _manager(tmp_path)
    retriever = CanonicalMemoryRetriever(manager)
    original_tokenize = memory_module._tokenize
    marker_calls = 0

    def counting_tokenize(text: str) -> list[str]:
        nonlocal marker_calls
        if "PREPARED_CORPUS_MARKER" in text:
            marker_calls += 1
        return original_tokenize(text)

    monkeypatch.setattr(memory_module, "_tokenize", counting_tokenize)

    assert retriever.retrieve(_request()).rendered_ids == (entry_id,)
    first_calls = marker_calls
    assert first_calls > 0

    assert retriever.retrieve(_request()).rendered_ids == (entry_id,)
    assert marker_calls == first_calls


def test_unchanged_authority_reuses_prepared_path_features(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, entry_id = _manager(tmp_path)
    retriever = CanonicalMemoryRetriever(manager)
    original_extract = retrieval_module.extract_complete_path_identities
    marker_calls = 0

    def counting_extract(value: object, *args: object, **kwargs: object):
        nonlocal marker_calls
        if "PREPARED_CORPUS_MARKER" in str(value):
            marker_calls += 1
        return original_extract(value, *args, **kwargs)

    monkeypatch.setattr(
        retrieval_module,
        "extract_complete_path_identities",
        counting_extract,
    )

    assert retriever.retrieve(_request()).rendered_ids == (entry_id,)
    first_calls = marker_calls
    assert first_calls > 0

    assert retriever.retrieve(_request()).rendered_ids == (entry_id,)
    assert marker_calls == first_calls


def test_observation_only_write_keeps_prepared_corpus_reusable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, entry_id = _manager(tmp_path)
    retriever = CanonicalMemoryRetriever(manager)
    original_tokenize = memory_module._tokenize
    marker_calls = 0

    def counting_tokenize(text: str) -> list[str]:
        nonlocal marker_calls
        if "PREPARED_CORPUS_MARKER" in text:
            marker_calls += 1
        return original_tokenize(text)

    monkeypatch.setattr(memory_module, "_tokenize", counting_tokenize)
    assert retriever.retrieve(_request()).rendered_ids == (entry_id,)
    first_calls = marker_calls

    manager.record_retrievals([entry_id])

    assert retriever.retrieve(_request()).rendered_ids == (entry_id,)
    assert marker_calls == first_calls


def test_authority_change_rebuilds_prepared_entry_features(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, entry_id = _manager(tmp_path)
    retriever = CanonicalMemoryRetriever(manager)
    original_tokenize = memory_module._tokenize
    marker_calls = 0

    def counting_tokenize(text: str) -> list[str]:
        nonlocal marker_calls
        if "PREPARED_CORPUS_MARKER" in text:
            marker_calls += 1
        return original_tokenize(text)

    monkeypatch.setattr(memory_module, "_tokenize", counting_tokenize)
    assert retriever.retrieve(_request()).rendered_ids == (entry_id,)
    first_calls = marker_calls

    assert manager.update_entry(
        MemoryScope.PROJECT,
        entry_id,
        "PREPARED_CORPUS_MARKER checkout rounding recovery corrected",
    )

    assert retriever.retrieve(_request()).rendered_ids == (entry_id,)
    assert marker_calls > first_calls


def test_preparation_and_legacy_scoring_run_outside_store_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, entry_id = _manager(tmp_path)
    observed_states: list[tuple[str, bool]] = []
    original_build = retrieval_module._build_prepared_corpus
    original_score = retrieval_module._manager_scores_from_prepared_corpus

    def observed_build(*args: object, **kwargs: object):
        observed_states.append(("build", manager.in_write_transaction))
        return original_build(*args, **kwargs)

    def observed_score(*args: object, **kwargs: object):
        observed_states.append(("score", manager.in_write_transaction))
        return original_score(*args, **kwargs)

    monkeypatch.setattr(retrieval_module, "_build_prepared_corpus", observed_build)
    monkeypatch.setattr(
        retrieval_module,
        "_manager_scores_from_prepared_corpus",
        observed_score,
    )

    result = CanonicalMemoryRetriever(manager).retrieve(_request())

    assert result.rendered_ids == (entry_id,)
    assert observed_states == [("build", False), ("score", False)]


def test_hybrid_provider_cannot_mutate_reusable_authority_corpus(
    tmp_path: Path,
) -> None:
    manager, entry_id = _manager(tmp_path)

    class MutatingHybrid:
        def adjudicate(self, *, entries, lexical_accepted_ids, **_kwargs):
            entries[0].content = "POISONED BY PROVIDER"
            return HybridAdjudication(
                signals=tuple(
                    HybridCandidateSignal(
                        entry_id=candidate_id,
                        dense_score=0.9,
                        relevance_score=0.9,
                        accepted=True,
                        reason_codes=("test_semantic_match",),
                    )
                    for candidate_id in sorted(lexical_accepted_ids)
                ),
                diagnostics={"provider": "mutating-test"},
            )

    retriever = CanonicalMemoryRetriever(
        manager,
        hybrid_provider=MutatingHybrid(),
    )

    first = retriever.retrieve(_request())
    second = retriever.retrieve(_request())

    assert first.rendered_ids == second.rendered_ids == (entry_id,)
    assert "POISONED BY PROVIDER" not in first.prompt_text
    assert "POISONED BY PROVIDER" not in second.prompt_text


def test_prepared_corpus_is_differentially_equal_to_legacy_scoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _entry_id = _manager(tmp_path)
    for scope, category, content, tags, domains in (
        (
            MemoryScope.LOCAL,
            "testing",
            "pytest checkout fixture recovery",
            ["checkout fixture"],
            ["testing"],
        ),
        (
            MemoryScope.USER,
            "preference",
            "Always preserve checkout rounding behavior",
            ["rounding"],
            [],
        ),
        (
            MemoryScope.USER,
            "duplicate",
            "PREPARED_CORPUS_MARKER checkout rounding recovery",
            ["lower authority duplicate"],
            [],
        ),
    ):
        assert manager.add_entry(
            scope,
            category,
            content,
            tags=tags,
            domains=domains,
            approval_policy=MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED,
        ) is not None

    frozen_now = 2_000_000_000.0
    monkeypatch.setattr(memory_module.time, "time", lambda: frozen_now)
    monkeypatch.setattr(retrieval_module.time, "time", lambda: frozen_now)
    request = MemoryRetrievalRequest(
        query="checkout rounding recovery",
        active_domains=("testing",),
        max_memories=5,
        max_total_tokens=1_500,
        max_tokens_per_memory=240,
    )
    active_entries = tuple(
        deepcopy(
            sorted(
                (
                    entry
                    for memory_file in manager.memories.values()
                    for entry in memory_file.entries
                    if entry.is_active
                ),
                key=lambda entry: (entry.scope.value, entry.id),
            )
        )
    )
    legacy_entries = manager.search(
        request.query,
        scope=None,
        limit=len(active_entries),
        min_relevance=0.0,
        active_domains=list(request.active_domains),
        record_usage=False,
    )
    legacy_scores = {
        entry.id: max(0.0, float(entry._last_relevance))
        for entry in legacy_entries
    }
    revision = manager.prompt_authority_revision()
    corpus = retrieval_module._build_prepared_corpus(revision, active_entries)
    prepared_scores = retrieval_module._manager_scores_from_prepared_corpus(
        corpus,
        request.query,
        request.active_domains,
        len(active_entries),
    )

    assert prepared_scores == pytest.approx(legacy_scores)

    retriever = CanonicalMemoryRetriever(manager)
    preflight = retriever._preflight(request)
    legacy_result = retriever._retrieve_snapshot(
        request,
        preflight_snapshot=preflight,
        active_entries_snapshot=active_entries,
        manager_scores_snapshot=legacy_scores,
    )
    prepared_result = retriever.retrieve(request)

    assert replace(prepared_result, authority_revision="") == legacy_result
