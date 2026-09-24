"""RED contracts for typed, segmented Memory approval audit history.

The primary Memory authority must stay bounded without discarding audit
history.  These tests describe the next authority revision: a typed hot tail
inside ``memory.json`` and immutable content-addressed cold segments linked
from one bounded root head.

Keep this file separate from the green lifecycle suite until the shared audit
decoder and rotation protocol are implemented.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import pytest

import minicode.memory as memory_mod
import minicode.memory_approval as approval_mod
import minicode.memory_authority_io as authority_io_mod
import minicode.web.read_model as dashboard_mod
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_approval import MemoryApprovalAuthority, MemoryApprovalError
from minicode.memory_blob_store import MemoryBlobReference, MemoryBlobStore
from minicode.memory_store import MemoryStoreConflict
from minicode.web.read_model import DashboardReadModel


_AUDIT_HEAD_FIELDS = frozenset({"blob", "segmentCount", "recordCount"})
_AUDIT_SEGMENT_FIELDS = frozenset(
    {"schemaVersion", "kind", "scope", "previous", "records"}
)


def _new_manager(tmp_path: Path, *, readonly_load: bool = False) -> MemoryManager:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "data"
    workspace.mkdir(exist_ok=True)
    data_root.mkdir(exist_ok=True)
    return MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=readonly_load,
    )


def _authority_path(tmp_path: Path) -> Path:
    return tmp_path / "workspace" / ".mini-code-memory" / "memory.json"


def _scope_root(tmp_path: Path) -> Path:
    return _authority_path(tmp_path).parent


def _read_authority(tmp_path: Path) -> dict[str, Any]:
    value = json.loads(_authority_path(tmp_path).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_authority(tmp_path: Path, value: dict[str, Any]) -> None:
    _authority_path(tmp_path).write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes]]:
    """Capture files and directories without following fixture symlinks."""

    snapshot: dict[str, tuple[str, bytes]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", str(path.readlink()).encode())
        elif path.is_dir():
            snapshot[relative] = ("directory", b"")
        elif path.is_file():
            snapshot[relative] = ("file", path.read_bytes())
        else:
            snapshot[relative] = ("special", b"")
    return snapshot


def _seed_current_authority(tmp_path: Path) -> tuple[str, dict[str, Any]]:
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Keep every approval transition typed and tamper evident.",
    )
    assert entry is not None
    authority = _read_authority(tmp_path)
    assert authority["approval_audit"]
    return entry.id, authority


def _patch_hot_window(
    monkeypatch: pytest.MonkeyPatch,
    limit: int,
) -> None:
    """Use the existing shared limit as the fast test seam for the hot tail."""

    for module in (memory_mod, approval_mod, authority_io_mod, dashboard_mod):
        if hasattr(module, "MAX_AUTHORITY_AUDIT_RECORDS"):
            monkeypatch.setattr(module, "MAX_AUTHORITY_AUDIT_RECORDS", limit)


def _assert_reader_fails_closed_without_writes(
    tmp_path: Path,
    reader: Literal["manager", "approval", "dashboard"],
) -> None:
    before = _tree_snapshot(tmp_path)
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "data"

    if reader == "manager":
        with pytest.raises(MemoryStoreConflict):
            MemoryManager(
                project_root=workspace,
                data_root=data_root,
                readonly_load=True,
            )
    elif reader == "approval":
        with pytest.raises(MemoryApprovalError) as captured:
            MemoryApprovalAuthority(workspace).read_scope_snapshot(
                MemoryScope.PROJECT
            )
        assert captured.value.code == "memory_approval_failed"
    else:
        model = DashboardReadModel(workspace, data_dir=data_root)
        page = model.memory()
        overview = model.snapshot()
        assert page["scopes"]["project"]["status"] == "error"
        assert page["summary"]["byScope"]["project"] is None
        assert all(item["scope"] != "project" for item in page["items"])
        assert overview["overview"]["memory"]["scopes"]["project"] == {
            "status": "error",
            "count": None,
            "tiers": None,
            "categories": None,
        }

    assert _tree_snapshot(tmp_path) == before


@pytest.mark.parametrize("reader", ["manager", "approval", "dashboard"])
@pytest.mark.parametrize(
    "corruption",
    [
        "malformed_timestamp",
        "duplicate_audit_id",
        "scope_mismatch",
        "oversized_reason",
    ],
)
def test_malformed_current_audit_is_rejected_by_every_reader_without_writes(
    tmp_path: Path,
    reader: Literal["manager", "approval", "dashboard"],
    corruption: str,
) -> None:
    _entry_id, authority = _seed_current_authority(tmp_path)
    audit = authority["approval_audit"]
    assert isinstance(audit, list) and isinstance(audit[0], dict)

    if corruption == "malformed_timestamp":
        audit[0]["created_at"] = "bad"
    elif corruption == "duplicate_audit_id":
        audit.append(dict(audit[0]))
    elif corruption == "scope_mismatch":
        audit[0]["scope"] = "user"
    else:
        # The producer bounds human-readable reasons at 240 characters.  A
        # current authority must reject, not silently truncate, a larger value.
        audit[0]["reason"] = "x" * 241

    _write_authority(tmp_path, authority)
    _assert_reader_fails_closed_without_writes(tmp_path, reader)


def _read_segment_chain(
    scope_root: Path,
    authority: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Verify and return cold records oldest-first and segments newest-first."""

    assert "approval_audit_head" in authority
    head = authority["approval_audit_head"]
    assert isinstance(head, dict) and set(head) == _AUDIT_HEAD_FIELDS
    reference = MemoryBlobReference.from_dict(head["blob"])
    store = MemoryBlobStore(scope_root)
    seen: set[str] = set()
    newest_first: list[dict[str, Any]] = []

    while True:
        assert reference.digest not in seen
        seen.add(reference.digest)
        segment = store.read_json(reference)
        assert isinstance(segment, dict)
        assert set(segment) == _AUDIT_SEGMENT_FIELDS
        assert segment["schemaVersion"] == 1
        assert segment["kind"] == "memory-approval-audit-segment"
        assert segment["scope"] == "project"
        assert isinstance(segment["records"], list) and segment["records"]
        newest_first.append(segment)
        previous = segment["previous"]
        if previous is None:
            break
        reference = MemoryBlobReference.from_dict(previous)

    cold_records = [
        record
        for segment in reversed(newest_first)
        for record in segment["records"]
    ]
    assert head["segmentCount"] == len(newest_first)
    assert head["recordCount"] == len(cold_records)
    return cold_records, newest_first


def _seed_rotated_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    hot_limit: int = 2,
    total_records: int = 7,
) -> tuple[str, list[str]]:
    _patch_hot_window(monkeypatch, hot_limit)
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Preserve the audit chain across bounded authority rotations.",
    )
    assert entry is not None
    expected_ids = [
        str(manager.approval_audit[MemoryScope.PROJECT][-1]["audit_id"])
    ]
    for index in range(total_records - 1):
        record = manager._append_approval_audit(
            MemoryScope.PROJECT,
            entry,
            action=f"rotation_probe_{index}",
            actor="test",
            previous_approval=entry.approval_status,
            previous_lifecycle=entry.lifecycle_status,
            reason="exercise the bounded immutable audit segment",
        )
        expected_ids.append(str(record["audit_id"]))
        manager._save_scope(MemoryScope.PROJECT)
    return entry.id, expected_ids


def test_hot_window_rotation_preserves_complete_ordered_hash_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_id, expected_ids = _seed_rotated_history(tmp_path, monkeypatch)
    authority = _read_authority(tmp_path)
    hot = authority["approval_audit"]

    assert isinstance(hot, list)
    assert 0 <= len(hot) <= 2
    cold, _segments = _read_segment_chain(_scope_root(tmp_path), authority)
    assert [record["audit_id"] for record in [*cold, *hot]] == expected_ids

    manager = _new_manager(tmp_path, readonly_load=True)
    assert [
        record["audit_id"] for record in manager.get_approval_audit(entry_id)
    ] == expected_ids
    approval = MemoryApprovalAuthority(
        tmp_path / "workspace"
    ).read_scope_snapshot(MemoryScope.PROJECT)
    assert [record["audit_id"] for record in approval.approval_audit] == expected_ids


def test_byte_pressure_rotates_before_count_limit_instead_of_freezing_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _new_manager(tmp_path)
    original = "A bounded audit root must rotate on encoded byte pressure."
    replacement = "B bounded audit root must rotate on encoded byte pressure."
    assert len(original) == len(replacement)
    entry = manager.add_entry(MemoryScope.PROJECT, "testing", original)
    assert entry is not None
    authority_path = _authority_path(tmp_path)
    initial_size = len(authority_path.read_bytes())

    # One additional typed record is larger than this allowance, while a root
    # containing a compact chain head and a rotated hot tail can still fit.
    root_budget = initial_size + 512
    monkeypatch.setattr(
        authority_io_mod,
        "MAX_AUTHORITY_FILE_BYTES",
        root_budget,
    )

    assert manager.update_entry(
        MemoryScope.PROJECT,
        entry.id,
        replacement,
    )

    authority = _read_authority(tmp_path)
    assert len(authority_path.read_bytes()) <= root_budget
    assert len(authority["approval_audit"]) < 2
    cold, _segments = _read_segment_chain(_scope_root(tmp_path), authority)
    assert len(cold) + len(authority["approval_audit"]) == 2
    reloaded = _new_manager(tmp_path, readonly_load=True)
    assert len(reloaded.get_approval_audit(entry.id)) == 2


@pytest.mark.parametrize("reader", ["manager", "approval", "dashboard"])
@pytest.mark.parametrize("corruption", ["missing", "corrupt"])
def test_referenced_audit_segment_failure_is_fail_closed_and_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reader: Literal["manager", "approval", "dashboard"],
    corruption: str,
) -> None:
    _seed_rotated_history(tmp_path, monkeypatch, total_records=4)
    authority = _read_authority(tmp_path)
    head = authority["approval_audit_head"]
    assert isinstance(head, dict)
    reference = MemoryBlobReference.from_dict(head["blob"])
    segment_path = MemoryBlobStore(_scope_root(tmp_path)).root / reference.filename
    assert segment_path.is_file()
    if corruption == "missing":
        segment_path.unlink()
    else:
        # Keep valid JSON so the failure proves address/size integrity, rather
        # than merely exercising JSON syntax rejection.
        segment_path.write_bytes(b'{"kind":"wrong-content"}')

    _assert_reader_fails_closed_without_writes(tmp_path, reader)


@pytest.mark.parametrize("reader", ["manager", "approval", "dashboard"])
@pytest.mark.parametrize(
    "corruption",
    ["segment_scope_mismatch", "duplicate_id_across_hot_and_cold"],
)
def test_content_addressed_but_semantically_invalid_segment_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reader: Literal["manager", "approval", "dashboard"],
    corruption: str,
) -> None:
    _seed_rotated_history(tmp_path, monkeypatch, total_records=4)
    authority = _read_authority(tmp_path)
    head = authority["approval_audit_head"]
    assert isinstance(head, dict)
    store = MemoryBlobStore(_scope_root(tmp_path))
    segment = store.read_json(MemoryBlobReference.from_dict(head["blob"]))
    assert isinstance(segment, dict)

    if corruption == "segment_scope_mismatch":
        segment["scope"] = "user"
    else:
        hot = authority["approval_audit"]
        assert isinstance(hot, list) and hot
        records = segment["records"]
        assert isinstance(records, list) and records
        records[-1]["audit_id"] = hot[0]["audit_id"]

    # Publish a correctly addressed canonical blob and point the authority at
    # it.  The reader must reject the typed/chain semantics independently of
    # the generic content-address integrity check.
    head["blob"] = store.put_json(segment).to_dict()
    _write_authority(tmp_path, authority)
    _assert_reader_fails_closed_without_writes(tmp_path, reader)


class _SimulatedRotationCrash(BaseException):
    pass


def test_rotation_crash_before_root_replace_leaves_only_unreferenced_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_hot_window(monkeypatch, 2)
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "The root must never reference an unpublished audit segment.",
    )
    assert entry is not None
    manager._append_approval_audit(
        MemoryScope.PROJECT,
        entry,
        action="fill_hot_window",
        actor="test",
        previous_approval=entry.approval_status,
        previous_lifecycle=entry.lifecycle_status,
    )
    manager._save_scope(MemoryScope.PROJECT)

    authority_path = _authority_path(tmp_path)
    committed_root = authority_path.read_bytes()
    blob_root = MemoryBlobStore(_scope_root(tmp_path)).root
    blobs_before = (
        {path.name for path in blob_root.glob("*.json")}
        if blob_root.exists()
        else set()
    )
    original_atomic_write = manager._atomic_write

    def crash_before_root_publish(target: Path, content: str) -> None:
        if target == authority_path:
            raise _SimulatedRotationCrash()
        original_atomic_write(target, content)

    monkeypatch.setattr(manager, "_atomic_write", crash_before_root_publish)

    with pytest.raises(_SimulatedRotationCrash):
        manager.update_entry(
            MemoryScope.PROJECT,
            entry.id,
            "The root must never reference a not-yet-published audit segment.",
        )

    assert authority_path.read_bytes() == committed_root
    blobs_after = {path.name for path in blob_root.glob("*.json")}
    orphaned = blobs_after - blobs_before
    assert orphaned, "the immutable segment must be durable before root publish"

    committed = _read_authority(tmp_path)
    referenced: set[str] = set()
    head = committed.get("approval_audit_head")
    if isinstance(head, dict):
        reference = MemoryBlobReference.from_dict(head["blob"])
        while reference.digest not in referenced:
            referenced.add(reference.digest)
            segment = MemoryBlobStore(_scope_root(tmp_path)).read_json(reference)
            previous = segment["previous"]
            if previous is None:
                break
            reference = MemoryBlobReference.from_dict(previous)
    assert all(name.removesuffix(".json") not in referenced for name in orphaned)

    # The old root remains a complete authority even though the harmless
    # orphan is present.  A read-only restart must not repair or delete it.
    before_reload = _tree_snapshot(tmp_path)
    reloaded = _new_manager(tmp_path, readonly_load=True)
    assert entry.id in reloaded.memories[MemoryScope.PROJECT]._id_index
    assert _tree_snapshot(tmp_path) == before_reload
