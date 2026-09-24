from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from minicode.memory import (
    MemoryApprovalPolicy,
    MemoryEntry,
    MemoryManager,
    MemoryScope,
    MemoryTier,
)
from minicode.memory_hybrid import HybridAdjudication, HybridCandidateSignal
from minicode.memory_retrieval import CanonicalMemoryRetriever, MemoryRetrievalRequest
from minicode.memory_store import MemoryStoreConflict


def _manager(
    workspace: Path,
    data_root: Path,
    *,
    entry_id: str,
    object_name: str,
    store_timeout: float,
) -> MemoryManager:
    manager = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        store_timeout=store_timeout,
    )
    manager.memories[MemoryScope.PROJECT].entries.append(
        MemoryEntry(
            id=entry_id,
            scope=MemoryScope.PROJECT,
            category="pattern",
            content=f"{object_name} guidance",
            tags=[object_name],
            tier=MemoryTier.SHORT_TERM,
            lifecycle_status="active",
            approval_status="approved",
            safety_status="safe",
        )
    )
    for memory_file in manager.memories.values():
        memory_file._rebuild_indices()
    return manager


class _BlockingHybridProvider:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def adjudicate(
        self,
        *,
        request: MemoryRetrievalRequest,
        entries: tuple[MemoryEntry, ...],
        lexical_accepted_ids: frozenset[str],
    ) -> HybridAdjudication:
        del request, entries
        self.started.set()
        if not self.release.wait(timeout=2.0):
            raise TimeoutError("test did not release blocking hybrid provider")
        return HybridAdjudication(
            signals=tuple(
                HybridCandidateSignal(
                    entry_id=entry_id,
                    dense_score=0.9,
                    relevance_score=0.9,
                    accepted=True,
                    reason_codes=("test_semantic_match",),
                )
                for entry_id in sorted(lexical_accepted_ids)
            ),
            diagnostics={"provider": "blocking-test"},
        )


def _request(object_name: str) -> MemoryRetrievalRequest:
    return MemoryRetrievalRequest(
        query=f"{object_name} zebra",
        min_relevance=0.3,
        max_total_tokens=800,
        max_tokens_per_memory=160,
    )


def test_slow_hybrid_retrieval_does_not_hold_global_store_lock(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "shared-memory-home"
    slow_manager = _manager(
        tmp_path / "workspace-a",
        data_root,
        entry_id="checkout-guidance",
        object_name="checkout",
        store_timeout=0.2,
    )
    fast_manager = _manager(
        tmp_path / "workspace-b",
        data_root,
        entry_id="deploy-guidance",
        object_name="deploy",
        store_timeout=0.1,
    )
    provider = _BlockingHybridProvider()
    slow_retriever = CanonicalMemoryRetriever(
        slow_manager,
        hybrid_provider=provider,
    )
    fast_retriever = CanonicalMemoryRetriever(fast_manager)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(slow_retriever.retrieve, _request("checkout"))
        assert provider.started.wait(timeout=1.0)
        try:
            fast_result = fast_retriever.retrieve(_request("deploy"))
        finally:
            provider.release.set()
        slow_result = future.result(timeout=2.0)

    assert fast_result.rendered_ids == ("deploy-guidance",)
    assert slow_result.rendered_ids == ("checkout-guidance",)


def test_authority_change_during_unlocked_semantic_work_fails_closed(
    tmp_path: Path,
) -> None:
    manager = _manager(
        tmp_path / "workspace",
        tmp_path / "memory-home",
        entry_id="checkout-guidance",
        object_name="checkout",
        store_timeout=0.2,
    )
    revision = ["revision-1"]

    @contextmanager
    def revision_snapshot():
        yield revision[0]

    manager.retrieval_snapshot = revision_snapshot  # type: ignore[method-assign]
    manager.prompt_authority_revision = lambda: revision[0]  # type: ignore[method-assign]
    provider = _BlockingHybridProvider()
    retriever = CanonicalMemoryRetriever(manager, hybrid_provider=provider)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(retriever.retrieve, _request("checkout"))
        assert provider.started.wait(timeout=1.0)
        revision[0] = "revision-2"
        provider.release.set()

        with pytest.raises(MemoryStoreConflict, match="changed during retrieval"):
            future.result(timeout=2.0)


def test_observation_counter_write_does_not_invalidate_prompt_authority(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "memory-home"
    reader = MemoryManager(project_root=workspace, data_root=data_root)
    entry = reader.add_entry(
        MemoryScope.PROJECT,
        "pattern",
        "checkout guidance",
        tags=["checkout"],
        approval_policy=MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED,
    )
    assert entry is not None
    observer = MemoryManager(project_root=workspace, data_root=data_root)
    provider = _BlockingHybridProvider()
    retriever = CanonicalMemoryRetriever(reader, hybrid_provider=provider)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(retriever.retrieve, _request("checkout"))
        assert provider.started.wait(timeout=1.0)
        observer.record_retrievals([entry.id])
        provider.release.set()
        result = future.result(timeout=2.0)

    assert result.rendered_ids == (entry.id,)
    assert result.authority_revision.startswith("memorypromptrev_")


def test_stale_retrieval_observer_reloads_before_mutating_live_state(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "memory-home"
    authority = MemoryManager(project_root=workspace, data_root=data_root)
    entry = authority.add_entry(
        MemoryScope.PROJECT,
        "pattern",
        "checkout guidance",
        tags=["checkout"],
        approval_policy=MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED,
    )
    assert entry is not None
    stale_observer = MemoryManager(project_root=workspace, data_root=data_root)
    assert authority.delete_entry(MemoryScope.PROJECT, entry.id) is True

    stale_observer.record_retrievals([entry.id])

    assert all(
        candidate.id != entry.id
        for candidate in stale_observer.memories[MemoryScope.PROJECT].entries
    )
    restarted = MemoryManager(project_root=workspace, data_root=data_root)
    assert all(
        candidate.id != entry.id
        for candidate in restarted.memories[MemoryScope.PROJECT].entries
    )


def test_readonly_snapshot_with_only_project_authority_does_not_require_user_lock(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "memory-home"
    writer = MemoryManager(project_root=workspace, data_root=data_root)
    entry = writer.add_entry(
        MemoryScope.PROJECT,
        "pattern",
        "project-only checkout guidance",
        tags=["checkout"],
        approval_policy=MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED,
    )
    assert entry is not None
    user_lock = data_root / "memory-store.lock"
    user_lock.unlink()
    reader = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )

    with reader.retrieval_snapshot() as revision:
        assert len(revision) == len(MemoryScope)
        assert entry.id in reader.memories[MemoryScope.PROJECT]._id_index

    assert not user_lock.exists()


def test_covered_retrieval_snapshot_rejects_fenced_but_stale_live_state(
    tmp_path: Path,
) -> None:
    """A current disk revision must never label an older in-memory corpus."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "memory-home"
    stale = MemoryManager(project_root=workspace, data_root=data_root)
    writer = MemoryManager(project_root=workspace, data_root=data_root)
    inserted = writer.add_entry(
        MemoryScope.PROJECT,
        "pattern",
        "new checkout guidance",
        tags=["checkout"],
        approval_policy=MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED,
    )
    assert inserted is not None
    assert inserted.id not in stale.memories[MemoryScope.PROJECT]._id_index

    with stale._transaction_for_scopes(tuple(MemoryScope)):
        with pytest.raises(MemoryStoreConflict, match="stale|changed"):
            with stale.retrieval_snapshot():
                pass


def test_covered_refresh_rejects_fenced_but_stale_live_state(
    tmp_path: Path,
) -> None:
    """The explicit refresh API must not silently bless stale live entries."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "memory-home"
    stale = MemoryManager(project_root=workspace, data_root=data_root)
    writer = MemoryManager(project_root=workspace, data_root=data_root)
    inserted = writer.add_entry(
        MemoryScope.PROJECT,
        "pattern",
        "new refresh guidance",
        tags=["refresh"],
        approval_policy=MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED,
    )
    assert inserted is not None
    assert inserted.id not in stale.memories[MemoryScope.PROJECT]._id_index

    with stale._transaction_for_scopes(tuple(MemoryScope)):
        with pytest.raises(MemoryStoreConflict, match="stale|changed"):
            stale.refresh_if_stale()


def test_covered_retrieval_snapshot_reloads_unfenced_readonly_state(
    tmp_path: Path,
) -> None:
    """An unfenced compatibility view may be replaced before it is exposed."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "memory-home"
    readonly = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )
    writer = MemoryManager(project_root=workspace, data_root=data_root)
    inserted = writer.add_entry(
        MemoryScope.PROJECT,
        "pattern",
        "fresh checkout guidance",
        tags=["checkout"],
        approval_policy=MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED,
    )
    assert inserted is not None

    with readonly._transaction_for_scopes(tuple(MemoryScope)):
        with readonly.retrieval_snapshot():
            assert inserted.id in readonly.memories[MemoryScope.PROJECT]._id_index


def test_real_rejection_during_semantic_work_invalidates_result(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "memory-home"
    reader = MemoryManager(project_root=workspace, data_root=data_root)
    entry = reader.add_entry(
        MemoryScope.PROJECT,
        "pattern",
        "checkout guidance",
        tags=["checkout"],
        approval_policy=MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED,
    )
    assert entry is not None
    writer = MemoryManager(project_root=workspace, data_root=data_root)
    provider = _BlockingHybridProvider()
    retriever = CanonicalMemoryRetriever(reader, hybrid_provider=provider)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(retriever.retrieve, _request("checkout"))
        assert provider.started.wait(timeout=1.0)
        assert "Rejected memory" in writer.reject_entry(entry.id)
        provider.release.set()

        with pytest.raises(MemoryStoreConflict, match="changed during retrieval"):
            future.result(timeout=2.0)


def test_feedback_score_change_during_semantic_work_invalidates_result(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "memory-home"
    reader = MemoryManager(project_root=workspace, data_root=data_root)
    entry = reader.add_entry(
        MemoryScope.PROJECT,
        "pattern",
        "checkout guidance",
        tags=["checkout"],
        approval_policy=MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED,
    )
    assert entry is not None
    writer = MemoryManager(project_root=workspace, data_root=data_root)
    provider = _BlockingHybridProvider()
    retriever = CanonicalMemoryRetriever(reader, hybrid_provider=provider)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(retriever.retrieve, _request("checkout"))
        assert provider.started.wait(timeout=1.0)
        writer.record_feedback(
            [entry.id],
            True,
            observation_id="concurrent-feedback",
        )
        provider.release.set()

        with pytest.raises(MemoryStoreConflict, match="changed during retrieval"):
            future.result(timeout=2.0)
