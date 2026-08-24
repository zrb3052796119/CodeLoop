"""Strong negative evidence must stop a bad Memory from being injected again."""

from __future__ import annotations

from pathlib import Path

import pytest

from minicode.memory import MemoryApprovalPolicy, MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline
from minicode.memory_retrieval import CanonicalMemoryRetriever, MemoryRetrievalRequest


def _active_rule(manager: MemoryManager):
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Invoice retry tests use the verified bounded failure schedule.",
        tags=["invoice", "retry", "tests"],
    )
    assert entry is not None
    return entry


def _reload_entry(project_root: Path, entry_id: str):
    manager = MemoryManager(project_root=project_root)
    return manager, manager.memories[MemoryScope.PROJECT]._id_index[entry_id]


def _rendered(manager: MemoryManager, entry_id: str) -> bool:
    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(query="invoice retry tests bounded schedule")
    )
    return entry_id in result.rendered_ids


def test_explicit_user_correction_quarantines_rendered_memory_immediately(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(project_root=tmp_path)
    entry = _active_rule(manager)
    assert _rendered(manager, entry.id)

    manager.record_corroborated_feedback(
        [entry.id],
        success=False,
        source="explicit_user_correction",
    )

    reloaded, updated = _reload_entry(tmp_path, entry.id)
    assert updated.corroborated_failure_count == 1
    assert updated.approval_status == "rejected"
    assert updated.lifecycle_status == "rejected"
    assert not _rendered(reloaded, entry.id)
    quarantine = [
        item
        for item in reloaded.get_approval_audit(entry.id)
        if item.get("action") == "feedback_quarantine"
    ]
    assert quarantine
    assert quarantine[-1]["actor"] == "explicit_user_correction"


def test_one_independent_verification_failure_does_not_overrule_memory(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(project_root=tmp_path)
    entry = _active_rule(manager)

    manager.record_corroborated_feedback(
        [entry.id],
        success=False,
        source="independent_verification",
        observation_id="run_" + "1" * 32,
    )

    reloaded, updated = _reload_entry(tmp_path, entry.id)
    assert updated.corroborated_failure_count == 1
    assert updated.approval_status == "approved"
    assert updated.lifecycle_status == "active"
    assert _rendered(reloaded, entry.id)


def test_replayed_independent_observation_is_idempotent_across_fresh_managers(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(project_root=tmp_path)
    entry = _active_rule(manager)
    observation_id = "run_" + "a" * 32

    manager.record_corroborated_feedback(
        [entry.id],
        success=False,
        source="independent_verification",
        observation_id=observation_id,
    )
    MemoryManager(project_root=tmp_path).record_corroborated_feedback(
        [entry.id],
        success=False,
        source="independent_verification",
        observation_id=observation_id,
    )

    _reloaded, updated = _reload_entry(tmp_path, entry.id)
    assert updated.corroborated_failure_count == 1
    assert updated.approval_status == "approved"
    assert updated.lifecycle_status == "active"
    assert len(updated.feedback_observations) == 1
    authority = (tmp_path / ".mini-code-memory" / "memory.json").read_text()
    assert observation_id not in authority


def test_distinct_independent_verification_failures_quarantine_memory(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(project_root=tmp_path)
    entry = _active_rule(manager)

    for index in range(2):
        MemoryManager(project_root=tmp_path).record_corroborated_feedback(
            [entry.id],
            success=False,
            source="independent_verification",
            observation_id=f"run_{index + 1:032x}",
        )

    reloaded, updated = _reload_entry(tmp_path, entry.id)
    assert updated.corroborated_failure_count == 2
    assert updated.approval_status == "rejected"
    assert updated.lifecycle_status == "rejected"
    assert not _rendered(reloaded, entry.id)


def test_whole_turn_failures_alone_never_quarantine_memory(tmp_path: Path) -> None:
    manager = MemoryManager(project_root=tmp_path)
    entry = _active_rule(manager)

    for _ in range(3):
        manager.record_feedback([entry.id], success=False)

    reloaded, updated = _reload_entry(tmp_path, entry.id)
    assert updated.failure_count == 3
    assert updated.approval_status == "approved"
    assert updated.lifecycle_status == "active"
    assert _rendered(reloaded, entry.id)


def test_explicit_correction_quarantines_pending_memory_after_a_state_race(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(project_root=tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Pending invoice retry advice awaiting review.",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    assert entry.approval_status == "pending"

    manager.record_corroborated_feedback(
        [entry.id],
        success=False,
        source="explicit_user_correction",
    )

    _reloaded, updated = _reload_entry(tmp_path, entry.id)
    assert updated.approval_status == "rejected"
    assert updated.lifecycle_status == "rejected"


def test_feedback_source_and_polarity_must_agree(tmp_path: Path) -> None:
    manager = MemoryManager(project_root=tmp_path)
    entry = _active_rule(manager)

    with pytest.raises(ValueError, match="cannot be successful"):
        manager.record_corroborated_feedback(
            [entry.id],
            success=True,
            source="explicit_user_reject",
        )

    with pytest.raises(ValueError, match="non-empty observation_id"):
        manager.record_corroborated_feedback(
            [entry.id],
            success=False,
            source="independent_verification",
        )

    observation_id = "run_" + "c" * 32
    manager.record_corroborated_feedback(
        [entry.id],
        success=False,
        source="independent_verification",
        observation_id=observation_id,
    )
    with pytest.raises(ValueError, match="conflicts with its first result"):
        MemoryManager(project_root=tmp_path).record_corroborated_feedback(
            [entry.id],
            success=True,
            source="independent_verification",
            observation_id=observation_id,
        )

    _reloaded, unchanged = _reload_entry(tmp_path, entry.id)
    assert unchanged.corroborated_success_count == 0
    assert unchanged.corroborated_failure_count == 1
    assert unchanged.approval_status == "approved"


def test_two_bound_fresh_turn_failures_quarantine_only_the_exact_memory_subset(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(project_root=tmp_path)
    disproved = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Invoice retry tests use the verified bounded failure schedule.",
        tags=["invoice", "retry", "tests"],
    )
    unrelated = manager.add_entry(
        MemoryScope.PROJECT,
        "release",
        "Release coverage reports use the verified branch aggregation window.",
        tags=["release", "coverage", "reports"],
    )
    assert disproved is not None and unrelated is not None

    for index in range(2):
        fresh_manager = MemoryManager(project_root=tmp_path)
        pipeline = MemoryPipeline(fresh_manager)
        pipeline.initialize(
            workspace_path=str(tmp_path),
            enable_reranker=False,
            enable_vector=False,
        )
        pipeline.inject(
            "review invoice retry tests and release coverage reports",
            [],
            [{"role": "system", "content": "system"}],
            context_usage=0.5,
            max_memories=2,
            min_relevance=0.0,
        )
        assert set(pipeline._last_injected_ids) == {disproved.id, unrelated.id}
        pipeline.feedback(
            True,
            verification_failed=1,
            verification_memory_ids=[disproved.id],
            observation_id=f"run_{index + 1:032x}",
        )

    reloaded = MemoryManager(project_root=tmp_path)
    disproved_after = reloaded.memories[MemoryScope.PROJECT]._id_index[disproved.id]
    unrelated_after = reloaded.memories[MemoryScope.PROJECT]._id_index[unrelated.id]
    assert disproved_after.corroborated_failure_count == 2
    assert disproved_after.approval_status == "rejected"
    assert disproved_after.lifecycle_status == "rejected"
    assert unrelated_after.corroborated_failure_count == 0
    assert unrelated_after.approval_status == "approved"
    assert unrelated_after.lifecycle_status == "active"
