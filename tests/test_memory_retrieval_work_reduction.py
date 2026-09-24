from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

import minicode.memory as memory_module
import minicode.memory_retrieval as retrieval_module
from minicode.memory import MemoryEntry, MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline
from minicode.memory_retrieval import (
    CanonicalMemoryRetriever,
    MemoryRetrievalRequest,
)

ENTRY_COUNT = 200
TARGET_INDEX = 137
TARGET_ID = f"work-reduction-{TARGET_INDEX:03d}"
SPARSE_TERM = "ultravioletquasar"
ENTRY_TEXT_MARKERS = ("entrypayloadmarker", "entrytagmarker")


def _large_manager(tmp_path: Path) -> MemoryManager:
    manager = MemoryManager(
        project_root=tmp_path / "workspace",
        data_root=tmp_path / "memory-home",
    )
    entries = []
    for index in range(ENTRY_COUNT):
        object_name = SPARSE_TERM if index == TARGET_INDEX else f"routine-{index:03d}"
        entries.append(
            MemoryEntry(
                id=f"work-reduction-{index:03d}",
                scope=MemoryScope.PROJECT,
                category="work-reduction-fixture",
                content=(
                    "entrypayloadmarker deterministic retrieval lesson "
                    f"for {object_name}"
                ),
                tags=[f"entrytagmarker-{index:03d}"],
            )
        )

    memory_file = manager.memories[MemoryScope.PROJECT]
    memory_file.entries.extend(entries)
    memory_file._rebuild_indices()
    manager._save_scope(MemoryScope.PROJECT)
    assert sum(len(item.entries) for item in manager.memories.values()) == ENTRY_COUNT
    return manager


def _request() -> MemoryRetrievalRequest:
    return MemoryRetrievalRequest(
        query=SPARSE_TERM,
        max_memories=3,
        max_total_tokens=800,
        max_tokens_per_memory=160,
    )


def _pipeline(manager: MemoryManager) -> MemoryPipeline:
    pipeline = MemoryPipeline(manager)
    pipeline.initialize()
    return pipeline


def _install_work_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, int]:
    counters = {"entry_tokenize": 0, "bm25": 0}
    original_tokenize: Callable[[str], list[str]] = memory_module._tokenize
    original_bm25 = memory_module._bm25_score

    def counting_tokenize(text: str) -> list[str]:
        lowered = text.lower()
        if any(marker in lowered for marker in ENTRY_TEXT_MARKERS):
            counters["entry_tokenize"] += 1
        return original_tokenize(text)

    def counting_bm25(*args: object, **kwargs: object) -> float:
        counters["bm25"] += 1
        return original_bm25(*args, **kwargs)

    monkeypatch.setattr(memory_module, "_tokenize", counting_tokenize)
    monkeypatch.setattr(memory_module, "_bm25_score", counting_bm25)
    return counters


def _reset(counters: dict[str, int]) -> None:
    counters["entry_tokenize"] = 0
    counters["bm25"] = 0


def test_warm_and_observation_only_queries_perform_no_entry_tokenization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _large_manager(tmp_path)
    retriever = CanonicalMemoryRetriever(manager)
    counters = _install_work_counters(monkeypatch)

    cold = retriever.retrieve(_request())

    assert cold.rendered_ids == (TARGET_ID,)
    assert counters == {
        "entry_tokenize": ENTRY_COUNT * 3,
        "bm25": 1,
    }

    _reset(counters)
    warm = retriever.retrieve(_request())

    assert warm.rendered_ids == (TARGET_ID,)
    assert counters == {"entry_tokenize": 0, "bm25": 1}

    manager.record_retrievals([TARGET_ID])
    _reset(counters)
    after_observation = retriever.retrieve(_request())

    assert after_observation.rendered_ids == (TARGET_ID,)
    assert counters == {"entry_tokenize": 0, "bm25": 1}


def test_sparse_prepared_scoring_calls_bm25_for_strict_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _large_manager(tmp_path)
    retriever = CanonicalMemoryRetriever(manager)
    counters = _install_work_counters(monkeypatch)

    indexed_result = retriever.retrieve(_request())
    indexed_bm25_calls = counters["bm25"]

    _reset(counters)
    legacy_results = manager.search(
        SPARSE_TERM,
        scope=None,
        limit=ENTRY_COUNT,
        min_relevance=0.0,
        record_usage=False,
    )
    legacy_bm25_calls = counters["bm25"]

    assert indexed_result.rendered_ids == (TARGET_ID,)
    assert legacy_results[0].id == TARGET_ID
    assert indexed_bm25_calls == 1
    assert legacy_bm25_calls == ENTRY_COUNT
    assert indexed_bm25_calls < legacy_bm25_calls


def test_authority_content_change_rebuilds_prepared_entry_features(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _large_manager(tmp_path)
    retriever = CanonicalMemoryRetriever(manager)
    counters = _install_work_counters(monkeypatch)

    assert retriever.retrieve(_request()).rendered_ids == (TARGET_ID,)
    _reset(counters)
    assert retriever.retrieve(_request()).rendered_ids == (TARGET_ID,)
    assert counters == {"entry_tokenize": 0, "bm25": 1}

    assert manager.update_entry(
        MemoryScope.PROJECT,
        TARGET_ID,
        (
            "entrypayloadmarker deterministic retrieval lesson for "
            f"{SPARSE_TERM} with corrected authority"
        ),
    )
    _reset(counters)

    rebuilt = retriever.retrieve(_request())

    assert rebuilt.rendered_ids == (TARGET_ID,)
    assert counters == {
        "entry_tokenize": ENTRY_COUNT * 3,
        "bm25": 1,
    }

    _reset(counters)
    assert retriever.retrieve(_request()).rendered_ids == (TARGET_ID,)
    assert counters == {"entry_tokenize": 0, "bm25": 1}


def test_cli_turn_pipelines_share_warm_corpus_for_same_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CLI conversation creates a fresh pipeline/retriever for each Turn."""
    manager = _large_manager(tmp_path)
    counters = _install_work_counters(monkeypatch)

    first_turn = _pipeline(manager)
    first = first_turn.read(SPARSE_TERM, max_results=3)

    assert [item["id"] for item in first] == [TARGET_ID]
    assert counters == {
        "entry_tokenize": ENTRY_COUNT * 3,
        "bm25": 1,
    }

    _reset(counters)
    second_turn = _pipeline(manager)
    second = second_turn.read(SPARSE_TERM, max_results=3)

    assert [item["id"] for item in second] == [TARGET_ID]
    assert counters == {"entry_tokenize": 0, "bm25": 1}


def test_cross_pipeline_cache_rebuilds_after_authority_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _large_manager(tmp_path)
    counters = _install_work_counters(monkeypatch)
    assert [
        item["id"] for item in _pipeline(manager).read(SPARSE_TERM, max_results=3)
    ] == [TARGET_ID]

    assert manager.update_entry(
        MemoryScope.PROJECT,
        TARGET_ID,
        (
            "entrypayloadmarker deterministic retrieval lesson for "
            f"{SPARSE_TERM} with cross-turn corrected authority"
        ),
    )
    _reset(counters)

    rebuilt = _pipeline(manager).read(SPARSE_TERM, max_results=3)

    assert [item["id"] for item in rebuilt] == [TARGET_ID]
    # The authority edit may also make MemoryManager rebuild its own lexical
    # index once; prepared-corpus work itself contributes exactly 3N calls.
    assert counters["entry_tokenize"] >= ENTRY_COUNT * 3
    assert counters["bm25"] == 1

    _reset(counters)
    warm_again = _pipeline(manager).read(SPARSE_TERM, max_results=3)
    assert [item["id"] for item in warm_again] == [TARGET_ID]
    assert counters == {"entry_tokenize": 0, "bm25": 1}


def test_prepared_corpus_is_not_shared_between_memory_managers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_manager = _large_manager(tmp_path / "first")
    second_manager = _large_manager(tmp_path / "second")
    # Force the same revision token so this test detects a cache keyed only by
    # revision (which could leak one workspace manager's corpus into another).
    monkeypatch.setattr(
        first_manager,
        "prompt_authority_revision",
        lambda: "shared-test-authority-revision",
    )
    monkeypatch.setattr(
        second_manager,
        "prompt_authority_revision",
        lambda: "shared-test-authority-revision",
    )
    counters = _install_work_counters(monkeypatch)

    assert CanonicalMemoryRetriever(first_manager).retrieve(
        _request()
    ).rendered_ids == (TARGET_ID,)
    _reset(counters)

    second_result = CanonicalMemoryRetriever(second_manager).retrieve(_request())

    assert second_result.rendered_ids == (TARGET_ID,)
    assert counters == {
        "entry_tokenize": ENTRY_COUNT * 3,
        "bm25": 1,
    }


def test_concurrent_retrievers_publish_one_valid_shared_corpus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _large_manager(tmp_path)
    retrievers = (
        CanonicalMemoryRetriever(manager),
        CanonicalMemoryRetriever(manager),
    )
    builds_started = Barrier(2)
    original_build = retrieval_module._build_prepared_corpus

    def synchronized_build(*args: object, **kwargs: object):
        builds_started.wait(timeout=5.0)
        return original_build(*args, **kwargs)

    monkeypatch.setattr(
        retrieval_module,
        "_build_prepared_corpus",
        synchronized_build,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(lambda retriever: retriever.retrieve(_request()), retrievers)
        )

    assert tuple(result.rendered_ids for result in results) == (
        (TARGET_ID,),
        (TARGET_ID,),
    )
    # A third short-lived retriever must consume the published corpus. If it
    # attempts another build, the two-party barrier raises instead.
    assert CanonicalMemoryRetriever(manager).retrieve(
        _request()
    ).rendered_ids == (TARGET_ID,)
