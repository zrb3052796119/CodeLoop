"""Strict records and immutable hash-chain segments for Memory approval audit."""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, fields
from typing import Any

from minicode.memory_authority_io import (
    MAX_AUTHORITY_AUDIT_RECORDS,
    MAX_MEMORY_COUNTER,
    MAX_MEMORY_TIMESTAMP,
    is_memory_entry_id,
    validate_memory_authority_value,
)
from minicode.memory_blob_store import (
    MAX_MEMORY_BLOB_BYTES,
    MemoryBlobReference,
    canonical_memory_blob_bytes,
    memory_blob_digest,
)

MEMORY_AUDIT_SEGMENT_VERSION = 1
MEMORY_AUDIT_SEGMENT_KIND = "memory-approval-audit-segment"
MAX_MEMORY_AUDIT_RECORDS_PER_SEGMENT = 256
MAX_MEMORY_AUDIT_CHAIN_SEGMENTS = 100_000
MAX_MEMORY_AUDIT_CHAIN_RECORDS = 100_000
MAX_MEMORY_AUDIT_RECORD_BYTES = 24 * 1024
MAX_MEMORY_AUDIT_METADATA_BYTES = 12 * 1024
MAX_MEMORY_AUDIT_METADATA_DEPTH = 4
MAX_MEMORY_AUDIT_METADATA_NODES = 512
MAX_MEMORY_AUDIT_METADATA_ITEMS = 128
MAX_MEMORY_AUDIT_METADATA_TEXT_BYTES = 2 * 1024

MEMORY_AUDIT_SCOPES = frozenset({"user", "project", "local"})
MEMORY_AUDIT_APPROVAL_STATUSES = frozenset({"approved", "pending", "rejected"})
MEMORY_AUDIT_PREVIOUS_APPROVAL_STATUSES = frozenset(
    {*MEMORY_AUDIT_APPROVAL_STATUSES, "new"}
)
MEMORY_AUDIT_LIFECYCLE_STATUSES = frozenset(
    {
        "active",
        "pending",
        "held",
        "archived",
        "deprecated",
        "rejected",
        "invalid",
        "superseded",
        "archived_duplicate",
        "evicted",
    }
)
MEMORY_AUDIT_PREVIOUS_LIFECYCLE_STATUSES = frozenset(
    {*MEMORY_AUDIT_LIFECYCLE_STATUSES, "new"}
)
MEMORY_AUDIT_SAFETY_STATUSES = frozenset({"safe", "suspicious", "unsafe"})
MEMORY_AUDIT_RISKS = frozenset({"low", "medium", "high"})

_AUDIT_ID_RE = re.compile(r"^audit-[1-9][0-9]{0,20}-[0-9a-f]{8}$")
_LEGACY_AUDIT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RECORD_FIELDS = frozenset(
    {
        "audit_id",
        "entry_id",
        "scope",
        "action",
        "actor",
        "created_at",
        "previous_approval_status",
        "approval_status",
        "previous_lifecycle_status",
        "lifecycle_status",
        "safety_status",
        "safety_reason",
        "risk",
        "content_hash",
        "reason",
        "source",
        "provenance",
        "extra",
    }
)
_SEGMENT_FIELDS = frozenset({"schemaVersion", "kind", "scope", "previous", "records"})
_HEAD_FIELDS = frozenset({"blob", "segmentCount", "recordCount"})


def is_memory_audit_id(value: object) -> bool:
    return isinstance(value, str) and _AUDIT_ID_RE.fullmatch(value) is not None


def _scope(value: object, expected: str | None = None) -> str:
    if not isinstance(value, str) or value not in MEMORY_AUDIT_SCOPES:
        raise ValueError("Memory audit scope is invalid")
    if expected is not None and value != expected:
        raise ValueError("Memory audit scope does not match its authority")
    return value


def _timestamp(value: object, label: str) -> int | float:
    if isinstance(value, bool) or type(value) not in {int, float}:
        raise TypeError(f"{label} must be a JSON number")
    if not math.isfinite(value) or not 0 <= value <= MAX_MEMORY_TIMESTAMP:
        raise ValueError(f"{label} is out of range")
    return value


def _count(value: object, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if not 1 <= value <= maximum:
        raise ValueError(f"{label} is out of range")
    return value


def _text(
    value: object,
    label: str,
    *,
    chars: int,
    allow_empty: bool = True,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    if (
        (not value and not allow_empty)
        or len(value) > chars
        or len(encoded) > chars * 4
    ):
        raise ValueError(f"{label} is out of range")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _json_size(value: object) -> int:
    validate_memory_authority_value(value)
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    )


def _metadata(value: object, label: str) -> dict[str, Any]:
    """Validate and detach a bounded redacted provenance/extra JSON object."""

    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    active: set[int] = set()
    nodes = 0

    def visit(item: object, depth: int) -> Any:
        nonlocal nodes
        nodes += 1
        if (
            nodes > MAX_MEMORY_AUDIT_METADATA_NODES
            or depth > MAX_MEMORY_AUDIT_METADATA_DEPTH
        ):
            raise ValueError(f"{label} is too deeply nested")
        if item is None or isinstance(item, bool):
            return item
        if type(item) is int:
            if not -MAX_MEMORY_COUNTER <= item <= MAX_MEMORY_COUNTER:
                raise ValueError(f"{label} integer is out of range")
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError(f"{label} contains a non-finite number")
            return item
        if isinstance(item, str):
            if len(item.encode("utf-8")) > MAX_MEMORY_AUDIT_METADATA_TEXT_BYTES:
                raise ValueError(f"{label} text is too large")
            return item
        if isinstance(item, (dict, list)):
            identity = id(item)
            if identity in active:
                raise ValueError(f"{label} contains a cycle")
            if len(item) > MAX_MEMORY_AUDIT_METADATA_ITEMS:
                raise ValueError(f"{label} contains too many items")
            active.add(identity)
            try:
                if isinstance(item, list):
                    return [visit(nested, depth + 1) for nested in item]
                copied: dict[str, Any] = {}
                for key, nested in item.items():
                    if not isinstance(key, str) or len(key.encode("utf-8")) > 256:
                        raise ValueError(f"{label} key is invalid")
                    copied[key] = visit(nested, depth + 1)
                return copied
            finally:
                active.remove(identity)
        raise ValueError(f"{label} contains a non-JSON value")

    copied = visit(value, 0)
    if _json_size(copied) > MAX_MEMORY_AUDIT_METADATA_BYTES:
        raise ValueError(f"{label} is too large")
    return copied


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryAuditRecord:
    audit_id: str
    entry_id: str
    scope: str
    action: str
    actor: str
    created_at: int | float
    previous_approval_status: str
    approval_status: str
    previous_lifecycle_status: str
    lifecycle_status: str
    safety_status: str
    safety_reason: str
    risk: str
    content_hash: str
    reason: str
    source: str
    provenance: dict[str, Any]
    extra: dict[str, Any]

    def __post_init__(self) -> None:
        if not is_memory_audit_id(self.audit_id):
            raise ValueError("Memory audit ID is invalid")
        if not is_memory_entry_id(self.entry_id):
            raise ValueError("Memory audit entry ID is invalid")
        _scope(self.scope)
        _text(self.action, "Memory audit action", chars=128, allow_empty=False)
        _text(self.actor, "Memory audit actor", chars=128, allow_empty=False)
        _timestamp(self.created_at, "Memory audit timestamp")
        if self.previous_approval_status not in MEMORY_AUDIT_PREVIOUS_APPROVAL_STATUSES:
            raise ValueError("Previous Memory approval status is invalid")
        if self.approval_status not in MEMORY_AUDIT_APPROVAL_STATUSES:
            raise ValueError("Memory approval status is invalid")
        if (
            self.previous_lifecycle_status
            not in MEMORY_AUDIT_PREVIOUS_LIFECYCLE_STATUSES
        ):
            raise ValueError("Previous Memory lifecycle status is invalid")
        if self.lifecycle_status not in MEMORY_AUDIT_LIFECYCLE_STATUSES:
            raise ValueError("Memory lifecycle status is invalid")
        if self.safety_status not in MEMORY_AUDIT_SAFETY_STATUSES:
            raise ValueError("Memory safety status is invalid")
        if self.risk not in MEMORY_AUDIT_RISKS:
            raise ValueError("Memory audit risk is invalid")
        _text(self.safety_reason, "Memory audit safety reason", chars=240)
        _digest(self.content_hash, "Memory audit content hash")
        _text(self.reason, "Memory audit reason", chars=240)
        _text(self.source, "Memory audit source", chars=120)
        object.__setattr__(self, "provenance", _metadata(self.provenance, "provenance"))
        object.__setattr__(self, "extra", _metadata(self.extra, "extra"))
        if _json_size(self.to_dict()) > MAX_MEMORY_AUDIT_RECORD_BYTES:
            raise ValueError("Memory audit record is too large")

    def to_dict(self) -> dict[str, object]:
        result = {field.name: getattr(self, field.name) for field in fields(self)}
        result["provenance"] = _metadata(self.provenance, "provenance")
        result["extra"] = _metadata(self.extra, "extra")
        return result

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        expected_scope: str | None = None,
        expected_entry_id: str | None = None,
    ) -> MemoryAuditRecord:
        if not isinstance(value, dict) or set(value) != _RECORD_FIELDS:
            raise ValueError("Memory audit record has an invalid schema")
        scope = _scope(value["scope"], expected_scope)
        if expected_entry_id is not None and value["entry_id"] != expected_entry_id:
            raise ValueError("Memory audit entry does not match its binding")
        bound = dict(value)
        bound["scope"] = scope
        return cls(**bound)  # type: ignore[arg-type]


def decode_memory_audit_records(
    value: object,
    *,
    expected_scope: str,
    expected_entry_id: str | None = None,
    maximum_records: int = MAX_AUTHORITY_AUDIT_RECORDS,
) -> tuple[MemoryAuditRecord, ...]:
    _scope(expected_scope)
    if (
        isinstance(maximum_records, bool)
        or not 0 <= maximum_records <= MAX_MEMORY_AUDIT_CHAIN_RECORDS
    ):
        raise ValueError("Memory audit record limit is invalid")
    if not isinstance(value, list) or len(value) > maximum_records:
        raise ValueError("Memory audit record list is invalid")
    records = tuple(
        MemoryAuditRecord.from_dict(
            item,
            expected_scope=expected_scope,
            expected_entry_id=expected_entry_id,
        )
        for item in value
    )
    ids = [record.audit_id for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Memory audit ID is duplicated")
    return records


def decode_legacy_memory_audit_records(
    value: object,
    *,
    expected_scope: str,
) -> tuple[dict[str, Any], ...]:
    """Validate bounded unversioned records without pretending they are current."""

    _scope(expected_scope)
    if not isinstance(value, list) or len(value) > MAX_AUTHORITY_AUDIT_RECORDS:
        raise ValueError("Legacy Memory audit record list is invalid")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Legacy Memory audit record is invalid")
        validate_memory_authority_value(item)
        audit_id = item.get("audit_id")
        if (
            not isinstance(audit_id, str)
            or not _LEGACY_AUDIT_ID_RE.fullmatch(audit_id)
            or audit_id in seen
        ):
            raise ValueError("Legacy Memory audit ID is invalid")
        if item.get("entry_id") is not None and not is_memory_entry_id(
            item["entry_id"]
        ):
            raise ValueError("Legacy Memory audit entry ID is invalid")
        if item.get("scope") is not None:
            _scope(item["scope"], expected_scope)
        if "created_at" in item:
            _timestamp(item["created_at"], "Legacy Memory audit timestamp")
        if _json_size(item) > MAX_MEMORY_BLOB_BYTES:
            raise ValueError("Legacy Memory audit record is too large")
        seen.add(audit_id)
        # The strict value validator above has already excluded custom JSON
        # values. Detach containers without parsing the authority twice.
        result.append(copy.deepcopy(item))
    return tuple(result)


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryAuditSegment:
    scope: str
    previous: MemoryBlobReference | None
    records: tuple[MemoryAuditRecord, ...]
    schema_version: int = MEMORY_AUDIT_SEGMENT_VERSION
    kind: str = MEMORY_AUDIT_SEGMENT_KIND

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != MEMORY_AUDIT_SEGMENT_VERSION
        ):
            raise ValueError("Memory audit segment schema version is unsupported")
        if self.kind != MEMORY_AUDIT_SEGMENT_KIND:
            raise ValueError("Memory audit segment kind is invalid")
        _scope(self.scope)
        if self.previous is not None and not isinstance(
            self.previous, MemoryBlobReference
        ):
            raise TypeError("Previous Memory audit segment reference is invalid")
        if (
            not isinstance(self.records, tuple)
            or not 1 <= len(self.records) <= MAX_MEMORY_AUDIT_RECORDS_PER_SEGMENT
        ):
            raise ValueError("Memory audit segment record count is out of range")
        ids: set[str] = set()
        previous_time: int | float | None = None
        for record in self.records:
            if not isinstance(record, MemoryAuditRecord) or record.scope != self.scope:
                raise ValueError("Memory audit segment record binding is invalid")
            if record.audit_id in ids or (
                previous_time is not None and record.created_at < previous_time
            ):
                raise ValueError(
                    "Memory audit segment records are duplicated or out of order"
                )
            ids.add(record.audit_id)
            previous_time = record.created_at
        canonical_memory_blob_bytes(self.to_dict())

    @classmethod
    def from_records(
        cls,
        records: Sequence[MemoryAuditRecord | dict[str, object]],
        *,
        scope: str,
        previous: MemoryBlobReference | dict[str, object] | None,
    ) -> MemoryAuditSegment:
        if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
            raise TypeError("Memory audit segment records must be a sequence")
        decoded = tuple(
            item
            if isinstance(item, MemoryAuditRecord)
            else MemoryAuditRecord.from_dict(item, expected_scope=scope)
            for item in records
        )
        previous_ref = (
            MemoryBlobReference.from_dict(previous)
            if isinstance(previous, dict)
            else previous
        )
        return cls(scope=_scope(scope), previous=previous_ref, records=decoded)

    @property
    def record_count(self) -> int:
        return len(self.records)

    @property
    def first_created_at(self) -> int | float:
        return self.records[0].created_at

    @property
    def last_created_at(self) -> int | float:
        return self.records[-1].created_at

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "kind": self.kind,
            "scope": self.scope,
            "previous": self.previous.to_dict() if self.previous else None,
            "records": [record.to_dict() for record in self.records],
        }

    @property
    def blob_reference(self) -> MemoryBlobReference:
        encoded = canonical_memory_blob_bytes(self.to_dict())
        return MemoryBlobReference(memory_blob_digest(encoded), len(encoded))

    @classmethod
    def from_dict(
        cls, value: object, *, expected_scope: str | None = None
    ) -> MemoryAuditSegment:
        if not isinstance(value, dict) or set(value) != _SEGMENT_FIELDS:
            raise ValueError("Memory audit segment has an invalid schema")
        if (
            type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != MEMORY_AUDIT_SEGMENT_VERSION
        ):
            raise ValueError("Memory audit segment schema version is unsupported")
        if value["kind"] != MEMORY_AUDIT_SEGMENT_KIND:
            raise ValueError("Memory audit segment kind is invalid")
        scope = _scope(value["scope"], expected_scope)
        if not isinstance(value["records"], list):
            raise TypeError("Memory audit segment records must be a list")
        previous = (
            None
            if value["previous"] is None
            else MemoryBlobReference.from_dict(value["previous"])
        )
        return cls(
            scope=scope,
            previous=previous,
            records=decode_memory_audit_records(
                value["records"],
                expected_scope=scope,
                maximum_records=MAX_MEMORY_AUDIT_RECORDS_PER_SEGMENT,
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryAuditHead:
    blob: MemoryBlobReference
    segment_count: int
    record_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.blob, MemoryBlobReference):
            raise TypeError("Memory audit head blob is invalid")
        _count(
            self.segment_count,
            "Memory audit head segment count",
            MAX_MEMORY_AUDIT_CHAIN_SEGMENTS,
        )
        _count(
            self.record_count,
            "Memory audit head record count",
            MAX_MEMORY_AUDIT_CHAIN_RECORDS,
        )

    @classmethod
    def from_segment(
        cls,
        segment: MemoryAuditSegment,
        *,
        previous_head: MemoryAuditHead | None = None,
    ) -> MemoryAuditHead:
        if previous_head is None:
            if segment.previous is not None:
                raise ValueError("First Memory audit segment must end at null")
            return cls(
                blob=segment.blob_reference,
                segment_count=1,
                record_count=segment.record_count,
            )
        if segment.previous != previous_head.blob:
            raise ValueError("Memory audit segment does not extend the previous head")
        return cls(
            blob=segment.blob_reference,
            segment_count=previous_head.segment_count + 1,
            record_count=previous_head.record_count + segment.record_count,
        )

    def validate_segment(self, segment: MemoryAuditSegment) -> None:
        if segment.blob_reference != self.blob:
            raise ValueError("Memory audit head does not match its segment")

    def to_dict(self) -> dict[str, object]:
        return {
            "blob": self.blob.to_dict(),
            "segmentCount": self.segment_count,
            "recordCount": self.record_count,
        }

    @classmethod
    def from_dict(cls, value: object) -> MemoryAuditHead:
        if not isinstance(value, dict) or set(value) != _HEAD_FIELDS:
            raise ValueError("Memory audit head has an invalid schema")
        return cls(
            blob=MemoryBlobReference.from_dict(value["blob"]),
            segment_count=value["segmentCount"],  # type: ignore[arg-type]
            record_count=value["recordCount"],  # type: ignore[arg-type]
        )


def decode_memory_audit_head(value: object) -> MemoryAuditHead | None:
    return None if value is None else MemoryAuditHead.from_dict(value)


def validate_memory_audit_segment_chain(
    head: MemoryAuditHead | None,
    segments: Sequence[MemoryAuditSegment],
    *,
    expected_scope: str,
    hot_records: Sequence[MemoryAuditRecord] = (),
) -> int:
    """Validate a complete newest-first chain, returning its cold record count."""

    _scope(expected_scope)
    if head is None:
        if segments:
            raise ValueError("Memory audit segments exist without a head")
        cold_ids: set[str] = set()
    else:
        if len(segments) != head.segment_count:
            raise ValueError("Memory audit segment count does not match its head")
        expected: MemoryBlobReference | None = head.blob
        cold_ids = set()
        newer_first: int | float | None = None
        total = 0
        seen_digests: set[str] = set()
        for segment in segments:
            actual = segment.blob_reference
            if segment.scope != expected_scope or actual != expected:
                raise ValueError("Memory audit segment chain is broken")
            if actual.digest in seen_digests:
                raise ValueError("Memory audit segment chain contains a cycle")
            if newer_first is not None and segment.last_created_at > newer_first:
                raise ValueError("Memory audit segment chain is out of order")
            newer_first = segment.first_created_at
            seen_digests.add(actual.digest)
            for record in segment.records:
                if record.audit_id in cold_ids:
                    raise ValueError("Memory audit ID is duplicated across segments")
                cold_ids.add(record.audit_id)
            total += segment.record_count
            if total > MAX_MEMORY_AUDIT_CHAIN_RECORDS:
                raise ValueError("Memory audit segment chain is too large")
            expected = segment.previous
        if expected is not None:
            raise ValueError("Memory audit segment chain is incomplete")
        if total != head.record_count:
            raise ValueError("Memory audit record count does not match its head")

    previous_hot: int | float | None = None
    newest_cold = segments[0].last_created_at if segments else None
    for record in hot_records:
        if not isinstance(record, MemoryAuditRecord) or record.scope != expected_scope:
            raise ValueError("Memory audit hot record binding is invalid")
        if record.audit_id in cold_ids:
            raise ValueError("Memory audit ID collides across hot and cold history")
        if newest_cold is not None and record.created_at < newest_cold:
            raise ValueError("Memory audit hot and cold history is out of order")
        if previous_hot is not None and record.created_at < previous_hot:
            raise ValueError("Memory audit hot records are out of order")
        cold_ids.add(record.audit_id)
        previous_hot = record.created_at
    return head.record_count if head is not None else 0


__all__ = [
    "MAX_MEMORY_AUDIT_CHAIN_RECORDS",
    "MAX_MEMORY_AUDIT_CHAIN_SEGMENTS",
    "MAX_MEMORY_AUDIT_RECORDS_PER_SEGMENT",
    "MEMORY_AUDIT_SEGMENT_KIND",
    "MEMORY_AUDIT_SEGMENT_VERSION",
    "MemoryAuditHead",
    "MemoryAuditRecord",
    "MemoryAuditSegment",
    "decode_legacy_memory_audit_records",
    "decode_memory_audit_head",
    "decode_memory_audit_records",
    "is_memory_audit_id",
    "validate_memory_audit_segment_chain",
]
