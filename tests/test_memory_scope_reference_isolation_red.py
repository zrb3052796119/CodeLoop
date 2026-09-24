"""Scope-local reference contracts for durable Memory authority.

Memory roots are committed independently.  A relation in one authority must
therefore never mutate, supersede, or depend on an entry owned by another
scope.  These regressions keep valid same-scope relations working while
requiring cross-scope references to fail before either root is changed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from minicode.memory import (
    MemoryApprovalPolicy,
    MemoryManager,
    MemoryScope,
    _approval_hash_for_entry,
)
from minicode.memory_store import MemoryStoreConflict


def _manager(tmp_path: Path) -> tuple[MemoryManager, Path, Path]:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "data"
    workspace.mkdir()
    data_root.mkdir()
    return (
        MemoryManager(project_root=workspace, data_root=data_root),
        workspace,
        data_root,
    )


def _authority_paths(workspace: Path, data_root: Path) -> dict[str, Path]:
    return {
        "user": data_root / "memory" / "memory.json",
        "project": workspace / ".mini-code-memory" / "memory.json",
    }


def _authority_bytes(paths: dict[str, Path]) -> dict[str, bytes]:
    return {scope: path.read_bytes() for scope, path in paths.items()}


def test_approval_rejects_project_supersession_of_user_authority_atomically(
    tmp_path: Path,
) -> None:
    manager, workspace, data_root = _manager(tmp_path)
    user = manager.add_entry(
        MemoryScope.USER,
        "testing",
        "Keep the user-wide parser recovery policy active.",
    )
    replacement = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Use the project parser recovery policy for this repository.",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert user is not None and replacement is not None
    assert replacement.approval_status == "pending"
    # Admission already rejects a persisted cross-scope relationship. Seal a
    # live candidate here so the approval transition itself remains covered
    # as a defense-in-depth capability boundary.
    replacement.metadata["supersedes"] = [user.id]
    replacement.approval_content_hash = _approval_hash_for_entry(replacement)
    replacement._seal_authority_state()
    paths = _authority_paths(workspace, data_root)
    before = _authority_bytes(paths)

    result = manager.approve_entry(
        replacement.id,
        actor="test",
        reason="exercise the scope isolation contract",
    )

    assert "blocked" in result.lower()
    assert _authority_bytes(paths) == before
    reloaded = MemoryManager(project_root=workspace, data_root=data_root)
    assert reloaded.memories[MemoryScope.USER]._id_index[user.id].is_active
    assert (
        reloaded.memories[MemoryScope.PROJECT]
        ._id_index[replacement.id]
        .approval_status
        == "pending"
    )


def test_apply_supersession_rejects_cross_scope_reference_atomically(
    tmp_path: Path,
) -> None:
    manager, workspace, data_root = _manager(tmp_path)
    user = manager.add_entry(
        MemoryScope.USER,
        "testing",
        "Preserve the global checkout recovery lesson.",
    )
    replacement = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Use the repository checkout recovery lesson.",
    )
    assert user is not None and replacement is not None
    replacement.metadata["supersedes"] = [user.id]
    replacement.approval_content_hash = _approval_hash_for_entry(replacement)
    replacement._seal_authority_state()
    paths = _authority_paths(workspace, data_root)
    before = _authority_bytes(paths)

    with pytest.raises(MemoryStoreConflict, match=r"cross.*scope"):
        manager.apply_reflection_supersession(replacement.id)

    assert _authority_bytes(paths) == before
    reloaded = MemoryManager(project_root=workspace, data_root=data_root)
    assert reloaded.memories[MemoryScope.USER]._id_index[user.id].is_active
    assert (
        reloaded.memories[MemoryScope.PROJECT]
        ._id_index[replacement.id]
        .is_active
    )


def test_versioned_cross_scope_related_to_fails_normal_and_readonly_load(
    tmp_path: Path,
) -> None:
    manager, workspace, data_root = _manager(tmp_path)
    user = manager.add_entry(
        MemoryScope.USER,
        "testing",
        "User-owned parser policy target.",
    )
    project = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Project-owned parser policy source.",
    )
    assert user is not None and project is not None
    paths = _authority_paths(workspace, data_root)
    project_authority = json.loads(paths["project"].read_text(encoding="utf-8"))
    stored_project = next(
        entry
        for entry in project_authority["entries"]
        if entry["id"] == project.id
    )
    stored_project["related_to"] = [user.id]
    paths["project"].write_text(
        json.dumps(project_authority, ensure_ascii=False),
        encoding="utf-8",
    )
    before = _authority_bytes(paths)

    with pytest.raises(MemoryStoreConflict, match="cross-scope"):
        MemoryManager(project_root=workspace, data_root=data_root)
    with pytest.raises(MemoryStoreConflict, match="cross-scope"):
        MemoryManager(
            project_root=workspace,
            data_root=data_root,
            readonly_load=True,
        )

    assert _authority_bytes(paths) == before


def test_same_scope_supersession_remains_supported(tmp_path: Path) -> None:
    manager, workspace, data_root = _manager(tmp_path)
    previous = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Use the first project-local parser recovery policy.",
    )
    replacement = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Use the corrected project-local parser recovery policy.",
        metadata={"supersedes": [previous.id if previous is not None else ""]},
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert previous is not None and replacement is not None

    result = manager.approve_entry(
        replacement.id,
        actor="test",
        reason="verified replacement",
    )

    assert result == f"Approved memory {replacement.id}."
    reloaded = MemoryManager(project_root=workspace, data_root=data_root)
    old = reloaded.memories[MemoryScope.PROJECT]._id_index[previous.id]
    new = reloaded.memories[MemoryScope.PROJECT]._id_index[replacement.id]
    assert old.approval_status == "rejected"
    assert old.lifecycle_status in {"rejected", "superseded"}
    assert old.metadata["superseded_by"] == replacement.id
    assert new.is_active


def test_versioned_same_scope_related_to_remains_loadable(tmp_path: Path) -> None:
    manager, workspace, data_root = _manager(tmp_path)
    first = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "First project-local parser recovery lesson.",
    )
    second = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Second project-local parser recovery lesson.",
    )
    assert first is not None and second is not None
    project_authority = workspace / ".mini-code-memory" / "memory.json"
    authority = json.loads(project_authority.read_text(encoding="utf-8"))
    stored_second = next(
        entry for entry in authority["entries"] if entry["id"] == second.id
    )
    stored_second["related_to"] = [first.id]
    project_authority.write_text(
        json.dumps(authority, ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = MemoryManager(project_root=workspace, data_root=data_root)
    readonly = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )

    assert (
        loaded.memories[MemoryScope.PROJECT]
        ._id_index[second.id]
        .related_to
        == [first.id]
    )
    assert (
        readonly.memories[MemoryScope.PROJECT]
        ._id_index[second.id]
        .related_to
        == [first.id]
    )
