from __future__ import annotations

import json
from pathlib import Path

import pytest

import minicode.memory as memory_module
from minicode.memory import (
    MemoryApprovalPolicy,
    MemoryManager,
    MemoryScope,
    MemoryTier,
    ReflectionClaimWrite,
    _approval_hash_for_entry,
    conversation_fact_payload_digest,
    reflection_batch_payload_digest,
)
from minicode.memory_store import MemoryStoreConflict, MemoryStoreUnavailable
from minicode.memory_blob_store import MemoryBlobStore
from minicode.memory_write_contract import (
    MemoryAuthorityCommit,
    MemoryAuthorityReceiptReference,
)
from minicode.turn_cancellation import TurnCancellationRequested


def _manager(tmp_path: Path) -> tuple[MemoryManager, Path, Path]:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "data"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace, data_root=data_root)
    authority = workspace / ".mini-code-memory" / "memory.json"
    return manager, workspace, authority


def _claim(
    index: int,
    *,
    semantic_key: str | None = None,
    statement: str | None = None,
    tags: list[str] | None = None,
) -> ReflectionClaimWrite:
    canonical_statement = statement or f"lesson statement {index}"
    return ReflectionClaimWrite(
        claim_id=f"claim-{index}",
        semantic_key=semantic_key or f"lesson_{index}",
        statement=canonical_statement,
        add_entry_kwargs={
            "category": "reflection",
            "content": (
                f"Verified recovery {index}: {canonical_statement}; "
                "run the focused check before retrying."
            ),
            "tags": list(tags or ["reflection", "recovery"]),
            "domains": ["testing"],
            "metadata": {"recurrence": 1},
            "tier": MemoryTier.SHORT_TERM,
            "source": "reflection",
            "provenance": {"evidence_kind": "verified_recovery"},
            "approval_policy": MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED,
        },
    )


def _operation(index: int) -> str:
    return f"memoryop_{index:032x}"


def _commit(
    manager: MemoryManager,
    operation_index: int,
    claims: list[ReflectionClaimWrite] | tuple[ReflectionClaimWrite, ...],
):
    return manager.commit_reflection_batch(
        operation_id=_operation(operation_index),
        payload_digest=reflection_batch_payload_digest(claims),
        claims=claims,
    )


def _project_entries(manager: MemoryManager):
    return manager.memories[MemoryScope.PROJECT].entries


def _resolved_receipt_dict(authority: Path, index: int = 0) -> dict[str, object]:
    envelope = json.loads(authority.read_text(encoding="utf-8"))
    reference = MemoryAuthorityReceiptReference.from_dict(
        envelope["write_receipts"][index]
    )
    raw = MemoryBlobStore(authority.parent).read_json(reference.blob)
    commit = MemoryAuthorityCommit.from_dict(raw)
    reference.validate_commit(commit)
    return commit.to_dict()


def _conversation_fact_kwargs(
    *,
    tags: list[str] | None = None,
    observation: int = 1,
) -> dict[str, object]:
    return {
        "category": "conversation_fact",
        "content": "The user prefers focused parser checks before full suites.",
        "tags": list(tags or ["conversation-fact"]),
        "domains": ["memory"],
        "metadata": {"observation": observation},
        "tier": MemoryTier.SHORT_TERM,
        "source": "conversation_fact",
        "provenance": {"intake": "deterministic_conversation_fact"},
        "approval_policy": MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    }


def _commit_conversation_fact(
    manager: MemoryManager,
    operation_index: int,
    *,
    claim_id: str,
    add_entry_kwargs: dict[str, object],
):
    return manager.commit_conversation_fact(
        operation_id=_operation(operation_index),
        payload_digest=conversation_fact_payload_digest(
            claim_id=claim_id,
            add_entry_kwargs=add_entry_kwargs,
        ),
        claim_id=claim_id,
        add_entry_kwargs=add_entry_kwargs,
    )


def test_batch_uses_one_authority_replace_and_reloads_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, workspace, authority = _manager(tmp_path)
    claims = [_claim(1), _claim(2), _claim(3)]
    original_atomic_write = manager._atomic_write
    authority_replaces: list[Path] = []

    def tracking_atomic_write(target: Path, content: str) -> None:
        if target.name == "memory.json":
            authority_replaces.append(target)
        original_atomic_write(target, content)

    monkeypatch.setattr(manager, "_atomic_write", tracking_atomic_write)

    result = _commit(manager, 1, claims)

    assert result.replayed is False
    assert [item.claim_id for item in result.materializations] == [
        "claim-1",
        "claim-2",
        "claim-3",
    ]
    assert result.rejected_claim_ids == ()
    assert authority_replaces == [authority]
    persisted = json.loads(authority.read_text(encoding="utf-8"))
    assert len(persisted["entries"]) == 3
    assert len(persisted["write_receipts"]) == 1
    assert _resolved_receipt_dict(authority) == result.to_dict()

    reloaded = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
        readonly_load=True,
    )
    replay = _commit(reloaded, 1, claims)

    assert replay == result.as_replay()
    assert len(_project_entries(reloaded)) == 3
    assert authority_replaces == [authority]


def test_aggregate_envelope_and_exact_authority_digest_commit_together(
    tmp_path: Path,
) -> None:
    manager, workspace, authority = _manager(tmp_path)
    claims = [_claim(1)]
    exact_digest = reflection_batch_payload_digest(claims)
    aggregate_digest = "a" * 64

    result = manager.commit_reflection_batch(
        operation_id=_operation(1),
        payload_digest=aggregate_digest,
        authority_payload_digest=exact_digest,
        claims=claims,
    )

    assert result.payload_digest == aggregate_digest
    persisted = json.loads(authority.read_text(encoding="utf-8"))
    assert len(persisted["write_receipts"]) == 1
    assert _resolved_receipt_dict(authority) == result.to_dict()
    assert persisted["pipeline_operation_receipts"] == [
        {
            "operationId": _operation(1),
            "aggregateDigest": aggregate_digest,
        }
    ]

    fresh = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
        readonly_load=True,
    )
    replay = fresh.commit_reflection_batch(
        operation_id=_operation(1),
        payload_digest=aggregate_digest,
        authority_payload_digest=exact_digest,
        claims=claims,
    )
    assert replay == result.as_replay()


def test_legacy_authority_without_pipeline_envelopes_still_replays(
    tmp_path: Path,
) -> None:
    manager, workspace, authority = _manager(tmp_path)
    claims = [_claim(1)]
    result = _commit(manager, 1, claims)
    legacy = json.loads(authority.read_text(encoding="utf-8"))
    legacy["write_receipts"] = [_resolved_receipt_dict(authority)]
    legacy.pop("schemaVersion")
    legacy.pop("pipeline_operation_receipts")
    authority.write_text(json.dumps(legacy), encoding="utf-8")
    before = authority.read_bytes()

    fresh = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
        readonly_load=True,
    )
    replay = _commit(fresh, 1, claims)

    assert replay == result.as_replay()
    assert authority.read_bytes() == before


def test_legacy_reflection_receipt_upgrades_to_aggregate_envelope_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, workspace, authority = _manager(tmp_path)
    claims = [_claim(1)]
    exact_digest = reflection_batch_payload_digest(claims)
    legacy_result = _commit(manager, 1, claims)
    legacy = json.loads(authority.read_text(encoding="utf-8"))
    legacy["pipeline_operation_receipts"] = []
    authority.write_text(json.dumps(legacy), encoding="utf-8")

    fresh = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
        readonly_load=True,
    )
    recurrence_before = (
        fresh.memories[MemoryScope.PROJECT].entries[0].metadata["recurrence"]
    )
    aggregate_digest = "a" * 64
    original_atomic_write = fresh._atomic_write
    authority_replaces: list[Path] = []

    def track_authority_replace(target: Path, content: str) -> None:
        if target == authority:
            authority_replaces.append(target)
        original_atomic_write(target, content)

    monkeypatch.setattr(fresh, "_atomic_write", track_authority_replace)
    replay = fresh.commit_reflection_batch(
        operation_id=_operation(1),
        payload_digest=aggregate_digest,
        authority_payload_digest=exact_digest,
        claims=claims,
    )

    assert (
        replay
        == MemoryAuthorityCommit(
            operation_id=legacy_result.operation_id,
            payload_digest=aggregate_digest,
            scope=legacy_result.scope,
            materializations=legacy_result.materializations,
            rejected_claim_ids=legacy_result.rejected_claim_ids,
        ).as_replay()
    )
    assert authority_replaces == [authority]
    assert (
        fresh.memories[MemoryScope.PROJECT].entries[0].metadata["recurrence"]
        == recurrence_before
    )
    persisted = json.loads(authority.read_text(encoding="utf-8"))
    assert persisted["write_receipts"][0]["payloadDigest"] == aggregate_digest
    assert persisted["pipeline_operation_receipts"] == [
        {
            "operationId": _operation(1),
            "aggregateDigest": aggregate_digest,
        }
    ]


def test_missing_new_reflection_envelope_is_repaired_without_reinforcement(
    tmp_path: Path,
) -> None:
    manager, workspace, authority = _manager(tmp_path)
    claims = [_claim(1)]
    exact_digest = reflection_batch_payload_digest(claims)
    aggregate_digest = "b" * 64
    committed = manager.commit_reflection_batch(
        operation_id=_operation(1),
        payload_digest=aggregate_digest,
        authority_payload_digest=exact_digest,
        claims=claims,
    )
    damaged = json.loads(authority.read_text(encoding="utf-8"))
    damaged["pipeline_operation_receipts"] = []
    authority.write_text(json.dumps(damaged), encoding="utf-8")

    fresh = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
        readonly_load=True,
    )
    recurrence_before = (
        fresh.memories[MemoryScope.PROJECT].entries[0].metadata["recurrence"]
    )
    replay = fresh.commit_reflection_batch(
        operation_id=_operation(1),
        payload_digest=aggregate_digest,
        authority_payload_digest=exact_digest,
        claims=claims,
    )

    assert replay == committed.as_replay()
    assert (
        fresh.memories[MemoryScope.PROJECT].entries[0].metadata["recurrence"]
        == recurrence_before
    )
    persisted = json.loads(authority.read_text(encoding="utf-8"))
    assert (
        persisted["pipeline_operation_receipts"][0]["aggregateDigest"]
        == aggregate_digest
    )


def test_legacy_receipt_upgrade_replace_failure_rolls_back_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, workspace, authority = _manager(tmp_path)
    claims = [_claim(1)]
    exact_digest = reflection_batch_payload_digest(claims)
    _commit(manager, 1, claims)
    legacy = json.loads(authority.read_text(encoding="utf-8"))
    legacy["pipeline_operation_receipts"] = []
    authority.write_text(json.dumps(legacy), encoding="utf-8")
    before = authority.read_bytes()
    fresh = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
        readonly_load=True,
    )
    original_atomic_write = fresh._atomic_write

    def fail_authority_replace(target: Path, content: str) -> None:
        if target == authority:
            raise OSError("controlled receipt migration replace failure")
        original_atomic_write(target, content)

    with monkeypatch.context() as patch:
        patch.setattr(fresh, "_atomic_write", fail_authority_replace)
        with pytest.raises(OSError, match="migration replace failure"):
            fresh.commit_reflection_batch(
                operation_id=_operation(1),
                payload_digest="c" * 64,
                authority_payload_digest=exact_digest,
                claims=claims,
            )

    assert authority.read_bytes() == before
    assert (
        fresh._memory_write_receipts[MemoryScope.PROJECT][_operation(1)].payload_digest
        == exact_digest
    )
    assert fresh._pipeline_operation_receipts[MemoryScope.PROJECT] == {}

    replay = fresh.commit_reflection_batch(
        operation_id=_operation(1),
        payload_digest="c" * 64,
        authority_payload_digest=exact_digest,
        claims=claims,
    )
    assert replay.replayed is True
    assert replay.payload_digest == "c" * 64


def test_replay_is_byte_stable_and_payload_conflicts_fail_closed(
    tmp_path: Path,
) -> None:
    manager, _workspace, authority = _manager(tmp_path)
    claims = [_claim(1)]
    original = _commit(manager, 1, claims)
    before = authority.read_bytes()
    entry = _project_entries(manager)[0]
    recurrence_before = entry.metadata["recurrence"]

    replay = _commit(manager, 1, claims)

    assert replay == original.as_replay()
    assert authority.read_bytes() == before
    assert entry.metadata["recurrence"] == recurrence_before

    changed = [_claim(2)]
    with pytest.raises(MemoryStoreConflict, match="payload changed"):
        manager.commit_reflection_batch(
            operation_id=_operation(1),
            payload_digest=reflection_batch_payload_digest(changed),
            claims=changed,
        )
    with pytest.raises(MemoryStoreConflict, match="digest does not match"):
        manager.commit_reflection_batch(
            operation_id=_operation(1),
            payload_digest=reflection_batch_payload_digest(claims),
            claims=changed,
        )

    assert authority.read_bytes() == before
    assert len(_project_entries(manager)) == 1


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: RuntimeError("staged mutation failed"),
        lambda: TurnCancellationRequested("turn-atomic-batch"),
    ],
    ids=["error", "cancellation"],
)
def test_failure_during_staging_rolls_back_and_exact_retry_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_factory,
) -> None:
    manager, _workspace, authority = _manager(tmp_path)
    claims = [_claim(1), _claim(2)]
    original_add_entry = manager.add_entry
    calls = 0

    def fail_second_add(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise error_factory()
        return original_add_entry(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(manager, "add_entry", fail_second_add)
        with pytest.raises((RuntimeError, TurnCancellationRequested)):
            _commit(manager, 1, claims)

    assert not authority.exists()
    assert _project_entries(manager) == []
    assert manager._memory_write_receipts[MemoryScope.PROJECT] == {}
    assert manager._pipeline_operation_receipts[MemoryScope.PROJECT] == {}

    result = _commit(manager, 1, claims)

    assert result.replayed is False
    assert len(result.materializations) == 2
    assert len(_project_entries(manager)) == 2


def test_authority_replace_failure_leaves_no_receipt_and_retry_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _workspace, authority = _manager(tmp_path)
    claims = [_claim(1)]
    original_atomic_write = manager._atomic_write

    def fail_authority_replace(target: Path, content: str) -> None:
        if target.name == "memory.json":
            raise OSError("simulated authority replace failure")
        original_atomic_write(target, content)

    with monkeypatch.context() as patch:
        patch.setattr(manager, "_atomic_write", fail_authority_replace)
        with pytest.raises(OSError, match="replace failure"):
            _commit(manager, 1, claims)

    assert not authority.exists()
    assert _project_entries(manager) == []
    assert manager._memory_write_receipts[MemoryScope.PROJECT] == {}
    assert manager._pipeline_operation_receipts[MemoryScope.PROJECT] == {}

    result = _commit(manager, 1, claims)

    assert result.replayed is False
    assert authority.exists()


def test_post_replace_revision_read_cannot_mask_committed_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, workspace, authority = _manager(tmp_path)
    claims = [_claim(1)]
    original_revision = manager._scope_disk_revision
    post_replace_reads = 0

    def fail_if_read_after_replace(scope: MemoryScope) -> str:
        nonlocal post_replace_reads
        if authority.exists():
            post_replace_reads += 1
            raise MemoryStoreUnavailable(
                "controlled post-replace revision read failure"
            )
        return original_revision(scope)

    with monkeypatch.context() as patch:
        patch.setattr(manager, "_scope_disk_revision", fail_if_read_after_replace)
        result = _commit(manager, 1, claims)

    assert result.replayed is False
    assert len(result.materializations) == 1
    assert post_replace_reads == 0
    assert authority.exists()
    persisted = json.loads(authority.read_text(encoding="utf-8"))
    assert len(persisted["write_receipts"]) == 1
    assert _resolved_receipt_dict(authority) == result.to_dict()

    fresh = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
        readonly_load=True,
    )
    assert (
        manager._disk_revisions[MemoryScope.PROJECT]
        == fresh._disk_revisions[MemoryScope.PROJECT]
    )
    assert _commit(fresh, 1, claims) == result.as_replay()


def test_stale_manager_resolves_recurrence_from_fresh_locked_authority(
    tmp_path: Path,
) -> None:
    manager, workspace, _authority = _manager(tmp_path)
    stale_manager = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
    )
    first_claim = _claim(
        1,
        semantic_key="parser_recovery",
        statement="run the parser smoke test before retry",
    )
    initial = _commit(manager, 1, [first_claim])
    original_entry_id = initial.materializations[0].entry_id
    recurrence_claim = ReflectionClaimWrite(
        claim_id="claim-recurrence",
        semantic_key=first_claim.semantic_key,
        statement=first_claim.statement,
        add_entry_kwargs=first_claim.add_entry_kwargs,
    )

    reinforced = _commit(stale_manager, 2, [recurrence_claim])

    assert reinforced.materializations[0].disposition == "reinforced"
    assert reinforced.materializations[0].entry_id == original_entry_id
    assert len(_project_entries(stale_manager)) == 1
    assert _project_entries(stale_manager)[0].metadata["recurrence"] == 2


def test_batch_resolves_recurrence_replacement_and_creation_before_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _workspace, authority = _manager(tmp_path)
    same = _claim(
        1,
        semantic_key="same_lesson",
        statement="keep the focused parser check",
    )
    changed = _claim(
        2,
        semantic_key="changed_lesson",
        statement="use the old validation command",
    )
    seeded = _commit(manager, 1, [same, changed])
    old_changed_id = seeded.materializations[1].entry_id
    recurrence = ReflectionClaimWrite(
        claim_id="claim-recurrence",
        semantic_key=same.semantic_key,
        statement=same.statement,
        add_entry_kwargs=same.add_entry_kwargs,
    )
    replacement = _claim(
        4,
        semantic_key="changed_lesson",
        statement="use the corrected validation command",
    )
    created = _claim(5, semantic_key="new_lesson")
    original_atomic_write = manager._atomic_write
    authority_replaces = 0

    def tracking_atomic_write(target: Path, content: str) -> None:
        nonlocal authority_replaces
        if target == authority:
            authority_replaces += 1
        original_atomic_write(target, content)

    monkeypatch.setattr(manager, "_atomic_write", tracking_atomic_write)

    result = _commit(manager, 2, [recurrence, replacement, created])

    outcomes = {item.claim_id: item for item in result.materializations}
    assert outcomes["claim-recurrence"].disposition == "reinforced"
    assert outcomes["claim-4"].disposition == "created"
    assert outcomes["claim-4"].superseded_entry_ids == (old_changed_id,)
    assert outcomes["claim-5"].disposition == "created"
    assert authority_replaces == 1
    old_changed = next(
        entry for entry in _project_entries(manager) if entry.id == old_changed_id
    )
    assert old_changed.lifecycle_status == "superseded"
    assert old_changed.approval_status == "rejected"
    for entry_id, digest in result.authority_versions:
        entry = next(
            entry for entry in _project_entries(manager) if entry.id == entry_id
        )
        assert digest == _approval_hash_for_entry(entry)


def test_retention_displacement_is_rejected_not_materialized(
    tmp_path: Path,
) -> None:
    manager, _workspace, _authority = _manager(tmp_path)
    manager.memories[MemoryScope.PROJECT].max_entries = 1

    result = _commit(manager, 1, [_claim(1), _claim(2)])

    assert [item.claim_id for item in result.materializations] == ["claim-2"]
    assert result.rejected_claim_ids == ("claim-1",)
    assert len(result.authority_versions) == 1
    assert len(_project_entries(manager)) == 1
    assert _project_entries(manager)[0].metadata["claim_identity"]["claim_id"] == (
        "claim-2"
    )


def test_receipt_digest_binds_post_sanitization_authority_version(
    tmp_path: Path,
) -> None:
    manager, _workspace, authority = _manager(tmp_path)
    claim = _claim(
        1,
        tags=["reflection", "\x1b[31mparser\x00tag"],
    )

    result = _commit(manager, 1, [claim])
    materialization = result.materializations[0]
    entry = _project_entries(manager)[0]

    assert entry.tags == ["reflection", "parser\\x00tag"]
    assert materialization.authority_digest == _approval_hash_for_entry(entry)
    persisted = json.loads(authority.read_text(encoding="utf-8"))
    resolved = _resolved_receipt_dict(authority)
    assert resolved == result.to_dict()
    assert resolved["materializations"][0][
        "authorityDigest"
    ] == _approval_hash_for_entry(entry)


def test_malformed_or_oversized_receipt_authority_fails_closed(
    tmp_path: Path,
) -> None:
    manager, workspace, authority = _manager(tmp_path)
    _commit(manager, 1, [_claim(1)])
    valid = json.loads(authority.read_text(encoding="utf-8"))

    malformed = json.loads(json.dumps(valid))
    malformed["write_receipts"][0]["unexpected"] = True
    authority.write_text(json.dumps(malformed), encoding="utf-8")
    malformed_before = authority.read_bytes()
    with pytest.raises(MemoryStoreConflict, match="write receipts are invalid"):
        MemoryManager(project_root=workspace, data_root=manager._data_root)
    assert authority.read_bytes() == malformed_before

    oversized_receipts = []
    for index in range(memory_module._MAX_MEMORY_WRITE_RECEIPTS + 1):
        oversized_receipts.append(
            MemoryAuthorityCommit(
                operation_id=_operation(index + 100),
                payload_digest=f"{index:064x}",
                scope="project",
                rejected_claim_ids=(f"rejected-{index}",),
            ).to_dict()
        )
    valid["write_receipts"] = oversized_receipts
    authority.write_text(json.dumps(valid), encoding="utf-8")
    oversized_before = authority.read_bytes()
    with pytest.raises(MemoryStoreConflict, match="write receipts are invalid"):
        MemoryManager(project_root=workspace, data_root=manager._data_root)
    assert authority.read_bytes() == oversized_before


def test_malformed_or_inconsistent_pipeline_envelope_fails_closed(
    tmp_path: Path,
) -> None:
    manager, workspace, authority = _manager(tmp_path)
    claims = [_claim(1)]
    result = _commit(manager, 1, claims)
    valid = json.loads(authority.read_text(encoding="utf-8"))

    malformed = json.loads(json.dumps(valid))
    malformed["pipeline_operation_receipts"][0]["unexpected"] = True
    authority.write_text(json.dumps(malformed), encoding="utf-8")
    malformed_before = authority.read_bytes()
    with pytest.raises(
        MemoryStoreConflict,
        match="Pipeline operation receipt.*invalid",
    ):
        MemoryManager(project_root=workspace, data_root=manager._data_root)
    assert authority.read_bytes() == malformed_before

    inconsistent = json.loads(json.dumps(valid))
    inconsistent["pipeline_operation_receipts"][0]["aggregateDigest"] = "f" * 64
    assert result.payload_digest != "f" * 64
    authority.write_text(json.dumps(inconsistent), encoding="utf-8")
    inconsistent_before = authority.read_bytes()
    with pytest.raises(MemoryStoreConflict, match="receipt digests disagree"):
        MemoryManager(project_root=workspace, data_root=manager._data_root)
    assert authority.read_bytes() == inconsistent_before


def test_pipeline_operation_envelopes_keep_only_bounded_recent_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, workspace, authority = _manager(tmp_path)
    monkeypatch.setattr(memory_module, "_MAX_PIPELINE_OPERATION_RECEIPTS", 2)

    for index in range(1, 4):
        assert (
            manager.bind_memory_write_operation(
                operation_id=_operation(index),
                payload_digest=f"{index:064x}",
            )
            is True
        )

    persisted = json.loads(authority.read_text(encoding="utf-8"))
    assert [
        receipt["operationId"] for receipt in persisted["pipeline_operation_receipts"]
    ] == [_operation(2), _operation(3)]
    reloaded = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
        readonly_load=True,
    )
    assert list(reloaded._pipeline_operation_receipts[MemoryScope.PROJECT]) == [
        _operation(2),
        _operation(3),
    ]


def test_successful_commits_keep_only_the_bounded_receipt_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, workspace, authority = _manager(tmp_path)
    monkeypatch.setattr(memory_module, "_MAX_MEMORY_WRITE_RECEIPTS", 2)

    _commit(manager, 1, [_claim(1)])
    _commit(manager, 2, [_claim(2)])
    _commit(manager, 3, [_claim(3)])

    persisted = json.loads(authority.read_text(encoding="utf-8"))
    assert [receipt["operationId"] for receipt in persisted["write_receipts"]] == [
        _operation(2),
        _operation(3),
    ]
    assert list(manager._memory_write_receipts[MemoryScope.PROJECT]) == [
        _operation(2),
        _operation(3),
    ]
    reloaded = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
        readonly_load=True,
    )
    assert list(reloaded._memory_write_receipts[MemoryScope.PROJECT]) == [
        _operation(2),
        _operation(3),
    ]


def test_batch_contract_rejects_ambiguous_or_cross_scope_requests(
    tmp_path: Path,
) -> None:
    manager, _workspace, _authority = _manager(tmp_path)
    duplicate_key = [
        _claim(1, semantic_key="same_key"),
        _claim(2, semantic_key="same_key"),
    ]
    with pytest.raises(ValueError, match="duplicate semantic keys"):
        _commit(manager, 1, duplicate_key)

    with pytest.raises(ValueError, match="unsupported fields"):
        ReflectionClaimWrite(
            claim_id="claim-cross-scope",
            semantic_key="cross_scope",
            statement="do not escape project authority",
            add_entry_kwargs={
                "content": "This must not be written outside Project scope.",
                "scope": MemoryScope.USER,
            },
        )

    assert _project_entries(manager) == []


def test_facts_only_envelope_blocks_later_lesson_kind_change(
    tmp_path: Path,
) -> None:
    manager, _workspace, authority = _manager(tmp_path)
    operation_id = _operation(9)
    facts_digest = "a" * 64
    lesson_digest = "b" * 64
    claims = [_claim(1)]

    assert (
        manager.bind_memory_write_operation(
            operation_id=operation_id,
            payload_digest=facts_digest,
        )
        is True
    )
    before = authority.read_bytes()

    with pytest.raises(MemoryStoreConflict, match="payload changed"):
        manager.commit_reflection_batch(
            operation_id=operation_id,
            payload_digest=lesson_digest,
            authority_payload_digest=reflection_batch_payload_digest(claims),
            claims=claims,
        )

    assert authority.read_bytes() == before
    assert _project_entries(manager) == []


def test_lesson_envelope_blocks_later_facts_only_kind_change(
    tmp_path: Path,
) -> None:
    manager, _workspace, authority = _manager(tmp_path)
    operation_id = _operation(9)
    lesson_digest = "b" * 64
    claims = [_claim(1)]
    result = manager.commit_reflection_batch(
        operation_id=operation_id,
        payload_digest=lesson_digest,
        authority_payload_digest=reflection_batch_payload_digest(claims),
        claims=claims,
    )
    before = authority.read_bytes()

    with pytest.raises(MemoryStoreConflict, match="payload changed"):
        manager.bind_memory_write_operation(
            operation_id=operation_id,
            payload_digest="a" * 64,
        )

    assert result.replayed is False
    assert authority.read_bytes() == before
    assert len(_project_entries(manager)) == 1


def test_facts_only_envelope_replay_is_byte_stable_and_conflict_safe(
    tmp_path: Path,
) -> None:
    manager, workspace, authority = _manager(tmp_path)
    operation_id = _operation(12)
    payload_digest = "c" * 64

    assert (
        manager.bind_memory_write_operation(
            operation_id=operation_id,
            payload_digest=payload_digest,
        )
        is True
    )
    before = authority.read_bytes()
    fresh = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
        readonly_load=True,
    )

    assert (
        fresh.bind_memory_write_operation(
            operation_id=operation_id,
            payload_digest=payload_digest,
        )
        is False
    )
    assert authority.read_bytes() == before
    with pytest.raises(MemoryStoreConflict, match="payload changed"):
        fresh.bind_memory_write_operation(
            operation_id=operation_id,
            payload_digest="d" * 64,
        )
    assert authority.read_bytes() == before


def test_project_operation_cannot_reappear_as_user_conversation_fact(
    tmp_path: Path,
) -> None:
    manager, workspace, project_authority = _manager(tmp_path)
    stale_manager = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
    )
    operation_index = 10
    project_commit = _commit(manager, operation_index, [_claim(1)])
    project_before = project_authority.read_bytes()
    user_authority = manager.paths.user_memory / "memory.json"

    with pytest.raises(
        MemoryStoreConflict,
        match="already committed in another authority scope",
    ):
        _commit_conversation_fact(
            stale_manager,
            operation_index,
            claim_id="conversation-fact-cross-scope",
            add_entry_kwargs=_conversation_fact_kwargs(),
        )

    assert project_authority.read_bytes() == project_before
    assert user_authority.exists() is False
    assert (
        stale_manager._memory_write_receipts[MemoryScope.PROJECT][
            _operation(operation_index)
        ]
        == project_commit
    )
    assert stale_manager._memory_write_receipts[MemoryScope.USER] == {}


def test_user_operation_cannot_reappear_as_project_reflection_batch(
    tmp_path: Path,
) -> None:
    manager, workspace, project_authority = _manager(tmp_path)
    stale_manager = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
    )
    operation_index = 11
    user_commit = _commit_conversation_fact(
        manager,
        operation_index,
        claim_id="conversation-fact-cross-scope",
        add_entry_kwargs=_conversation_fact_kwargs(),
    )
    user_authority = manager.paths.user_memory / "memory.json"
    user_before = user_authority.read_bytes()

    with pytest.raises(
        MemoryStoreConflict,
        match="already committed in another authority scope",
    ):
        _commit(stale_manager, operation_index, [_claim(1)])

    assert user_authority.read_bytes() == user_before
    assert project_authority.exists() is False
    assert (
        stale_manager._memory_write_receipts[MemoryScope.USER][
            _operation(operation_index)
        ]
        == user_commit
    )
    assert stale_manager._memory_write_receipts[MemoryScope.PROJECT] == {}


def test_conversation_fact_commit_is_atomic_replay_safe_and_user_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, workspace, project_authority = _manager(tmp_path)
    user_authority = manager.paths.user_memory / "memory.json"
    kwargs = _conversation_fact_kwargs()
    original_atomic_write = manager._atomic_write
    authority_replaces: list[Path] = []

    def tracking_atomic_write(target: Path, content: str) -> None:
        if target.name == "memory.json":
            authority_replaces.append(target)
        original_atomic_write(target, content)

    monkeypatch.setattr(manager, "_atomic_write", tracking_atomic_write)

    result = _commit_conversation_fact(
        manager,
        20,
        claim_id="conversation-fact-1",
        add_entry_kwargs=kwargs,
    )

    assert result.scope == "user"
    assert result.materializations[0].disposition == "created"
    assert result.materializations[0].entry_id.startswith("user-")
    assert authority_replaces == [user_authority]
    assert not project_authority.exists()
    assert manager.memories[MemoryScope.PROJECT].entries == []
    assert len(manager.memories[MemoryScope.USER].entries) == 1
    before = user_authority.read_bytes()
    reloaded = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
        readonly_load=True,
    )
    entry = reloaded.memories[MemoryScope.USER].entries[0]
    updated_at = entry.updated_at
    authority_digest = _approval_hash_for_entry(entry)

    replay = _commit_conversation_fact(
        reloaded,
        20,
        claim_id="conversation-fact-1",
        add_entry_kwargs=kwargs,
    )

    assert replay == result.as_replay()
    assert user_authority.read_bytes() == before
    assert entry.updated_at == updated_at
    assert _approval_hash_for_entry(entry) == authority_digest
    assert authority_replaces == [user_authority]


def test_conversation_fact_binds_aggregate_and_exact_digests_separately(
    tmp_path: Path,
) -> None:
    manager, workspace, _project_authority = _manager(tmp_path)
    kwargs = _conversation_fact_kwargs()
    claim_id = "conversation-fact-aggregate"
    exact_digest = conversation_fact_payload_digest(
        claim_id=claim_id,
        add_entry_kwargs=kwargs,
    )
    aggregate_digest = "e" * 64

    result = manager.commit_conversation_fact(
        operation_id=_operation(22),
        payload_digest=aggregate_digest,
        authority_payload_digest=exact_digest,
        claim_id=claim_id,
        add_entry_kwargs=kwargs,
    )

    assert result.payload_digest == aggregate_digest
    user_authority = manager.paths.user_memory / "memory.json"
    persisted = json.loads(user_authority.read_text(encoding="utf-8"))
    assert persisted["pipeline_operation_receipts"] == [
        {
            "operationId": _operation(22),
            "aggregateDigest": aggregate_digest,
        }
    ]
    fresh = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
        readonly_load=True,
    )
    replay = fresh.commit_conversation_fact(
        operation_id=_operation(22),
        payload_digest=aggregate_digest,
        authority_payload_digest=exact_digest,
        claim_id=claim_id,
        add_entry_kwargs=kwargs,
    )
    assert replay == result.as_replay()


def test_legacy_conversation_receipt_upgrades_to_aggregate_envelope(
    tmp_path: Path,
) -> None:
    manager, workspace, _project_authority = _manager(tmp_path)
    kwargs = _conversation_fact_kwargs()
    claim_id = "conversation-fact-legacy-upgrade"
    exact_digest = conversation_fact_payload_digest(
        claim_id=claim_id,
        add_entry_kwargs=kwargs,
    )
    legacy_result = manager.commit_conversation_fact(
        operation_id=_operation(22),
        payload_digest=exact_digest,
        claim_id=claim_id,
        add_entry_kwargs=kwargs,
    )
    user_authority = manager.paths.user_memory / "memory.json"
    legacy = json.loads(user_authority.read_text(encoding="utf-8"))
    legacy["pipeline_operation_receipts"] = []
    user_authority.write_text(json.dumps(legacy), encoding="utf-8")

    fresh = MemoryManager(
        project_root=workspace,
        data_root=manager._data_root,
        readonly_load=True,
    )
    entry_before = fresh.memories[MemoryScope.USER].entries[0]
    updated_at_before = entry_before.updated_at
    aggregate_digest = "d" * 64
    replay = fresh.commit_conversation_fact(
        operation_id=_operation(22),
        payload_digest=aggregate_digest,
        authority_payload_digest=exact_digest,
        claim_id=claim_id,
        add_entry_kwargs=kwargs,
    )

    assert replay.replayed is True
    assert replay.payload_digest == aggregate_digest
    assert replay.materializations == legacy_result.materializations
    assert fresh.memories[MemoryScope.USER].entries[0].updated_at == updated_at_before
    persisted = json.loads(user_authority.read_text(encoding="utf-8"))
    assert persisted["write_receipts"][0]["payloadDigest"] == aggregate_digest
    assert persisted["pipeline_operation_receipts"] == [
        {
            "operationId": _operation(22),
            "aggregateDigest": aggregate_digest,
        }
    ]


def test_legacy_user_entry_id_recurrence_returns_structured_authority(
    tmp_path: Path,
) -> None:
    manager, workspace, _project_authority = _manager(tmp_path)
    kwargs = _conversation_fact_kwargs()
    _commit_conversation_fact(
        manager,
        23,
        claim_id="conversation-fact-legacy-seed",
        add_entry_kwargs=kwargs,
    )
    user_authority = manager.paths.user_memory / "memory.json"
    legacy = json.loads(user_authority.read_text(encoding="utf-8"))
    legacy.pop("schemaVersion")
    legacy["entries"][0]["id"] = "user-1"
    legacy["entries"][0].pop("authority_state_digest", None)
    legacy["write_receipts"] = []
    legacy["pipeline_operation_receipts"] = []
    user_authority.write_text(json.dumps(legacy), encoding="utf-8")

    fresh = MemoryManager(project_root=workspace, data_root=manager._data_root)
    result = _commit_conversation_fact(
        fresh,
        24,
        claim_id="conversation-fact-legacy-recurrence",
        add_entry_kwargs=kwargs,
    )

    assert result.materializations[0].disposition == "reinforced"
    assert result.materializations[0].entry_id == "user-1"
    assert result.authority_versions[0][0] == "user-1"


def test_legacy_project_entry_id_recurrence_returns_structured_authority(
    tmp_path: Path,
) -> None:
    manager, workspace, project_authority = _manager(tmp_path)
    claims = [_claim(1)]
    _commit(manager, 25, claims)
    legacy = json.loads(project_authority.read_text(encoding="utf-8"))
    legacy.pop("schemaVersion")
    legacy["entries"][0]["id"] = "project-1"
    legacy["entries"][0].pop("authority_state_digest", None)
    legacy["write_receipts"] = []
    legacy["pipeline_operation_receipts"] = []
    project_authority.write_text(json.dumps(legacy), encoding="utf-8")

    fresh = MemoryManager(project_root=workspace, data_root=manager._data_root)
    result = _commit(fresh, 26, claims)

    assert result.materializations[0].disposition == "reinforced"
    assert result.materializations[0].entry_id == "project-1"
    assert result.authority_versions[0][0] == "project-1"


def test_conversation_fact_new_operation_merges_once_then_replays(
    tmp_path: Path,
) -> None:
    manager, _workspace, _project_authority = _manager(tmp_path)
    initial_kwargs = _conversation_fact_kwargs()
    initial = _commit_conversation_fact(
        manager,
        20,
        claim_id="conversation-fact-1",
        add_entry_kwargs=initial_kwargs,
    )
    entry_id = initial.materializations[0].entry_id
    merged_kwargs = _conversation_fact_kwargs(
        tags=["conversation-fact", "preference"],
        observation=2,
    )

    merged = _commit_conversation_fact(
        manager,
        21,
        claim_id="conversation-fact-2",
        add_entry_kwargs=merged_kwargs,
    )

    assert merged.materializations[0].disposition == "reinforced"
    assert merged.materializations[0].entry_id == entry_id
    assert len(manager.memories[MemoryScope.USER].entries) == 1
    entry = manager.memories[MemoryScope.USER].entries[0]
    assert entry.tags == ["conversation-fact", "preference"]
    assert entry.metadata["observation"] == 2
    merged_updated_at = entry.updated_at
    merged_digest = _approval_hash_for_entry(entry)

    replay = _commit_conversation_fact(
        manager,
        21,
        claim_id="conversation-fact-2",
        add_entry_kwargs=merged_kwargs,
    )

    assert replay == merged.as_replay()
    assert entry.updated_at == merged_updated_at
    assert _approval_hash_for_entry(entry) == merged_digest
    assert entry.metadata["observation"] == 2


def test_conversation_fact_conflict_or_replace_failure_preserves_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _workspace, _project_authority = _manager(tmp_path)
    user_authority = manager.paths.user_memory / "memory.json"
    kwargs = _conversation_fact_kwargs()
    _commit_conversation_fact(
        manager,
        20,
        claim_id="conversation-fact-1",
        add_entry_kwargs=kwargs,
    )
    before = user_authority.read_bytes()
    changed_kwargs = _conversation_fact_kwargs(observation=2)

    with pytest.raises(MemoryStoreConflict, match="payload changed"):
        _commit_conversation_fact(
            manager,
            20,
            claim_id="conversation-fact-2",
            add_entry_kwargs=changed_kwargs,
        )

    original_atomic_write = manager._atomic_write

    def fail_user_authority(target: Path, content: str) -> None:
        if target == user_authority:
            raise OSError("simulated User authority replace failure")
        original_atomic_write(target, content)

    with monkeypatch.context() as patch:
        patch.setattr(manager, "_atomic_write", fail_user_authority)
        with pytest.raises(OSError, match="replace failure"):
            _commit_conversation_fact(
                manager,
                21,
                claim_id="conversation-fact-2",
                add_entry_kwargs=changed_kwargs,
            )

    assert user_authority.read_bytes() == before
    assert len(manager.memories[MemoryScope.USER].entries) == 1
    assert manager.memories[MemoryScope.USER].entries[0].metadata["observation"] == 1
    assert _operation(21) not in manager._memory_write_receipts[MemoryScope.USER]
    assert _operation(21) not in manager._pipeline_operation_receipts[MemoryScope.USER]

    retry = _commit_conversation_fact(
        manager,
        21,
        claim_id="conversation-fact-2",
        add_entry_kwargs=changed_kwargs,
    )
    assert retry.replayed is False
    assert retry.materializations[0].disposition == "reinforced"
