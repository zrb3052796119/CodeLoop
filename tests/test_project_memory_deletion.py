from __future__ import annotations

import hashlib
import multiprocessing
import threading
from pathlib import Path

import pytest

import minicode.memory as memory_module
from minicode.memory import MemoryManager, MemoryScope
from minicode.project_memory_deletion import (
    ProjectMemoryDeletionAuthority,
    ProjectMemoryDeletionError,
)


def _delete_project_memory_in_process(
    workspace: str,
    data_dir: str,
    memory_id: str,
    revision: str,
    barrier: object,
    results: object,
) -> None:
    import minicode.memory as child_memory_module

    child_memory_module.MINI_CODE_DIR = Path(data_dir)
    barrier.wait(timeout=10)  # type: ignore[attr-defined]
    try:
        result = ProjectMemoryDeletionAuthority(
            workspace,
            data_dir=data_dir,
        ).delete(memory_id, revision)
        results.put(("result", result["status"]))  # type: ignore[attr-defined]
    except ProjectMemoryDeletionError as error:
        results.put(("error", error.code))  # type: ignore[attr-defined]


@pytest.fixture
def memory_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr(memory_module, "MINI_CODE_DIR", data_dir)
    return workspace, data_dir


def _file_facts(root: Path) -> dict[str, tuple[str, int, int]]:
    facts: dict[str, tuple[str, int, int]] = {}
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            raw = path.read_bytes()
            facts[path.relative_to(root).as_posix()] = (
                hashlib.sha256(raw).hexdigest(),
                len(raw),
                path.stat().st_mtime_ns,
            )
    return facts


def test_project_memory_authority_removes_entry_audit_and_backlinks_only(
    memory_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = memory_workspace
    manager = MemoryManager(project_root=workspace)
    target = manager.add_entry(
        MemoryScope.PROJECT,
        "note",
        "private target memory must disappear",
    )
    neighbor = manager.add_entry(
        MemoryScope.PROJECT,
        "architecture",
        "adjacent project memory remains",
    )
    user = manager.add_entry(MemoryScope.USER, "note", "user remains")
    local = manager.add_entry(MemoryScope.LOCAL, "note", "local remains")
    assert target is not None and neighbor is not None
    assert user is not None and local is not None

    def link() -> None:
        current = manager.memories[MemoryScope.PROJECT]._id_index[neighbor.id]
        current.related_to = [target.id, target.id]
        manager._save_scope(MemoryScope.PROJECT)

    manager.coordinated_write((MemoryScope.PROJECT,), link)
    authority = ProjectMemoryDeletionAuthority(
        workspace,
        data_dir=data_dir,
    )
    before_preview = _file_facts(workspace)

    preview = authority.snapshot(target.id)

    assert preview["status"] == "ready"
    assert preview["target"] == {
        "memoryId": target.id,
        "scope": "project",
        "category": "note",
        "tier": "short_term",
        "lifecycleStatus": "active",
        "approvalStatus": "approved",
    }
    assert preview["affected"] == {
        "entries": 1,
        "approvalAuditRecords": 1,
        "backlinks": 2,
    }
    assert _file_facts(workspace) == before_preview
    assert not (data_dir / "dashboard").exists()
    assert "private target" not in str(preview)
    assert str(workspace) not in str(preview)

    result = authority.delete(target.id, str(preview["deletionRevision"]))

    assert result["status"] == "completed"
    assert result["deleted"] == {
        "entries": 1,
        "approvalAuditRecords": 1,
        "backlinks": 2,
    }
    reloaded = MemoryManager(project_root=workspace)
    project = reloaded.memories[MemoryScope.PROJECT]
    assert target.id not in project._id_index
    assert project._id_index[neighbor.id].related_to == []
    assert reloaded.get_approval_audit(target.id) == []
    assert user.id in reloaded.memories[MemoryScope.USER]._id_index
    assert local.id in reloaded.memories[MemoryScope.LOCAL]._id_index

    duplicate = authority.delete(target.id, str(preview["deletionRevision"]))
    assert duplicate["status"] == "already_absent"

    with pytest.raises(ProjectMemoryDeletionError) as forged:
        authority.delete("project-forged", str(preview["deletionRevision"]))
    assert forged.value.code == "deletion_target_not_found"


def test_project_memory_revision_rejects_update_after_preview(
    memory_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = memory_workspace
    manager = MemoryManager(project_root=workspace)
    target = manager.add_entry(MemoryScope.PROJECT, "note", "original")
    assert target is not None
    authority = ProjectMemoryDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority.snapshot(target.id)

    updater = MemoryManager(project_root=workspace)
    assert updater.update_entry(MemoryScope.PROJECT, target.id, "changed")

    with pytest.raises(ProjectMemoryDeletionError) as stale:
        authority.delete(target.id, str(preview["deletionRevision"]))
    assert stale.value.code == "deletion_revision_stale"
    assert (
        MemoryManager(project_root=workspace)
        .memories[MemoryScope.PROJECT]
        ._id_index[target.id]
        .content
        == "changed"
    )


@pytest.mark.parametrize("change", ["approval", "backlink"])
def test_project_memory_revision_rejects_approval_or_backlink_change(
    memory_workspace: tuple[Path, Path],
    change: str,
) -> None:
    workspace, data_dir = memory_workspace
    manager = MemoryManager(project_root=workspace)
    target = manager.add_entry(MemoryScope.PROJECT, "note", "revision target")
    neighbor = manager.add_entry(MemoryScope.PROJECT, "note", "revision neighbor")
    assert target is not None and neighbor is not None
    authority = ProjectMemoryDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority.snapshot(target.id)

    if change == "approval":
        assert manager.reject_entry(target.id).startswith("Rejected memory")
    else:
        def add_backlink() -> None:
            current = manager.memories[MemoryScope.PROJECT]._id_index[neighbor.id]
            current.related_to.append(target.id)
            manager._save_scope(MemoryScope.PROJECT)

        manager.coordinated_write((MemoryScope.PROJECT,), add_backlink)

    with pytest.raises(ProjectMemoryDeletionError) as stale:
        authority.delete(target.id, str(preview["deletionRevision"]))
    assert stale.value.code == "deletion_revision_stale"
    assert target.id in (
        MemoryManager(project_root=workspace)
        .memories[MemoryScope.PROJECT]
        ._id_index
    )


@pytest.mark.parametrize(
    "lifecycle_status",
    ["pending", "active", "rejected", "held", "archived"],
)
def test_project_memory_supported_lifecycle_states_remain_deletable(
    memory_workspace: tuple[Path, Path],
    lifecycle_status: str,
) -> None:
    workspace, data_dir = memory_workspace
    manager = MemoryManager(project_root=workspace)
    target = manager.add_entry(MemoryScope.PROJECT, "note", "lifecycle target")
    assert target is not None

    def set_lifecycle() -> None:
        current = manager.memories[MemoryScope.PROJECT]._id_index[target.id]
        current.lifecycle_status = lifecycle_status
        manager._save_scope(MemoryScope.PROJECT)

    manager.coordinated_write((MemoryScope.PROJECT,), set_lifecycle)
    authority = ProjectMemoryDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority.snapshot(target.id)

    assert preview["status"] == "ready"
    assert preview["target"]["lifecycleStatus"] == lifecycle_status
    result = authority.delete(target.id, str(preview["deletionRevision"]))
    assert result["status"] == "completed"


def test_project_memory_unknown_metadata_fails_closed(
    memory_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = memory_workspace
    manager = MemoryManager(project_root=workspace)
    target = manager.add_entry(MemoryScope.PROJECT, "note", "metadata target")
    assert target is not None

    def make_unknown() -> None:
        current = manager.memories[MemoryScope.PROJECT]._id_index[target.id]
        current.lifecycle_status = "private-unknown-status"
        manager._save_scope(MemoryScope.PROJECT)

    manager.coordinated_write((MemoryScope.PROJECT,), make_unknown)
    authority = ProjectMemoryDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority.snapshot(target.id)

    assert preview["status"] == "unavailable"
    assert preview["target"]["lifecycleStatus"] == "unknown"
    assert preview["diagnostics"] == [{"code": "memory_metadata_invalid"}]
    assert "private-unknown-status" not in str(preview)
    with pytest.raises(ProjectMemoryDeletionError) as unavailable:
        authority.delete(target.id, str(preview["deletionRevision"]))
    assert unavailable.value.code == "deletion_unavailable"


def test_project_memory_symlinked_authority_file_fails_closed(
    memory_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = memory_workspace
    manager = MemoryManager(project_root=workspace)
    target = manager.add_entry(MemoryScope.PROJECT, "note", "symlink target")
    assert target is not None
    memory_path = workspace / ".mini-code-memory" / "memory.json"
    outside = workspace.parent / "outside-memory.json"
    outside.write_text(memory_path.read_text(encoding="utf-8"), encoding="utf-8")
    memory_path.unlink()
    memory_path.symlink_to(outside)
    authority = ProjectMemoryDeletionAuthority(workspace, data_dir=data_dir)

    with pytest.raises(ProjectMemoryDeletionError) as unavailable:
        authority.snapshot(target.id)
    assert unavailable.value.code == "deletion_unavailable"
    assert "symlink target" in outside.read_text(encoding="utf-8")


def test_project_memory_orphan_audit_and_backlink_are_previewed_and_cleaned(
    memory_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = memory_workspace
    manager = MemoryManager(project_root=workspace)
    target = manager.add_entry(MemoryScope.PROJECT, "note", "orphan target")
    neighbor = manager.add_entry(
        MemoryScope.PROJECT, "architecture", "neighbor remains"
    )
    assert target is not None and neighbor is not None

    def orphan() -> None:
        manager.memories[MemoryScope.PROJECT]._id_index[neighbor.id].related_to = [
            target.id
        ]
        assert manager.memories[MemoryScope.PROJECT].delete_entry(target.id)
        manager._save_scope(MemoryScope.PROJECT)

    manager.coordinated_write((MemoryScope.PROJECT,), orphan)
    authority = ProjectMemoryDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority.snapshot(target.id)

    assert preview["status"] == "ready"
    assert preview["target"]["category"] == "unknown"
    assert preview["affected"] == {
        "entries": 0,
        "approvalAuditRecords": 1,
        "backlinks": 0,
    }
    result = authority.delete(target.id, str(preview["deletionRevision"]))
    assert result["status"] == "completed"
    reloaded = MemoryManager(project_root=workspace)
    assert reloaded.get_approval_audit(target.id) == []
    assert (
        reloaded.memories[MemoryScope.PROJECT]
        ._id_index[neighbor.id]
        .related_to
        == []
    )


def test_project_memory_deletion_does_not_depend_on_audit_projection(
    memory_workspace: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, data_dir = memory_workspace
    manager = MemoryManager(project_root=workspace)
    target = manager.add_entry(MemoryScope.PROJECT, "note", "partial target")
    assert target is not None
    authority = ProjectMemoryDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority.snapshot(target.id)
    original_save_audit = MemoryManager._save_approval_audit

    def fail_audit(self: MemoryManager, scope: MemoryScope) -> None:
        raise OSError("private audit path must not escape")

    monkeypatch.setattr(MemoryManager, "_save_approval_audit", fail_audit)
    result = authority.delete(target.id, str(preview["deletionRevision"]))

    assert result["status"] == "completed"
    assert str(workspace) not in str(result)
    monkeypatch.setattr(MemoryManager, "_save_approval_audit", original_save_audit)
    restarted = ProjectMemoryDeletionAuthority(workspace, data_dir=data_dir)
    restart_preview = restarted.snapshot(target.id)
    assert restart_preview["status"] == "completed"
    assert restart_preview["affected"] == {
        "entries": 0,
        "approvalAuditRecords": 0,
        "backlinks": 0,
    }
    assert MemoryManager(project_root=workspace).get_approval_audit(target.id) == []


def test_concurrent_project_memory_delete_reconciles_through_receipt(
    memory_workspace: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, data_dir = memory_workspace
    manager = MemoryManager(project_root=workspace)
    target = manager.add_entry(MemoryScope.PROJECT, "note", "concurrent target")
    assert target is not None
    authority_one = ProjectMemoryDeletionAuthority(workspace, data_dir=data_dir)
    authority_two = ProjectMemoryDeletionAuthority(workspace, data_dir=data_dir)
    preview = authority_one.snapshot(target.id)
    revision = str(preview["deletionRevision"])

    first_commit_written = threading.Event()
    allow_first_to_finish = threading.Event()
    original_save_scope = MemoryManager._save_scope
    save_calls = 0
    save_calls_lock = threading.Lock()

    def pause_after_first_memory_commit(
        self: MemoryManager,
        scope: MemoryScope,
    ) -> None:
        nonlocal save_calls
        original_save_scope(self, scope)
        with save_calls_lock:
            save_calls += 1
            is_first = save_calls == 1
        if is_first:
            first_commit_written.set()
            assert allow_first_to_finish.wait(timeout=5)

    monkeypatch.setattr(MemoryManager, "_save_scope", pause_after_first_memory_commit)
    outcomes: list[dict[str, object] | BaseException] = []

    def run_delete(authority: ProjectMemoryDeletionAuthority) -> None:
        try:
            outcomes.append(authority.delete(target.id, revision))
        except BaseException as error:  # capture the competing result for assertion
            outcomes.append(error)

    first = threading.Thread(target=run_delete, args=(authority_one,))
    second = threading.Thread(target=run_delete, args=(authority_two,))
    first.start()
    assert first_commit_written.wait(timeout=5)
    second.start()
    allow_first_to_finish.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert not any(isinstance(outcome, BaseException) for outcome in outcomes)
    assert sorted(str(outcome["status"]) for outcome in outcomes) == [
        "already_absent",
        "completed",
    ]


def test_two_processes_deleting_same_project_memory_are_idempotent(
    memory_workspace: tuple[Path, Path],
) -> None:
    workspace, data_dir = memory_workspace
    manager = MemoryManager(project_root=workspace)
    target = manager.add_entry(MemoryScope.PROJECT, "note", "process target")
    assert target is not None
    authority = ProjectMemoryDeletionAuthority(workspace, data_dir=data_dir)
    revision = str(authority.snapshot(target.id)["deletionRevision"])
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_delete_project_memory_in_process,
            args=(
                str(workspace),
                str(data_dir),
                target.id,
                revision,
                barrier,
                results,
            ),
        )
        for _ in range(2)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)

    assert all(not process.is_alive() for process in processes)
    assert [process.exitcode for process in processes] == [0, 0]
    outcomes = sorted(results.get(timeout=2) for _ in processes)
    assert outcomes == [
        ("result", "already_absent"),
        ("result", "completed"),
    ]
