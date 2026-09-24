"""Failure-injection contracts for retrying multi-scope Memory operations.

``memory.json`` is the commit point for one scope, but several maintenance and
telemetry APIs mutate more than one scope before publishing those roots in
sequence.  A caller cannot know that the first publish committed when the
second one raises, so retrying the same logical operation must not apply the
first scope's mutation twice.

These tests intentionally exercise that crash boundary through the existing
``_atomic_write`` monkeypatch seam and then retry from a fresh manager, which
matches process-level recovery rather than relying on the failed instance's
in-memory state.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable

import pytest

from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_store import MemoryStoreConflict
from minicode.memory_write_contract import MemoryAuthorityCommit


def _manager(workspace: Path, data_root: Path) -> MemoryManager:
    return MemoryManager(project_root=workspace, data_root=data_root)


def _authority_path(workspace: Path, scope: MemoryScope, data_root: Path) -> Path:
    if scope is MemoryScope.USER:
        return data_root / "memory" / "memory.json"
    suffix = (
        ".mini-code-memory"
        if scope is MemoryScope.PROJECT
        else ".mini-code-memory-local"
    )
    return workspace / suffix / "memory.json"


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _fail_second_authority_publish(
    manager: MemoryManager,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], tuple[Path, ...]]:
    """Fail before replacing the second scope's authoritative root."""

    original_atomic_write = manager._atomic_write
    attempted_roots: list[Path] = []

    def fail_second_memory_json(target: Path, content: str) -> None:
        if target.name == "memory.json":
            attempted_roots.append(target)
            if len(attempted_roots) == 2:
                raise OSError("simulated second-scope authority failure")
        original_atomic_write(target, content)

    monkeypatch.setattr(manager, "_atomic_write", fail_second_memory_json)
    return lambda: tuple(attempted_roots)


def _persist_old_usage(
    manager: MemoryManager,
    scope: MemoryScope,
    *,
    usage_count: int,
) -> str:
    entry = manager.add_entry(
        scope,
        "testing",
        f"Retry-safe decay fixture for {scope.value}",
        source="test",
    )
    assert entry is not None
    entry.usage_count = usage_count
    entry.updated_at = time.time() - (60 * 86400)
    entry._seal_authority_state()
    manager._save_scope(scope)
    return entry.id


def test_decay_retry_after_second_scope_failure_applies_each_scope_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data"
    manager = _manager(workspace, data_root)
    user_id = _persist_old_usage(
        manager,
        MemoryScope.USER,
        usage_count=8,
    )
    project_id = _persist_old_usage(
        manager,
        MemoryScope.PROJECT,
        usage_count=8,
    )
    attempted_roots = _fail_second_authority_publish(manager, monkeypatch)

    with pytest.raises(OSError, match="second-scope authority failure"):
        manager.decay_memories(
            max_age_days=30,
            decay_factor=0.5,
            operation_id="maintenance-decay-2026-09-03",
        )

    assert len(attempted_roots()) == 2

    # The caller only observed failure and therefore retries the same logical
    # maintenance operation after restarting the process.
    retried = _manager(workspace, data_root)
    retried.decay_memories(
        max_age_days=30,
        decay_factor=0.5,
        operation_id="maintenance-decay-2026-09-03",
    )
    persisted = _manager(workspace, data_root)

    assert persisted.memories[MemoryScope.USER]._id_index[user_id].usage_count == 4
    assert (
        persisted.memories[MemoryScope.PROJECT]
        ._id_index[project_id]
        .usage_count
        == 4
    )


def test_task_start_telemetry_retry_after_second_scope_failure_counts_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data"
    manager = _manager(workspace, data_root)
    project = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Retry-safe task-start telemetry for the project scope",
        source="test",
    )
    user = manager.add_entry(
        MemoryScope.USER,
        "testing",
        "Retry-safe task-start telemetry for the user scope",
        source="test",
    )
    assert project is not None
    assert user is not None
    attempted_roots = _fail_second_authority_publish(manager, monkeypatch)
    ids = [project.id, user.id]

    with pytest.raises(OSError, match="second-scope authority failure"):
        manager.record_retrievals_and_injections(
            ids,
            ids,
            observation_id="prompt-receipt-abc",
        )

    assert len(attempted_roots()) == 2

    retried = _manager(workspace, data_root)
    retried.record_retrievals_and_injections(
        ids,
        ids,
        observation_id="prompt-receipt-abc",
    )
    persisted = _manager(workspace, data_root)

    for scope, entry_id in (
        (MemoryScope.PROJECT, project.id),
        (MemoryScope.USER, user.id),
    ):
        entry = persisted.memories[scope]._id_index[entry_id]
        assert entry.retrieval_count == 1
        assert entry.injection_count == 1


def test_decay_operation_id_is_bound_to_the_exact_maintenance_policy(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data"
    manager = _manager(workspace, data_root)
    entry_id = _persist_old_usage(
        manager,
        MemoryScope.PROJECT,
        usage_count=8,
    )

    manager.decay_memories(
        max_age_days=30,
        decay_factor=0.5,
        operation_id="maintenance-decay-2026-09-03",
    )

    # A stable ID denotes one exact logical operation, not a reusable label.
    # Rebinding it to a different threshold/factor would make replay safety
    # ambiguous and therefore must fail closed before changing authority.
    replay = _manager(workspace, data_root)
    with pytest.raises(MemoryStoreConflict, match="(digest|payload|operation)"):
        replay.decay_memories(
            max_age_days=45,
            decay_factor=0.25,
            operation_id="maintenance-decay-2026-09-03",
        )

    persisted = _manager(workspace, data_root)
    assert (
        persisted.memories[MemoryScope.PROJECT]
        ._id_index[entry_id]
        .usage_count
        == 4
    )


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param("missing", id="missing-observation-receipts"),
        pytest.param("malformed", id="malformed-observation-receipts"),
        pytest.param("duplicate", id="duplicate-observation-digest"),
    ],
)
def test_v6_observation_receipts_fail_closed(
    tmp_path: Path,
    damage: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data"
    manager = _manager(workspace, data_root)
    assert manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "The v6 scope receipt envelope is strict authority.",
        source="test",
    ) is not None
    path = _authority_path(workspace, MemoryScope.PROJECT, data_root)
    authority = json.loads(path.read_text(encoding="utf-8"))
    assert authority["schemaVersion"] == 6

    if damage == "missing":
        authority.pop("observation_receipts")
    elif damage == "malformed":
        authority["observation_receipts"] = {
            "maintenance": [],
            "telemetry": "not-a-receipt-list",
        }
    else:
        duplicate = {
            "observationDigest": "a" * 64,
            "payloadDigest": "b" * 64,
        }
        authority["observation_receipts"] = {
            "maintenance": [],
            "telemetry": [duplicate, dict(duplicate)],
        }
    path.write_text(json.dumps(authority), encoding="utf-8")
    damaged_bytes = path.read_bytes()

    with pytest.raises(
        MemoryStoreConflict,
        match=r"Memory (?:authority|observation receipts) (?:is|are) invalid",
    ):
        MemoryManager(
            project_root=workspace,
            data_root=data_root,
            readonly_load=True,
        )

    assert path.read_bytes() == damaged_bytes


def test_observation_receipt_lanes_have_independent_capacity_and_authority(
    tmp_path: Path,
) -> None:
    import minicode.memory as memory_module

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data"
    manager = _manager(workspace, data_root)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Receipt lanes must not evict durable writes or user feedback.",
        source="test",
    )
    assert entry is not None
    manager.record_feedback(
        [entry.id],
        True,
        observation_id="feedback-capacity-sentinel",
    )
    entry = manager.memories[MemoryScope.PROJECT]._id_index[entry.id]
    feedback_before = dict(entry.feedback_observations)

    write_operation_id = "memoryop_" + "1" * 32
    pipeline_operation_id = "memoryop_" + "2" * 32
    manager._memory_write_receipts[MemoryScope.PROJECT][write_operation_id] = (
        MemoryAuthorityCommit(
            operation_id=write_operation_id,
            payload_digest="3" * 64,
            scope=MemoryScope.PROJECT.value,
            rejected_claim_ids=("claim-write-capacity-sentinel",),
        )
    )
    manager._pipeline_operation_receipts[MemoryScope.PROJECT][
        pipeline_operation_id
    ] = "4" * 64

    limit = memory_module._MAX_OBSERVATION_RECEIPTS_PER_LANE
    maintenance_sentinel = (
        _digest("maintenance-observation-sentinel"),
        _digest("maintenance-payload-sentinel"),
    )
    assert manager._claim_scope_effect_receipt(
        MemoryScope.PROJECT,
        lane="maintenance",
        receipt=maintenance_sentinel,
    )
    for index in range(limit + 1):
        manager._claim_scope_effect_receipt(
            MemoryScope.PROJECT,
            lane="telemetry",
            receipt=(
                _digest(f"telemetry-observation-{index}"),
                _digest(f"telemetry-payload-{index}"),
            ),
        )

    telemetry_before_maintenance_pressure = dict(
        manager._observation_receipts[MemoryScope.PROJECT]["telemetry"]
    )
    assert len(telemetry_before_maintenance_pressure) == limit
    assert (
        maintenance_sentinel[0]
        in manager._observation_receipts[MemoryScope.PROJECT]["maintenance"]
    )

    for index in range(limit + 1):
        manager._claim_scope_effect_receipt(
            MemoryScope.PROJECT,
            lane="maintenance",
            receipt=(
                _digest(f"maintenance-observation-{index}"),
                _digest(f"maintenance-payload-{index}"),
            ),
        )
    assert manager._observation_receipts[MemoryScope.PROJECT][
        "telemetry"
    ] == telemetry_before_maintenance_pressure
    assert len(
        manager._observation_receipts[MemoryScope.PROJECT]["maintenance"]
    ) == limit

    manager._save_scope(MemoryScope.PROJECT)
    persisted = _manager(workspace, data_root)
    assert set(persisted._memory_write_receipts[MemoryScope.PROJECT]) == {
        write_operation_id
    }
    assert persisted._pipeline_operation_receipts[MemoryScope.PROJECT] == {
        pipeline_operation_id: "4" * 64
    }
    persisted_entry = persisted.memories[MemoryScope.PROJECT]._id_index[
        entry.id
    ]
    assert persisted_entry.feedback_observations == feedback_before
    assert persisted._observation_receipts[MemoryScope.PROJECT][
        "telemetry"
    ] == telemetry_before_maintenance_pressure
    assert len(
        persisted._observation_receipts[MemoryScope.PROJECT]["maintenance"]
    ) == limit


def test_completed_scope_effect_replay_is_authority_byte_stable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data"
    manager = _manager(workspace, data_root)
    ids = [
        _persist_old_usage(manager, scope, usage_count=8)
        for scope in (MemoryScope.PROJECT, MemoryScope.USER)
    ]
    effective_at = time.time()
    manager.record_retrievals_and_injections(
        ids,
        ids,
        observation_id="prompt-receipt-byte-stable",
    )
    manager.decay_memories(
        max_age_days=30,
        decay_factor=0.5,
        operation_id="maintenance-decay-byte-stable",
        effective_at=effective_at,
    )
    paths = {
        scope: _authority_path(workspace, scope, data_root)
        for scope in (MemoryScope.PROJECT, MemoryScope.USER)
    }
    authority_before = {
        scope: path.read_bytes() for scope, path in paths.items()
    }

    replay = _manager(workspace, data_root)
    replay.record_retrievals_and_injections(
        ids,
        ids,
        observation_id="prompt-receipt-byte-stable",
    )
    replay.decay_memories(
        max_age_days=30,
        decay_factor=0.5,
        operation_id="maintenance-decay-byte-stable",
        effective_at=effective_at,
    )

    assert {
        scope: path.read_bytes() for scope, path in paths.items()
    } == authority_before
    persisted = _manager(workspace, data_root)
    for scope, entry_id in zip(
        (MemoryScope.PROJECT, MemoryScope.USER),
        ids,
        strict=True,
    ):
        entry = persisted.memories[scope]._id_index[entry_id]
        assert entry.retrieval_count == 1
        assert entry.injection_count == 1
        assert entry.usage_count == 4


def test_multiscope_accounting_without_observation_id_is_rejected(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data"
    manager = _manager(workspace, data_root)
    entries = [
        manager.add_entry(
            scope,
            "testing",
            f"No-ID accounting fixture for {scope.value}",
            source="test",
        )
        for scope in (MemoryScope.PROJECT, MemoryScope.USER)
    ]
    assert all(entry is not None for entry in entries)
    ids = [entry.id for entry in entries if entry is not None]

    with pytest.raises(MemoryStoreConflict, match="stable observation_id"):
        manager.record_retrievals_and_injections(ids, ids)

    persisted = _manager(workspace, data_root)
    for scope, entry_id in zip(
        (MemoryScope.PROJECT, MemoryScope.USER),
        ids,
        strict=True,
    ):
        entry = persisted.memories[scope]._id_index[entry_id]
        assert entry.retrieval_count == 0
        assert entry.injection_count == 0


def test_multiscope_decay_without_operation_id_is_rejected(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data"
    manager = _manager(workspace, data_root)
    ids = [
        _persist_old_usage(manager, scope, usage_count=8)
        for scope in (MemoryScope.PROJECT, MemoryScope.USER)
    ]

    with pytest.raises(MemoryStoreConflict, match="stable observation_id"):
        manager.decay_memories(max_age_days=30, decay_factor=0.5)

    persisted = _manager(workspace, data_root)
    for scope, entry_id in zip(
        (MemoryScope.PROJECT, MemoryScope.USER),
        ids,
        strict=True,
    ):
        assert (
            persisted.memories[scope]._id_index[entry_id].usage_count
            == 8
        )
