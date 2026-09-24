from __future__ import annotations

import copy

import pytest

import minicode.memory_audit_contract as contract
from minicode.memory_audit_contract import (
    MemoryAuditHead,
    MemoryAuditRecord,
    MemoryAuditSegment,
    decode_legacy_memory_audit_records,
    decode_memory_audit_head,
    decode_memory_audit_records,
    validate_memory_audit_segment_chain,
)
from minicode.memory_blob_store import MemoryBlobReference, canonical_memory_blob_bytes


def _raw(index: int = 1, *, timestamp: float | None = None) -> dict[str, object]:
    return {
        "audit_id": f"audit-{1_700_000_000_000_000_000 + index}-{index:08x}",
        "entry_id": "project-1700000000-deadbeef",
        "scope": "project",
        "action": "write",
        "actor": "safety_gate",
        "created_at": float(index) if timestamp is None else timestamp,
        "previous_approval_status": "new" if index == 1 else "approved",
        "approval_status": "approved",
        "previous_lifecycle_status": "new" if index == 1 else "active",
        "lifecycle_status": "active",
        "safety_status": "safe",
        "safety_reason": "",
        "risk": "low",
        "content_hash": "a" * 64,
        "reason": "verified",
        "source": "reflection",
        "provenance": {"run": ["verified"]},
        "extra": {"attempt": index},
    }


def _record(index: int = 1, *, timestamp: float | None = None) -> MemoryAuditRecord:
    return MemoryAuditRecord.from_dict(_raw(index, timestamp=timestamp))


def test_current_record_round_trip_is_closed_and_detached() -> None:
    raw = _raw()
    record = MemoryAuditRecord.from_dict(raw, expected_scope="project")
    raw["provenance"]["run"].append("mutated")  # type: ignore[index,union-attr]

    projected = record.to_dict()
    assert projected["provenance"] == {"run": ["verified"]}
    assert set(projected) == set(_raw())
    projected["extra"]["attempt"] = 9  # type: ignore[index]
    assert record.extra == {"attempt": 1}
    assert MemoryAuditRecord.from_dict(record.to_dict()) == record


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_current_record_rejects_open_or_incomplete_schema(mutation: str) -> None:
    raw = _raw()
    if mutation == "missing":
        raw.pop("risk")
    else:
        raw["unexpected"] = True
    with pytest.raises(ValueError, match="schema"):
        MemoryAuditRecord.from_dict(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("audit_id", "audit-1"),
        ("entry_id", "../escape"),
        ("scope", "global"),
        ("previous_approval_status", "unknown"),
        ("approval_status", "new"),
        ("previous_lifecycle_status", "unknown"),
        ("lifecycle_status", "new"),
        ("safety_status", "maybe"),
        ("risk", "critical"),
        ("content_hash", "A" * 64),
        ("reason", "x" * 241),
        ("safety_reason", "x" * 241),
        ("source", "x" * 121),
    ],
)
def test_current_record_rejects_invalid_typed_fields(field: str, value: object) -> None:
    raw = _raw()
    raw[field] = value
    with pytest.raises((TypeError, ValueError)):
        MemoryAuditRecord.from_dict(raw)


@pytest.mark.parametrize("value", [True, "1", -1.0, float("nan"), float("inf")])
def test_current_record_rejects_invalid_timestamp(value: object) -> None:
    raw = _raw()
    raw["created_at"] = value
    with pytest.raises((TypeError, ValueError)):
        MemoryAuditRecord.from_dict(raw)


def test_record_list_binds_scope_entry_and_unique_audit_id() -> None:
    valid = _raw()
    assert (
        len(
            decode_memory_audit_records(
                [valid],
                expected_scope="project",
                expected_entry_id=valid["entry_id"],  # type: ignore[arg-type]
            )
        )
        == 1
    )
    with pytest.raises(ValueError, match="duplicated"):
        decode_memory_audit_records(
            [valid, copy.deepcopy(valid)], expected_scope="project"
        )
    with pytest.raises(ValueError, match="scope"):
        decode_memory_audit_records([valid], expected_scope="user")
    with pytest.raises(ValueError, match="binding"):
        decode_memory_audit_records(
            [valid], expected_scope="project", expected_entry_id="other-entry"
        )


def test_metadata_depth_node_text_and_json_type_are_bounded() -> None:
    mutations = [
        {"deep": {"a": {"b": {"c": {"d": "too deep"}}}}},
        {str(index): [1, 2, 3] for index in range(128)},
        {"text": "x" * (contract.MAX_MEMORY_AUDIT_METADATA_TEXT_BYTES + 1)},
        {"set": {1, 2}},
    ]
    for extra in mutations:
        raw = _raw()
        raw["extra"] = extra
        with pytest.raises(ValueError):
            MemoryAuditRecord.from_dict(raw)


def test_explicit_legacy_decoder_does_not_make_sparse_data_current() -> None:
    legacy = [{"audit_id": "audit-1", "detail": "historic"}]
    assert decode_legacy_memory_audit_records(
        legacy, expected_scope="project"
    ) == tuple(legacy)
    with pytest.raises(ValueError, match="schema"):
        decode_memory_audit_records(legacy, expected_scope="project")
    with pytest.raises(ValueError, match="scope"):
        decode_legacy_memory_audit_records(
            [{"audit_id": "audit-1", "scope": "user"}],
            expected_scope="project",
        )


def test_segment_and_head_match_documented_closed_shapes() -> None:
    segment = MemoryAuditSegment.from_records(
        [_record(1), _record(2)], scope="project", previous=None
    )
    head = MemoryAuditHead.from_segment(segment)

    assert set(segment.to_dict()) == {
        "schemaVersion",
        "kind",
        "scope",
        "previous",
        "records",
    }
    assert set(head.to_dict()) == {"blob", "segmentCount", "recordCount"}
    assert len(canonical_memory_blob_bytes(segment.to_dict())) <= 64 * 1024
    assert MemoryAuditSegment.from_dict(segment.to_dict()) == segment
    assert decode_memory_audit_head(head.to_dict()) == head
    head.validate_segment(segment)


def test_two_segment_newest_first_chain_round_trip() -> None:
    oldest = MemoryAuditSegment.from_records(
        [_record(1)], scope="project", previous=None
    )
    old_head = MemoryAuditHead.from_segment(oldest)
    newest = MemoryAuditSegment.from_records(
        [_record(2), _record(3)], scope="project", previous=old_head.blob
    )
    head = MemoryAuditHead.from_segment(newest, previous_head=old_head)

    assert head.segment_count == 2
    assert head.record_count == 3
    assert (
        validate_memory_audit_segment_chain(
            head, [newest, oldest], expected_scope="project"
        )
        == 3
    )


@pytest.mark.parametrize(
    "field", ["schemaVersion", "kind", "scope", "previous", "records"]
)
def test_segment_rejects_missing_fields(field: str) -> None:
    raw = MemoryAuditSegment.from_records(
        [_record()], scope="project", previous=None
    ).to_dict()
    raw.pop(field)
    with pytest.raises(ValueError, match="schema"):
        MemoryAuditSegment.from_dict(raw)


def test_segment_rejects_empty_duplicate_out_of_order_and_wrong_scope_records() -> None:
    with pytest.raises(ValueError, match="count"):
        MemoryAuditSegment.from_records([], scope="project", previous=None)
    with pytest.raises(ValueError, match="duplicated"):
        MemoryAuditSegment.from_records(
            [_record(1), _record(1)], scope="project", previous=None
        )
    with pytest.raises(ValueError, match="order"):
        MemoryAuditSegment.from_records(
            [_record(2), _record(1)], scope="project", previous=None
        )
    wrong = _raw()
    wrong["scope"] = "user"
    with pytest.raises(ValueError, match="scope"):
        MemoryAuditSegment.from_records([wrong], scope="project", previous=None)


def test_head_rejects_open_schema_invalid_counts_and_wrong_blob() -> None:
    segment = MemoryAuditSegment.from_records(
        [_record()], scope="project", previous=None
    )
    head = MemoryAuditHead.from_segment(segment)
    with pytest.raises(ValueError, match="schema"):
        MemoryAuditHead.from_dict({**head.to_dict(), "extra": True})
    with pytest.raises(ValueError, match="count"):
        MemoryAuditHead(blob=head.blob, segment_count=0, record_count=1)
    wrong = MemoryAuditHead(
        blob=MemoryBlobReference("f" * 64, 1), segment_count=1, record_count=1
    )
    with pytest.raises(ValueError, match="match"):
        wrong.validate_segment(segment)


def test_chain_rejects_count_break_order_and_hot_collision() -> None:
    oldest = MemoryAuditSegment.from_records(
        [_record(1)], scope="project", previous=None
    )
    old_head = MemoryAuditHead.from_segment(oldest)
    newest = MemoryAuditSegment.from_records(
        [_record(2)], scope="project", previous=old_head.blob
    )
    head = MemoryAuditHead.from_segment(newest, previous_head=old_head)

    with pytest.raises(ValueError, match="count"):
        validate_memory_audit_segment_chain(head, [newest], expected_scope="project")
    broken = MemoryAuditSegment.from_records(
        [_record(2)],
        scope="project",
        previous=MemoryBlobReference("f" * 64, 1),
    )
    broken_head = MemoryAuditHead(
        blob=broken.blob_reference, segment_count=2, record_count=2
    )
    with pytest.raises(ValueError, match="broken"):
        validate_memory_audit_segment_chain(
            broken_head, [broken, oldest], expected_scope="project"
        )
    out_of_order = MemoryAuditSegment.from_records(
        [_record(3, timestamp=0.5)], scope="project", previous=old_head.blob
    )
    order_head = MemoryAuditHead.from_segment(out_of_order, previous_head=old_head)
    with pytest.raises(ValueError, match="order"):
        validate_memory_audit_segment_chain(
            order_head, [out_of_order, oldest], expected_scope="project"
        )
    with pytest.raises(ValueError, match="collides"):
        validate_memory_audit_segment_chain(
            head, [newest, oldest], expected_scope="project", hot_records=[_record(2)]
        )


def test_chain_limit_is_enforced_without_allocating_unbounded_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segment = MemoryAuditSegment.from_records(
        [_record()], scope="project", previous=None
    )
    head = MemoryAuditHead.from_segment(segment)
    monkeypatch.setattr(contract, "MAX_MEMORY_AUDIT_CHAIN_RECORDS", 0)
    with pytest.raises(ValueError, match="too large"):
        validate_memory_audit_segment_chain(head, [segment], expected_scope="project")
