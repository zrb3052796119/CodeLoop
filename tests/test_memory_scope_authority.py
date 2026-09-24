from __future__ import annotations

import json
from pathlib import Path

import pytest

import minicode.memory as memory_mod
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_approval import MemoryApprovalAuthority, MemoryApprovalError
from minicode.memory_retrieval import CanonicalMemoryRetriever, MemoryRetrievalRequest
from minicode.memory_store import MemoryStoreConflict


def _write_project_authority(
    workspace: Path,
    *,
    container_scope: str | None = "project",
    entries: list[dict[str, object]],
) -> Path:
    root = workspace / ".mini-code-memory"
    root.mkdir(parents=True)
    authority: dict[str, object] = {
        "entries": entries,
        "approval_audit": [],
    }
    if container_scope is not None:
        authority["scope"] = container_scope
    path = root / "memory.json"
    path.write_text(json.dumps(authority), encoding="utf-8")
    return path


def _write_authority_root(
    root: Path,
    *,
    scope: str,
    entries: list[dict[str, object]],
) -> Path:
    root.mkdir(parents=True)
    path = root / "memory.json"
    path.write_text(
        json.dumps(
            {"scope": scope, "entries": entries, "approval_audit": []}
        ),
        encoding="utf-8",
    )
    return path


def test_container_scope_mismatch_fails_both_loaders_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    strict_workspace = tmp_path / "strict"
    recover_workspace = tmp_path / "recover"
    data_root.mkdir()
    strict_workspace.mkdir()
    recover_workspace.mkdir()
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", data_root)
    entry = {
        "id": "scope-bound-entry",
        "scope": "project",
        "content": "Run the scope-bound parser check.",
    }
    strict_path = _write_project_authority(
        strict_workspace,
        container_scope="local",
        entries=[entry],
    )
    recover_path = _write_project_authority(
        recover_workspace,
        container_scope="local",
        entries=[entry],
    )
    strict_before = strict_path.read_bytes()
    recover_before = recover_path.read_bytes()

    with pytest.raises(MemoryApprovalError) as strict_error:
        MemoryApprovalAuthority(strict_workspace).read_scope_snapshot(
            MemoryScope.PROJECT
        )
    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        MemoryManager(project_root=recover_workspace, data_root=data_root)

    assert strict_error.value.code == "memory_approval_failed"
    assert strict_path.read_bytes() == strict_before
    assert recover_path.read_bytes() == recover_before
    assert not strict_path.with_suffix(".json.bak").exists()
    assert not recover_path.with_suffix(".json.bak").exists()


def test_cross_scope_duplicate_id_fails_manager_before_any_repair_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_root.mkdir()
    workspace.mkdir()
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", data_root)
    shared_id = "cross-scope-id"
    user_path = _write_authority_root(
        data_root / "memory",
        scope="user",
        entries=[
            {
                "id": shared_id,
                "scope": "user",
                "content": "Use the global parser policy.",
            }
        ],
    )
    project_path = _write_authority_root(
        workspace / ".mini-code-memory",
        scope="project",
        entries=[
            {
                "id": shared_id,
                "scope": "project",
                "content": "Use the project parser policy.",
            }
        ],
    )
    before = {user_path: user_path.read_bytes(), project_path: project_path.read_bytes()}

    with pytest.raises(MemoryApprovalError) as strict_error:
        MemoryApprovalAuthority(workspace).snapshot()
    with pytest.raises(MemoryStoreConflict, match="cross-scope duplicate"):
        MemoryManager(project_root=workspace, data_root=data_root)

    assert strict_error.value.code == "memory_approval_failed"
    assert {path: path.read_bytes() for path in before} == before
    assert not user_path.with_suffix(".json.bak").exists()
    assert not project_path.with_suffix(".json.bak").exists()


def test_legacy_markdown_id_conflict_is_found_before_legacy_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    user_root = data_root / "memory"
    user_root.mkdir(parents=True)
    workspace.mkdir()
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", data_root)
    user_markdown = user_root / "MEMORY.md"
    user_markdown.write_text(
        "# User Memory\n\n## Testing\n\n- Run the legacy global parser.\n",
        encoding="utf-8",
    )
    project_path = _write_authority_root(
        workspace / ".mini-code-memory",
        scope="project",
        entries=[
            {
                "id": "user-1",
                "scope": "project",
                "content": "Do not collide with a generated legacy ID.",
            }
        ],
    )
    markdown_before = user_markdown.read_bytes()
    project_before = project_path.read_bytes()

    with pytest.raises(MemoryStoreConflict, match="cross-scope duplicate"):
        MemoryManager(project_root=workspace, data_root=data_root)

    assert user_markdown.read_bytes() == markdown_before
    assert project_path.read_bytes() == project_before
    assert not (user_root / "memory.json").exists()


def test_explicit_entry_scope_mismatch_is_not_partial_recovery_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_root.mkdir()
    workspace.mkdir()
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", data_root)
    authority_path = _write_project_authority(
        workspace,
        entries=[
            {
                "id": "valid-project-entry",
                "content": "Keep this otherwise valid project entry.",
            },
            {
                "id": "foreign-local-entry",
                "scope": "local",
                "content": "Never move this entry across authority roots.",
            },
        ],
    )
    before = authority_path.read_bytes()

    with pytest.raises(MemoryApprovalError) as strict_error:
        MemoryApprovalAuthority(workspace).read_scope_snapshot(MemoryScope.PROJECT)
    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        MemoryManager(project_root=workspace, data_root=data_root)

    assert strict_error.value.code == "memory_approval_failed"
    assert authority_path.read_bytes() == before
    assert not authority_path.with_suffix(".json.bak").exists()


def test_missing_entry_scope_inherits_container_scope_and_remains_retrievable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_root.mkdir()
    workspace.mkdir()
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", data_root)
    authority_path = _write_project_authority(
        workspace,
        container_scope=None,
        entries=[
            {
                "id": "legacy-scope-entry",
                "content": "Run the heliotrope parser before deployment.",
            }
        ],
    )

    strict = MemoryApprovalAuthority(workspace).read_scope_snapshot(
        MemoryScope.PROJECT
    )
    manager = MemoryManager(project_root=workspace, data_root=data_root)
    loaded = manager.memories[MemoryScope.PROJECT].entries[0]
    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(query="heliotrope parser deployment")
    )

    assert strict.entries[0].scope is MemoryScope.PROJECT
    assert loaded.scope is MemoryScope.PROJECT
    assert result.rendered_ids == ("legacy-scope-entry",)
    persisted = json.loads(authority_path.read_text(encoding="utf-8"))
    assert persisted["scope"] == "project"
    assert persisted["entries"][0]["scope"] == "project"


def test_retrieval_rejects_new_cross_scope_conflict_before_prepared_corpus_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_root.mkdir()
    workspace.mkdir()
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", data_root)
    manager = MemoryManager(project_root=workspace, data_root=data_root)
    user_entry = manager.add_entry(
        MemoryScope.USER,
        "testing",
        "Run the indigo global parser check.",
    )
    project_entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Run the amber project parser check.",
    )
    assert user_entry is not None and project_entry is not None
    retriever = CanonicalMemoryRetriever(manager)
    retriever.retrieve(MemoryRetrievalRequest(query="amber project parser"))

    project_path = workspace / ".mini-code-memory" / "memory.json"
    project_data = json.loads(project_path.read_text(encoding="utf-8"))
    project_data["entries"][0]["id"] = user_entry.id
    project_path.write_text(json.dumps(project_data), encoding="utf-8")
    conflict_bytes = project_path.read_bytes()

    def prepared_corpus_must_not_be_touched(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("prepared retrieval touched conflicting authority")

    monkeypatch.setattr(
        retriever,
        "_cached_prepared_corpus",
        prepared_corpus_must_not_be_touched,
    )

    with pytest.raises(MemoryStoreConflict, match="cross-scope duplicate"):
        retriever.retrieve(MemoryRetrievalRequest(query="amber project parser"))

    assert project_path.read_bytes() == conflict_bytes
    assert manager.memories[MemoryScope.PROJECT].entries[0].id == project_entry.id
