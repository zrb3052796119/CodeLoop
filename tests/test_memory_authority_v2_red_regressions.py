"""RED contracts for the next version of the canonical Memory authority.

These tests intentionally describe behavior that the current implementation does
not yet provide.  Keep them separate from the green authority suite so the
production seams can be repaired one contract at a time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import minicode.memory as memory_mod
import minicode.memory_authority_io as authority_io_mod
from minicode.memory import (
    MemoryApprovalPolicy,
    MemoryManager,
    MemoryScope,
    MemoryTier,
)
from minicode.memory_approval import MemoryApprovalAuthority, MemoryApprovalError
from minicode.memory_approval_interpretation import ApprovalInterpretationMode
from minicode.memory_blob_store import MemoryBlobStore
from minicode.memory_authority_io import (
    MAX_CURRENT_AUTHORITY_CONTENT_BYTES,
    MAX_MEMORY_COUNTER,
    MAX_MEMORY_TIMESTAMP,
)
from minicode.memory_store import MemoryStoreConflict
from minicode.memory_write_contract import (
    MemoryAuthorityCommit,
    MemoryAuthorityReceiptReference,
    MemoryMaterialization,
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


def _project_authority_path(tmp_path: Path) -> Path:
    return tmp_path / "workspace" / ".mini-code-memory" / "memory.json"


def _write_legacy_project_authority(
    tmp_path: Path,
    *,
    entries: list[dict[str, Any]],
    approval_audit: list[dict[str, Any]] | None = None,
) -> Path:
    path = _project_authority_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "scope": "project",
                "entries": entries,
                "approval_audit": approval_audit or [],
            }
        ),
        encoding="utf-8",
    )
    return path


def _assert_versioned_envelope(authority: dict[str, Any]) -> None:
    schema_version = authority.get("schemaVersion")
    assert type(schema_version) is int
    assert schema_version >= 1


def _receipt(index: int = 1) -> MemoryAuthorityCommit:
    return MemoryAuthorityCommit(
        operation_id=f"memoryop_{index:032x}",
        payload_digest=f"{index:064x}",
        scope="project",
        rejected_claim_ids=(f"claim-{index}",),
    )


def test_new_authority_commit_declares_a_root_schema_version(tmp_path: Path) -> None:
    manager = _new_manager(tmp_path)

    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Keep the parser recovery command bounded and deterministic.",
    )

    assert entry is not None
    authority = json.loads(_project_authority_path(tmp_path).read_text("utf-8"))
    _assert_versioned_envelope(authority)


def test_legacy_authority_is_migrated_to_a_versioned_envelope_without_data_loss(
    tmp_path: Path,
) -> None:
    legacy_content = "Retry the formatter with the repository-local executable."
    path = _write_legacy_project_authority(
        tmp_path,
        entries=[
            {
                "id": "project-legacy-1",
                "scope": "project",
                "category": "tool_recovery",
                "content": legacy_content,
            }
        ],
    )

    manager = _new_manager(tmp_path)

    assert [
        entry.content for entry in manager.memories[MemoryScope.PROJECT].entries
    ] == [legacy_content]
    migrated = json.loads(path.read_text("utf-8"))
    _assert_versioned_envelope(migrated)
    assert [entry["content"] for entry in migrated["entries"]] == [legacy_content]


def test_preseal_v2_migrates_to_current_without_losing_authority(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    created = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Migrate the complete pre-seal v2 authority exactly once.",
    )
    assert created is not None and created.approval_status == "approved"
    path = _project_authority_path(tmp_path)
    authority = json.loads(path.read_text("utf-8"))
    authority["schemaVersion"] = 2
    authority.pop("approval_audit_head")
    authority.pop("observation_receipts")
    for raw_entry in authority["entries"]:
        raw_entry.pop("authority_state_digest")
    path.write_text(json.dumps(authority), encoding="utf-8")
    preseal_v2 = path.read_bytes()

    with pytest.raises(MemoryStoreConflict, match="requires schema migration"):
        _new_manager(tmp_path, readonly_load=True)
    assert path.read_bytes() == preseal_v2

    migrated_manager = _new_manager(tmp_path)
    migrated_entry = migrated_manager.memories[MemoryScope.PROJECT]._id_index[
        created.id
    ]
    migrated = json.loads(path.read_text("utf-8"))

    assert migrated["schemaVersion"] == (
        authority_io_mod.CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION
    )
    assert migrated_entry.content == created.content
    assert migrated_entry.approval_status == "pending"
    assert migrated_entry.approval_actor == "migration"
    assert migrated_entry.is_active is False
    assert any(
        record.get("action") == "authority_schema_migration"
        and record.get("entry_id") == created.id
        for record in migrated["approval_audit"]
    )
    state_digest = migrated["entries"][0]["authority_state_digest"]
    assert len(state_digest) == 64
    assert int(state_digest, 16) >= 0
    strict_reload = _new_manager(tmp_path, readonly_load=True)
    assert created.id in strict_reload.memories[MemoryScope.PROJECT]._id_index


def test_current_authority_stores_full_write_receipt_in_content_addressed_blob(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    commit = MemoryAuthorityCommit(
        operation_id="memoryop_" + "1" * 32,
        payload_digest="1" * 64,
        scope="project",
        rejected_claim_ids=tuple(
            f"claim-{index:02d}-" + "x" * 100
            for index in range(64)
        ),
    )
    manager._memory_write_receipts[MemoryScope.PROJECT][commit.operation_id] = commit
    manager._pipeline_operation_receipts[MemoryScope.PROJECT][
        commit.operation_id
    ] = commit.payload_digest

    manager._save_scope(MemoryScope.PROJECT)

    authority_path = _project_authority_path(tmp_path)
    authority = json.loads(authority_path.read_text("utf-8"))
    reference = MemoryAuthorityReceiptReference.from_dict(
        authority["write_receipts"][0]
    )
    assert authority["schemaVersion"] == (
        authority_io_mod.CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION
    )
    assert "materializations" not in authority["write_receipts"][0]
    assert len(json.dumps(reference.to_dict())) < len(commit.to_json())

    resolved = MemoryBlobStore(authority_path.parent).read_json(reference.blob)
    assert resolved == commit.to_dict()
    reference.validate_commit(MemoryAuthorityCommit.from_dict(resolved))

    strict = _new_manager(tmp_path, readonly_load=True)
    assert strict._memory_write_receipts[MemoryScope.PROJECT][
        commit.operation_id
    ] == commit


def test_v3_inline_receipt_migrates_to_current_blob_without_demoting_sealed_entry(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "The v3 authority seal remains valid during receipt migration.",
    )
    assert entry is not None and entry.approval_status == "approved"
    commit = _receipt(2)
    manager._memory_write_receipts[MemoryScope.PROJECT][commit.operation_id] = commit
    manager._pipeline_operation_receipts[MemoryScope.PROJECT][
        commit.operation_id
    ] = commit.payload_digest
    manager._save_scope(MemoryScope.PROJECT)

    path = _project_authority_path(tmp_path)
    v3 = json.loads(path.read_text("utf-8"))
    v3["schemaVersion"] = 3
    v3.pop("approval_audit_head")
    v3.pop("observation_receipts")
    v3["write_receipts"] = [commit.to_dict()]
    path.write_text(json.dumps(v3), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(MemoryStoreConflict, match="requires schema migration"):
        _new_manager(tmp_path, readonly_load=True)
    assert path.read_bytes() == before

    migrated = _new_manager(tmp_path)
    persisted = json.loads(path.read_text("utf-8"))
    reference = MemoryAuthorityReceiptReference.from_dict(
        persisted["write_receipts"][0]
    )
    assert persisted["schemaVersion"] == (
        authority_io_mod.CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION
    )
    assert migrated.memories[MemoryScope.PROJECT]._id_index[
        entry.id
    ].approval_status == "approved"
    assert migrated._memory_write_receipts[MemoryScope.PROJECT][
        commit.operation_id
    ] == commit
    assert MemoryBlobStore(path.parent).read_json(reference.blob) == commit.to_dict()


def test_v4_blob_receipt_migrates_to_v5_audit_head_without_demoting_sealed_entry(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "The v4 authority seal and blob receipt survive audit-head migration.",
    )
    assert entry is not None and entry.approval_status == "approved"
    commit = _receipt(22)
    manager._memory_write_receipts[MemoryScope.PROJECT][commit.operation_id] = commit
    manager._pipeline_operation_receipts[MemoryScope.PROJECT][
        commit.operation_id
    ] = commit.payload_digest
    manager._save_scope(MemoryScope.PROJECT)

    path = _project_authority_path(tmp_path)
    v4 = json.loads(path.read_text("utf-8"))
    v4["schemaVersion"] = 4
    v4.pop("approval_audit_head")
    v4.pop("observation_receipts")
    path.write_text(json.dumps(v4), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(MemoryStoreConflict, match="requires schema migration"):
        _new_manager(tmp_path, readonly_load=True)
    assert path.read_bytes() == before

    migrated = _new_manager(tmp_path)
    persisted = json.loads(path.read_text("utf-8"))

    assert persisted["schemaVersion"] == (
        authority_io_mod.CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION
    )
    assert persisted["approval_audit_head"] is None
    assert migrated.memories[MemoryScope.PROJECT]._id_index[
        entry.id
    ].approval_status == "approved"
    assert migrated._memory_write_receipts[MemoryScope.PROJECT][
        commit.operation_id
    ] == commit
    strict = _new_manager(tmp_path, readonly_load=True)
    assert strict.memories[MemoryScope.PROJECT]._id_index[
        entry.id
    ].approval_status == "approved"


def test_v5_segmented_audit_migrates_to_current_observation_receipts(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "The v5 segmented authority gains isolated replay receipt lanes.",
    )
    assert entry is not None and entry.approval_status == "approved"
    path = _project_authority_path(tmp_path)
    v5 = json.loads(path.read_text("utf-8"))
    v5["schemaVersion"] = 5
    v5.pop("observation_receipts")
    path.write_text(json.dumps(v5), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(MemoryStoreConflict, match="requires schema migration"):
        _new_manager(tmp_path, readonly_load=True)
    assert path.read_bytes() == before

    migrated = _new_manager(tmp_path)
    persisted = json.loads(path.read_text("utf-8"))

    assert persisted["schemaVersion"] == (
        authority_io_mod.CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION
    )
    assert persisted["observation_receipts"] == {
        "maintenance": [],
        "telemetry": [],
    }
    assert migrated.memories[MemoryScope.PROJECT]._id_index[
        entry.id
    ].approval_status == "approved"


def test_unversioned_sparse_audit_can_migrate_without_becoming_current_or_vanishing(
    tmp_path: Path,
) -> None:
    entry_id = "project-legacy-audit-1"
    legacy_audit_id = "legacy-audit-1"
    path = _write_legacy_project_authority(
        tmp_path,
        entries=[
            {
                "id": entry_id,
                "scope": "project",
                "category": "tool_recovery",
                "content": "Retry a failed tool through the supported recovery path.",
            }
        ],
        approval_audit=[
            {
                "audit_id": legacy_audit_id,
                "entry_id": entry_id,
                "action": "historic_approval",
                "created_at": 1_700_000_000.0,
            }
        ],
    )

    manager = _new_manager(tmp_path)
    persisted = json.loads(path.read_text("utf-8"))

    assert entry_id in manager.memories[MemoryScope.PROJECT]._id_index
    assert persisted["schemaVersion"] == (
        authority_io_mod.CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION
    )
    # A sparse legacy object cannot be copied into the strict v5 hot history.
    # Its source identity must nevertheless remain recoverable either in a
    # typed migration record or in the migration quarantine.
    current_text = json.dumps(persisted, sort_keys=True)
    quarantine_text = "\n".join(
        backup.read_text("utf-8")
        for backup in (tmp_path / "data" / "memory-migration-quarantine").glob(
            "*/*.bak"
        )
    )
    assert legacy_audit_id in current_text or legacy_audit_id in quarantine_text
    _new_manager(tmp_path, readonly_load=True)


def test_missing_current_receipt_blob_fails_closed_without_rewriting_root(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    commit = _receipt(3)
    manager._memory_write_receipts[MemoryScope.PROJECT][commit.operation_id] = commit
    manager._save_scope(MemoryScope.PROJECT)
    path = _project_authority_path(tmp_path)
    authority = json.loads(path.read_text("utf-8"))
    reference = MemoryAuthorityReceiptReference.from_dict(
        authority["write_receipts"][0]
    )
    blob_path = MemoryBlobStore(path.parent).root / reference.blob.filename
    blob_path.unlink()
    committed_root = path.read_bytes()

    with pytest.raises(MemoryStoreConflict, match="blob is missing"):
        _new_manager(tmp_path, readonly_load=True)

    assert path.read_bytes() == committed_root
    assert blob_path.exists() is False


def test_full_receipt_window_no_longer_consumes_primary_authority_budget(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    materializations = tuple(
        MemoryMaterialization(
            claim_id=f"claim-{index:02d}-" + "x" * 100,
            entry_id=(
                f"project-{1_788_000_000_000_000_000 + index}-{index:08x}"
            ),
            authority_digest=f"{index + 1:064x}",
            disposition="created",
            superseded_entry_ids=tuple(
                "project-"
                f"{1_700_000_000_000_000_000 + index * 16 + prior}-"
                f"{prior:08x}"
                for prior in range(16)
            ),
        )
        for index in range(64)
    )
    inline_bytes = 0
    for index in range(memory_mod._MAX_MEMORY_WRITE_RECEIPTS):
        commit = MemoryAuthorityCommit(
            operation_id=f"memoryop_{index + 1:032x}",
            payload_digest=f"{index + 1:064x}",
            scope="project",
            materializations=materializations,
        )
        inline_bytes += len(commit.to_json().encode("utf-8"))
        manager._memory_write_receipts[MemoryScope.PROJECT][
            commit.operation_id
        ] = commit
        manager._pipeline_operation_receipts[MemoryScope.PROJECT][
            commit.operation_id
        ] = commit.payload_digest

    assert inline_bytes > authority_io_mod.MAX_AUTHORITY_FILE_BYTES
    manager._save_scope(MemoryScope.PROJECT)

    path = _project_authority_path(tmp_path)
    authority_bytes = path.read_bytes()
    authority = json.loads(authority_bytes)
    assert len(authority_bytes) < authority_io_mod.MAX_AUTHORITY_FILE_BYTES
    assert len(authority["write_receipts"]) == (
        memory_mod._MAX_MEMORY_WRITE_RECEIPTS
    )
    strict = _new_manager(tmp_path, readonly_load=True)
    assert len(strict._memory_write_receipts[MemoryScope.PROJECT]) == (
        memory_mod._MAX_MEMORY_WRITE_RECEIPTS
    )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        pytest.param("usage_count", -1, id="negative-counter"),
        pytest.param("retrieval_count", True, id="boolean-counter"),
        pytest.param("usefulness_score", float("nan"), id="nan-score"),
        pytest.param("usefulness_score", 1.01, id="score-above-contract"),
        pytest.param(
            "corroborated_usefulness_score",
            -1.01,
            id="score-below-contract",
        ),
        pytest.param(
            "success_count",
            MAX_MEMORY_COUNTER + 1,
            id="counter-above-json-safe-range",
        ),
        pytest.param("last_accessed", float("inf"), id="infinite-timestamp"),
    ],
)
def test_invalid_numeric_authority_values_fail_closed_before_entry_construction(
    tmp_path: Path,
    field_name: str,
    invalid_value: Any,
) -> None:
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Validate persisted feedback accounting before retrieval.",
    )
    assert entry is not None
    path = _project_authority_path(tmp_path)
    authority = json.loads(path.read_text("utf-8"))
    authority["entries"][0][field_name] = invalid_value
    path.write_text(
        json.dumps(authority, allow_nan=True),
        encoding="utf-8",
    )

    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        _new_manager(tmp_path, readonly_load=True)


@pytest.mark.parametrize(
    ("success_field", "failure_field", "score_field"),
    [
        ("success_count", "failure_count", "usefulness_score"),
        (
            "corroborated_success_count",
            "corroborated_failure_count",
            "corroborated_usefulness_score",
        ),
    ],
)
def test_versioned_usefulness_scores_must_match_their_counters(
    tmp_path: Path,
    success_field: str,
    failure_field: str,
    score_field: str,
) -> None:
    manager = _new_manager(tmp_path)
    assert manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Feedback ranking is a derived authority value.",
    ) is not None
    path = _project_authority_path(tmp_path)
    authority = json.loads(path.read_text("utf-8"))
    raw = authority["entries"][0]
    raw[success_field] = 0
    raw[failure_field] = 10
    raw[score_field] = 1.0
    path.write_text(json.dumps(authority), encoding="utf-8")

    with pytest.raises(MemoryApprovalError):
        MemoryApprovalAuthority(tmp_path / "workspace").read_scope_snapshot(
            MemoryScope.PROJECT
        )
    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        _new_manager(tmp_path, readonly_load=True)


def test_canonical_write_recomputes_live_usefulness_views_without_reapproval(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Treat feedback scores as derived views of their evidence counters.",
    )
    assert entry is not None
    assert entry.approval_status == "approved"
    approval_hash = entry.approval_content_hash
    audit_before = manager.get_approval_audit(entry.id)

    entry.success_count = 3
    entry.failure_count = 1
    entry.usefulness_score = -1.0
    entry.corroborated_success_count = 1
    entry.corroborated_failure_count = 3
    entry.corroborated_usefulness_score = 1.0

    # Retrieval accounting is a normal production write seam. It must not be
    # made unavailable by a stale denormalized score on a live entry.
    manager.record_retrievals([entry.id])

    assert entry.usefulness_score == pytest.approx(0.5)
    assert entry.corroborated_usefulness_score == pytest.approx(-0.5)
    assert entry.approval_status == "approved"
    assert entry.approval_content_hash == approval_hash
    assert manager.get_approval_audit(entry.id) == audit_before

    persisted = json.loads(
        _project_authority_path(tmp_path).read_text("utf-8")
    )["entries"][0]
    assert persisted["usefulness_score"] == pytest.approx(0.5)
    assert persisted["corroborated_usefulness_score"] == pytest.approx(-0.5)

    reloaded = _new_manager(tmp_path, readonly_load=True)
    durable = reloaded.memories[MemoryScope.PROJECT]._id_index[entry.id]
    assert durable.usefulness_score == pytest.approx(0.5)
    assert durable.corroborated_usefulness_score == pytest.approx(-0.5)
    assert durable.approval_status == "approved"
    assert durable.approval_content_hash == approval_hash


def test_legacy_unversioned_feedback_is_reset_during_migration(
    tmp_path: Path,
) -> None:
    path = _write_legacy_project_authority(
        tmp_path,
        entries=[
            {
                "id": "project-legacy-score",
                "scope": "project",
                "content": "Normalize redundant feedback statistics.",
                "success_count": 0,
                "failure_count": 10,
                "usefulness_score": 1.0,
            }
        ],
    )

    manager = _new_manager(tmp_path)
    entry = manager.memories[MemoryScope.PROJECT]._id_index[
        "project-legacy-score"
    ]
    persisted = json.loads(path.read_text("utf-8"))["entries"][0]

    assert entry.success_count == entry.failure_count == 0
    assert entry.usefulness_score == 0.0
    assert persisted["success_count"] == persisted["failure_count"] == 0
    assert persisted["usefulness_score"] == 0.0


@pytest.mark.parametrize(
    "timestamp_field",
    ["created_at", "updated_at", "last_accessed", "last_used"],
)
def test_versioned_timestamps_fit_the_shared_runtime_range(
    tmp_path: Path,
    timestamp_field: str,
) -> None:
    manager = _new_manager(tmp_path)
    assert manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Timestamp authority must be renderable by every consumer.",
    ) is not None
    path = _project_authority_path(tmp_path)
    authority = json.loads(path.read_text("utf-8"))
    authority["entries"][0][timestamp_field] = MAX_MEMORY_TIMESTAMP + 1
    path.write_text(json.dumps(authority), encoding="utf-8")

    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        _new_manager(tmp_path, readonly_load=True)


def test_future_clock_skew_cannot_amplify_recency_rank() -> None:
    assert memory_mod._bounded_age_hours(2_000.0, now=1_000.0) == 0.0
    assert memory_mod._bounded_age_hours(500.0, now=1_000.0) == pytest.approx(
        500.0 / 3600.0
    )


def test_metric_counters_saturate_without_breaking_normal_task_accounting(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    created = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Counter saturation must not interrupt a successful task.",
    )
    assert created is not None
    path = _project_authority_path(tmp_path)
    authority = json.loads(path.read_text("utf-8"))
    raw = authority["entries"][0]
    raw["retrieval_count"] = MAX_MEMORY_COUNTER
    raw["injection_count"] = MAX_MEMORY_COUNTER
    raw["success_count"] = MAX_MEMORY_COUNTER
    raw["failure_count"] = 0
    raw["usefulness_score"] = 1.0
    raw["corroborated_success_count"] = MAX_MEMORY_COUNTER
    raw["corroborated_failure_count"] = 0
    raw["corroborated_usefulness_score"] = 1.0
    path.write_text(json.dumps(authority), encoding="utf-8")

    saturated = _new_manager(tmp_path)
    saturated.record_retrievals_and_injections(
        [created.id],
        [created.id],
    )
    saturated.record_feedback(
        [created.id],
        True,
        observation_id="counter-saturation-ordinary",
    )
    saturated.record_corroborated_feedback(
        [created.id],
        True,
        source="explicit_user_accept",
        observation_id="counter-saturation-corroborated",
    )

    reloaded = _new_manager(tmp_path, readonly_load=True)
    entry = reloaded.memories[MemoryScope.PROJECT]._id_index[created.id]
    assert entry.retrieval_count == MAX_MEMORY_COUNTER
    assert entry.injection_count == MAX_MEMORY_COUNTER
    assert entry.success_count == MAX_MEMORY_COUNTER
    assert entry.corroborated_success_count == MAX_MEMORY_COUNTER
    assert entry.usefulness_score == 1.0
    assert entry.corroborated_usefulness_score == 1.0


@pytest.mark.parametrize(
    "invalid_id",
    ["", "contains whitespace", "../escapes", "x" * 129],
)
def test_v2_authority_rejects_invalid_entry_ids(
    tmp_path: Path,
    invalid_id: str,
) -> None:
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Validate the persisted entry identity before indexing.",
    )
    assert entry is not None
    path = _project_authority_path(tmp_path)
    authority = json.loads(path.read_text("utf-8"))
    authority["entries"][0]["id"] = invalid_id
    path.write_text(json.dumps(authority), encoding="utf-8")

    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        _new_manager(tmp_path, readonly_load=True)


def test_v2_authority_rejects_duplicate_entry_ids(tmp_path: Path) -> None:
    manager = _new_manager(tmp_path)
    first = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "First entry identity must remain unique.",
    )
    second = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Second entry identity must remain unique.",
    )
    assert first is not None and second is not None
    path = _project_authority_path(tmp_path)
    authority = json.loads(path.read_text("utf-8"))
    authority["entries"][1]["id"] = authority["entries"][0]["id"]
    path.write_text(json.dumps(authority), encoding="utf-8")

    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        _new_manager(tmp_path, readonly_load=True)


def test_v2_authority_rejects_duplicate_json_members(tmp_path: Path) -> None:
    path = _project_authority_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"schemaVersion":3,"scope":"project","entries":[],"entries":[]}',
        encoding="utf-8",
    )

    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        _new_manager(tmp_path, readonly_load=True)


def test_approval_reader_uses_the_same_strict_json_contract(tmp_path: Path) -> None:
    path = _project_authority_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"schemaVersion":3,"scope":"project","entries":[],"entries":[]}',
        encoding="utf-8",
    )

    with pytest.raises(MemoryApprovalError) as captured:
        MemoryApprovalAuthority(tmp_path / "workspace").read_scope_snapshot(
            MemoryScope.PROJECT
        )

    assert captured.value.code == "memory_approval_failed"


def test_strict_reader_rejects_overflow_hidden_in_nested_metadata(
    tmp_path: Path,
) -> None:
    path = _project_authority_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"schemaVersion":3,"scope":"project","entries":['
        '{"id":"project-overflow","scope":"project","content":"rule",'
        '"metadata":{"weight":1e999}}]}',
        encoding="utf-8",
    )

    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        _new_manager(tmp_path, readonly_load=True)


def test_live_invalid_numeric_state_is_rejected_before_authority_replace(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Do not publish a counter that another reader cannot represent exactly.",
    )
    assert entry is not None
    path = _project_authority_path(tmp_path)
    committed = path.read_bytes()

    def corrupt_then_save() -> None:
        entry.success_count = MAX_MEMORY_COUNTER + 1
        manager._save_scope(MemoryScope.PROJECT)

    with pytest.raises(MemoryStoreConflict, match="invalid state"):
        manager.coordinated_write(
            (MemoryScope.PROJECT,),
            corrupt_then_save,
        )

    assert path.read_bytes() == committed
    assert manager.memories[MemoryScope.PROJECT].entries[0].success_count == 0


def test_live_scope_mutation_cannot_publish_cross_root_authority(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "A mutable entry must remain bound to its canonical root scope.",
    )
    assert entry is not None
    path = _project_authority_path(tmp_path)
    committed = path.read_bytes()

    entry.scope = MemoryScope.USER

    with pytest.raises(MemoryStoreConflict, match="scope does not match"):
        manager.add_tag(MemoryScope.PROJECT, entry.id, "scope-fence")

    assert path.read_bytes() == committed
    live = manager.memories[MemoryScope.PROJECT].entries[0]
    assert live.scope is MemoryScope.PROJECT
    assert "scope-fence" not in live.tags
    assert _new_manager(
        tmp_path,
        readonly_load=True,
    ).memories[MemoryScope.PROJECT]._id_index[live.id].scope is MemoryScope.PROJECT


def test_live_id_mutation_cannot_publish_a_cross_scope_duplicate(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    project_entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Project identity must not collide with another scope.",
    )
    user_entry = manager.add_entry(
        MemoryScope.USER,
        "testing",
        "User identity remains globally unique.",
    )
    assert project_entry is not None and user_entry is not None
    path = _project_authority_path(tmp_path)
    committed = path.read_bytes()
    original_project_id = project_entry.id

    project_entry.id = user_entry.id

    with pytest.raises(MemoryStoreConflict, match="cross-scope duplicate"):
        manager.add_tag(
            MemoryScope.PROJECT,
            user_entry.id,
            "identity-fence",
        )

    assert path.read_bytes() == committed
    live = manager.memories[MemoryScope.PROJECT].entries[0]
    assert live.id == original_project_id
    assert "identity-fence" not in live.tags
    strict = _new_manager(tmp_path, readonly_load=True)
    assert original_project_id in strict.memories[MemoryScope.PROJECT]._id_index
    assert user_entry.id in strict.memories[MemoryScope.USER]._id_index


def test_live_policy_mutation_cannot_bypass_user_review(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "This lesson must remain pending until an explicit review.",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    assert entry.approval_status == "pending"
    path = _project_authority_path(tmp_path)
    committed = path.read_bytes()

    entry.approval_policy = MemoryApprovalPolicy.USER_EXPLICIT

    with pytest.raises(MemoryStoreConflict, match="authority changed"):
        manager.add_tag(MemoryScope.PROJECT, entry.id, "policy-fence")

    assert path.read_bytes() == committed
    live = manager.memories[MemoryScope.PROJECT].entries[0]
    assert live.approval_policy is MemoryApprovalPolicy.USER_REVIEW_REQUIRED
    assert live.approval_status == "pending"
    assert live.is_active is False
    strict = _new_manager(tmp_path, readonly_load=True)
    persisted = strict.memories[MemoryScope.PROJECT]._id_index[live.id]
    assert persisted.approval_policy is MemoryApprovalPolicy.USER_REVIEW_REQUIRED
    assert persisted.approval_status == "pending"
    assert persisted.is_active is False


def test_live_status_mutation_cannot_be_laundered_by_recurrence(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "tool_recovery",
        "A recurrence must not approve a lesson that still needs review.",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    assert entry.approval_status == "pending"
    path = _project_authority_path(tmp_path)
    committed = path.read_bytes()

    entry.approval_status = "approved"

    with pytest.raises(MemoryStoreConflict, match="authority changed"):
        manager.reinforce_reflection_entry(entry.id)

    assert path.read_bytes() == committed
    live = manager.memories[MemoryScope.PROJECT].entries[0]
    assert live.approval_status == "pending"
    assert live.is_active is False
    strict = _new_manager(tmp_path, readonly_load=True)
    persisted = strict.memories[MemoryScope.PROJECT]._id_index[live.id]
    assert persisted.approval_status == "pending"
    assert persisted.is_active is False


def test_nested_transaction_cannot_launder_live_status_mutation(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "tool_recovery",
        "An outer transaction must not weaken nested mutation checks.",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None and entry.approval_status == "pending"
    path = _project_authority_path(tmp_path)
    committed = path.read_bytes()

    def mutate_then_reinforce() -> bool | tuple[str, str]:
        entry.approval_status = "approved"
        return manager.reinforce_reflection_entry(entry.id)

    with pytest.raises(MemoryStoreConflict, match="does not cover"):
        manager.coordinated_write(
            (MemoryScope.PROJECT,),
            mutate_then_reinforce,
        )

    assert path.read_bytes() == committed
    live = manager.memories[MemoryScope.PROJECT].entries[0]
    assert live.approval_status == "pending"
    assert live.metadata.get("recurrence") is None
    assert live.is_active is False
    strict = _new_manager(tmp_path, readonly_load=True)
    persisted = strict.memories[MemoryScope.PROJECT]._id_index[live.id]
    assert persisted.approval_status == "pending"
    assert persisted.metadata.get("recurrence") is None


@pytest.mark.parametrize(
    "field_name",
    [
        "scope",
        "category",
        "tier",
        "lifecycle_status",
        "curator_locked",
        "safety_status",
        "approval_status",
        "approval_content_hash",
        "approval_policy",
        "authority_state_digest",
    ],
)
def test_current_entry_never_invents_missing_authority_fields(
    tmp_path: Path,
    field_name: str,
) -> None:
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Never replace missing versioned authority with a permissive default.",
    )
    assert entry is not None
    path = _project_authority_path(tmp_path)
    authority = json.loads(path.read_text("utf-8"))
    authority["entries"][0].pop(field_name)
    path.write_text(json.dumps(authority), encoding="utf-8")
    tampered = path.read_bytes()

    with pytest.raises(MemoryApprovalError) as captured:
        MemoryApprovalAuthority(tmp_path / "workspace").read_scope_snapshot(
            MemoryScope.PROJECT
        )
    assert captured.value.code == "memory_approval_failed"
    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        _new_manager(tmp_path, readonly_load=True)

    assert path.read_bytes() == tampered


@pytest.mark.parametrize(
    "field_name",
    [
        "scope",
        "last_updated",
        "approval_audit",
        "write_receipts",
        "pipeline_operation_receipts",
    ],
)
def test_current_root_never_falls_back_when_commit_fields_are_missing(
    tmp_path: Path,
    field_name: str,
) -> None:
    manager = _new_manager(tmp_path)
    assert manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "The versioned envelope is the complete commit authority.",
    ) is not None
    path = _project_authority_path(tmp_path)
    authority = json.loads(path.read_text("utf-8"))
    authority.pop(field_name)
    path.write_text(json.dumps(authority), encoding="utf-8")

    with pytest.raises(MemoryApprovalError):
        MemoryApprovalAuthority(tmp_path / "workspace").read_scope_snapshot(
            MemoryScope.PROJECT
        )
    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        _new_manager(tmp_path, readonly_load=True)


@pytest.mark.parametrize(
    "field_name",
    ["write_receipts", "pipeline_operation_receipts"],
)
def test_all_readers_share_the_versioned_receipt_contract(
    tmp_path: Path,
    field_name: str,
) -> None:
    manager = _new_manager(tmp_path)
    assert manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Replay authority must have one meaning for every reader.",
    ) is not None
    path = _project_authority_path(tmp_path)
    authority = json.loads(path.read_text("utf-8"))
    authority[field_name] = "malformed"
    path.write_text(json.dumps(authority), encoding="utf-8")

    with pytest.raises(MemoryApprovalError):
        MemoryApprovalAuthority(tmp_path / "workspace").read_scope_snapshot(
            MemoryScope.PROJECT
        )
    with pytest.raises(MemoryStoreConflict, match="receipts are invalid"):
        _new_manager(tmp_path, readonly_load=True)


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    [
        pytest.param("approval_status", "approved", id="pending-to-approved"),
        pytest.param("tier", "short_term", id="archival-to-injectable"),
        pytest.param("curator_locked", False, id="locked-to-unlocked"),
        pytest.param("lifecycle_status", "active", id="deprecated-to-active"),
        pytest.param("approval_content_hash", "", id="empty-approval-hash"),
    ],
)
def test_authority_state_digest_detects_unsealed_privilege_edits(
    tmp_path: Path,
    field_name: str,
    tampered_value: Any,
) -> None:
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Persisted permission gates must remain bound to one state digest.",
        tier=MemoryTier.ARCHIVAL,
        lifecycle_status="deprecated",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    path = _project_authority_path(tmp_path)
    authority = json.loads(path.read_text("utf-8"))
    raw = authority["entries"][0]
    if field_name == "curator_locked":
        raw["curator_locked"] = True
        raw["authority_state_digest"] = (
            memory_mod._authority_state_digest_for_data(raw)
        )
    elif field_name == "lifecycle_status":
        raw["lifecycle_status"] = "deprecated"
        raw["authority_state_digest"] = (
            memory_mod._authority_state_digest_for_data(raw)
        )
    raw[field_name] = tampered_value
    path.write_text(json.dumps(authority), encoding="utf-8")

    with pytest.raises(MemoryApprovalError):
        MemoryApprovalAuthority(tmp_path / "workspace").read_scope_snapshot(
            MemoryScope.PROJECT
        )
    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        _new_manager(tmp_path, readonly_load=True)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("tier", "mystery"),
        ("lifecycle_status", "mystery"),
        ("safety_status", "mystery"),
        ("approval_status", "mystery"),
        ("approval_policy", "mystery"),
    ],
)
def test_current_authority_rejects_unknown_permission_enums(
    tmp_path: Path,
    field_name: str,
    invalid_value: str,
) -> None:
    manager = _new_manager(tmp_path)
    assert manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Unknown authority states must not be normalized during v2 load.",
    ) is not None
    path = _project_authority_path(tmp_path)
    authority = json.loads(path.read_text("utf-8"))
    authority["entries"][0][field_name] = invalid_value
    authority["entries"][0]["authority_state_digest"] = (
        memory_mod._authority_state_digest_for_data(authority["entries"][0])
    )
    path.write_text(json.dumps(authority), encoding="utf-8")

    with pytest.raises(MemoryStoreConflict, match="Memory authority is invalid"):
        _new_manager(tmp_path, readonly_load=True)


def test_current_nested_metadata_must_fit_the_canonical_decoder_depth(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    assert manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "The validator and canonical decoder share one depth budget.",
    ) is not None
    path = _project_authority_path(tmp_path)
    authority = json.loads(path.read_text("utf-8"))
    nested: dict[str, Any] = {"leaf": "value"}
    for _ in range(20):
        nested = {"nested": nested}
    authority["entries"][0]["metadata"] = nested
    valid, errors = memory_mod._validate_memory_data(authority)

    assert valid is False
    assert any("exceeds persistence depth" in error for error in errors)


def test_legacy_201_entry_authority_converges_to_runtime_capacity_on_load(
    tmp_path: Path,
) -> None:
    path = _write_legacy_project_authority(
        tmp_path,
        entries=[
            {
                "id": f"project-legacy-{index}",
                "scope": "project",
                "category": "testing",
                "content": f"Legacy recovery rule {index}.",
            }
            for index in range(201)
        ],
    )

    manager = _new_manager(tmp_path)
    memory_file = manager.memories[MemoryScope.PROJECT]

    assert len(memory_file.entries) <= memory_file.max_entries
    persisted = json.loads(path.read_text("utf-8"))
    assert len(persisted["entries"]) <= memory_file.max_entries


def test_capacity_migration_is_auditable_cleans_backlinks_and_is_stable(
    tmp_path: Path,
) -> None:
    entries = [
        {
            "id": f"project-legacy-{index:03d}",
            "scope": "project",
            "category": "testing",
            "content": f"Legacy recovery rule {index}.",
            "tier": "archival" if index == 0 else "short_term",
            "related_to": ["project-legacy-000"] if index == 1 else [],
        }
        for index in range(201)
    ]
    path = _write_legacy_project_authority(tmp_path, entries=entries)
    original = path.read_bytes()

    manager = _new_manager(tmp_path)
    memory_file = manager.memories[MemoryScope.PROJECT]

    assert "project-legacy-000" not in memory_file._id_index
    assert (
        memory_file._id_index["project-legacy-001"].related_to == []
    )
    eviction = next(
        record
        for record in manager.approval_audit[MemoryScope.PROJECT]
        if record.get("action") == "capacity_migration_eviction"
    )
    assert eviction["entry_id"] == "project-legacy-000"
    assert eviction["extra"]["retention_policy"] == "authority_first_v1"
    assert len(eviction["extra"]["evicted_content_hash"]) == 64
    backups = list(
        (tmp_path / "data" / "memory-migration-quarantine").glob(
            "*/*.bak"
        )
    )
    assert len(backups) == 1
    backup = backups[0]
    assert backup.read_bytes() == original

    migrated = path.read_bytes()
    _new_manager(tmp_path)
    assert path.read_bytes() == migrated


def test_readonly_legacy_over_capacity_requires_migration_without_writing(
    tmp_path: Path,
) -> None:
    path = _write_legacy_project_authority(
        tmp_path,
        entries=[
            {
                "id": f"project-legacy-{index:03d}",
                "scope": "project",
                "content": f"rule {index}",
            }
            for index in range(201)
        ],
    )
    original = path.read_bytes()

    with pytest.raises(MemoryStoreConflict, match="requires capacity migration"):
        _new_manager(tmp_path, readonly_load=True)

    assert path.read_bytes() == original
    assert not (tmp_path / "data" / "memory-migration-quarantine").exists()


def test_versioned_authority_over_capacity_fails_closed_without_migration(
    tmp_path: Path,
) -> None:
    path = _write_legacy_project_authority(
        tmp_path,
        entries=[
            {
                "id": f"project-versioned-{index:03d}",
                "scope": "project",
                "content": f"rule {index}",
            }
            for index in range(201)
        ],
    )
    authority = json.loads(path.read_text("utf-8"))
    authority["schemaVersion"] = authority_io_mod.CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION
    path.write_text(json.dumps(authority), encoding="utf-8")
    original = path.read_bytes()

    with pytest.raises(MemoryStoreConflict, match="exceeds runtime capacity"):
        _new_manager(tmp_path)

    assert path.read_bytes() == original
    assert not (tmp_path / "data" / "memory-migration-quarantine").exists()


def test_legacy_byte_overflow_uses_the_same_capacity_migration(tmp_path: Path) -> None:
    path = _write_legacy_project_authority(
        tmp_path,
        entries=[
            {
                "id": f"project-large-{index}",
                "scope": "project",
                "content": chr(ord("a") + index) * (14 * 1024),
            }
            for index in range(2)
        ],
    )

    manager = _new_manager(tmp_path)
    memory_file = manager.memories[MemoryScope.PROJECT]

    assert memory_file.size_bytes <= memory_file.max_size_bytes
    assert len(memory_file.entries) == 1
    persisted = json.loads(path.read_text("utf-8"))
    assert persisted["schemaVersion"] == (
        authority_io_mod.CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION
    )


def test_legacy_markdown_capacity_uses_the_same_migration_path(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "data"
    scope_root = workspace / ".mini-code-memory"
    workspace.mkdir()
    data_root.mkdir()
    scope_root.mkdir()
    source = scope_root / "MEMORY.md"
    source.write_text(
        "# Project Memory\n\n"
        + "\n".join(f"- markdown rule {index}" for index in range(201))
        + "\n",
        encoding="utf-8",
    )
    original = source.read_bytes()

    manager = MemoryManager(project_root=workspace, data_root=data_root)

    assert len(manager.memories[MemoryScope.PROJECT].entries) == 200
    authority = json.loads(_project_authority_path(tmp_path).read_text("utf-8"))
    assert len(authority["entries"]) == 200
    assert any(
        record.get("action") == "capacity_migration_eviction"
        for record in authority["approval_audit"]
    )
    backups = list(
        (data_root / "memory-migration-quarantine").glob("*/*.bak")
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def test_readonly_legacy_markdown_over_capacity_fails_without_writing(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "data"
    scope_root = workspace / ".mini-code-memory"
    workspace.mkdir()
    data_root.mkdir()
    scope_root.mkdir()
    source = scope_root / "MEMORY.md"
    source.write_text(
        "# Project Memory\n\n"
        + "\n".join(f"- markdown rule {index}" for index in range(201))
        + "\n",
        encoding="utf-8",
    )
    original = source.read_bytes()

    with pytest.raises(MemoryStoreConflict, match="requires capacity migration"):
        MemoryManager(
            project_root=workspace,
            data_root=data_root,
            readonly_load=True,
        )

    assert source.read_bytes() == original
    assert not _project_authority_path(tmp_path).exists()
    assert not (data_root / "memory-migration-quarantine").exists()


def test_approval_reader_rejects_versioned_byte_overflow(tmp_path: Path) -> None:
    path = _write_legacy_project_authority(
        tmp_path,
        entries=[
            {
                "id": f"project-large-{index}",
                "scope": "project",
                "content": chr(ord("a") + index) * (14 * 1024),
            }
            for index in range(2)
        ],
    )
    authority = json.loads(path.read_text("utf-8"))
    authority["schemaVersion"] = authority_io_mod.CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION
    path.write_text(json.dumps(authority), encoding="utf-8")

    with pytest.raises(MemoryApprovalError) as captured:
        MemoryApprovalAuthority(tmp_path / "workspace").read_scope_snapshot(
            MemoryScope.PROJECT
        )

    assert captured.value.code == "memory_approval_failed"


def test_update_rejects_runtime_byte_overflow_without_poisoning_authority(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Keep the last valid authority readable after a rejected update.",
    )
    assert entry is not None
    authority_path = _project_authority_path(tmp_path)
    authority_before = authority_path.read_bytes()
    original_content = entry.content

    with pytest.raises(MemoryStoreConflict, match="runtime capacity"):
        manager.update_entry(
            MemoryScope.PROJECT,
            entry.id,
            "x" * (MAX_CURRENT_AUTHORITY_CONTENT_BYTES + 1),
        )

    live_entry = manager.memories[MemoryScope.PROJECT]._id_index[entry.id]
    assert live_entry.content == original_content
    assert authority_path.read_bytes() == authority_before

    strict_reader = MemoryManager(
        project_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        readonly_load=True,
        approval_interpretation_mode=(
            ApprovalInterpretationMode.STRICT_FAIL_CLOSED
        ),
    )
    assert (
        strict_reader.memories[MemoryScope.PROJECT]._id_index[entry.id].content
        == original_content
    )


def test_full_audit_window_rotates_instead_of_freezing_ordinary_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exercise the real append/save path with a small configured window rather
    # than constructing a multi-megabyte fixture.  Both modules own a current
    # copy of this limit, which is itself the production seam under test.
    audit_limit = 3
    monkeypatch.setattr(memory_mod, "MAX_AUTHORITY_AUDIT_RECORDS", audit_limit)
    monkeypatch.setattr(
        authority_io_mod,
        "MAX_AUTHORITY_AUDIT_RECORDS",
        audit_limit,
    )
    manager = _new_manager(tmp_path)
    first = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Preserve the first valid audit record.",
    )
    assert first is not None
    template = manager.approval_audit[MemoryScope.PROJECT][-1]
    manager.approval_audit[MemoryScope.PROJECT] = [
        {
            **template,
            "audit_id": f"audit-170000000000000000{index}-{index:08x}",
            "created_at": 1_700_000_000.0 + index,
        }
        for index in range(audit_limit)
    ]
    manager._save_scope(MemoryScope.PROJECT)

    second = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "A full audit history must not block the next ordinary memory write.",
    )

    assert second is not None
    assert len(manager.approval_audit[MemoryScope.PROJECT]) <= audit_limit
    reloaded = _new_manager(tmp_path, readonly_load=True)
    assert second.id in reloaded.memories[MemoryScope.PROJECT]._id_index
