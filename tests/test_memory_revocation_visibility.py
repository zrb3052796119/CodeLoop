"""A revoked memory must not reach the prompt even once.

Writes already reload through `_coordinated_all_write`, but the read path did
not compare disk revision, so a long-lived manager kept serving an in-memory
view where an entry revoked elsewhere (Dashboard approval, another session)
was still approved — and it could be selected and rendered one final time.

Note the bug is intermittent in real use: `MemoryInjectionController` has a
30s injection cooldown, so a second injection inside that window is skipped
for unrelated reasons and masks the defect. These tests therefore revoke
*before* the first injection.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import minicode.memory as memory_module
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr(memory_module, "MINI_CODE_DIR", home)
    monkeypatch.setattr(
        memory_module.MemoryPaths,
        "for_workspace",
        classmethod(
            lambda cls, ws: cls(
                user_memory=home / "memory",
                project_memory=Path(ws) / ".mini-code-memory",
                local_memory=Path(ws) / ".mini-code-memory-local",
            )
        ),
    )
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def _pipeline(manager: MemoryManager, ws: Path) -> MemoryPipeline:
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(ws), enable_reranker=False)
    return pipeline


def _disk_status(ws: Path, entry_id: str) -> str:
    data = json.loads((ws / ".mini-code-memory" / "memory.json").read_text())
    return next(e["approval_status"] for e in data["entries"] if e["id"] == entry_id)


def test_entry_revoked_by_another_process_is_not_injected(workspace: Path) -> None:
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT, "convention",
        "SENTINEL use pytest fixtures for auth tests", tags=["pytest", "auth"],
    )
    assert entry is not None
    pipeline = _pipeline(manager, workspace)

    # Another process revokes it; this manager's in-memory view goes stale.
    MemoryManager(project_root=workspace).reject_entry(entry.id, reason="dashboard_rejected")
    assert _disk_status(workspace, entry.id) == "rejected"

    messages = pipeline.inject(
        "auth pytest fixtures", ["tests/test_auth.py"],
        [{"role": "system", "content": "S"}], context_usage=0.4,
    )

    assert not any("SENTINEL" in m.get("content", "") for m in messages)
    # The stale view was actually resynced, not merely filtered downstream.
    assert manager.memories[MemoryScope.PROJECT]._id_index[entry.id].approval_status == "rejected"


def test_still_approved_entry_is_unaffected_by_the_resync(workspace: Path) -> None:
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT, "convention",
        "SENTINEL use pytest fixtures for auth tests", tags=["pytest", "auth"],
    )
    assert entry is not None
    pipeline = _pipeline(manager, workspace)

    messages = pipeline.inject(
        "auth pytest fixtures", ["tests/test_auth.py"],
        [{"role": "system", "content": "S"}], context_usage=0.4,
    )

    assert any("SENTINEL" in m.get("content", "") for m in messages)


def test_refresh_if_stale_reports_only_changed_scopes(workspace: Path) -> None:
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(MemoryScope.PROJECT, "convention", "project note")
    manager.add_entry(MemoryScope.LOCAL, "note", "local note")
    assert entry is not None

    assert manager.refresh_if_stale() == ()

    MemoryManager(project_root=workspace).reject_entry(entry.id, reason="dashboard_rejected")
    assert manager.refresh_if_stale() == (MemoryScope.PROJECT,)
    # Idempotent once synced.
    assert manager.refresh_if_stale() == ()


def test_refresh_is_a_noop_inside_an_open_write_transaction(workspace: Path) -> None:
    """Reloading under a caller's own transaction would discard its
    uncommitted state, so the guard must stand down there."""
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(MemoryScope.PROJECT, "convention", "project note")
    assert entry is not None
    MemoryManager(project_root=workspace).reject_entry(entry.id, reason="dashboard_rejected")

    with manager._store.transaction():
        assert manager.in_write_transaction is True
        assert manager.refresh_if_stale() == ()
