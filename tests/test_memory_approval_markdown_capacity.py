from __future__ import annotations

from pathlib import Path

import pytest

import minicode.memory as memory_mod
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_approval import MemoryApprovalAuthority, MemoryApprovalError
from minicode.memory_store import MemoryStoreConflict


def _write_project_markdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> tuple[Path, Path, Path]:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    scope_root = workspace / ".mini-code-memory"
    data_root.mkdir()
    workspace.mkdir()
    scope_root.mkdir()
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", data_root)
    source = scope_root / "MEMORY.md"
    source.write_text(
        "# Project Memory\n\n## Testing\n\n" + body + "\n",
        encoding="utf-8",
    )
    return data_root, workspace, source


@pytest.mark.parametrize(
    "body",
    [
        "\n".join(f"- markdown rule {index}" for index in range(201)),
        "\n".join(
            f"- {character * (14 * 1024)}"
            for character in ("a", "b")
        ),
    ],
    ids=("entry-count", "content-bytes"),
)
def test_approval_and_readonly_manager_reject_oversized_legacy_markdown_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> None:
    data_root, workspace, source = _write_project_markdown(
        tmp_path,
        monkeypatch,
        body,
    )
    original = source.read_bytes()

    with pytest.raises(MemoryApprovalError) as approval_error:
        MemoryApprovalAuthority(workspace).read_scope_snapshot(
            MemoryScope.PROJECT
        )
    with pytest.raises(MemoryStoreConflict, match="requires capacity migration"):
        MemoryManager(
            project_root=workspace,
            data_root=data_root,
            readonly_load=True,
        )

    assert approval_error.value.code == "memory_approval_failed"
    assert source.read_bytes() == original
    assert not (source.parent / "memory.json").exists()
    assert not (data_root / "memory-migration-quarantine").exists()
