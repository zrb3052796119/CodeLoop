from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from minicode.memory_write_contract import (
    MAX_MEMORY_AUTHORITY_RECEIPT_BYTES,
    MAX_MEMORY_CLAIMS_PER_BATCH,
    MAX_MEMORY_MATERIALIZATIONS_PER_RECEIPT,
    MAX_SUPERSEDED_ENTRY_IDS,
    MemoryAuthorityCommit,
    MemoryAuthorityReceiptReference,
    MemoryMaterialization,
    MemoryWriteResult,
    ProjectFactsEffect,
    new_memory_operation_id,
)
from minicode.memory_blob_store import MemoryBlobReference

OPERATION_ID = "memoryop_" + "a" * 32
PAYLOAD_DIGEST = "b" * 64


def _entry(index: int, *, scope: str = "project") -> str:
    return f"{scope}-1785082406796413000-{index:08x}"


def _materialization(
    index: int = 1,
    *,
    disposition: str = "created",
    scope: str = "project",
    superseded_entry_ids: tuple[str, ...] = (),
) -> MemoryMaterialization:
    if disposition == "rejected_retention":
        entry_id = None
        authority_digest = None
    else:
        entry_id = _entry(index, scope=scope)
        authority_digest = f"{index:064x}"
    return MemoryMaterialization(
        claim_id=f"claim-{index:06d}",
        entry_id=entry_id,
        authority_digest=authority_digest,
        disposition=disposition,
        superseded_entry_ids=superseded_entry_ids,
    )


def _commit(
    *materializations: MemoryMaterialization,
    scope: str = "project",
    rejected_claim_ids: tuple[str, ...] = (),
) -> MemoryAuthorityCommit:
    return MemoryAuthorityCommit(
        operation_id=OPERATION_ID,
        payload_digest=PAYLOAD_DIGEST,
        scope=scope,
        materializations=materializations or (_materialization(scope=scope),),
        rejected_claim_ids=rejected_claim_ids,
    )


def test_contract_values_are_frozen_slotted_and_content_free() -> None:
    materialization = _materialization()
    commit = _commit(materialization)
    facts = ProjectFactsEffect(status="committed", added_count=1)
    result = MemoryWriteResult(
        operation_id=OPERATION_ID,
        payload_digest=PAYLOAD_DIGEST,
        authority_kind="lesson",
        authority_commit=commit,
        project_facts_effect=facts,
        reason_codes=("verified_recovery",),
    )

    for value in (materialization, commit, facts, result):
        assert not hasattr(value, "__dict__")
        with pytest.raises(FrozenInstanceError):
            setattr(value, next(iter(value.__dataclass_fields__)), None)

    receipt = commit.to_json()
    assert "statement" not in receipt
    assert "content" not in receipt
    assert "semanticKey" not in receipt
    assert "verified_recovery" not in receipt


def test_receipt_reference_binds_compact_metadata_to_exact_blob() -> None:
    commit = _commit(_materialization())
    encoded = commit.to_json().encode("utf-8")
    blob = MemoryBlobReference(
        digest=commit.receipt_digest,
        size_bytes=len(encoded),
    )
    reference = MemoryAuthorityReceiptReference.from_commit(commit, blob)

    assert set(reference.to_dict()) == {
        "referenceVersion",
        "operationId",
        "payloadDigest",
        "scope",
        "blob",
    }
    assert MemoryAuthorityReceiptReference.from_dict(reference.to_dict()) == (
        reference
    )
    reference.validate_commit(commit)

    wrong_blob = MemoryBlobReference(
        digest="f" * 64,
        size_bytes=len(encoded),
    )
    with pytest.raises(ValueError, match="does not match"):
        MemoryAuthorityReceiptReference.from_commit(commit, wrong_blob)


def test_operation_and_digest_contracts_are_exact_lowercase_shapes() -> None:
    generated = new_memory_operation_id()
    assert generated.startswith("memoryop_")
    assert len(generated) == len("memoryop_") + 32
    assert set(generated.removeprefix("memoryop_")) <= set(
        "0123456789abcdef"
    )

    for operation_id in (
        "a" * 32,
        "memoryop_" + "A" * 32,
        "memoryop_" + "a" * 31,
    ):
        with pytest.raises(ValueError, match="operation ID"):
            MemoryWriteResult(
                operation_id=operation_id,
                payload_digest=PAYLOAD_DIGEST,
            )
    for digest in ("sha256:" + "b" * 64, "B" * 64, "b" * 63):
        with pytest.raises(ValueError, match="payload digest"):
            MemoryWriteResult(
                operation_id=OPERATION_ID,
                payload_digest=digest,
            )


@pytest.mark.parametrize(
    "entry_id",
    [
        "project-1",
        "user-12345678901234567890",
        "local-0",
        "project-1-deadbeef",
    ],
)
def test_materialization_transports_bounded_legacy_and_modern_entry_ids(
    entry_id: str,
) -> None:
    value = MemoryMaterialization(
        claim_id="claim-legacy-transport",
        entry_id=entry_id,
        authority_digest="1" * 64,
        disposition="created",
    )

    assert MemoryMaterialization.from_dict(value.to_dict()) == value


@pytest.mark.parametrize(
    "entry_id",
    [
        "project-",
        "project-123456789012345678901",
        "project-1-DEADBEEF",
        "project-1-deadbee",
        "workspace-1",
    ],
)
def test_materialization_rejects_unbounded_or_malformed_legacy_entry_ids(
    entry_id: str,
) -> None:
    with pytest.raises(ValueError, match="entry ID"):
        MemoryMaterialization(
            claim_id="claim-invalid-legacy-transport",
            entry_id=entry_id,
            authority_digest="1" * 64,
            disposition="created",
        )


@pytest.mark.parametrize(
    ("disposition", "entry_required"),
    [
        ("created", True),
        ("reinforced", True),
        ("rejected_existing", True),
        ("rejected_retention", False),
    ],
)
def test_materialization_dispositions_have_closed_authority_semantics(
    disposition: str,
    entry_required: bool,
) -> None:
    materialization = _materialization(disposition=disposition)
    assert (materialization.entry_id is not None) is entry_required
    assert materialization.remains_materialized is (
        disposition in {"created", "reinforced"}
    )
    assert MemoryMaterialization.from_dict(materialization.to_dict()) == (
        materialization
    )


def test_materialization_rejects_ambiguous_or_invalid_authority() -> None:
    with pytest.raises(ValueError, match="requires an entry authority"):
        MemoryMaterialization(
            claim_id="claim-000001",
            entry_id=None,
            authority_digest=None,
            disposition="created",
        )
    with pytest.raises(ValueError, match="cannot claim"):
        MemoryMaterialization(
            claim_id="claim-000001",
            entry_id=_entry(1),
            authority_digest="1" * 64,
            disposition="rejected_retention",
        )
    with pytest.raises(ValueError, match="authority digest"):
        MemoryMaterialization(
            claim_id="claim-000001",
            entry_id=_entry(1),
            authority_digest="F" * 64,
            disposition="created",
        )
    with pytest.raises(ValueError, match="supersede itself"):
        _materialization(1, superseded_entry_ids=(_entry(1),))
    with pytest.raises(ValueError, match="only a created"):
        _materialization(
            1,
            disposition="reinforced",
            superseded_entry_ids=(_entry(2),),
        )


def test_commit_is_single_scope_and_has_one_outcome_per_claim() -> None:
    with pytest.raises(ValueError, match="outside the commit scope"):
        _commit(_materialization(scope="user"), scope="project")
    with pytest.raises(ValueError, match="repeats a claim ID"):
        _commit(_materialization(), _materialization())
    with pytest.raises(ValueError, match="multiple commit outcomes"):
        _commit(_materialization(), rejected_claim_ids=("claim-000001",))
    with pytest.raises(ValueError, match="must contain a claim outcome"):
        MemoryAuthorityCommit(
            operation_id=OPERATION_ID,
            payload_digest=PAYLOAD_DIGEST,
            scope="project",
        )


def test_commit_bounds_claims_materializations_and_superseded_entries() -> None:
    materializations = tuple(
        _materialization(index)
        for index in range(1, MAX_MEMORY_MATERIALIZATIONS_PER_RECEIPT + 2)
    )
    with pytest.raises(ValueError, match="too many materializations"):
        _commit(*materializations)

    rejected = tuple(
        f"rejected-{index:06d}"
        for index in range(MAX_MEMORY_CLAIMS_PER_BATCH + 1)
    )
    with pytest.raises(ValueError, match="too many claim outcomes"):
        MemoryAuthorityCommit(
            operation_id=OPERATION_ID,
            payload_digest=PAYLOAD_DIGEST,
            scope="project",
            rejected_claim_ids=rejected,
        )

    superseded = tuple(
        _entry(index + 100)
        for index in range(MAX_SUPERSEDED_ENTRY_IDS + 1)
    )
    with pytest.raises(ValueError, match="supersedes too many"):
        _materialization(1, superseded_entry_ids=superseded)


def test_canonical_receipt_round_trip_and_replay_are_byte_stable() -> None:
    original = _commit(
        _materialization(
            2,
            superseded_entry_ids=(_entry(1),),
        ),
        _materialization(3, disposition="rejected_existing"),
        _materialization(4, disposition="rejected_retention"),
        rejected_claim_ids=("claim-validation-rejected",),
    )

    encoded = original.to_json()
    restored = MemoryAuthorityCommit.from_json(encoded)
    replayed = restored.as_replay()

    assert restored == original
    assert replayed.replayed is True
    assert replayed.operation_id == original.operation_id
    assert replayed.payload_digest == original.payload_digest
    assert replayed.to_json() == encoded
    assert replayed.to_dict()["replayed"] is False
    assert replayed.receipt_digest == original.receipt_digest
    assert MemoryAuthorityCommit.from_json(replayed.to_json()) == original
    assert encoded == json.dumps(
        json.loads(encoded),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_receipt_reader_rejects_open_schema_duplicate_keys_and_runtime_replay() -> None:
    commit = _commit()
    document = commit.to_dict()
    document["unexpected"] = "field"
    with pytest.raises(ValueError, match="invalid schema"):
        MemoryAuthorityCommit.from_dict(document)

    replay_document = commit.to_dict()
    replay_document["replayed"] = True
    with pytest.raises(ValueError, match="cannot be replayed"):
        MemoryAuthorityCommit.from_dict(replay_document)

    duplicate = commit.to_json().replace(
        '"scope":"project"',
        '"scope":"project","scope":"user"',
    )
    with pytest.raises(ValueError, match="not valid JSON"):
        MemoryAuthorityCommit.from_json(duplicate)
    with pytest.raises(ValueError, match="byte limit"):
        MemoryAuthorityCommit.from_json(
            " " * (MAX_MEMORY_AUTHORITY_RECEIPT_BYTES + 1)
        )


@pytest.mark.parametrize(
    "effect",
    [
        ProjectFactsEffect(),
        ProjectFactsEffect(status="committed", added_count=2, updated_count=1),
        ProjectFactsEffect(status="replayed"),
        ProjectFactsEffect(status="skipped"),
        ProjectFactsEffect(status="failed", failure_code="facts_store_busy"),
    ],
)
def test_project_facts_effect_round_trips_closed_statuses(
    effect: ProjectFactsEffect,
) -> None:
    assert ProjectFactsEffect.from_dict(effect.to_dict()) == effect


def test_project_facts_effect_rejects_impossible_outcomes() -> None:
    with pytest.raises(ValueError, match="must change"):
        ProjectFactsEffect(status="committed")
    with pytest.raises(ValueError, match="only a committed"):
        ProjectFactsEffect(status="replayed", added_count=1)
    with pytest.raises(ValueError, match="needs a failure code"):
        ProjectFactsEffect(status="failed")
    with pytest.raises(ValueError, match="only a failed"):
        ProjectFactsEffect(status="skipped", failure_code="facts_skipped")
    with pytest.raises(TypeError, match="integer"):
        ProjectFactsEffect(status="committed", added_count=True)


def test_write_result_projects_only_authority_that_remains_materialized() -> None:
    old_entry = _entry(1)
    replacement = _materialization(
        2,
        superseded_entry_ids=(old_entry,),
    )
    commit = _commit(
        _materialization(1, disposition="reinforced"),
        replacement,
        _materialization(3, disposition="rejected_existing"),
        _materialization(4, disposition="rejected_retention"),
    )
    result = MemoryWriteResult(
        operation_id=OPERATION_ID,
        payload_digest=PAYLOAD_DIGEST,
        authority_kind="lesson",
        authority_commit=commit,
        project_facts_effect=ProjectFactsEffect(status="skipped"),
    )

    assert commit.authority_versions == (
        (replacement.entry_id, replacement.authority_digest),
    )
    assert result.authority_versions == commit.authority_versions
    assert result.representative_id == replacement.entry_id


def test_write_result_binds_commit_identity_and_authority_kind() -> None:
    commit = _commit()
    with pytest.raises(ValueError, match="operation ID does not match"):
        MemoryWriteResult(
            operation_id="memoryop_" + "c" * 32,
            payload_digest=PAYLOAD_DIGEST,
            authority_kind="lesson",
            authority_commit=commit,
        )
    with pytest.raises(ValueError, match="payload digest does not match"):
        MemoryWriteResult(
            operation_id=OPERATION_ID,
            payload_digest="d" * 64,
            authority_kind="lesson",
            authority_commit=commit,
        )
    with pytest.raises(ValueError, match="cannot be none"):
        MemoryWriteResult(
            operation_id=OPERATION_ID,
            payload_digest=PAYLOAD_DIGEST,
            authority_commit=commit,
        )

    user_commit = _commit(_materialization(scope="user"), scope="user")
    conversation_fact = MemoryWriteResult(
        operation_id=OPERATION_ID,
        payload_digest=PAYLOAD_DIGEST,
        authority_kind="conversation_fact",
        authority_commit=user_commit,
    )
    assert conversation_fact.representative_id == _entry(1, scope="user")
    with pytest.raises(ValueError, match="require user authority"):
        MemoryWriteResult(
            operation_id=OPERATION_ID,
            payload_digest=PAYLOAD_DIGEST,
            authority_kind="conversation_fact",
            authority_commit=commit,
        )


def test_write_result_without_authority_has_empty_compatibility_projection() -> None:
    result = MemoryWriteResult(
        operation_id=OPERATION_ID,
        payload_digest=PAYLOAD_DIGEST,
        reason_codes=("no_persistable_claims",),
        post_commit_interrupted=False,
    )

    assert result.authority_versions == ()
    assert result.representative_id is None
