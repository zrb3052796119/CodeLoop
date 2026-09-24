from __future__ import annotations

import itertools
from pathlib import Path

from minicode.memory import (
    MemoryApprovalPolicy,
    MemoryEntry,
    MemoryFile,
    MemoryManager,
    MemoryScope,
    MemoryTier,
)


def _entry(
    entry_id: str,
    *,
    tier: MemoryTier = MemoryTier.SHORT_TERM,
    approval_status: str = "approved",
    lifecycle_status: str = "active",
    safety_status: str = "safe",
    created_at: float = 1.0,
    corroborated_usefulness_score: float = 0.0,
    corroborated_success_count: int = 0,
    usefulness_score: float = 0.0,
    success_count: int = 0,
    last_used: float = 0.0,
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        scope=MemoryScope.PROJECT,
        category="retention",
        content=f"retention policy {entry_id}",
        tier=tier,
        approval_status=approval_status,
        lifecycle_status=lifecycle_status,
        safety_status=safety_status,
        created_at=created_at,
        updated_at=created_at,
        last_accessed=created_at,
        corroborated_usefulness_score=corroborated_usefulness_score,
        corroborated_success_count=corroborated_success_count,
        usefulness_score=usefulness_score,
        success_count=success_count,
        last_used=last_used,
    )


def test_pending_admission_cannot_evict_approved_active_long_term() -> None:
    memory_file = MemoryFile(
        scope=MemoryScope.PROJECT,
        max_entries=1,
        max_size_bytes=10_000,
    )
    durable = _entry("durable", tier=MemoryTier.LONG_TERM, created_at=1.0)
    pending = _entry("pending", approval_status="pending", created_at=2.0)
    evicted_ids: list[str] = []

    memory_file.add_entry(durable)
    admitted = memory_file.add_entry(
        pending,
        on_evict=lambda entry, _lifecycle, _reason: evicted_ids.append(entry.id),
    )

    assert admitted is False
    assert [entry.id for entry in memory_file.entries] == [durable.id]
    assert evicted_ids == [pending.id]
    assert pending.lifecycle_status == "evicted"
    assert memory_file._id_index == {durable.id: durable}


def test_non_authoritative_entries_cannot_displace_active_approved_memory() -> None:
    cases = (
        {"entry_id": "rejected", "approval_status": "rejected"},
        {"entry_id": "unsafe", "safety_status": "unsafe"},
        {"entry_id": "inactive", "lifecycle_status": "deprecated"},
    )
    for case in cases:
        memory_file = MemoryFile(
            scope=MemoryScope.PROJECT,
            max_entries=1,
            max_size_bytes=10_000,
        )
        durable = _entry(
            f"durable-{case['entry_id']}",
            tier=MemoryTier.LONG_TERM,
            created_at=1.0,
        )
        candidate = _entry(
            case["entry_id"],
            tier=MemoryTier.LONG_TERM,
            approval_status=case.get("approval_status", "approved"),
            lifecycle_status=case.get("lifecycle_status", "active"),
            safety_status=case.get("safety_status", "safe"),
            created_at=2.0,
            corroborated_usefulness_score=1.0,
            corroborated_success_count=100,
            usefulness_score=1.0,
            success_count=100,
            last_used=100.0,
        )

        memory_file.add_entry(durable)
        admitted = memory_file.add_entry(candidate)

        assert admitted is False, case["entry_id"]
        assert [entry.id for entry in memory_file.entries] == [durable.id]


def test_equal_authority_and_feedback_evicts_oldest_entry() -> None:
    memory_file = MemoryFile(
        scope=MemoryScope.PROJECT,
        max_entries=2,
        max_size_bytes=10_000,
    )
    oldest = _entry("oldest", created_at=1.0)
    middle = _entry("middle", created_at=2.0)
    newest = _entry("newest", created_at=3.0)

    memory_file.add_entry(oldest)
    memory_file.add_entry(middle)
    admitted = memory_file.add_entry(newest)

    assert admitted is True
    assert [entry.id for entry in memory_file.entries] == [middle.id, newest.id]
    assert oldest.lifecycle_status == "evicted"


def test_corroborated_value_precedes_ordinary_value_and_recency() -> None:
    memory_file = MemoryFile(
        scope=MemoryScope.PROJECT,
        max_entries=1,
        max_size_bytes=10_000,
    )
    corroborated = _entry(
        "corroborated",
        tier=MemoryTier.LONG_TERM,
        created_at=1.0,
        corroborated_usefulness_score=1.0,
        corroborated_success_count=2,
    )
    merely_recent = _entry(
        "merely-recent",
        tier=MemoryTier.LONG_TERM,
        created_at=2.0,
        usefulness_score=1.0,
        success_count=100,
        last_used=100.0,
    )

    memory_file.add_entry(corroborated)
    admitted = memory_file.add_entry(merely_recent)

    assert admitted is False
    assert [entry.id for entry in memory_file.entries] == [corroborated.id]
    assert merely_recent.lifecycle_status == "evicted"


def test_tier_precedes_feedback_and_ordinary_feedback_precedes_recency() -> None:
    long_term = _entry(
        "long-term",
        tier=MemoryTier.LONG_TERM,
        created_at=1.0,
    )
    high_feedback_short_term = _entry(
        "short-term",
        tier=MemoryTier.SHORT_TERM,
        created_at=2.0,
        corroborated_usefulness_score=1.0,
        corroborated_success_count=100,
        usefulness_score=1.0,
        success_count=100,
        last_used=100.0,
    )
    tier_store = MemoryFile(
        scope=MemoryScope.PROJECT,
        max_entries=1,
        max_size_bytes=10_000,
    )
    tier_store.add_entry(long_term)

    assert tier_store.add_entry(high_feedback_short_term) is False
    assert [entry.id for entry in tier_store.entries] == [long_term.id]

    useful = _entry(
        "useful",
        created_at=1.0,
        usefulness_score=0.8,
        success_count=2,
    )
    merely_recent = _entry(
        "recent",
        created_at=2.0,
        usefulness_score=0.2,
        success_count=100,
        last_used=100.0,
    )
    feedback_store = MemoryFile(
        scope=MemoryScope.PROJECT,
        max_entries=1,
        max_size_bytes=10_000,
    )
    feedback_store.add_entry(useful)

    assert feedback_store.add_entry(merely_recent) is False
    assert [entry.id for entry in feedback_store.entries] == [useful.id]


def test_recent_use_precedes_created_time_within_an_exact_value_tie() -> None:
    recently_used = _entry("recently-used", created_at=1.0, last_used=100.0)
    merely_new = _entry("merely-new", created_at=2.0)
    memory_file = MemoryFile(
        scope=MemoryScope.PROJECT,
        max_entries=1,
        max_size_bytes=10_000,
    )

    memory_file.add_entry(recently_used)
    admitted = memory_file.add_entry(merely_new)

    assert admitted is False
    assert [entry.id for entry in memory_file.entries] == [recently_used.id]


def test_byte_limit_uses_the_same_authority_first_admission_policy() -> None:
    durable = _entry("durable-byte", tier=MemoryTier.LONG_TERM, created_at=1.0)
    pending = _entry("pending-byte", approval_status="pending", created_at=2.0)
    memory_file = MemoryFile(
        scope=MemoryScope.PROJECT,
        max_entries=10,
        max_size_bytes=len(durable.content.encode("utf-8")) + 1,
    )

    memory_file.add_entry(durable)
    admitted = memory_file.add_entry(pending)

    assert admitted is False
    assert [entry.id for entry in memory_file.entries] == [durable.id]
    assert memory_file.size_bytes <= memory_file.max_size_bytes


def test_oversized_new_entry_rejects_itself_without_clearing_existing_store() -> None:
    existing = _entry("existing", created_at=1.0)
    oversized = _entry("oversized", tier=MemoryTier.LONG_TERM, created_at=2.0)
    oversized.content = "x" * 200
    memory_file = MemoryFile(
        scope=MemoryScope.PROJECT,
        max_entries=10,
        max_size_bytes=len(existing.content.encode("utf-8")) + 1,
    )

    memory_file.add_entry(existing)
    admitted = memory_file.add_entry(oversized)

    assert admitted is False
    assert [entry.id for entry in memory_file.entries] == [existing.id]
    assert oversized.lifecycle_status == "evicted"


def test_retention_is_deterministic_across_insertion_orders() -> None:
    survivor_sets: set[tuple[str, ...]] = set()
    evicted_sets: set[tuple[str, ...]] = set()

    for insertion_order in itertools.permutations(("alpha", "beta", "gamma")):
        memory_file = MemoryFile(
            scope=MemoryScope.PROJECT,
            max_entries=2,
            max_size_bytes=10_000,
        )
        evicted_ids: list[str] = []
        for entry_id in insertion_order:
            memory_file.add_entry(
                _entry(entry_id, created_at=1.0),
                on_evict=lambda entry, _lifecycle, _reason, evicted_ids=evicted_ids: evicted_ids.append(entry.id),
            )
        survivor_sets.add(tuple(sorted(entry.id for entry in memory_file.entries)))
        evicted_sets.add(tuple(sorted(evicted_ids)))

    assert survivor_sets == {("beta", "gamma")}
    assert evicted_sets == {("alpha",)}


def test_manager_returns_none_for_self_eviction_but_persists_tombstone(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    memory_file = manager.memories[MemoryScope.PROJECT]
    memory_file.max_entries = 1
    memory_file.max_size_bytes = 10_000
    durable = manager.add_entry(
        MemoryScope.PROJECT,
        "retention",
        "approved durable retention policy",
        tier=MemoryTier.LONG_TERM,
        approval_policy=MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED,
    )
    assert durable is not None

    rejected_admission = manager.add_entry(
        MemoryScope.PROJECT,
        "retention",
        "unreviewed replacement retention policy",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )

    assert rejected_admission is None
    assert [entry.id for entry in memory_file.entries] == [durable.id]
    eviction = next(
        record
        for record in manager.approval_audit[MemoryScope.PROJECT]
        if record["action"] == "capacity_eviction"
    )
    assert eviction["entry_id"] != durable.id
    assert eviction["extra"]["admission_rejected"] is True
    assert eviction["lifecycle_status"] == "evicted"

    reloaded = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    persisted_eviction = next(
        record
        for record in reloaded.approval_audit[MemoryScope.PROJECT]
        if record["action"] == "capacity_eviction"
    )
    assert persisted_eviction["entry_id"] == eviction["entry_id"]
    assert persisted_eviction["extra"]["admission_rejected"] is True
