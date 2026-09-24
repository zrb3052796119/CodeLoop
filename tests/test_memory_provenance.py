from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minicode.memory_provenance import (
    MAX_MEMORY_PROVENANCE_BATCHES,
    MAX_MEMORY_PROVENANCE_VERSIONS,
    MEMORY_PROVENANCE_SCHEMA_VERSION,
    MemoryProvenanceBatch,
    normalize_memory_provenance_batches,
)

ENTRY_A = "project-1785082406796413000-b6ecf281"
ENTRY_B = "user-1785082406796413001-a1b2c3d4"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
RECEIPT_ID = "c" * 32
CONTEXT_OPERATION_ID = "ctxop_" + "d" * 32


def _batch_dict(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": MEMORY_PROVENANCE_SCHEMA_VERSION,
        "effect": "rendered",
        "origin": "context_compaction",
        "authorityVersions": [[ENTRY_A, DIGEST_A]],
        "receiptId": RECEIPT_ID,
        "contextOperationId": CONTEXT_OPERATION_ID,
        "coverage": "complete",
    }
    payload.update(updates)
    return payload


def test_batch_round_trips_canonical_content_free_payload() -> None:
    batch = MemoryProvenanceBatch(
        effect="rendered",
        origin="context_compaction",
        authority_versions=((ENTRY_A, DIGEST_A), (ENTRY_B, DIGEST_B)),
        receipt_id=RECEIPT_ID,
        context_operation_id=CONTEXT_OPERATION_ID,
        coverage="complete",
    )

    payload = batch.to_dict()

    assert MemoryProvenanceBatch.from_dict(payload) == batch
    assert set(payload) == {
        "schemaVersion",
        "effect",
        "origin",
        "authorityVersions",
        "receiptId",
        "contextOperationId",
        "coverage",
    }
    serialized = repr(payload).lower()
    for forbidden in ("content", "query", "path", "subagentid"):
        assert forbidden not in serialized
    with pytest.raises(FrozenInstanceError):
        batch.effect = "written"  # type: ignore[misc]


@pytest.mark.parametrize(
    "entry_id",
    ["project-1", "user-12345678901234567890", "local-0"],
)
def test_provenance_round_trips_bounded_legacy_entry_ids(entry_id: str) -> None:
    batch = MemoryProvenanceBatch(
        effect="written",
        origin="reflection",
        authority_versions=((entry_id, DIGEST_A),),
        coverage="complete",
    )

    assert MemoryProvenanceBatch.from_dict(batch.to_dict()) == batch


def test_optional_identifiers_are_omitted_and_unknown_coverage_is_empty() -> None:
    batch = MemoryProvenanceBatch(
        effect="written",
        origin="reflection",
        authority_versions=(),
        coverage="unknown",
    )

    payload = batch.to_dict()

    assert "receiptId" not in payload
    assert "contextOperationId" not in payload
    assert payload["authorityVersions"] == []
    assert MemoryProvenanceBatch.from_dict(payload) == batch


@pytest.mark.parametrize("coverage", ["complete", "partial"])
def test_known_coverage_requires_at_least_one_version(coverage: str) -> None:
    with pytest.raises(ValueError, match="requires an authority version"):
        MemoryProvenanceBatch(
            effect="rendered",
            origin="task_start",
            authority_versions=(),
            coverage=coverage,
        )


def test_unknown_coverage_rejects_versions() -> None:
    with pytest.raises(ValueError, match="cannot contain versions"):
        MemoryProvenanceBatch(
            effect="rendered",
            origin="task_start",
            authority_versions=((ENTRY_A, DIGEST_A),),
            coverage="unknown",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("effect", "retrieved"),
        ("effect", 1),
        ("origin", "subagent"),
        ("origin", None),
        ("coverage", "truncated"),
        ("coverage", False),
        ("receipt_id", "A" * 32),
        ("receipt_id", "a" * 31),
        ("context_operation_id", "ctxop_" + "A" * 32),
        ("context_operation_id", "ctx_" + "a" * 32),
    ],
)
def test_constructor_rejects_invalid_scalar_contract_fields(
    field: str,
    value: object,
) -> None:
    kwargs: dict[str, object] = {
        "effect": "rendered",
        "origin": "task_start",
        "authority_versions": ((ENTRY_A, DIGEST_A),),
        "coverage": "complete",
    }
    kwargs[field] = value

    with pytest.raises((TypeError, ValueError)):
        MemoryProvenanceBatch(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "versions",
    [
        [(ENTRY_A, DIGEST_A)],
        (("../../secret", DIGEST_A),),
        ((ENTRY_A, "A" * 64),),
        ((ENTRY_A, "a" * 63),),
        ((ENTRY_A, DIGEST_A, "extra"),),
        ((ENTRY_A, 1),),
        ((ENTRY_A, DIGEST_A), (ENTRY_A, DIGEST_A)),
    ],
)
def test_constructor_rejects_invalid_or_duplicate_authority_versions(
    versions: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        MemoryProvenanceBatch(
            effect="rendered",
            origin="task_start",
            authority_versions=versions,  # type: ignore[arg-type]
            coverage="complete",
        )


def test_constructor_enforces_per_batch_version_limit() -> None:
    versions = tuple(
        (
            f"project-{index + 1}-{index:08x}",
            f"{index:064x}",
        )
        for index in range(MAX_MEMORY_PROVENANCE_VERSIONS + 1)
    )

    with pytest.raises(ValueError, match="too many"):
        MemoryProvenanceBatch(
            effect="rendered",
            origin="task_start",
            authority_versions=versions,
            coverage="complete",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        {"content": "secret"},
        {"query": "private query"},
        {"path": "/private/workspace"},
        {"subagentId": "sub_" + "a" * 32},
        {"unexpected": True},
    ],
)
def test_from_dict_rejects_unknown_and_forbidden_fields(
    mutation: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="unknown fields"):
        MemoryProvenanceBatch.from_dict({**_batch_dict(), **mutation})


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"schemaVersion": 1},
        _batch_dict(authorityVersions="not-a-list"),
        _batch_dict(authorityVersions=[[ENTRY_A]]),
        _batch_dict(authorityVersions=[[ENTRY_A, 1]]),
    ],
)
def test_from_dict_rejects_invalid_payload_shape(payload: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        MemoryProvenanceBatch.from_dict(payload)


def test_normalizer_accepts_multiple_valid_batches() -> None:
    rendered = _batch_dict()
    written = _batch_dict(
        effect="written",
        origin="reflection",
        authorityVersions=[[ENTRY_B, DIGEST_B]],
        receiptId=RECEIPT_ID,
        contextOperationId=CONTEXT_OPERATION_ID,
        coverage="partial",
    )

    normalized = normalize_memory_provenance_batches([rendered, written])

    assert normalized == (
        MemoryProvenanceBatch.from_dict(rendered),
        MemoryProvenanceBatch.from_dict(written),
    )


def test_normalizer_rejects_entire_collection_when_one_batch_is_invalid() -> None:
    invalid = {**_batch_dict(), "content": "must not partially pass"}

    assert normalize_memory_provenance_batches([_batch_dict(), invalid]) == ()


def test_normalizer_rejects_non_collection_and_batch_overflow() -> None:
    assert normalize_memory_provenance_batches(_batch_dict()) == ()
    assert (
        normalize_memory_provenance_batches(
            [_batch_dict()] * (MAX_MEMORY_PROVENANCE_BATCHES + 1)
        )
        == ()
    )


@pytest.mark.parametrize("max_batches", [-1, True, 1.5, "1"])
def test_normalizer_rejects_invalid_trusted_limit(max_batches: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        normalize_memory_provenance_batches(
            [],
            max_batches=max_batches,  # type: ignore[arg-type]
        )
