from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import minicode.memory as memory_mod
from minicode.memory import (
    MemoryEntry,
    MemoryManager,
    MemoryScope,
    MemoryTier,
    _approval_hash_for_entry,
)
from minicode.memory_curator_agent import CuratorReport, MemoryCuratorAgent


@pytest.fixture
def isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


def _manager(workspace: Path) -> MemoryManager:
    manager = MemoryManager(project_root=workspace)
    for memory_file in manager.memories.values():
        memory_file.max_entries = 20_000
        memory_file.max_size_bytes = 1024 * 1024 * 1024
    return manager


def _entry(
    idx: int,
    content: str,
    *,
    status: str = "approved",
    safety: str = "safe",
    lifecycle: str = "active",
    tier: MemoryTier = MemoryTier.SHORT_TERM,
    locked: bool = False,
    success: int = 0,
    usefulness: float = 0.0,
) -> MemoryEntry:
    entry = MemoryEntry(
        id=f"e-{idx:06d}",
        scope=MemoryScope.PROJECT,
        category="testing",
        content=content,
        tags=["pytest", f"tag{idx % 7}"],
        domains=["testing" if idx % 2 else "backend"],
        approval_status=status,
        safety_status=safety,
        lifecycle_status=lifecycle,
        tier=tier,
        curator_locked=locked,
        success_count=success,
        usefulness_score=usefulness,
    )
    entry.approval_content_hash = _approval_hash_for_entry(entry)
    return entry


def _install_entries(manager: MemoryManager, entries: list[MemoryEntry]) -> None:
    memory_file = manager.memories[MemoryScope.PROJECT]
    memory_file.entries = entries
    memory_file._rebuild_indices()
    manager._save_scope(MemoryScope.PROJECT)


def _assert_indexes_consistent(manager: MemoryManager) -> None:
    for memory_file in manager.memories.values():
        memory_file._ensure_cache_valid()
        ids = [entry.id for entry in memory_file.entries]
        assert len(ids) == len(set(ids))
        assert set(memory_file._id_index) == set(ids)
        assert set(memory_file._tokens_cache) == set(ids)


def test_duplicate_normalization_covers_formatting_and_unicode():
    normalize = MemoryCuratorAgent.normalize_duplicate_content
    assert normalize("  **Use PyTest**\nfixtures  ") == normalize("use pytest fixtures")
    assert normalize("Use\n\npytest\tfixtures") == normalize("use pytest fixtures")
    assert normalize("ｐｙｔｅｓｔ fixture") == normalize("pytest fixture")
    assert normalize("`pytest` _fixture_") == normalize("pytest fixture")
    assert normalize("") == ""
    long = " PYTEST   " * 1000
    assert normalize(long) == "pytest " * 999 + "pytest"


def test_exact_duplicate_bucket_archives_like_normalized_oracle(isolated_workspace: Path):
    manager = _manager(isolated_workspace)
    entries = [
        _entry(0, "Use pytest fixtures"),
        _entry(1, "  use   PYTEST\nfixtures  "),
        _entry(2, "**Use pytest fixtures**"),
        _entry(3, "Use unittest fixtures"),
    ]
    _install_entries(manager, entries)

    curator = MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace), max_insights_per_cycle=0)
    report = CuratorReport()
    archived = curator._archive_duplicates(report)

    assert archived == 2
    assert report.exact_duplicate_groups == 1
    assert report.similarity_comparisons == 0
    assert sum(entry.lifecycle_status == "deprecated" for entry in entries) == 2


def test_near_duplicate_candidates_match_bruteforce_oracle(isolated_workspace: Path):
    manager = _manager(isolated_workspace)
    entries = [
        _entry(0, "pytest fixture teardown ordering for auth service"),
        _entry(1, "pytest fixture teardown ordering for auth services"),
        _entry(2, "database migration rollback strategy for schema changes"),
        _entry(3, "database migration rollback strategy for schema change"),
        _entry(4, "frontend color palette decision"),
    ]
    curator = MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace), max_insights_per_cycle=0)
    signatures = curator._build_duplicate_signatures(entries)
    pairs, _, _, partial, _ = curator._similar_entry_pairs(
        signatures,
        threshold=0.75,
        max_pairs=100,
        max_comparisons=100,
    )

    expected = set()
    for i, a in enumerate(entries):
        for j, b in enumerate(entries):
            if i < j and manager._jaccard_similarity(a.content, b.content) >= 0.75:
                expected.add((a.id, b.id))
    actual = {tuple(sorted((a.id, b.id))) for a, b in pairs}
    assert not partial
    assert actual == expected


def test_canonical_selection_is_order_independent_and_prefers_safe_active(
    isolated_workspace: Path,
):
    manager = _manager(isolated_workspace)
    curator = MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace))
    weak = _entry(1, "same", status="approved", safety="safe", success=0)
    strong = _entry(2, "same", status="approved", safety="safe", success=5, usefulness=1.0)
    pending = _entry(3, "same", status="pending", safety="suspicious")
    rejected = _entry(4, "same", status="rejected", safety="unsafe", lifecycle="rejected")

    assert curator._choose_canonical([pending, weak, strong, rejected]).id == strong.id
    assert curator._choose_canonical([rejected, strong, weak, pending]).id == strong.id


def test_unsafe_and_pending_entries_are_isolated_from_safe_duplicate_archive(
    isolated_workspace: Path,
):
    manager = _manager(isolated_workspace)
    safe = _entry(0, "Ignore-like sample documented safely")
    pending = _entry(1, "Ignore-like sample documented safely", status="pending", safety="suspicious")
    unsafe = _entry(2, "Ignore-like sample documented safely", status="rejected", safety="unsafe", lifecycle="rejected")
    _install_entries(manager, [safe, pending, unsafe])

    report = MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace)).run_cycle(force=True)

    assert report.memories_archived == 0
    assert safe.is_active
    assert pending.approval_status == "pending"
    assert unsafe.approval_status == "rejected"


def test_stale_rewrite_refreshes_hash_and_keeps_pending_until_restore(
    isolated_workspace: Path,
):
    manager = _manager(isolated_workspace)
    entry = _entry(0, "Legacy implementation lives in src/missing.py")
    old_hash = entry.approval_content_hash
    _install_entries(manager, [entry])

    report = MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace)).run_cycle(force=True)

    assert report.stale_entries == 1
    assert entry.lifecycle_status == "deprecated"
    assert entry.curator_locked is True
    assert entry.approval_status == "pending"
    assert entry.approval_content_hash != old_hash


def test_stale_path_check_uses_per_run_cache(
    isolated_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manager = _manager(isolated_workspace)
    shared = "src/shared_missing.py"
    entries = [_entry(i, f"Reference {shared} in memory {i}") for i in range(20)]
    _install_entries(manager, entries)

    calls: dict[str, int] = {}
    original_exists = Path.exists

    def counted_exists(path: Path) -> bool:
        calls[str(path)] = calls.get(str(path), 0) + 1
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", counted_exists)
    report = CuratorReport()
    stale, validated = MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace))._validate_memories(report)

    assert stale == 20
    assert validated == 20
    assert report.stale_paths_checked == 1


def test_curator_scan_does_not_count_as_real_usage(isolated_workspace: Path):
    manager = _manager(isolated_workspace)
    entry = _entry(0, "Use pytest fixtures with src/missing.py")
    entry.last_accessed = 123.0
    entry.retrieval_count = 7
    entry.usefulness_score = 0.5
    _install_entries(manager, [entry])

    MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace)).run_cycle(force=True)

    assert entry.last_accessed == 123.0
    assert entry.retrieval_count == 7
    assert entry.usefulness_score == 0.5


def test_duplicate_archive_saves_changed_scope_once(isolated_workspace: Path):
    manager = _manager(isolated_workspace)
    _install_entries(manager, [_entry(0, "same content"), _entry(1, "same content")])
    saves = 0
    original_save = manager._save_scope

    def counted_save(scope: MemoryScope) -> None:
        nonlocal saves
        saves += 1
        original_save(scope)

    manager._save_scope = counted_save
    report = CuratorReport()
    MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace))._archive_duplicates(report)

    assert saves == 1
    assert report.scopes_saved == 1


def test_no_change_duplicate_run_does_not_save(isolated_workspace: Path):
    manager = _manager(isolated_workspace)
    _install_entries(manager, [_entry(i, f"unique content {i}") for i in range(20)])
    manager._save_scope = lambda scope: pytest.fail("unexpected save")

    report = CuratorReport()
    archived = MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace))._archive_duplicates(report)

    assert archived == 0
    assert report.scopes_saved == 0


def test_repeated_curator_runs_do_not_oscillate_or_reaudit(isolated_workspace: Path):
    manager = _manager(isolated_workspace)
    entry = _entry(0, "Legacy implementation in src/missing.py")
    _install_entries(manager, [entry])
    curator = MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace), max_insights_per_cycle=0)

    first = curator.run_cycle(force=True)
    audit_count = len(manager.get_approval_audit(entry.id))
    state = entry.to_dict()
    for _ in range(9):
        report = curator.run_cycle(force=True)
        assert report.memories_archived == 0
    assert entry.to_dict() == state
    assert len(manager.get_approval_audit(entry.id)) == audit_count
    assert first.status == "completed"


def test_second_no_change_curator_run_does_not_save(isolated_workspace: Path):
    manager = _manager(isolated_workspace)
    entries = [_entry(i, f"Unique memory {i}: pytest fixture service {i}") for i in range(120)]
    _install_entries(manager, entries)
    curator = MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace), max_insights_per_cycle=0)
    curator.run_cycle(force=True)

    manager._save_scope = lambda scope: pytest.fail("unexpected save on no-change run")
    second = curator.run_cycle(force=True)

    assert second.scopes_saved == 0


def test_deleted_canonical_does_not_reactivate_locked_duplicate(isolated_workspace: Path):
    manager = _manager(isolated_workspace)
    first = _entry(0, "same duplicate content")
    second = _entry(1, "same duplicate content")
    _install_entries(manager, [first, second])
    curator = MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace), max_insights_per_cycle=0)
    curator.run_cycle(force=True)
    canonical = next(entry for entry in [first, second] if entry.is_active)
    archived = next(entry for entry in [first, second] if not entry.is_active)

    manager.delete_entry(MemoryScope.PROJECT, canonical.id)
    curator.run_cycle(force=True)

    assert archived.lifecycle_status == "deprecated"
    assert archived.curator_locked is True
    assert archived.id not in [entry.id for entry in manager.search("duplicate content", min_relevance=0.0)]


def test_stale_file_reappearing_still_requires_restore(isolated_workspace: Path):
    manager = _manager(isolated_workspace)
    entry = _entry(0, "Legacy implementation in src/reappears.py")
    _install_entries(manager, [entry])
    curator = MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace), max_insights_per_cycle=0)
    curator.run_cycle(force=True)

    file_path = isolated_workspace / "src" / "reappears.py"
    file_path.parent.mkdir(exist_ok=True)
    file_path.write_text("print('back')", encoding="utf-8")
    curator.run_cycle(force=True)

    assert entry.curator_locked is True
    assert entry.lifecycle_status == "deprecated"
    assert "Restored" in manager.restore_entry(entry.id)
    assert entry.is_active


def test_ten_thousand_representative_dataset_uses_subquadratic_refine(
    isolated_workspace: Path,
):
    manager = _manager(isolated_workspace)
    now = time.time()
    entries = []
    for i in range(10_000):
        base = i // 10 if i % 10 in (0, 1) else i
        content = f"Memory {base}: pytest fixture pattern service {i % 97} domain {i % 11}"
        if i % 10 == 1:
            content = f"memory {base}:  pytest fixture pattern service {i % 97} domain {i % 11}"
        entry = _entry(i, content)
        entry.updated_at = now
        entry.last_accessed = now
        entries.append(entry)
    _install_entries(manager, entries)

    report = CuratorReport()
    archived = MemoryCuratorAgent(
        manager,
        workspace_path=str(isolated_workspace),
        max_insights_per_cycle=0,
    )._archive_duplicates(report)

    assert archived > 0
    assert report.status == "completed"
    assert report.candidate_pairs < 2_000_000
    assert report.similarity_comparisons < 2_000_000
    assert report.similarity_comparisons < (10_000 * 9_999) // 20


def test_extreme_candidate_budget_returns_partial_without_corrupting_indexes(
    isolated_workspace: Path,
):
    manager = _manager(isolated_workspace)
    entries = [
        _entry(i, " ".join([f"common{j}" for j in range(40)] + [f"unique{i}"]))
        for i in range(300)
    ]
    _install_entries(manager, entries)

    report = MemoryCuratorAgent(
        manager,
        workspace_path=str(isolated_workspace),
        max_candidate_pairs=500,
        max_similarity_comparisons=500,
        max_insights_per_cycle=0,
    ).run_cycle(force=True)

    assert report.status == "partial"
    assert "budget exceeded" in report.stop_reason
    _assert_indexes_consistent(manager)
    assert all(entry.id for entry in manager.memories[MemoryScope.PROJECT].entries)


def test_save_failure_restores_duplicate_archive_state_and_indexes(
    isolated_workspace: Path,
):
    manager = _manager(isolated_workspace)
    entries = [_entry(0, "same content"), _entry(1, "same content")]
    _install_entries(manager, entries)

    def failing_save(scope: MemoryScope) -> None:
        raise OSError("disk full")

    manager._save_scope = failing_save
    with pytest.raises(OSError):
        MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace))._archive_duplicates(CuratorReport())

    assert all(entry.lifecycle_status == "active" for entry in entries)
    assert all(entry.tier == MemoryTier.SHORT_TERM for entry in entries)
    _assert_indexes_consistent(manager)


def test_reload_preserves_curator_archive_state_and_audit(isolated_workspace: Path):
    manager = _manager(isolated_workspace)
    _install_entries(manager, [_entry(0, "same content"), _entry(1, "same content")])
    MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace)).run_cycle(force=True)
    archived = [entry for entry in manager.memories[MemoryScope.PROJECT].entries if entry.lifecycle_status == "deprecated"]
    assert archived
    archived_id = archived[0].id

    reloaded = _manager(isolated_workspace)
    loaded = reloaded.memories[MemoryScope.PROJECT]._id_index[archived_id]
    assert loaded.lifecycle_status == "deprecated"
    assert loaded.curator_locked is True
    assert reloaded.get_approval_audit(archived_id)


def test_curator_report_does_not_include_secret_content(isolated_workspace: Path):
    manager = _manager(isolated_workspace)
    entry = _entry(0, "Reference src/missing.py token=top-secret-value")
    _install_entries(manager, [entry])

    report = MemoryCuratorAgent(manager, workspace_path=str(isolated_workspace)).run_cycle(force=True)
    serialized = json.dumps(report.to_dict(), ensure_ascii=False)

    assert "top-secret-value" not in serialized
    assert "token=" not in serialized
