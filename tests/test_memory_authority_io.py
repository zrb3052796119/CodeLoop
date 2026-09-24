from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import minicode.memory as memory_mod
import minicode.memory_approval as memory_approval_mod
from minicode.memory import (
    MemoryApprovalPolicy,
    MemoryEntry,
    MemoryManager,
    MemoryScope,
)
from minicode.memory_approval import MemoryApprovalAuthority, MemoryApprovalError
from minicode.memory_authority_io import (
    MAX_AUTHORITY_AUDIT_RECORDS,
    MAX_AUTHORITY_ENTRIES,
    MAX_AUTHORITY_FILE_BYTES,
    MemoryAuthoritySourceError,
    read_memory_authority_file,
)
from minicode.memory_store import (
    MemoryStoreConflict,
    MemoryStoreUnavailable,
    MemoryStoreUnsafePath,
)


def test_manager_rejects_bytes_over_the_authority_limit_before_json_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    scope_root = workspace / ".mini-code-memory"
    data_root.mkdir()
    scope_root.mkdir(parents=True)
    (scope_root / "memory.json").write_bytes(
        b" " * (MAX_AUTHORITY_FILE_BYTES + 1)
    )
    parse_attempted = False

    def fail_if_parsed(_source: object) -> object:
        nonlocal parse_attempted
        parse_attempted = True
        raise AssertionError("oversized authority reached json.loads")

    monkeypatch.setattr(memory_mod.json, "loads", fail_if_parsed)

    with pytest.raises(MemoryStoreUnavailable):
        MemoryManager(
            project_root=workspace,
            data_root=data_root,
            readonly_load=True,
        )
    assert parse_attempted is False


def test_manager_and_approval_reader_accept_audit_above_entry_cap_below_audit_cap(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    scope_root = workspace / ".mini-code-memory"
    data_root.mkdir()
    scope_root.mkdir(parents=True)
    audit_count = MAX_AUTHORITY_ENTRIES + 1
    assert audit_count < MAX_AUTHORITY_AUDIT_RECORDS
    audit = [{"audit_id": f"audit-{index}"} for index in range(audit_count)]
    (scope_root / "memory.json").write_text(
        json.dumps(
            {
                "scope": "project",
                "entries": [],
                "approval_audit": audit,
            }
        ),
        encoding="utf-8",
    )

    manager = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )
    snapshot = MemoryApprovalAuthority(workspace).read_scope_snapshot(
        MemoryScope.PROJECT
    )

    assert len(manager.approval_audit[MemoryScope.PROJECT]) == audit_count
    assert len(snapshot.approval_audit) == audit_count


def test_manager_and_approval_reader_reject_audit_above_the_audit_cap(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    scope_root = workspace / ".mini-code-memory"
    data_root.mkdir()
    scope_root.mkdir(parents=True)
    audit = [
        {"audit_id": f"audit-{index}"}
        for index in range(MAX_AUTHORITY_AUDIT_RECORDS + 1)
    ]
    (scope_root / "memory.json").write_text(
        json.dumps(
            {
                "scope": "project",
                "entries": [],
                "approval_audit": audit,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(MemoryStoreConflict, match="approval audit is invalid"):
        MemoryManager(
            project_root=workspace,
            data_root=data_root,
            readonly_load=True,
        )
    with pytest.raises(MemoryApprovalError) as captured:
        MemoryApprovalAuthority(workspace).read_scope_snapshot(
            MemoryScope.PROJECT
        )

    assert captured.value.code == "memory_approval_failed"


def test_manager_rejects_an_authority_with_more_than_the_entry_cap(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    scope_root = workspace / ".mini-code-memory"
    data_root.mkdir()
    scope_root.mkdir(parents=True)
    entries = [
        {"id": f"project-{index}", "scope": "project", "content": f"rule {index}"}
        for index in range(MAX_AUTHORITY_ENTRIES + 1)
    ]
    (scope_root / "memory.json").write_text(
        json.dumps({"scope": "project", "entries": entries}),
        encoding="utf-8",
    )

    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        MemoryManager(
            project_root=workspace,
            data_root=data_root,
            readonly_load=True,
        )
    with pytest.raises(MemoryApprovalError) as captured:
        MemoryApprovalAuthority(workspace).read_scope_snapshot(
            MemoryScope.PROJECT
        )

    assert captured.value.code == "memory_approval_failed"


def test_coordinated_write_preflight_rolls_back_invalid_audit_before_commit(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_root.mkdir()
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace, data_root=data_root)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Keep the committed parser authority unchanged.",
    )
    assert entry is not None
    memory_json = workspace / ".mini-code-memory" / "memory.json"
    committed = memory_json.read_bytes()
    committed_audit_count = len(manager.approval_audit[MemoryScope.PROJECT])

    def overflow_then_commit() -> None:
        manager.approval_audit[MemoryScope.PROJECT] = [
            {"audit_id": "invalid-incomplete-current-record"}
        ]
        manager._save_scope(MemoryScope.PROJECT)

    with pytest.raises(MemoryStoreConflict, match="approval audit is invalid"):
        manager.coordinated_write(
            (MemoryScope.PROJECT,),
            overflow_then_commit,
        )

    assert memory_json.read_bytes() == committed
    assert len(manager.approval_audit[MemoryScope.PROJECT]) == committed_audit_count
    assert entry.id in manager.memories[MemoryScope.PROJECT]._id_index


def test_coordinated_write_rejects_oversized_audit_record_before_commit(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_root.mkdir()
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace, data_root=data_root)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Preserve the bounded authority commit.",
    )
    assert entry is not None
    memory_json = workspace / ".mini-code-memory" / "memory.json"
    committed = memory_json.read_bytes()

    def oversize_then_commit() -> None:
        oversized = dict(manager.approval_audit[MemoryScope.PROJECT][-1])
        oversized["audit_id"] = "audit-1700000000000000000-deadbeef"
        oversized["reason"] = "x" * MAX_AUTHORITY_FILE_BYTES
        manager.approval_audit[MemoryScope.PROJECT].append(oversized)
        manager._save_scope(MemoryScope.PROJECT)

    with pytest.raises(MemoryStoreConflict, match="approval audit is invalid"):
        manager.coordinated_write(
            (MemoryScope.PROJECT,),
            oversize_then_commit,
        )

    assert memory_json.read_bytes() == committed
    assert all(
        record.get("audit_id") != "audit-1700000000000000000-deadbeef"
        for record in manager.approval_audit[MemoryScope.PROJECT]
    )


def test_partial_schema_recovery_preserves_embedded_audit_and_backs_up_read_bytes(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_root.mkdir()
    workspace.mkdir()
    seed = MemoryManager(project_root=workspace, data_root=data_root)
    entry = seed.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Recover the valid parser rule only.",
    )
    assert entry is not None
    scope_root = workspace / ".mini-code-memory"
    memory_json = scope_root / "memory.json"
    authority = json.loads(memory_json.read_text(encoding="utf-8"))
    authority["entries"].append({"id": 7, "content": "invalid id"})
    embedded_audit = list(authority["approval_audit"])
    corrupt_bytes = json.dumps(authority, ensure_ascii=False).encode("utf-8")
    memory_json.write_bytes(corrupt_bytes)
    (scope_root / "approval_audit.json").write_text("[]", encoding="utf-8")

    recovered = MemoryManager(project_root=workspace, data_root=data_root)
    persisted = json.loads(memory_json.read_text(encoding="utf-8"))

    assert [item.id for item in recovered.memories[MemoryScope.PROJECT].entries] == [
        entry.id
    ]
    assert persisted["approval_audit"] == embedded_audit
    assert recovered.approval_audit[MemoryScope.PROJECT] == embedded_audit
    assert memory_json.with_suffix(".json.bak").read_bytes() == corrupt_bytes


def test_manager_maps_normalized_entry_decode_failure_to_fixed_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    scope_root = workspace / ".mini-code-memory"
    data_root.mkdir()
    scope_root.mkdir(parents=True)
    (scope_root / "memory.json").write_text(
        json.dumps(
            {
                "scope": "project",
                "entries": [
                    {"id": "project-decode", "scope": "project", "content": "rule"}
                ],
                "approval_audit": [],
            }
        ),
        encoding="utf-8",
    )

    def fail_decode(_cls: type[MemoryEntry], _raw: object) -> MemoryEntry:
        raise ValueError("synthetic normalized decode failure")

    monkeypatch.setattr(MemoryEntry, "from_dict", classmethod(fail_decode))

    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        MemoryManager(
            project_root=workspace,
            data_root=data_root,
            readonly_load=True,
        )


@pytest.mark.parametrize(
    "kind",
    [
        "directory",
        "symlink",
        pytest.param(
            "fifo",
            marks=pytest.mark.skipif(
                not hasattr(os, "mkfifo"),
                reason="named FIFOs are POSIX-only",
            ),
        ),
    ],
)
def test_shared_reader_rejects_non_regular_sources_without_following_or_blocking(
    tmp_path: Path,
    kind: str,
) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    source = root / "memory.json"
    if kind == "directory":
        source.mkdir()
    elif kind == "symlink":
        outside = tmp_path / "outside.json"
        outside.write_text('{"entries": []}', encoding="utf-8")
        source.symlink_to(outside)
    else:
        os.mkfifo(source)

    with pytest.raises(MemoryAuthoritySourceError):
        read_memory_authority_file(root, "memory.json")


@pytest.mark.parametrize(
    ("kind", "expected_error"),
    [
        ("directory", MemoryStoreUnavailable),
        ("symlink", MemoryStoreUnsafePath),
        pytest.param(
            "fifo",
            MemoryStoreUnavailable,
            marks=pytest.mark.skipif(
                not hasattr(os, "mkfifo"),
                reason="named FIFOs are POSIX-only",
            ),
        ),
    ],
)
def test_manager_rejects_special_authority_without_overwriting_it(
    tmp_path: Path,
    kind: str,
    expected_error: type[Exception],
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    scope_root = workspace / ".mini-code-memory"
    data_root.mkdir()
    scope_root.mkdir(parents=True)
    source = scope_root / "memory.json"
    if kind == "directory":
        source.mkdir()
    elif kind == "symlink":
        outside = tmp_path / "outside.json"
        outside.write_text('{"entries": []}', encoding="utf-8")
        source.symlink_to(outside)
    else:
        os.mkfifo(source)

    with pytest.raises(expected_error):
        MemoryManager(project_root=workspace, data_root=data_root)

    assert source.exists() or source.is_symlink()


def test_shared_reader_rejects_invalid_utf8_as_authority_unavailable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    (root / "memory.json").write_bytes(b'{"entries":[]}\xff')

    with pytest.raises(MemoryAuthoritySourceError):
        read_memory_authority_file(root, "memory.json")


def test_shared_reader_accepts_the_exact_authority_byte_limit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    payload = b" " * MAX_AUTHORITY_FILE_BYTES
    (root / "MEMORY.md").write_bytes(payload)

    source = read_memory_authority_file(root, "MEMORY.md")

    assert source is not None
    assert source.data == payload
    assert len(source.text.encode("utf-8")) == MAX_AUTHORITY_FILE_BYTES


def test_legacy_markdown_change_invalidates_revision_and_reloads_scope(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    scope_root = workspace / ".mini-code-memory"
    data_root.mkdir()
    scope_root.mkdir(parents=True)
    memory_md = scope_root / "MEMORY.md"
    memory_md.write_text(
        "# Project Memory\n\n- First parser convention\n",
        encoding="utf-8",
    )
    manager = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )

    memory_md.write_text(
        "# Project Memory\n\n- Updated parser convention\n",
        encoding="utf-8",
    )
    reloaded = manager.refresh_if_stale()

    assert reloaded == (MemoryScope.PROJECT,)
    assert [
        entry.content for entry in manager.memories[MemoryScope.PROJECT].entries
    ] == ["Updated parser convention"]
    assert not (scope_root / "memory.json").exists()


def test_manager_parses_canonical_json_once_during_initial_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    scope_root = workspace / ".mini-code-memory"
    data_root.mkdir()
    scope_root.mkdir(parents=True)
    (scope_root / "memory.json").write_text(
        json.dumps(
            {
                "scope": "project",
                "entries": [],
                "approval_audit": [{"audit_id": "embedded"}],
            }
        ),
        encoding="utf-8",
    )
    original_loads = memory_mod.json.loads
    parse_count = 0

    def counted_loads(source: object, *args: object, **kwargs: object) -> object:
        nonlocal parse_count
        parse_count += 1
        return original_loads(source, *args, **kwargs)

    monkeypatch.setattr(memory_mod.json, "loads", counted_loads)

    manager = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )

    assert manager.approval_audit[MemoryScope.PROJECT] == [
        {"audit_id": "embedded"}
    ]
    assert parse_count == 1


def test_revision_refresh_uses_the_same_bounded_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_root.mkdir()
    workspace.mkdir()
    manager = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )
    scope_root = workspace / ".mini-code-memory"
    scope_root.mkdir()
    (scope_root / "memory.json").write_bytes(
        b" " * (MAX_AUTHORITY_FILE_BYTES + 1)
    )

    def forbid_unbounded_read(_path: Path) -> bytes:
        raise AssertionError("revision used Path.read_bytes")

    monkeypatch.setattr(Path, "read_bytes", forbid_unbounded_read)

    with pytest.raises(MemoryStoreUnavailable):
        manager.refresh_if_stale()


def test_approval_reader_maps_json_recursion_to_fixed_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    scope_root = workspace / ".mini-code-memory"
    scope_root.mkdir(parents=True)
    (scope_root / "memory.json").write_text(
        '{"scope":"project","entries":[],"approval_audit":[]}',
        encoding="utf-8",
    )

    def fail_deep_parse(_source: object) -> object:
        raise RecursionError("synthetic deeply nested JSON")

    monkeypatch.setattr(memory_approval_mod.json, "loads", fail_deep_parse)

    with pytest.raises(MemoryApprovalError) as captured:
        MemoryApprovalAuthority(workspace).read_scope_snapshot(
            MemoryScope.PROJECT
        )

    assert captured.value.code == "memory_approval_failed"


def test_json_authority_ignores_oversized_markdown_readonly_and_repairs_on_load(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_root.mkdir()
    workspace.mkdir()
    seed = MemoryManager(project_root=workspace, data_root=data_root)
    entry = seed.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Render the canonical parser rule.",
    )
    assert entry is not None
    memory_md = workspace / ".mini-code-memory" / "MEMORY.md"
    oversized = b"x" * (MAX_AUTHORITY_FILE_BYTES + 1)
    memory_md.write_bytes(oversized)

    readonly = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )

    assert entry.id in readonly.memories[MemoryScope.PROJECT]._id_index
    assert memory_md.read_bytes() == oversized

    MemoryManager(project_root=workspace, data_root=data_root)

    repaired = memory_md.read_text(encoding="utf-8")
    assert entry.content in repaired
    assert len(repaired.encode("utf-8")) <= MAX_AUTHORITY_FILE_BYTES


@pytest.mark.parametrize("projection_name", ["MEMORY.md", "approval_audit.json"])
def test_projection_failure_after_authority_commit_returns_committed_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    projection_name: str,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_root.mkdir()
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace, data_root=data_root)
    original_atomic_write = manager._atomic_write

    def fail_projection(target: Path, content: str) -> None:
        if target.name == projection_name:
            raise MemoryStoreConflict("synthetic projection failure")
        original_atomic_write(target, content)

    monkeypatch.setattr(manager, "_atomic_write", fail_projection)

    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        f"Commit parser authority despite {projection_name} failure.",
    )

    assert entry is not None
    authority = json.loads(
        (workspace / ".mini-code-memory" / "memory.json").read_text(
            encoding="utf-8"
        )
    )
    assert [raw["id"] for raw in authority["entries"]] == [entry.id]
    assert entry.id in manager.memories[MemoryScope.PROJECT]._id_index


def test_decide_does_not_repair_partial_corruption_before_failing_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_root.mkdir()
    workspace.mkdir()
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", data_root)
    seed = MemoryManager(project_root=workspace, data_root=data_root)
    entry = seed.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Review the parser decision without repairing authority.",
        source="reflection",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    authority = MemoryApprovalAuthority(workspace)
    review_revision = authority.snapshot()["items"][0]["reviewRevision"]
    memory_json = workspace / ".mini-code-memory" / "memory.json"
    corrupted = json.loads(memory_json.read_text(encoding="utf-8"))
    corrupted["entries"].append({"id": 7, "content": "invalid id"})
    corrupt_bytes = json.dumps(corrupted, ensure_ascii=False).encode("utf-8")
    memory_json.write_bytes(corrupt_bytes)

    with pytest.raises(MemoryApprovalError) as captured:
        authority.decide(
            memory_id=entry.id,
            decision="reject",
            review_revision=review_revision,
        )

    assert captured.value.code == "memory_write_conflict"
    assert memory_json.read_bytes() == corrupt_bytes
    assert not memory_json.with_suffix(".json.bak").exists()


def test_decide_strictly_rejects_a_stale_approval_hash_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_root.mkdir()
    workspace.mkdir()
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", data_root)
    seed = MemoryManager(project_root=workspace, data_root=data_root)
    entry = seed.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Review the original parser authority.",
        source="reflection",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    authority = MemoryApprovalAuthority(workspace)
    review_revision = authority.snapshot()["items"][0]["reviewRevision"]
    memory_json = workspace / ".mini-code-memory" / "memory.json"
    tampered = json.loads(memory_json.read_text(encoding="utf-8"))
    tampered["entries"][0]["content"] = "Tampered parser authority."
    tampered_bytes = json.dumps(tampered, ensure_ascii=False).encode("utf-8")
    memory_json.write_bytes(tampered_bytes)
    compatibility_view = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )
    _, recovered_entry = compatibility_view._find_entry_by_id(entry.id)
    assert recovered_entry is not None
    bypass_revision = authority._review_revision(recovered_entry)
    assert bypass_revision != review_revision

    with pytest.raises(MemoryApprovalError) as snapshot_failure:
        authority.snapshot()
    with pytest.raises(MemoryApprovalError) as decide_failure:
        authority.decide(
            memory_id=entry.id,
            decision="approve",
            review_revision=bypass_revision,
        )

    assert snapshot_failure.value.code == "memory_approval_failed"
    assert decide_failure.value.code == "memory_write_conflict"
    assert memory_json.read_bytes() == tampered_bytes
    assert not memory_json.with_suffix(".json.bak").exists()


def test_legacy_json_audit_projection_change_invalidates_scope_revision(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_root.mkdir()
    workspace.mkdir()
    seed = MemoryManager(project_root=workspace, data_root=data_root)
    entry = seed.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Retain the legacy projected audit revision fence.",
    )
    assert entry is not None
    scope_root = workspace / ".mini-code-memory"
    memory_json = scope_root / "memory.json"
    memory_data = json.loads(memory_json.read_text(encoding="utf-8"))
    memory_data.pop("schemaVersion")
    memory_data.pop("approval_audit")
    memory_json.write_text(json.dumps(memory_data), encoding="utf-8")
    manager = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )
    audit_projection = scope_root / "approval_audit.json"
    audit_data = json.loads(audit_projection.read_text(encoding="utf-8"))
    audit_data["records"].append(
        {
            "audit_id": "audit-out-of-band",
            "entry_id": entry.id,
            "action": "legacy_projection_change",
        }
    )
    audit_projection.write_text(json.dumps(audit_data), encoding="utf-8")

    reloaded = manager.refresh_if_stale()

    assert reloaded == (MemoryScope.PROJECT,)
    assert manager.approval_audit[MemoryScope.PROJECT][-1]["audit_id"] == (
        "audit-out-of-band"
    )
