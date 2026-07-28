"""Corroborated (verification/user-signal backed) Memory feedback ranking.

Kept in its own file rather than tests/test_memory_retrieval_phase2a.py,
which is a pinned/frozen asset for the Phase2A evaluation harness.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from minicode.memory import MemoryEntry, MemoryManager, MemoryScope, MemoryTier
from minicode.memory_retrieval import (
    CanonicalMemoryRetriever,
    MemoryRetrievalRequest,
    RetrievalSource,
)


def _manager(tmp_path: Path, entries: list[MemoryEntry]) -> MemoryManager:
    manager = MemoryManager(project_root=tmp_path)
    for entry in entries:
        manager.memories[entry.scope].entries.append(entry)
    for memory_file in manager.memories.values():
        memory_file._rebuild_indices()
    return manager


def _entry(
    entry_id: str,
    content: str,
    *,
    scope: MemoryScope = MemoryScope.PROJECT,
    category: str = "architecture",
    tags: list[str] | None = None,
    domains: list[str] | None = None,
    tier: MemoryTier = MemoryTier.SHORT_TERM,
    updated_at: float = 1_700_000_000.0,
    corroborated_success_count: int = 0,
    corroborated_failure_count: int = 0,
    corroborated_usefulness_score: float = 0.0,
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        scope=scope,
        category=category,
        content=content,
        tags=list(tags or []),
        domains=list(domains or []),
        tier=tier,
        created_at=updated_at,
        updated_at=updated_at,
        last_accessed=updated_at,
        corroborated_success_count=corroborated_success_count,
        corroborated_failure_count=corroborated_failure_count,
        corroborated_usefulness_score=corroborated_usefulness_score,
    )


def _request(query: str, **overrides: object) -> MemoryRetrievalRequest:
    values: dict[str, object] = {
        "query": query,
        "current_files": (),
        "active_domains": (),
        "context_usage": 0.4,
        "max_memories": 5,
        "max_total_tokens": 800,
        "max_tokens_per_memory": 160,
        "min_relevance": 0.0,
        "source_entrypoint": RetrievalSource.CANONICAL,
    }
    values.update(overrides)
    return MemoryRetrievalRequest(**values)


def test_corroboration_cannot_activate_unrelated_memory(tmp_path: Path) -> None:
    unrelated = _entry(
        "verified-noise",
        "Frontend avatar colors follow the theme.",
        scope=MemoryScope.USER,
        corroborated_success_count=10,
        corroborated_usefulness_score=1.0,
    )
    manager = _manager(tmp_path, [unrelated])

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request("Repair database migration transaction retry")
    )

    assert result.rendered_ids == ()


def test_corroborated_success_outranks_an_otherwise_tied_uncorroborated_peer(
    tmp_path: Path,
) -> None:
    verified = _entry(
        "verified-webhook",
        "Payment webhook replay deduplication uses the provider event ID.",
        tags=["payment", "webhook"],
        corroborated_success_count=3,
        corroborated_usefulness_score=1.0,
    )
    unverified = _entry(
        "unverified-webhook",
        "Payment webhook replay deduplication checks the provider event ID.",
        tags=["payment", "webhook"],
    )
    manager = _manager(tmp_path, [verified, unverified])

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request("Fix payment webhook replay deduplication", max_memories=2)
    )

    assert result.rendered_ids[0] == verified.id
    assert result.selected[0].score.corroborated_score == pytest.approx(1.0)
    assert result.selected[1].score.corroborated_score == pytest.approx(0.0)


def test_a_single_uncorroborated_sample_is_discounted_below_full_confidence(
    tmp_path: Path,
) -> None:
    barely_sampled = _entry(
        "barely-sampled",
        "Payment webhook replay deduplication uses the provider event ID.",
        tags=["payment", "webhook"],
        corroborated_success_count=1,
        corroborated_usefulness_score=1.0,
    )
    manager = _manager(tmp_path, [barely_sampled])

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request("Fix payment webhook replay deduplication")
    )

    assert result.selected[0].score.corroborated_score == pytest.approx(1.0 / 3.0)


def test_failed_corroboration_discounts_final_score_below_neutral(
    tmp_path: Path,
) -> None:
    verified_bad = _entry(
        "verified-bad-webhook",
        "Payment webhook replay deduplication uses the provider event ID.",
        tags=["payment", "webhook"],
        corroborated_failure_count=3,
        corroborated_usefulness_score=-1.0,
    )
    manager = _manager(tmp_path, [verified_bad])

    result = CanonicalMemoryRetriever(manager).retrieve(
        _request("Fix payment webhook replay deduplication")
    )

    assert result.selected[0].score.corroborated_score == pytest.approx(-1.0)
