"""The plain MemoryManager must not write outside its scope's owner.

A symlinked scope root (or a symlinked store file inside it) silently
relocated `memory.json`, `MEMORY.md`, and the approval audit anywhere on
disk. The Dashboard approval authority already refused exactly this shape,
so the same store was reachable under two different safety rules.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import minicode.memory as memory_module
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_store import MemoryStoreUnsafePath


STORE_FILENAMES = ("memory.json", "MEMORY.md", "approval_audit.json")


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr(memory_module, "MINI_CODE_DIR", home)
    monkeypatch.setattr(
        memory_module.MemoryPaths,
        "for_workspace",
        classmethod(
            lambda cls, workspace: cls(
                user_memory=home / "memory",
                project_memory=Path(workspace) / ".mini-code-memory",
                local_memory=Path(workspace) / ".mini-code-memory-local",
            )
        ),
    )
    return home


def _workspace(tmp_path: Path, name: str = "ws") -> tuple[Path, Path]:
    workspace = tmp_path / name
    outside = tmp_path / f"{name}-outside"
    workspace.mkdir(parents=True)
    outside.mkdir(parents=True)
    return workspace, outside


@pytest.mark.parametrize(
    ("scope", "root_name"),
    [
        (MemoryScope.PROJECT, ".mini-code-memory"),
        (MemoryScope.LOCAL, ".mini-code-memory-local"),
    ],
)
def test_symlinked_scope_root_is_refused_without_writing_outside(
    tmp_path: Path, isolated_home: Path, scope: MemoryScope, root_name: str
) -> None:
    workspace, outside = _workspace(tmp_path)
    (workspace / root_name).symlink_to(outside)
    manager = MemoryManager(project_root=workspace)

    with pytest.raises(MemoryStoreUnsafePath):
        manager.add_entry(scope, "testing", "SENTINEL escaped content")

    assert [p for p in outside.rglob("*") if p.is_file()] == []


@pytest.mark.parametrize("filename", STORE_FILENAMES)
def test_symlinked_store_file_is_refused_and_target_left_untouched(
    tmp_path: Path, isolated_home: Path, filename: str
) -> None:
    workspace, outside = _workspace(tmp_path)
    (workspace / ".mini-code-memory").mkdir()
    target = outside / filename
    original = '{"scope":"project","entries":[],"marker":"untouched"}'
    target.write_text(original, encoding="utf-8")
    (workspace / ".mini-code-memory" / filename).symlink_to(target)
    manager = MemoryManager(project_root=workspace)

    with pytest.raises(MemoryStoreUnsafePath):
        manager.add_entry(MemoryScope.PROJECT, "testing", "SENTINEL escaped content")

    assert target.read_text(encoding="utf-8") == original
    assert "SENTINEL" not in target.read_text(encoding="utf-8")


def test_project_root_whose_parent_is_not_the_workspace_is_refused(
    tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Containment must not depend on the root being a symlink — a path
    pointing outright at another directory is the same escape."""
    workspace, outside = _workspace(tmp_path)
    monkeypatch.setattr(
        memory_module.MemoryPaths,
        "for_workspace",
        classmethod(
            lambda cls, ws: cls(
                user_memory=isolated_home / "memory",
                project_memory=outside / ".mini-code-memory",
                local_memory=Path(ws) / ".mini-code-memory-local",
            )
        ),
    )
    manager = MemoryManager(project_root=workspace)

    with pytest.raises(MemoryStoreUnsafePath):
        manager.add_entry(MemoryScope.PROJECT, "testing", "SENTINEL escaped content")


def test_user_scope_outside_the_workspace_still_works(
    tmp_path: Path, isolated_home: Path
) -> None:
    """USER memory legitimately lives under the home data dir, so the
    workspace-containment rule must apply only to PROJECT/LOCAL."""
    workspace, _ = _workspace(tmp_path)
    manager = MemoryManager(project_root=workspace)

    entry = manager.add_entry(MemoryScope.USER, "preference", "Prefer tabs")

    assert entry is not None
    assert (isolated_home / "memory" / "memory.json").is_file()


def test_ordinary_project_and_local_writes_are_unaffected(
    tmp_path: Path, isolated_home: Path
) -> None:
    workspace, _ = _workspace(tmp_path)
    manager = MemoryManager(project_root=workspace)

    project = manager.add_entry(MemoryScope.PROJECT, "convention", "Use pytest")
    local = manager.add_entry(MemoryScope.LOCAL, "note", "Local only note")

    assert project is not None and local is not None
    assert (workspace / ".mini-code-memory" / "memory.json").is_file()
    assert (workspace / ".mini-code-memory-local" / "memory.json").is_file()


def test_atomic_write_refuses_a_symlinked_target_on_its_own(
    tmp_path: Path, isolated_home: Path
) -> None:
    """Defense in depth: every store write funnels through _atomic_write, so
    a caller that skipped scope-root validation still cannot redirect it."""
    workspace, outside = _workspace(tmp_path)
    target_dir = workspace / ".mini-code-memory"
    target_dir.mkdir()
    escaped = outside / "escaped.json"
    escaped.write_text("original", encoding="utf-8")
    link = target_dir / "linked.json"
    link.symlink_to(escaped)

    with pytest.raises(MemoryStoreUnsafePath):
        MemoryManager._atomic_write(link, "SENTINEL escaped content")

    assert escaped.read_text(encoding="utf-8") == "original"
