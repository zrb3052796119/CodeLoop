"""Layered memory system for cross-session knowledge retention.

Provides three-tier memory hierarchy:
- User memory (~/.mini-code/memory/) - cross-project, persistent
- Project memory (.mini-code-memory/) - shared across sessions, can be versioned
- Local memory (.mini-code-memory-local/) - project-specific, not checked in

Memory is automatically injected into system prompts to give the agent
context about past decisions, codebase patterns, and project conventions.

Search uses TF-IDF relevance scoring for intelligent retrieval.
"""

from __future__ import annotations

import base64
import binascii
import copy
import functools
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
import uuid
from collections import Counter
from collections.abc import Sequence
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import minicode.memory_authority_io as memory_authority_io_module
from minicode.config import MINI_CODE_DIR
from minicode.memory_approval_interpretation import (
    ApprovalAuthorityState,
    ApprovalInterpretation,
    ApprovalInterpretationInput,
    ApprovalInterpretationMode,
    interpret_loaded_approval,
)
from minicode.memory_audit_contract import (
    MAX_MEMORY_AUDIT_CHAIN_RECORDS,
    MAX_MEMORY_AUDIT_RECORDS_PER_SEGMENT,
    MemoryAuditHead,
    MemoryAuditRecord,
    MemoryAuditSegment,
    decode_legacy_memory_audit_records,
    decode_memory_audit_head,
    decode_memory_audit_records,
    validate_memory_audit_segment_chain,
)
from minicode.memory_authority_io import (
    CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION,
    MAX_AUTHORITY_AUDIT_RECORDS,
    MAX_AUTHORITY_ENTRIES,
    MAX_CURRENT_AUTHORITY_CONTENT_BYTES,
    MAX_CURRENT_AUTHORITY_ENTRIES,
    MAX_MEMORY_COUNTER,
    MAX_MEMORY_TIMESTAMP,
    MAX_MEMORY_USEFULNESS_SCORE,
    MIGRATABLE_MEMORY_AUTHORITY_SCHEMA_VERSIONS,
    MIN_MEMORY_USEFULNESS_SCORE,
    MemoryAuthorityLimitError,
    MemoryAuthoritySource,
    MemoryAuthoritySourceError,
    MemoryAuthorityTooLargeError,
    is_memory_entry_id,
    parse_memory_authority_json,
    read_memory_authority_file,
    validate_memory_authority_commit,
    validate_memory_authority_value,
)
from minicode.memory_authority_publisher import MemoryAuthorityPublisher
from minicode.memory_blob_store import (
    MemoryBlobReference,
    MemoryBlobStore,
    canonical_memory_blob_bytes,
    memory_blob_digest,
)
from minicode.memory_store import (
    MemoryStoreBusy,
    MemoryStoreConflict,
    MemoryStoreCoordinator,
    MemoryStoreError,
    MemoryStoreUnavailable,
    MemoryStoreUnsafePath,
)
from minicode.memory_write_contract import (
    MAX_MEMORY_CLAIMS_PER_BATCH,
    MemoryAuthorityCommit,
    MemoryAuthorityReceiptReference,
    MemoryMaterialization,
)

logger = logging.getLogger(__name__)

_MEMORY_MANAGER_TRANSACTION_STATE = threading.local()


def _active_memory_manager_transaction() -> object | None:
    """Return this thread's manager owner, clearing inherited fork state."""

    pid = os.getpid()
    if getattr(_MEMORY_MANAGER_TRANSACTION_STATE, "pid", None) != pid:
        _MEMORY_MANAGER_TRANSACTION_STATE.pid = pid
        _MEMORY_MANAGER_TRANSACTION_STATE.owner = None
    return getattr(_MEMORY_MANAGER_TRANSACTION_STATE, "owner", None)

_CURRENT_ENTRY_REQUIRED_FIELDS = frozenset(
    {
        "id",
        "scope",
        "category",
        "content",
        "created_at",
        "updated_at",
        "tags",
        "usage_count",
        "domains",
        "tier",
        "last_accessed",
        "related_to",
        "metadata",
        "source",
        "provenance",
        "retrieval_count",
        "injection_count",
        "success_count",
        "failure_count",
        "last_used",
        "usefulness_score",
        "corroborated_success_count",
        "corroborated_failure_count",
        "corroborated_usefulness_score",
        "feedback_observations",
        "lifecycle_status",
        "tier_reason",
        "deprecated_at",
        "curator_locked",
        "safety_status",
        "safety_reason",
        "approval_status",
        "approval_content_hash",
        "approval_reason",
        "approval_actor",
        "approval_decided_at",
        "approval_policy",
        "authority_state_digest",
    }
)
_V2_ENTRY_REQUIRED_FIELDS = frozenset(
    _CURRENT_ENTRY_REQUIRED_FIELDS - {"authority_state_digest"}
)
_SEALED_ENTRY_SCHEMA_VERSIONS = frozenset(
    {3, 4, 5, CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION}
)
_RECEIPT_REFERENCE_SCHEMA_VERSIONS = frozenset(
    {4, 5, CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION}
)
_CURRENT_ROOT_REQUIRED_FIELDS = frozenset(
    {
        "schemaVersion",
        "scope",
        "last_updated",
        "entries",
        "approval_audit",
        "approval_audit_head",
        "write_receipts",
        "pipeline_operation_receipts",
        "observation_receipts",
    }
)
_V5_ROOT_REQUIRED_FIELDS = frozenset(
    _CURRENT_ROOT_REQUIRED_FIELDS - {"observation_receipts"}
)
_PRE_SEGMENTED_ROOT_REQUIRED_FIELDS = frozenset(
    _V5_ROOT_REQUIRED_FIELDS - {"approval_audit_head"}
)
_CURRENT_VALID_MEMORY_TIERS = frozenset(
    {"working", "short_term", "long_term", "archival"}
)
_VALID_LIFECYCLE_STATUSES = frozenset(
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
_CURRENT_VALID_APPROVAL_POLICIES = frozenset(
    {"user_explicit", "user_review_required", "auto_approve_verified"}
)
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _authority_state_digest_for_data(data: dict[str, Any]) -> str:
    """Bind every field that can make one persisted entry injectable.

    The approval-content hash remains the version identity used by feedback.
    This second, derived seal prevents a versioned decoder from silently
    replacing missing or edited authority fields with permissive defaults.
    It is an integrity invariant, not a secret-key signature.
    """

    payload = {
        "digestVersion": 1,
        "id": data.get("id"),
        "scope": data.get("scope"),
        "tier": data.get("tier"),
        "lifecycle_status": data.get("lifecycle_status"),
        "curator_locked": data.get("curator_locked"),
        "safety_status": data.get("safety_status"),
        "safety_reason": data.get("safety_reason"),
        "approval_status": data.get("approval_status"),
        "approval_content_hash": data.get("approval_content_hash"),
        "approval_reason": data.get("approval_reason"),
        "approval_actor": data.get("approval_actor"),
        "approval_decided_at": data.get("approval_decided_at"),
        "approval_policy": data.get("approval_policy"),
        "deprecated_at": data.get("deprecated_at"),
        "tier_reason": data.get("tier_reason"),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _derived_usefulness_score(success_count: int, failure_count: int) -> float:
    total = success_count + failure_count
    return (
        (success_count - failure_count) / total
        if total
        else 0.0
    )


def _saturating_counter_add(current: int, increment: int = 1) -> int:
    """Advance a persisted JSON counter without making the store unwritable."""

    if type(current) is not int or type(increment) is not int:
        raise TypeError("Memory counters require integer arithmetic")
    if current < 0 or increment < 0:
        raise ValueError("Memory counters cannot be negative")
    return min(MAX_MEMORY_COUNTER, current + increment)


def _bounded_age_hours(timestamp: float, *, now: float | None = None) -> float:
    """Return a non-negative age so future clock skew cannot amplify rank."""

    reference = time.time() if now is None else now
    return max(0.0, (reference - timestamp) / 3600.0)


def _fits_persistence_depth(value: object, *, initial_depth: int = 1) -> bool:
    """Match the canonical persistence sanitizer's structural depth budget."""

    stack: list[tuple[object, int]] = [(value, initial_depth)]
    while stack:
        current, depth = stack.pop()
        if depth > _PERSISTENCE_MAX_DEPTH:
            return False
        if isinstance(current, dict):
            stack.extend((nested, depth + 1) for nested in current.values())
        elif isinstance(current, list):
            stack.extend((nested, depth + 1) for nested in current)
    return True


def _parse_memory_write_receipt_authority(
    value: object,
    *,
    expected_scope: str,
    authority_schema_version: int | None = None,
) -> dict[
    str,
    MemoryAuthorityCommit | MemoryAuthorityReceiptReference,
]:
    """Parse inline legacy receipts or current content-addressed references."""

    if not isinstance(value, list) or len(value) > _MAX_MEMORY_WRITE_RECEIPTS:
        raise ValueError("invalid_memory_write_receipts")
    receipts: dict[
        str,
        MemoryAuthorityCommit | MemoryAuthorityReceiptReference,
    ] = {}
    current_references = (
        authority_schema_version in _RECEIPT_REFERENCE_SCHEMA_VERSIONS
    )
    for raw_receipt in value:
        receipt = (
            MemoryAuthorityReceiptReference.from_dict(raw_receipt)
            if current_references
            else MemoryAuthorityCommit.from_dict(raw_receipt)
        )
        if receipt.scope != expected_scope or receipt.operation_id in receipts:
            raise ValueError("invalid_memory_write_receipts")
        if isinstance(receipt, MemoryAuthorityCommit):
            receipt.to_json()
        receipts[receipt.operation_id] = receipt
    return receipts


def _parse_pipeline_operation_receipt_authority(
    value: object,
) -> dict[str, str]:
    """Parse the aggregate receipts shared by every authority reader."""

    if (
        not isinstance(value, list)
        or len(value) > _MAX_PIPELINE_OPERATION_RECEIPTS
    ):
        raise ValueError("invalid_pipeline_operation_receipts")
    receipts: dict[str, str] = {}
    for raw_receipt in value:
        if not isinstance(raw_receipt, dict) or set(raw_receipt) != {
            "operationId",
            "aggregateDigest",
        }:
            raise ValueError("invalid_pipeline_operation_receipts")
        operation_id = raw_receipt.get("operationId")
        aggregate_digest = raw_receipt.get("aggregateDigest")
        if (
            not isinstance(operation_id, str)
            or _MEMORY_WRITE_OPERATION_RE.fullmatch(operation_id) is None
            or not isinstance(aggregate_digest, str)
            or _MEMORY_WRITE_DIGEST_RE.fullmatch(aggregate_digest) is None
            or operation_id in receipts
        ):
            raise ValueError("invalid_pipeline_operation_receipts")
        receipts[operation_id] = aggregate_digest
    return receipts


def _parse_observation_receipt_authority(
    value: object,
) -> dict[str, dict[str, str]]:
    """Parse bounded, content-free multi-scope delivery receipts by lane."""

    if not isinstance(value, dict) or set(value) != _OBSERVATION_RECEIPT_LANES:
        raise ValueError("invalid_observation_receipts")
    receipts: dict[str, dict[str, str]] = {}
    for lane in sorted(_OBSERVATION_RECEIPT_LANES):
        raw_lane = value.get(lane)
        if (
            not isinstance(raw_lane, list)
            or len(raw_lane) > _MAX_OBSERVATION_RECEIPTS_PER_LANE
        ):
            raise ValueError("invalid_observation_receipts")
        lane_receipts: dict[str, str] = {}
        for raw_receipt in raw_lane:
            if not isinstance(raw_receipt, dict) or set(raw_receipt) != {
                "observationDigest",
                "payloadDigest",
            }:
                raise ValueError("invalid_observation_receipts")
            observation_digest = raw_receipt.get("observationDigest")
            payload_digest = raw_receipt.get("payloadDigest")
            if (
                not isinstance(observation_digest, str)
                or _MEMORY_WRITE_DIGEST_RE.fullmatch(observation_digest) is None
                or not isinstance(payload_digest, str)
                or _MEMORY_WRITE_DIGEST_RE.fullmatch(payload_digest) is None
                or observation_digest in lane_receipts
            ):
                raise ValueError("invalid_observation_receipts")
            lane_receipts[observation_digest] = payload_digest
        receipts[lane] = lane_receipts
    return receipts

# ---------------------------------------------------------------------------
# Memory data validation
# ---------------------------------------------------------------------------


def _bind_memory_authority_scope(
    data: Any,
    expected_scope: str,
) -> tuple[Any, list[str]]:
    """Bind one parsed authority container and its entries to its owning root.

    A missing container or entry scope is a supported legacy representation.
    An explicit mismatch is authority corruption and must be rejected before
    partial recovery can copy an entry into a different scope.
    """

    if not isinstance(data, dict):
        return data, []
    errors: list[str] = []
    schema_version = data.get("schemaVersion")
    versioned_schema = schema_version in (
        {CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION}
        | set(MIGRATABLE_MEMORY_AUTHORITY_SCHEMA_VERSIONS)
    )
    container_scope = data.get("scope")
    if versioned_schema and "scope" not in data:
        errors.append("Versioned authority is missing its scope")
    if container_scope is not None and container_scope != expected_scope:
        errors.append(
            f"Authority scope '{container_scope}' does not match root "
            f"'{expected_scope}'"
        )

    entries = data.get("entries")
    if not isinstance(entries, list):
        return data, errors
    normalized_entries: list[Any] = []
    for index, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, dict):
            normalized_entries.append(raw_entry)
            continue
        entry_scope = raw_entry.get("scope")
        if entry_scope is not None and entry_scope != expected_scope:
            errors.append(
                f"Entry at index {index} scope '{entry_scope}' does not match "
                f"root '{expected_scope}'"
            )
        normalized_entry = dict(raw_entry)
        if not versioned_schema:
            normalized_entry.setdefault("scope", expected_scope)
        normalized_entries.append(normalized_entry)
    normalized = dict(data)
    normalized["entries"] = normalized_entries
    return normalized, errors


def _validate_memory_data(data: Any) -> tuple[bool, list[str]]:
    """Validate the structure of memory JSON data before loading.

    Checks for:
    - Required fields present (entries)
    - Valid enum values for scope
    - Valid data types for all entry fields

    Args:
        data: Parsed JSON data dictionary

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors: list[str] = []

    if not isinstance(data, dict):
        return False, ["Root data must be a dictionary"]

    if "schemaVersion" in data:
        schema_version = data["schemaVersion"]
        if type(schema_version) is not int or schema_version not in (
            {CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION}
            | set(MIGRATABLE_MEMORY_AUTHORITY_SCHEMA_VERSIONS)
        ):
            errors.append("Unsupported Memory authority schemaVersion")

    schema_version = data.get("schemaVersion")
    versioned_schema = schema_version in (
        {CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION}
        | set(MIGRATABLE_MEMORY_AUTHORITY_SCHEMA_VERSIONS)
    )
    if versioned_schema:
        required_root_fields = (
            _CURRENT_ROOT_REQUIRED_FIELDS
            if schema_version == CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION
            else (
                _V5_ROOT_REQUIRED_FIELDS
                if schema_version == 5
                else _PRE_SEGMENTED_ROOT_REQUIRED_FIELDS
            )
        )
        for field_name in required_root_fields:
            if field_name not in data:
                errors.append(
                    f"Versioned authority is missing required field: '{field_name}'"
                )
        root_scope = data.get("scope")
        if not isinstance(root_scope, str) or root_scope not in _VALID_SCOPES:
            errors.append("Versioned authority has an invalid or missing scope")
        last_updated = data.get("last_updated")
        if (
            isinstance(last_updated, bool)
            or not isinstance(last_updated, (int, float))
            or not math.isfinite(last_updated)
            or not 0 <= last_updated <= MAX_MEMORY_TIMESTAMP
        ):
            errors.append("Versioned authority has an invalid last_updated")
        try:
            write_receipts = _parse_memory_write_receipt_authority(
                data.get("write_receipts"),
                expected_scope=str(root_scope),
                authority_schema_version=schema_version,
            )
            pipeline_receipts = _parse_pipeline_operation_receipt_authority(
                data.get("pipeline_operation_receipts")
            )
            if schema_version == CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION:
                _parse_observation_receipt_authority(
                    data.get("observation_receipts")
                )
            elif "observation_receipts" in data:
                raise ValueError(
                    "Legacy authority cannot declare observation receipts"
                )
            for operation_id in set(write_receipts).intersection(
                pipeline_receipts
            ):
                if (
                    write_receipts[operation_id].payload_digest
                    != pipeline_receipts[operation_id]
                ):
                    raise ValueError("receipt_digests_disagree")
        except (TypeError, ValueError):
            errors.append("Versioned authority has invalid operation receipts")
        try:
            audit_records = decode_memory_audit_records(
                data.get("approval_audit"),
                expected_scope=str(root_scope),
                maximum_records=MAX_AUTHORITY_AUDIT_RECORDS,
            )
            if schema_version == CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION:
                audit_head = decode_memory_audit_head(
                    data.get("approval_audit_head")
                )
                cold_count = audit_head.record_count if audit_head else 0
                if cold_count + len(audit_records) > MAX_MEMORY_AUDIT_CHAIN_RECORDS:
                    raise ValueError("Memory audit history is too large")
            elif "approval_audit_head" in data:
                raise ValueError(
                    "Pre-segmented authority cannot declare an audit head"
                )
        except (TypeError, ValueError):
            errors.append("Versioned authority has invalid approval audit")

    if "entries" not in data:
        errors.append("Missing required field: 'entries'")
        return False, errors

    entries = data.get("entries")
    if not isinstance(entries, list):
        errors.append("'entries' must be a list")
        return False, errors
    entry_limit = (
        MAX_CURRENT_AUTHORITY_ENTRIES
        if versioned_schema
        else MAX_AUTHORITY_ENTRIES
    )
    if len(entries) > entry_limit:
        errors.append("'entries' exceeds the bounded authority limit")
        return False, errors

    seen_entry_ids: set[str] = set()
    for idx, entry_data in enumerate(entries):
        _, entry_errors = _validate_entry(
            entry_data,
            idx,
            authority_schema_version=(
                schema_version if versioned_schema else None
            ),
        )
        errors.extend(entry_errors)
        if isinstance(entry_data, dict):
            entry_id = entry_data.get("id")
            if isinstance(entry_id, str) and entry_id in seen_entry_ids:
                errors.append(f"Duplicate Memory entry id: '{entry_id}'")
            elif isinstance(entry_id, str):
                seen_entry_ids.add(entry_id)

    return len(errors) == 0, errors


def _validate_entry(
    entry: Any,
    index: int,
    *,
    authority_schema_version: int | None = None,
) -> tuple[bool, list[str]]:
    """Validate a single memory entry dictionary.

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors: list[str] = []
    prefix = f"Entry at index {index}"

    if not isinstance(entry, dict):
        return False, [f"{prefix} is not a dictionary"]

    sealed_schema = authority_schema_version in _SEALED_ENTRY_SCHEMA_VERSIONS
    if sealed_schema:
        required_fields = _CURRENT_ENTRY_REQUIRED_FIELDS
    elif authority_schema_version == 2:
        required_fields = _V2_ENTRY_REQUIRED_FIELDS
    else:
        required_fields = frozenset({"id", "content"})
    for field_name in required_fields:
        if field_name not in entry:
            errors.append(f"{prefix} missing required field: '{field_name}'")

    if "id" in entry:
        entry_id = entry["id"]
        if (
            not isinstance(entry_id, str)
            or not is_memory_entry_id(entry_id)
        ):
            errors.append(f"{prefix} field 'id' is invalid")

    if "scope" in entry:
        scope_val = entry["scope"]
        if not isinstance(scope_val, str):
            errors.append(f"{prefix} field 'scope' must be a string")
        elif scope_val not in _VALID_SCOPES:
            errors.append(
                f"{prefix} has invalid scope value: '{scope_val}'. "
                f"Must be one of: {', '.join(sorted(_VALID_SCOPES))}"
            )

    if "category" in entry:
        if not isinstance(entry["category"], str):
            errors.append(f"{prefix} field 'category' must be a string")
        elif authority_schema_version is not None and not entry["category"].strip():
            errors.append(f"{prefix} field 'category' must not be empty")

    if "content" in entry:
        if not isinstance(entry["content"], str):
            errors.append(f"{prefix} field 'content' must be a string")
        elif authority_schema_version is not None and not entry["content"].strip():
            errors.append(f"{prefix} field 'content' must not be empty")

    if "created_at" in entry:
        val = entry["created_at"]
        if (
            isinstance(val, bool)
            or not isinstance(val, (int, float))
            or not math.isfinite(val)
            or not 0 <= val <= MAX_MEMORY_TIMESTAMP
        ):
            errors.append(f"{prefix} field 'created_at' must be a finite timestamp")

    if "updated_at" in entry:
        val = entry["updated_at"]
        if (
            isinstance(val, bool)
            or not isinstance(val, (int, float))
            or not math.isfinite(val)
            or not 0 <= val <= MAX_MEMORY_TIMESTAMP
        ):
            errors.append(f"{prefix} field 'updated_at' must be a finite timestamp")

    if "tags" in entry:
        val = entry["tags"]
        if not isinstance(val, list):
            errors.append(f"{prefix} field 'tags' must be a list")
        elif not all(isinstance(t, str) for t in val):
            errors.append(f"{prefix} field 'tags' must contain only strings")

    if "usage_count" in entry:
        val = entry["usage_count"]
        if type(val) is not int or not 0 <= val <= MAX_MEMORY_COUNTER:
            errors.append(f"{prefix} field 'usage_count' must be a non-negative integer")

    for list_field in ("domains", "related_to"):
        if list_field in entry:
            val = entry[list_field]
            if not isinstance(val, list):
                errors.append(f"{prefix} field '{list_field}' must be a list")
            elif not all(isinstance(t, str) for t in val):
                errors.append(
                    f"{prefix} field '{list_field}' must contain only strings"
                )

    if "feedback_observations" in entry:
        observations = entry["feedback_observations"]
        if not isinstance(observations, dict):
            errors.append(
                f"{prefix} field 'feedback_observations' must be a dictionary"
            )
        elif len(observations) > _MAX_FEEDBACK_OBSERVATIONS:
            errors.append(
                f"{prefix} field 'feedback_observations' exceeds the bounded limit"
            )
        elif not all(
            isinstance(digest, str)
            and _FEEDBACK_OBSERVATION_DIGEST_RE.fullmatch(digest)
            and isinstance(result, str)
            and result in _FEEDBACK_OBSERVATION_RESULTS
            for digest, result in observations.items()
        ):
            errors.append(
                f"{prefix} field 'feedback_observations' contains an invalid receipt"
            )

    for int_field in (
        "retrieval_count",
        "injection_count",
        "success_count",
        "failure_count",
        "corroborated_success_count",
        "corroborated_failure_count",
    ):
        if int_field in entry:
            value = entry[int_field]
            if (
                type(value) is not int
                or not 0 <= value <= MAX_MEMORY_COUNTER
            ):
                errors.append(
                    f"{prefix} field '{int_field}' must be a non-negative integer"
                )

    for float_field in (
        "last_accessed",
        "last_used",
        "usefulness_score",
        "corroborated_usefulness_score",
    ):
        if float_field in entry:
            value = entry[float_field]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or (
                    float_field in {"last_accessed", "last_used"}
                    and not 0 <= value <= MAX_MEMORY_TIMESTAMP
                )
                or (
                    float_field
                    in {
                        "usefulness_score",
                        "corroborated_usefulness_score",
                    }
                    and not (
                        MIN_MEMORY_USEFULNESS_SCORE
                        <= value
                        <= MAX_MEMORY_USEFULNESS_SCORE
                    )
                )
            ):
                errors.append(f"{prefix} field '{float_field}' must be finite")

    for dict_field in ("metadata", "provenance"):
        if dict_field in entry and not isinstance(entry[dict_field], dict):
            errors.append(f"{prefix} field '{dict_field}' must be a dictionary")
        elif (
            authority_schema_version is not None
            and dict_field in entry
            and not _fits_persistence_depth(entry[dict_field])
        ):
            errors.append(
                f"{prefix} field '{dict_field}' exceeds persistence depth"
            )

    for str_field in (
        "tier",
        "source",
        "lifecycle_status",
        "tier_reason",
        "safety_status",
        "safety_reason",
        "approval_status",
        "approval_content_hash",
        "approval_reason",
        "approval_actor",
        "approval_policy",
    ):
        if str_field in entry and not isinstance(entry[str_field], str):
            errors.append(f"{prefix} field '{str_field}' must be a string")

    for nullable_float_field in ("deprecated_at", "approval_decided_at"):
        if entry.get(nullable_float_field) is not None:
            value = entry[nullable_float_field]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= MAX_MEMORY_TIMESTAMP
            ):
                errors.append(
                    f"{prefix} field '{nullable_float_field}' must be a finite timestamp or null"
                )

    if authority_schema_version is not None:
        for success_field, failure_field, score_field in (
            ("success_count", "failure_count", "usefulness_score"),
            (
                "corroborated_success_count",
                "corroborated_failure_count",
                "corroborated_usefulness_score",
            ),
        ):
            success_value = entry.get(success_field)
            failure_value = entry.get(failure_field)
            score_value = entry.get(score_field)
            if (
                type(success_value) is int
                and type(failure_value) is int
                and isinstance(score_value, (int, float))
                and not isinstance(score_value, bool)
                and math.isfinite(score_value)
            ):
                expected_score = _derived_usefulness_score(
                    success_value,
                    failure_value,
                )
                if not math.isclose(
                    float(score_value),
                    expected_score,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                ):
                    errors.append(
                        f"{prefix} field '{score_field}' disagrees with its counters"
                    )

    if "curator_locked" in entry and not isinstance(entry["curator_locked"], bool):
        errors.append(f"{prefix} field 'curator_locked' must be a boolean")

    if authority_schema_version is not None:
        enum_contracts = {
            "tier": _CURRENT_VALID_MEMORY_TIERS,
            "lifecycle_status": _VALID_LIFECYCLE_STATUSES,
            "safety_status": _VALID_SAFETY_STATUSES,
            "approval_status": _VALID_APPROVAL_STATUSES,
            "approval_policy": _CURRENT_VALID_APPROVAL_POLICIES,
        }
        for field_name, allowed in enum_contracts.items():
            value = entry.get(field_name)
            if not isinstance(value, str) or value not in allowed:
                errors.append(
                    f"{prefix} field '{field_name}' has an invalid value"
                )
        digest_fields = ["approval_content_hash"]
        if sealed_schema:
            digest_fields.append("authority_state_digest")
        for digest_field in digest_fields:
            value = entry.get(digest_field)
            if not isinstance(value, str) or _SHA256_HEX_RE.fullmatch(value) is None:
                errors.append(
                    f"{prefix} field '{digest_field}' must be a SHA-256 digest"
                )
        if sealed_schema and _CURRENT_ENTRY_REQUIRED_FIELDS.issubset(entry):
            try:
                expected_digest = _authority_state_digest_for_data(entry)
            except (TypeError, ValueError, UnicodeEncodeError):
                errors.append(f"{prefix} authority state cannot be digested")
            else:
                if entry.get("authority_state_digest") != expected_digest:
                    errors.append(f"{prefix} authority state digest does not match")

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Corrupted data recovery
# ---------------------------------------------------------------------------

def _recover_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return valid entries from one already bounded, parsed authority."""
    entries = data.get("entries", [])
    valid_entries: list[dict[str, Any]] = []
    recovered_count = 0

    schema_version = data.get("schemaVersion")
    versioned_schema = schema_version in (
        {CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION}
        | set(MIGRATABLE_MEMORY_AUTHORITY_SCHEMA_VERSIONS)
    )
    seen_ids: set[str] = set()
    for idx, entry_data in enumerate(entries):
        entry_valid, _ = _validate_entry(
            entry_data,
            idx,
            authority_schema_version=(
                schema_version if versioned_schema else None
            ),
        )
        entry_id = entry_data.get("id") if isinstance(entry_data, dict) else None
        content = (
            entry_data.get("content") if isinstance(entry_data, dict) else None
        )
        if (
            not isinstance(content, str)
            or not content.strip()
            or not isinstance(entry_id, str)
            or entry_id in seen_ids
        ):
            entry_valid = False
        if not entry_valid:
            logger.warning("Skipping corrupted entry at index %d", idx)
        else:
            valid_entries.append(entry_data)
            seen_ids.add(entry_id)
            recovered_count += 1

    total = len(entries)
    logger.info(
        "Recovery complete: %d/%d entries recovered", recovered_count, total
    )
    return valid_entries




# ---------------------------------------------------------------------------
# TF-IDF search utilities
# ---------------------------------------------------------------------------

# Tokenize text into lowercase words, individual CJK chars, and CJK bigrams
_WORD_RE = re.compile(r'[a-zA-Z0-9]+|[\u4e00-\u9fff]')
_CJK_BIGRAM_RE = re.compile(r'[\u4e00-\u9fff]{2}')

# Common code terminology expansions (bidirectional)
_CODE_TERM_EXPANSIONS: dict[str, list[str]] = {
    "函数": ["function", "func", "method"],
    "function": ["函数", "func", "method"],
    "func": ["函数", "function", "method"],
    "method": ["函数", "function", "func"],
    "类": ["class", "type"],
    "class": ["类", "type"],
    "type": ["类", "class"],
    "变量": ["variable", "var"],
    "variable": ["变量", "var"],
    "var": ["变量", "variable"],
    "参数": ["parameter", "param", "argument", "arg"],
    "parameter": ["参数", "param", "argument"],
    "param": ["参数", "parameter", "arg"],
    "argument": ["参数", "parameter", "arg"],
    "属性": ["attribute", "attr", "property", "prop"],
    "attribute": ["属性", "attr", "property"],
    "property": ["属性", "attr", "prop"],
    "接口": ["interface"],
    "interface": ["接口"],
    "模块": ["module"],
    "module": ["模块"],
    "包": ["package"],
    "package": ["包"],
    "方法": ["method", "function"],
    "对象": ["object", "obj"],
    "object": ["对象", "obj"],
    "继承": ["inherit", "inheritance", "extends"],
    "inherit": ["继承"],
    "多态": ["polymorphism"],
    "封装": ["encapsulation", "encapsulate"],
    "异常": ["exception", "error"],
    "exception": ["异常"],
    "error": ["错误", "异常"],
    "错误": ["error", "bug"],
    "bug": ["错误", "bug", "缺陷"],
    "循环": ["loop", "iteration", "iterate"],
    "loop": ["循环"],
    "条件": ["condition"],
    "condition": ["条件"],
    "数组": ["array"],
    "array": ["数组"],
    "列表": ["list"],
    "list": ["列表"],
    "字典": ["dict", "dictionary", "map"],
    "dict": ["字典", "dictionary"],
    "dictionary": ["字典", "dict"],
    "map": ["字典", "映射"],
    "映射": ["map"],
    "集合": ["set"],
    "set": ["集合"],
    "字符串": ["string", "str"],
    "string": ["字符串"],
    "整数": ["int", "integer"],
    "integer": ["整数"],
    "浮点": ["float"],
    "float": ["浮点"],
    "布尔": ["bool", "boolean"],
    "boolean": ["布尔"],
    "同步": ["sync", "synchronous"],
    "异步": ["async", "asynchronous"],
    "async": ["异步"],
    "回调": ["callback"],
    "callback": ["回调"],
    "事件": ["event"],
    "event": ["事件"],
    "装饰器": ["decorator"],
    "decorator": ["装饰器"],
    "生成器": ["generator"],
    "generator": ["生成器"],
    "迭代器": ["iterator"],
    "iterator": ["迭代器"],
    "测试": ["test", "testing"],
    "test": ["测试"],
    "调试": ["debug", "debugging"],
    "debug": ["调试"],
    "配置": ["config", "configuration"],
    "config": ["配置"],
    "数据库": ["database", "db"],
    "database": ["数据库", "db"],
    "缓存": ["cache"],
    "cache": ["缓存"],
    "队列": ["queue"],
    "queue": ["队列"],
    "栈": ["stack"],
    "stack": ["栈"],
    "树": ["tree"],
    "tree": ["树"],
    "图": ["graph"],
    "graph": ["图"],
    "搜索": ["search"],
    "search": ["搜索"],
    "排序": ["sort", "sorting"],
    "sort": ["排序"],
    "文件": ["file"],
    "file": ["文件"],
    "路径": ["path"],
    "path": ["路径"],
    "网络": ["network"],
    "network": ["网络"],
    "请求": ["request"],
    "request": ["请求"],
    "响应": ["response"],
    "response": ["响应"],
}


def _expand_query_terms(terms: list[str], active_domains: list[str] | None = None) -> list[str]:
    """Expand query terms using code terminology + domain-specific dictionaries."""
    expanded = list(terms)
    for term in terms:
        if term in _CODE_TERM_EXPANSIONS:
            expanded.extend(_CODE_TERM_EXPANSIONS[term])
    # Domain-specific expansions
    if active_domains:
        for domain in active_domains:
            domain_dict = _DOMAIN_TERM_EXPANSIONS.get(domain, {})
            for term in terms:
                if term in domain_dict:
                    expanded.extend(domain_dict[term])
    return expanded


# ── Domain-specific term expansions ─────────────────────────────────

_DOMAIN_TERM_EXPANSIONS: dict[str, dict[str, list[str]]] = {
    "frontend": {
        "component": ["组件", "widget", "control", "element"],
        "组件": ["component", "widget", "control"],
        "form": ["表单", "input", "field"],
        "表单": ["form", "input", "field"],
        "style": ["样式", "css", "theme", "design"],
        "样式": ["style", "css", "theme"],
        "css": ["样式", "style", "theme", "tailwind"],
        "render": ["渲染", "display", "paint"],
        "渲染": ["render", "display"],
        "state": ["状态", "store", "context"],
        "状态": ["state", "store"],
        "hook": ["hooks", "钩子"],
        "router": ["路由", "navigation"],
        "路由": ["router", "navigation", "route"],
        "button": ["按钮", "btn"],
        "modal": ["弹窗", "dialog", "popup"],
        "layout": ["布局", "grid", "flex"],
        "布局": ["layout", "grid", "flexbox"],
        "animation": ["动画", "transition", "motion"],
        "event": ["事件", "handler", "listener"],
        "props": ["属性", "properties", "parameters"],
        "dom": ["文档", "document", "node", "element"],
        "responsive": ["响应式", "adaptive", "mobile"],
        "typescript": ["ts", "type"],
    },
    "backend": {
        "api": ["端点", "endpoint", "路由", "route", "handler"],
        "endpoint": ["端点", "api", "路由"],
        "route": ["路由", "path", "endpoint", "api"],
        "auth": ["认证", "鉴权", "login", "token", "jwt", "oauth"],
        "认证": ["auth", "authentication", "login"],
        "middleware": ["中间件", "interceptor", "filter"],
        "中间件": ["middleware", "interceptor"],
        "request": ["请求", "req"],
        "response": ["响应", "res", "reply"],
        "server": ["服务器", "服务端", "host"],
        "服务器": ["server", "host"],
        "queue": ["队列", "message", "mq", "worker"],
        "队列": ["queue", "message", "worker"],
        "cache": ["缓存", "redis", "memcache"],
        "缓存": ["cache", "redis"],
        "cron": ["定时", "schedule", "job", "task"],
        "定时": ["cron", "schedule", "timer"],
        "log": ["日志", "logging", "trace"],
        "日志": ["log", "logging"],
        "validate": ["校验", "验证", "sanitize", "check"],
        "校验": ["validate", "validation", "check"],
        "rate limit": ["限流", "throttle", "quota"],
        "限流": ["rate limit", "throttle"],
        "serialize": ["序列化", "marshal", "json"],
        "序列化": ["serialize", "marshal"],
    },
    "database": {
        "migration": ["迁移", "schema change", "ddl", "alembic", "flyway"],
        "迁移": ["migration", "schema change"],
        "schema": ["模式", "结构", "ddl", "table def"],
        "query": ["查询", "select", "sql"],
        "查询": ["query", "select", "read"],
        "index": ["索引", "btree", "hash"],
        "索引": ["index", "lookup"],
        "transaction": ["事务", "commit", "rollback", "acid"],
        "事务": ["transaction", "commit"],
        "connection": ["连接", "pool", "session"],
        "连接": ["connection", "pool"],
        "postgres": ["postgresql", "pg"],
        "orm": ["prisma", "typeorm", "sequelize", "drizzle", "sqlalchemy"],
        "backup": ["备份", "dump", "restore"],
        "备份": ["backup", "dump"],
        "replica": ["副本", "standby", "slave"],
        "partition": ["分区", "shard", "split"],
    },
    "devops": {
        "deploy": ["部署", "release", "ship"],
        "部署": ["deploy", "release"],
        "docker": ["容器", "container", "image"],
        "容器": ["docker", "container"],
        "ci": ["持续集成", "pipeline", "build"],
        "pipeline": ["流水线", "ci/cd", "workflow"],
        "monitor": ["监控", "alert", "observe", "metrics"],
        "监控": ["monitor", "alert", "metrics"],
        "secret": ["密钥", "credentials", "env"],
        "密钥": ["secret", "credentials", "token"],
        "kubernetes": ["k8s", "pod", "cluster"],
        "k8s": ["kubernetes", "cluster"],
        "nginx": ["反向代理", "proxy", "gateway"],
        "terraform": ["基础设施", "infrastructure", "iac"],
        "log": ["日志", "logging", "收集", "aggregate"],
        "backup": ["备份", "snapshot", "restore"],
    },
    "testing": {
        "test": ["测试", "spec", "assert"],
        "mock": ["模拟", "stub", "fake", "spy"],
        "模拟": ["mock", "stub", "fake"],
        "assert": ["断言", "expect", "should"],
        "断言": ["assert", "expect"],
        "coverage": ["覆盖率", "cover"],
        "e2e": ["端到端", "end-to-end", "integration"],
        "unit": ["单元", "unit test"],
        "fixture": ["夹具", "setup", "teardown"],
        "regression": ["回归", "replay"],
    },
}


@functools.lru_cache(maxsize=1024)
def _tokenize(text: str) -> list[str]:
    """Tokenize text into words for TF-IDF scoring.

    Handles alphanumeric words, individual CJK characters, and CJK bigrams
    for better Chinese text semantic matching.
    """
    tokens = [w.lower() for w in _WORD_RE.findall(text)]
    cjk_bigrams = [match.lower() for match in _CJK_BIGRAM_RE.findall(text)]
    return tokens + cjk_bigrams


# BM25 parameters
_BM25_K1 = 1.5  # Term frequency scaling
_BM25_B = 0.75  # Document length normalization


def _compute_tf(tokens: list[str]) -> dict[str, float]:
    """Compute term frequency for a list of tokens."""
    if not tokens:
        return {}
    counts = Counter(tokens)
    total = len(tokens)
    return {term: count / total for term, count in counts.items()}


def _compute_idf(documents: list[list[str]]) -> dict[str, float]:
    """Compute inverse document frequency across documents.

    Uses smoothed IDF formula: log((N + 1) / (df + 1)) + 1
    """
    n = len(documents)
    if n == 0:
        return {}
    doc_freq: dict[str, int] = {}
    for doc_tokens in documents:
        seen = set(doc_tokens)
        for term in seen:
            doc_freq[term] = doc_freq.get(term, 0) + 1
    return {
        term: math.log((n + 1) / (df + 1)) + 1
        for term, df in doc_freq.items()
    }


def _compute_avgdl(documents: list[list[str]]) -> float:
    """Compute average document length."""
    if not documents:
        return 0.0
    return sum(len(doc) for doc in documents) / len(documents)


def _bm25_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    idf: dict[str, float],
    avgdl: float,
    *,
    k1: float = _BM25_K1,
    b: float = _BM25_B,
) -> float:
    """Compute Okapi BM25 score between query and document.

    Formula:
        score(q,d) = sum(IDF(qi) * (tf(qi,d) * (k1 + 1)) /
                         (tf(qi,d) + k1 * (1 - b + b * |d|/avgdl)))
    """
    if not query_tokens or not doc_tokens or avgdl == 0:
        return 0.0

    doc_len = len(doc_tokens)
    tf_doc = _compute_tf(doc_tokens)
    total_tokens = doc_len

    score = 0.0
    for term in set(query_tokens):
        if term not in idf:
            continue
        tf = tf_doc.get(term, 0.0)
        if tf == 0:
            continue
        numerator = tf * (k1 + 1)
        denominator = tf + k1 * (1 - b + b * (total_tokens / avgdl))
        score += idf[term] * (numerator / denominator)

    return score


def _tfidf_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    idf: dict[str, float],
    avgdl: float = 0.0,
) -> float:
    """Compute BM25 score between query and document.

    Note: This function name is kept for backward compatibility but now
    uses BM25 scoring internally for better short-text ranking.
    """
    return _bm25_score(query_tokens, doc_tokens, idf, avgdl)


def get_tfidf_keywords(text: str, top_n: int = 10) -> list[tuple[str, float]]:
    """Extract top N most important terms from text using TF scores.

    Useful for auto-categorization and understanding key topics in text.

    Args:
        text: Input text to analyze
        top_n: Number of top keywords to return

    Returns:
        List of (term, tf_score) tuples sorted by importance
    """
    tokens = _tokenize(text)
    if not tokens:
        return []
    tf = _compute_tf(tokens)
    sorted_terms = sorted(tf.items(), key=lambda x: x[1], reverse=True)
    return sorted_terms[:top_n]


# ---------------------------------------------------------------------------
# Auto-classification heuristics
# ---------------------------------------------------------------------------

_CLASSIFICATION_RULES: list[tuple[str, list[str], list[str]]] = [
    ("architecture", ["architecture", "design", "pattern", "api", "rest", "backend", "service", "架构", "设计", "模式"]),
    ("code-pattern", ["function", "method", "def", "class", "函数", "方法", "类"]),
    ("testing", ["test", "assert", "pytest", "unit", "测试", "断言"]),
    ("configuration", ["config", "settings", "env", "配置", "设置", "环境"]),
    ("workflow", ["git", "commit", "branch", "merge", "工作流", "分支", "合并"]),
    ("security", ["security", "auth", "permission", "安全", "认证", "权限"]),
    ("performance", ["performance", "optimization", "benchmark", "性能", "优化", "基准"]),
    ("convention", ["convention", "style", "naming", "规范", "风格", "命名"]),
]


def _auto_classify_content(content: str) -> tuple[str, list[str]]:
    """Analyze content and return (category, tags) using keyword heuristics.

    Supports both English and Chinese keywords. Returns "general" category
    with empty tags if no classification rules match.

    Args:
        content: Text content to classify

    Returns:
        Tuple of (category, tags) - e.g., ("architecture", ["design-pattern"])
    """
    content_lower = content.lower()
    category_scores: dict[str, int] = {}
    matched_tags: list[str] = []

    category_to_tags = {
        "architecture": ["design-pattern"],
        "code-pattern": ["function"],
        "testing": ["test"],
        "configuration": ["config"],
        "workflow": ["git"],
        "security": ["security"],
        "performance": ["optimization"],
        "convention": ["style"],
    }

    for category, keywords in (
        (rule[0], rule[1]) for rule in _CLASSIFICATION_RULES
    ):
        score = sum(1 for kw in keywords if kw in content_lower)
        if score > 0:
            category_scores[category] = score
            matched_tags.extend(category_to_tags.get(category, []))

    if not category_scores:
        return "general", []

    best_category = max(category_scores, key=category_scores.get)
    return best_category, matched_tags


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class MemoryScope(str, Enum):
    """Memory scope levels."""
    USER = "user"       # Cross-project, ~/.mini-code/memory/
    PROJECT = "project" # Project-shared, .mini-code-memory/
    LOCAL = "local"     # Project-local, .mini-code-memory-local/


class MemoryApprovalPolicy(str, Enum):
    """Typed authority policy for durable Memory creation."""

    USER_EXPLICIT = "user_explicit"
    USER_REVIEW_REQUIRED = "user_review_required"
    # Lessons carrying independently verified durable signals (verified
    # recovery, verified approach, explicit user correction) are approved on
    # write: the verification chain, not a human click, is the authority.
    # Users can still reject them afterwards through the normal review flow.
    AUTO_APPROVE_VERIFIED = "auto_approve_verified"


class MemoryTier(str, Enum):
    """Memory tier for multi-level storage architecture.

    Inspired by human memory models (Atkinson-Shiffrin) and Letta/MemGPT:
      WORKING    → current session, full detail, fast access
      SHORT_TERM → recent (< 7 days), full detail
      LONG_TERM  → consolidated (< 30 days), compressed
      ARCHIVAL   → permanent, heavily summarized
    """
    WORKING = "working"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    ARCHIVAL = "archival"


_VALID_SCOPES = {m.value for m in MemoryScope}
_ACTIVE_LIFECYCLE = "active"
_SAFETY_SAFE = "safe"
_SAFETY_SUSPICIOUS = "suspicious"
_SAFETY_UNSAFE = "unsafe"
_VALID_SAFETY_STATUSES = {_SAFETY_SAFE, _SAFETY_SUSPICIOUS, _SAFETY_UNSAFE}

_APPROVAL_APPROVED = "approved"
_APPROVAL_PENDING = "pending"
_APPROVAL_REJECTED = "rejected"
_VALID_APPROVAL_STATUSES = {
    _APPROVAL_APPROVED,
    _APPROVAL_PENDING,
    _APPROVAL_REJECTED,
}
_CORROBORATED_FAILURE_QUARANTINE_THRESHOLD = 2
# Independent later traces re-observing the same pending claim before it is
# auto-promoted to approved. Mirrors the quarantine threshold's philosophy:
# corroboration across independent observations, never single-trace trust.
RECURRENCE_AUTO_APPROVE_THRESHOLD = 3
# Independent personal-facet observations before a USER profile candidate is
# flagged as review-ready.  Unlike lesson recurrence, reaching the threshold
# never auto-approves: a USER facet flows cross-project, so a human confirms
# it exactly once.
USER_FACET_REVIEW_THRESHOLD = 3
USER_PROFILE_CATEGORY = "user_profile"
_MAX_FEEDBACK_OBSERVATIONS = 256
_FEEDBACK_OBSERVATION_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_FEEDBACK_OBSERVATION_RESULTS = frozenset({"success", "failure"})
_ORDINARY_FEEDBACK_SOURCES = frozenset(
    {"task_outcome", "explicit_run_outcome", "contribution_review"}
)
_CORROBORATED_FEEDBACK_SOURCES = frozenset(
    {
        "independent_verification",
        "explicit_user_accept",
        "explicit_user_correction",
        "explicit_user_reject",
    }
)

# Backward-compatible names for older callers that import these constants.
_SAFETY_ACTIVE = _SAFETY_SAFE
_SAFETY_REJECTED = _SAFETY_UNSAFE
_NON_INJECTABLE_LIFECYCLES = _VALID_LIFECYCLE_STATUSES - {"active"}


@dataclass(frozen=True)
class MemorySafetyResult:
    """Result of scanning a proposed durable memory write."""

    status: str
    reason: str = ""
    risk: str = "low"

    @property
    def allowed(self) -> bool:
        return self.status == _SAFETY_SAFE

    @property
    def needs_approval(self) -> bool:
        return self.status == _SAFETY_SUSPICIOUS

    @property
    def rejected(self) -> bool:
        return self.status == _SAFETY_UNSAFE


@dataclass(frozen=True, slots=True)
class MemoryApprovalMutation:
    """Typed result of one durable pending-Memory decision."""

    memory_id: str
    scope: MemoryScope
    status: str
    decision: str
    decision_accepted: bool
    updated_at: float
    compatibility_message: str = ""


def _normalize_memory_content(content: str) -> str:
    """Normalize memory content for duplicate detection."""
    return re.sub(r"\s+", " ", content.strip().lower())


_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_HOMOGLYPH_TRANSLATION = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
    "а": "a", "е": "e", "і": "i", "ј": "j", "о": "o", "р": "p",
    "с": "c", "ѕ": "s", "у": "y", "х": "x",
    "Α": "A", "Β": "B", "Ε": "E", "Η": "H", "Ι": "I", "Κ": "K",
    "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Χ": "X",
    "Υ": "Y", "Ζ": "Z",
    "α": "a", "β": "b", "ε": "e", "η": "h", "ι": "i", "κ": "k",
    "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "χ": "x", "υ": "y",
})


def _normalize_safety_text(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content).translate(_HOMOGLYPH_TRANSLATION)
    normalized = _ZERO_WIDTH_RE.sub("", normalized)
    return _normalize_memory_content(normalized)


def _collapse_spaced_ascii_letters(text: str) -> str:
    """Collapse simple letter-spaced obfuscations like 'i g n o r e'."""
    return re.sub(
        r"(?i)(?<![a-z])(?:[a-z]\s+){2,}[a-z](?![a-z])",
        lambda match: re.sub(r"\s+", "", match.group(0)),
        text,
    )


def _decoded_base64_fragments(text: str) -> list[str]:
    decoded: list[str] = []
    for match in re.findall(r"(?<![A-Za-z0-9+/=])([A-Za-z0-9+/]{24,}={0,2})(?![A-Za-z0-9+/=])", text):
        if len(match) % 4 != 0:
            continue
        try:
            raw = base64.b64decode(match, validate=True)
            if not raw or b"\x00" in raw[:64]:
                continue
            decoded_text = raw[:2000].decode("utf-8", errors="ignore")
        except (binascii.Error, ValueError):
            continue
        if decoded_text.strip():
            decoded.append(decoded_text)
    return decoded


def _memory_safety_variants(content: str) -> list[str]:
    base = _normalize_safety_text(content)
    variants = [base, _collapse_spaced_ascii_letters(base)]
    for decoded in _decoded_base64_fragments(content):
        normalized = _normalize_safety_text(decoded)
        variants.append(normalized)
        variants.append(_collapse_spaced_ascii_letters(normalized))
    deduped: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        if variant and variant not in seen:
            seen.add(variant)
            deduped.append(variant)
    return deduped


def _memory_safety_variant_records(content: str) -> list[tuple[str, bool]]:
    """Return normalized variants plus whether each came from encoded content."""
    records: list[tuple[str, bool]] = []
    seen: set[str] = set()

    base = _normalize_safety_text(content)
    for variant in (base, _collapse_spaced_ascii_letters(base)):
        if variant and variant not in seen:
            seen.add(variant)
            records.append((variant, False))

    for decoded in _decoded_base64_fragments(content):
        normalized = _normalize_safety_text(decoded)
        for variant in (normalized, _collapse_spaced_ascii_letters(normalized)):
            if variant and variant not in seen:
                seen.add(variant)
                records.append((variant, True))
    return records


_UNTRUSTED_FRAMING_RE = re.compile(
    r"\b("
    r"quote|quoted|example|sample|fixture|test case|unit test|regression test|"
    r"incident|log|document|documentation|research note|security research|"
    r"prompt injection|malicious input|attack sample|transcript"
    r")\b|"
    r"(引用|示例|样本|测试|日志|文档|研究|安全研究|攻击样本|恶意输入|转录)",
    flags=re.I,
)


def _has_untrusted_framing(content: str, normalized_variant: str) -> bool:
    """Detect text that discusses or quotes risky instructions instead of issuing them."""
    raw_normalized = _normalize_safety_text(content)
    if _UNTRUSTED_FRAMING_RE.search(content) or _UNTRUSTED_FRAMING_RE.search(raw_normalized):
        return True
    # Markdown quote or fenced block around the matched instruction is not proof
    # of safety, but it is enough to require human review rather than reject.
    if re.search(r"(^|\n)\s*(>|```)", content):
        return True
    return _UNTRUSTED_FRAMING_RE.search(normalized_variant) is not None


def _classified_safety_result(
    content: str,
    variant: str,
    reason: str,
    *,
    encoded: bool = False,
) -> MemorySafetyResult:
    if encoded:
        return MemorySafetyResult(
            _SAFETY_SUSPICIOUS,
            f"encoded content may contain unsafe instructions: {reason}",
            "medium",
        )
    if _has_untrusted_framing(content, variant):
        return MemorySafetyResult(
            _SAFETY_SUSPICIOUS,
            f"quoted or documented unsafe instruction requires approval: {reason}",
            "medium",
        )
    return MemorySafetyResult(_SAFETY_UNSAFE, reason, "high")


def _coerce_scope(value: Any, default: MemoryScope = MemoryScope.USER) -> MemoryScope:
    if isinstance(value, MemoryScope):
        return value
    try:
        return MemoryScope(str(value))
    except (TypeError, ValueError):
        return default


def _coerce_tier(value: Any, default: MemoryTier = MemoryTier.SHORT_TERM) -> MemoryTier:
    if isinstance(value, MemoryTier):
        return value
    try:
        return MemoryTier(str(value))
    except (TypeError, ValueError):
        return default


def _safe_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if isinstance(v, str)]


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_feedback_observations(value: Any) -> dict[str, str]:
    """Load only bounded, content-free feedback delivery receipts."""
    if not isinstance(value, dict):
        return {}
    valid = [
        (digest, result)
        for digest, result in value.items()
        if isinstance(digest, str)
        and _FEEDBACK_OBSERVATION_DIGEST_RE.fullmatch(digest)
        and isinstance(result, str)
        and result in _FEEDBACK_OBSERVATION_RESULTS
    ]
    return dict(valid[-_MAX_FEEDBACK_OBSERVATIONS:])


def _normalize_delivery_id(value: str | None, *, label: str) -> str | None:
    """Validate a caller-owned retry identity without persisting raw text."""

    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    normalized = value.strip()
    if len(normalized.encode("utf-8")) > 256:
        raise ValueError(f"{label} is too long")
    return normalized


def _feedback_observation_digest(
    observation_id: str | None,
    *,
    channel: str,
) -> str | None:
    """Return a content-free receipt key for one stable feedback delivery."""
    normalized = _normalize_delivery_id(
        observation_id,
        label="feedback observation_id",
    )
    if normalized is None:
        return None
    envelope = f"minicode-memory-feedback-v1\0{channel}\0{normalized}"
    return hashlib.sha256(envelope.encode("utf-8")).hexdigest()


def _operation_observation_receipt(
    observation_id: str | None,
    *,
    channel: str,
    payload: object,
) -> tuple[str, str] | None:
    """Bind one retry identity to an exact content-free operation payload."""

    normalized = _normalize_delivery_id(
        observation_id,
        label="Memory operation observation_id",
    )
    if normalized is None:
        return None
    observation_envelope = (
        f"minicode-memory-observation-v1\0{channel}\0{normalized}"
    )
    try:
        payload_bytes = json.dumps(
            {
                "schema": "memory_observation_payload_v1",
                "channel": channel,
                "payload": payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError("Memory operation payload is invalid") from error
    return (
        hashlib.sha256(observation_envelope.encode("utf-8")).hexdigest(),
        hashlib.sha256(payload_bytes).hexdigest(),
    )


def _claim_feedback_observation(
    entry: "MemoryEntry",
    digest: str | None,
    *,
    success: bool,
) -> bool:
    """Claim one receipt, returning false for an idempotent replay."""
    if digest is None:
        return True
    result = "success" if success else "failure"
    previous = entry.feedback_observations.get(digest)
    if previous is not None:
        if previous != result:
            raise ValueError("feedback observation conflicts with its first result")
        return False
    entry.feedback_observations[digest] = result
    while len(entry.feedback_observations) > _MAX_FEEDBACK_OBSERVATIONS:
        oldest = next(iter(entry.feedback_observations))
        del entry.feedback_observations[oldest]
    return True


def _expected_memory_versions(
    value: object,
) -> dict[str, frozenset[str]] | None:
    """Validate optional full-authority fences for feedback mutation."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise ValueError("expected Memory versions must be a list")
    by_id: dict[str, set[str]] = {}
    for raw_version in value:
        if not isinstance(raw_version, (list, tuple)) or len(raw_version) != 2:
            raise ValueError("expected Memory version is invalid")
        entry_id, digest = raw_version
        if (
            not isinstance(entry_id, str)
            or not entry_id
            or len(entry_id) > 128
            or not isinstance(digest, str)
            or not _FEEDBACK_OBSERVATION_DIGEST_RE.fullmatch(digest)
        ):
            raise ValueError("expected Memory version is invalid")
        by_id.setdefault(entry_id, set()).add(digest)
    return {
        entry_id: frozenset(digests)
        for entry_id, digests in by_id.items()
    }


def _reset_version_scoped_observations(
    entry: "MemoryEntry",
) -> dict[str, int | float]:
    """Reset evidence that belongs to the previous authority version."""
    previous: dict[str, int | float] = {
        "retrieval_count": entry.retrieval_count,
        "injection_count": entry.injection_count,
        "usage_count": entry.usage_count,
        "success_count": entry.success_count,
        "failure_count": entry.failure_count,
        "usefulness_score": entry.usefulness_score,
        "corroborated_success_count": entry.corroborated_success_count,
        "corroborated_failure_count": entry.corroborated_failure_count,
        "corroborated_usefulness_score": entry.corroborated_usefulness_score,
        "feedback_observation_count": len(entry.feedback_observations),
        "last_accessed": entry.last_accessed,
    }
    entry.retrieval_count = 0
    entry.injection_count = 0
    entry.usage_count = 0
    entry.success_count = 0
    entry.failure_count = 0
    entry.usefulness_score = 0.0
    entry.corroborated_success_count = 0
    entry.corroborated_failure_count = 0
    entry.corroborated_usefulness_score = 0.0
    entry.feedback_observations = {}
    entry.last_used = 0.0
    entry.last_accessed = 0.0
    return previous


def _normalize_safety_status(value: Any) -> str:
    status = str(value or _SAFETY_SAFE).lower()
    if status in {"active", "allowed", "approved", _SAFETY_SAFE}:
        return _SAFETY_SAFE
    if status in {"pending", "review", _SAFETY_SUSPICIOUS}:
        return _SAFETY_SUSPICIOUS
    if status in {"rejected", "blocked", "invalid", _SAFETY_UNSAFE}:
        return _SAFETY_UNSAFE
    return _SAFETY_SUSPICIOUS


def _normalize_approval_status(value: Any) -> str:
    status = str(value or _APPROVAL_PENDING).lower()
    if status in {"active", "approved", "allow", "allowed"}:
        return _APPROVAL_APPROVED
    if status in {"pending", "review", "needs_review"}:
        return _APPROVAL_PENDING
    if status in {"rejected", "blocked", "invalid", "unsafe"}:
        return _APPROVAL_REJECTED
    return _APPROVAL_PENDING


def _json_default(value: Any) -> str:
    return repr(value)


def _approval_hash_payload(entry: "MemoryEntry") -> dict[str, Any]:
    """Safety-sensitive fields bound to a human approval decision."""
    return {
        "content": entry.content,
        "category": entry.category,
        "tags": sorted(entry.tags),
        "domains": sorted(entry.domains),
        "source": entry.source,
        "provenance": entry.provenance,
        "metadata": entry.metadata,
    }


def _approval_hash_for_entry(entry: "MemoryEntry") -> str:
    payload = json.dumps(
        _approval_hash_payload(entry),
        sort_keys=True,
        ensure_ascii=False,
        default=_json_default,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assess_memory_safety(content: str, source: str = "") -> MemorySafetyResult:
    """Scan durable memory content before it can become injectable context.

    The gate intentionally looks for instruction intent, not isolated keywords,
    so benign notes like "test prompt injection detection" remain writable.
    """
    variants = _memory_safety_variant_records(content)
    if not variants:
        return MemorySafetyResult(_SAFETY_UNSAFE, "empty memory content", "high")

    checks: list[tuple[str, str]] = [
        (
            r"\b(ignore|disregard|override|bypass|forget)\b.{0,80}"
            r"\b(system|developer|previous|above|prior)\b.{0,50}"
            r"\b(instruction|instructions|message|prompt|rules?)\b",
            "attempts to override higher-priority instructions",
        ),
        (
            r"\b(system|developer)\s*:\s*"
            r"(ignore|override|bypass|forget|reveal|leak|dump|print)\b",
            "masquerades as a higher-priority instruction",
        ),
        (
            r"\b(reveal|print|dump|exfiltrate|leak|send|upload)\b.{0,90}"
            r"\b(system prompt|developer message|secret|api key|token|credential|"
            r"password|private key|env(?:ironment)? variables?)\b",
            "requests disclosure or exfiltration of secrets/prompts",
        ),
        (
            r"\b(from now on|in all future sessions|forever)\b.{0,90}"
            r"\b(always|must|never|ignore|execute|run|send|upload)\b",
            "tries to install unconditional future behavior",
        ),
        (
            r"\b(always|must|never)\b.{0,80}"
            r"\b(ignore|override|bypass|disregard)\b",
            "tries to install unconditional instruction override behavior",
        ),
        (
            r"\b(curl|wget|webhook|http post|post to|upload|send)\b.{0,90}"
            r"\b(secret|token|api key|credential|password|private key|env)\b",
            "directs tool-based secret exfiltration",
        ),
        (
            r"\b(act as|pretend to be|you are now)\b.{0,50}"
            r"\b(system|developer|root|policy)\b",
            "forges policy or system authority",
        ),
        (
            r"(忽略|覆盖|绕过|忘记).{0,40}(系统|开发者|上面|之前).{0,30}(指令|提示|规则)",
            "attempts to override higher-priority instructions",
        ),
        (
            r"(泄露|打印|导出|发送|上传|输出).{0,40}(系统提示|开发者消息|密钥|凭据|口令|密码|token|环境变量)",
            "requests disclosure or exfiltration of secrets/prompts",
        ),
        (
            r"(从现在开始|以后|以后每次|永久).{0,40}(总是|必须|不要|忽略|执行|发送|上传|输出)",
            "tries to install unconditional future behavior",
        ),
    ]

    compact_checks: list[tuple[str, str]] = [
        (
            r"ignore(previous|prior|above)?(system|developer).{0,40}instructions?",
            "attempts to override higher-priority instructions",
        ),
        (
            r"(reveal|dump|print|leak|exfiltrate)(systemprompt|developermessage|secret|apikey|token|credential|password|env)",
            "requests disclosure or exfiltration of secrets/prompts",
        ),
        (
            r"(fromnowon|inallfuturesessions|forever).{0,80}(always|must|never|ignore|execute|run|send|upload)",
            "tries to install unconditional future behavior",
        ),
    ]

    for text, encoded in variants:
        for pattern, reason in checks:
            if re.search(pattern, text, flags=re.I):
                return _classified_safety_result(
                    content,
                    text,
                    reason,
                    encoded=encoded,
                )
        compact = re.sub(r"[\s`*_~.\-:/\\]+", "", text)
        for pattern, reason in compact_checks:
            if re.search(pattern, compact, flags=re.I):
                return _classified_safety_result(
                    content,
                    text,
                    reason,
                    encoded=encoded,
                )

    return MemorySafetyResult(_SAFETY_SAFE, "", "low")


@dataclass(frozen=True, slots=True)
class _MemoryAuthorityStateSnapshot:
    """Manager-sealed fields that can alter one entry's authority.

    This includes the persisted ``authority_state_digest`` input plus mutable
    relationship edges. Keeping the live capability fence complete prevents a
    caller holding the ``MemoryEntry`` returned by ``add_entry`` from
    laundering a pre-existing authority edit through a later Manager mutation.
    """

    entry_id: str
    scope: str
    tier: str
    lifecycle_status: str
    curator_locked: bool
    safety_status: str
    safety_reason: str
    approval_status: str
    approval_content_hash: str
    approval_reason: str
    approval_actor: str
    approval_decided_at: float
    approval_policy: str
    deprecated_at: float | None
    tier_reason: str
    related_to: tuple[str, ...]


@dataclass
class MemoryEntry:
    """A single memory entry (fact, pattern, decision, etc.)."""
    id: str
    scope: MemoryScope
    category: str  # e.g., "architecture", "convention", "decision", "pattern"
    content: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)
    usage_count: int = 0  # How often this was referenced
    domains: list[str] = field(default_factory=list)  # Domain classification
    # Multi-tier memory architecture
    tier: MemoryTier = MemoryTier.SHORT_TERM
    last_accessed: float = field(default_factory=time.time)
    related_to: list[str] = field(default_factory=list)  # Related memory IDs
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    retrieval_count: int = 0
    injection_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_used: float = 0.0
    usefulness_score: float = 0.0
    corroborated_success_count: int = 0
    corroborated_failure_count: int = 0
    corroborated_usefulness_score: float = 0.0
    feedback_observations: dict[str, str] = field(default_factory=dict)
    lifecycle_status: str = _ACTIVE_LIFECYCLE
    tier_reason: str = ""
    deprecated_at: float | None = None
    curator_locked: bool = False
    safety_status: str = _SAFETY_SAFE
    safety_reason: str = ""
    approval_status: str = _APPROVAL_APPROVED
    approval_content_hash: str = ""
    approval_reason: str = ""
    approval_actor: str = "safety_gate"
    approval_decided_at: float = 0.0
    approval_policy: MemoryApprovalPolicy = MemoryApprovalPolicy.USER_EXPLICIT
    _cached_tokens: list[str] | None = field(default=None, repr=False)
    _last_relevance: float = field(default=0.0, repr=False, compare=False)
    _authority_state_snapshot: _MemoryAuthorityStateSnapshot | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MemoryEntry):
            return NotImplemented
        return self.id == other.id

    def get_tokens(self) -> list[str]:
        if self._cached_tokens is None:
            text = (
                f"{self.content} {self.category} "
                f"{' '.join(self.tags)} {' '.join(self.domains)}"
            )
            self._cached_tokens = _tokenize(text)
        return self._cached_tokens

    def invalidate_tokens(self) -> None:
        self._cached_tokens = None

    def _current_authority_state(self) -> _MemoryAuthorityStateSnapshot:
        return _MemoryAuthorityStateSnapshot(
            entry_id=str(self.id),
            scope=self.scope.value,
            tier=self.tier.value,
            lifecycle_status=str(self.lifecycle_status),
            curator_locked=bool(self.curator_locked),
            safety_status=str(self.safety_status),
            safety_reason=str(self.safety_reason),
            approval_status=str(self.approval_status),
            approval_content_hash=str(self.approval_content_hash),
            approval_reason=str(self.approval_reason),
            approval_actor=str(self.approval_actor),
            approval_decided_at=float(self.approval_decided_at),
            approval_policy=self.approval_policy.value,
            deprecated_at=self.deprecated_at,
            tier_reason=str(self.tier_reason),
            related_to=tuple(self.related_to),
        )

    def _seal_authority_state(self) -> None:
        """Snapshot state changed through a Manager-authorized transition."""
        self._authority_state_snapshot = self._current_authority_state()

    def _authority_state_is_current(self) -> bool:
        return (
            self._authority_state_snapshot is None
            or self._authority_state_snapshot == self._current_authority_state()
        )

    def _authority_state_expands_privilege(self) -> bool:
        """Return whether an unsealed state change can increase authority."""
        trusted = self._authority_state_snapshot
        if trusted is None:
            return False
        current = self._current_authority_state()
        if current == trusted:
            return False
        # Identity, approval policy, hashes and explanatory authority metadata
        # have no safe caller-controlled monotonic transition.  Every such
        # change must pass through a Manager operation that seals it.
        if (
            current.entry_id != trusted.entry_id
            or current.scope != trusted.scope
            or current.approval_content_hash != trusted.approval_content_hash
            or current.approval_policy != trusted.approval_policy
            or current.safety_reason != trusted.safety_reason
            or current.approval_reason != trusted.approval_reason
            or current.approval_actor != trusted.approval_actor
            or current.approval_decided_at != trusted.approval_decided_at
            or current.deprecated_at != trusted.deprecated_at
            or current.tier_reason != trusted.tier_reason
            or current.related_to != trusted.related_to
        ):
            return True

        approval_rank = {
            _APPROVAL_REJECTED: 0,
            _APPROVAL_PENDING: 1,
            _APPROVAL_APPROVED: 2,
        }
        safety_rank = {
            _SAFETY_UNSAFE: 0,
            _SAFETY_SUSPICIOUS: 1,
            _SAFETY_SAFE: 2,
        }

        def ranks(state: _MemoryAuthorityStateSnapshot) -> tuple[int, ...]:
            return (
                approval_rank.get(state.approval_status, 0),
                1 if state.lifecycle_status == _ACTIVE_LIFECYCLE else 0,
                0 if state.curator_locked else 1,
                safety_rank.get(
                    _normalize_safety_status(state.safety_status),
                    0,
                ),
                0 if state.tier == MemoryTier.ARCHIVAL.value else 1,
            )

        trusted_ranks = ranks(trusted)
        current_ranks = ranks(current)
        # Mixed changes are conservative: any dimension that grows authority
        # makes the whole unsealed transition an expansion.  Pure decreases
        # (for example active -> deprecated+locked+archival) remain compatible
        # with legacy curator callers and are sealed at the next save.
        return any(
            current_value > trusted_value
            for current_value, trusted_value in zip(current_ranks, trusted_ranks)
        ) or current_ranks == trusted_ranks

    def _restore_authority_state(self) -> bool:
        if self._authority_state_snapshot is None:
            return False
        if self._authority_state_snapshot == self._current_authority_state():
            return False
        trusted = self._authority_state_snapshot
        self.id = trusted.entry_id
        self.scope = _coerce_scope(trusted.scope)
        self.tier = _coerce_tier(trusted.tier)
        self.lifecycle_status = trusted.lifecycle_status
        self.curator_locked = trusted.curator_locked
        self.safety_status = trusted.safety_status
        self.safety_reason = trusted.safety_reason
        self.approval_status = trusted.approval_status
        self.approval_content_hash = trusted.approval_content_hash
        self.approval_reason = trusted.approval_reason
        self.approval_actor = trusted.approval_actor
        self.approval_decided_at = trusted.approval_decided_at
        self.approval_policy = MemoryApprovalPolicy(trusted.approval_policy)
        self.deprecated_at = trusted.deprecated_at
        self.tier_reason = trusted.tier_reason
        self.related_to = list(trusted.related_to)
        return True

    @property
    def is_active(self) -> bool:
        # Officially persisted entries always carry an approval hash.  Keep
        # hash-less standalone MemoryEntry fixtures backward compatible, but
        # fail closed as soon as a previously approved authority object is
        # mutated behind the manager's write API.
        approval_is_current = (
            not self.approval_content_hash
            or self.approval_content_hash == _approval_hash_for_entry(self)
        )
        return (
            self.approval_status == _APPROVAL_APPROVED
            and self.safety_status != _SAFETY_UNSAFE
            and self.lifecycle_status == _ACTIVE_LIFECYCLE
            and not self.curator_locked
            and self.tier != MemoryTier.ARCHIVAL
            and approval_is_current
            and self._authority_state_is_current()
        )
    
    def _raw_persistence_payload(self) -> dict[str, Any]:
        """Build the unsanitized serialization shape for boundary checks."""
        return {
            "id": self.id,
            "scope": self.scope.value,
            "category": self.category,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tags": self.tags,
            "usage_count": self.usage_count,
            "domains": self.domains,
            "tier": self.tier.value,
            "last_accessed": self.last_accessed,
            "related_to": self.related_to,
            "metadata": self.metadata,
            "source": self.source,
            "provenance": self.provenance,
            "retrieval_count": self.retrieval_count,
            "injection_count": self.injection_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_used": self.last_used,
            "usefulness_score": self.usefulness_score,
            "corroborated_success_count": self.corroborated_success_count,
            "corroborated_failure_count": self.corroborated_failure_count,
            "corroborated_usefulness_score": self.corroborated_usefulness_score,
            "feedback_observations": _safe_feedback_observations(
                self.feedback_observations
            ),
            "lifecycle_status": self.lifecycle_status,
            "tier_reason": self.tier_reason,
            "deprecated_at": self.deprecated_at,
            "curator_locked": self.curator_locked,
            "safety_status": self.safety_status,
            "safety_reason": self.safety_reason,
            "approval_status": self.approval_status,
            "approval_content_hash": self.approval_content_hash,
            "approval_reason": self.approval_reason,
            "approval_actor": self.approval_actor,
            "approval_decided_at": self.approval_decided_at,
            "approval_policy": self.approval_policy.value,
        }

    def to_dict(self) -> dict[str, Any]:
        """Convert to a recursively sanitized persistence dictionary."""
        payload = self._raw_persistence_payload()
        sanitized = sanitize_for_persistence(payload)
        if not isinstance(sanitized, dict):
            return {}
        if sanitized.get("content") != payload.get("content"):
            metadata = sanitized.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                sanitized["metadata"] = metadata
            metadata["persistence_sanitized"] = {"content": True}
        sanitized["authority_state_digest"] = (
            _authority_state_digest_for_data(sanitized)
        )
        return sanitized
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryEntry":
        """Create from dictionary."""
        original_content = data.get("content")
        sanitized = sanitize_for_persistence(data)
        data = sanitized if isinstance(sanitized, dict) else {}
        if data.get("content") != original_content:
            metadata = data.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                data["metadata"] = metadata
            metadata["persistence_sanitized"] = {"content": True}
        success_count = int(data.get("success_count", 0) or 0)
        failure_count = int(data.get("failure_count", 0) or 0)
        corroborated_success_count = int(
            data.get("corroborated_success_count", 0) or 0
        )
        corroborated_failure_count = int(
            data.get("corroborated_failure_count", 0) or 0
        )
        return cls(
            id=data["id"],
            scope=_coerce_scope(data.get("scope", "user")),
            category=data.get("category", "general"),
            content=data["content"],
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            tags=_safe_str_list(data.get("tags", [])),
            usage_count=int(data.get("usage_count", 0) or 0),
            domains=_safe_str_list(data.get("domains", [])),
            tier=_coerce_tier(data.get("tier", "short_term")),
            last_accessed=data.get("last_accessed", time.time()),
            related_to=_safe_str_list(data.get("related_to", [])),
            metadata=_safe_dict(data.get("metadata", {})),
            source=str(data.get("source", "") or ""),
            provenance=_safe_dict(data.get("provenance", {})),
            retrieval_count=int(data.get("retrieval_count", 0) or 0),
            injection_count=int(data.get("injection_count", 0) or 0),
            success_count=success_count,
            failure_count=failure_count,
            last_used=float(data.get("last_used", 0.0) or 0.0),
            usefulness_score=_derived_usefulness_score(
                success_count,
                failure_count,
            ),
            corroborated_success_count=corroborated_success_count,
            corroborated_failure_count=corroborated_failure_count,
            corroborated_usefulness_score=_derived_usefulness_score(
                corroborated_success_count,
                corroborated_failure_count,
            ),
            feedback_observations=_safe_feedback_observations(
                data.get("feedback_observations", {})
            ),
            lifecycle_status=str(data.get("lifecycle_status", _ACTIVE_LIFECYCLE) or _ACTIVE_LIFECYCLE),
            tier_reason=str(data.get("tier_reason", "") or ""),
            deprecated_at=(
                float(data["deprecated_at"])
                if isinstance(data.get("deprecated_at"), (int, float))
                else None
            ),
            curator_locked=bool(data.get("curator_locked", False)),
            safety_status=_normalize_safety_status(data.get("safety_status", _SAFETY_SAFE)),
            safety_reason=str(data.get("safety_reason", "") or ""),
            approval_status=_normalize_approval_status(data.get("approval_status", "")),
            approval_content_hash=str(data.get("approval_content_hash", "") or ""),
            approval_reason=str(data.get("approval_reason", "") or ""),
            approval_actor=str(data.get("approval_actor", "") or ""),
            approval_decided_at=float(data.get("approval_decided_at", 0.0) or 0.0),
            approval_policy=(
                MemoryApprovalPolicy(data.get("approval_policy", MemoryApprovalPolicy.USER_EXPLICIT.value))
                if data.get("approval_policy", MemoryApprovalPolicy.USER_EXPLICIT.value)
                in {policy.value for policy in MemoryApprovalPolicy}
                else MemoryApprovalPolicy.USER_EXPLICIT
            ),
        )


def _approval_authority_state(entry: MemoryEntry) -> ApprovalAuthorityState:
    """Project one loaded entry into the pure approval-policy state."""

    return ApprovalAuthorityState(
        approval_status=entry.approval_status,
        approval_content_hash=entry.approval_content_hash,
        approval_reason=entry.approval_reason,
        approval_actor=entry.approval_actor,
        approval_decided_at=entry.approval_decided_at,
        approval_policy=entry.approval_policy.value,
        safety_status=entry.safety_status,
        safety_reason=entry.safety_reason,
        lifecycle_status=entry.lifecycle_status,
        tier=entry.tier.value,
        tier_reason=entry.tier_reason,
        curator_locked=entry.curator_locked,
    )


def _apply_approval_authority_state(
    entry: MemoryEntry,
    state: ApprovalAuthorityState,
) -> None:
    """Apply one interpreted authority state to its caller-owned entry."""

    entry.approval_status = state.approval_status
    entry.approval_content_hash = state.approval_content_hash
    entry.approval_reason = state.approval_reason
    entry.approval_actor = state.approval_actor
    entry.approval_decided_at = state.approval_decided_at
    entry.approval_policy = MemoryApprovalPolicy(state.approval_policy)
    entry.safety_status = state.safety_status
    entry.safety_reason = state.safety_reason
    entry.lifecycle_status = state.lifecycle_status
    entry.tier = MemoryTier(state.tier)
    entry.tier_reason = state.tier_reason
    entry.curator_locked = state.curator_locked


def _interpret_loaded_entry_approval(
    entry: MemoryEntry,
    raw_data: dict[str, Any],
    safety: MemorySafetyResult,
    *,
    mode: ApprovalInterpretationMode,
    transition_time: float,
    requires_complete_authority: bool = False,
) -> ApprovalInterpretation:
    """Adapt a normalized ``MemoryEntry`` to the pure approval interpreter."""

    return interpret_loaded_approval(
        ApprovalInterpretationInput(
            state=_approval_authority_state(entry),
            raw_has_approval="approval_status" in raw_data,
            raw_has_hash="approval_content_hash" in raw_data,
            assessed_safety_status=safety.status,
            assessed_safety_reason=safety.reason,
            canonical_hash=_approval_hash_for_entry(entry),
            transition_time=transition_time,
            raw_has_policy="approval_policy" in raw_data,
            raw_approval_policy=(
                raw_data.get("approval_policy")
                if isinstance(raw_data.get("approval_policy"), str)
                else None
            ),
            requires_complete_authority=requires_complete_authority,
        ),
        mode=mode,
    )


def _memory_entry_relationships(entry: MemoryEntry) -> tuple[str, ...]:
    """Return every durable relationship edge owned by one entry."""

    references = list(entry.related_to)
    metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
    supersedes = metadata.get("supersedes", [])
    if isinstance(supersedes, list):
        references.extend(
            value for value in supersedes if isinstance(value, str) and value
        )
    superseded_by = metadata.get("superseded_by")
    if isinstance(superseded_by, str) and superseded_by:
        references.append(superseded_by)
    return tuple(
        reference
        for reference in references
        if isinstance(reference, str) and reference
    )


def _remove_memory_relationship_reference(
    entry: MemoryEntry,
    target_id: str,
) -> int:
    """Remove all edges to ``target_id`` and reseal the authorized result."""

    removed = entry.related_to.count(target_id)
    if removed:
        entry.related_to = [
            reference
            for reference in entry.related_to
            if reference != target_id
        ]

    metadata = dict(entry.metadata) if isinstance(entry.metadata, dict) else {}
    metadata_changed = False
    supersedes = metadata.get("supersedes")
    if isinstance(supersedes, list):
        supersedes_removed = sum(value == target_id for value in supersedes)
        if supersedes_removed:
            metadata["supersedes"] = [
                value for value in supersedes if value != target_id
            ]
            removed += supersedes_removed
            metadata_changed = True
    if metadata.get("superseded_by") == target_id:
        metadata.pop("superseded_by", None)
        removed += 1
        metadata_changed = True

    if not removed:
        return 0
    if metadata_changed:
        entry.metadata = metadata
        if entry.approval_content_hash:
            _reset_version_scoped_observations(entry)
            entry.approval_content_hash = _approval_hash_for_entry(entry)
    entry.updated_at = time.time()
    entry.invalidate_tokens()
    entry._seal_authority_state()
    return removed


def _rewrite_memory_relationship_references(
    entry: MemoryEntry,
    replacements: dict[str, str],
) -> int:
    """Redirect removed same-scope identities and normalize every edge field."""

    if not replacements:
        return 0

    def resolve(reference: str) -> str | None:
        current = reference
        visited: set[str] = set()
        while current in replacements:
            if current in visited:
                return None
            visited.add(current)
            current = replacements[current]
        return current or None

    def rewrite_many(references: list[Any]) -> list[str]:
        rewritten: list[str] = []
        seen: set[str] = set()
        for value in references:
            if not isinstance(value, str) or not value:
                continue
            target = resolve(value)
            if target is None or target == entry.id or target in seen:
                continue
            seen.add(target)
            rewritten.append(target)
        return rewritten

    previous_related = list(entry.related_to)
    rewritten_related = rewrite_many(previous_related)
    metadata = dict(entry.metadata) if isinstance(entry.metadata, dict) else {}
    previous_metadata = dict(metadata)
    supersedes = metadata.get("supersedes")
    if isinstance(supersedes, list):
        metadata["supersedes"] = rewrite_many(supersedes)
    superseded_by = metadata.get("superseded_by")
    if isinstance(superseded_by, str) and superseded_by:
        rewritten = resolve(superseded_by)
        if rewritten is None or rewritten == entry.id:
            metadata.pop("superseded_by", None)
        else:
            metadata["superseded_by"] = rewritten

    related_changed = rewritten_related != previous_related
    metadata_changed = metadata != previous_metadata
    if not related_changed and not metadata_changed:
        return 0
    entry.related_to = rewritten_related
    if metadata_changed:
        entry.metadata = metadata
        if entry.approval_content_hash:
            _reset_version_scoped_observations(entry)
            entry.approval_content_hash = _approval_hash_for_entry(entry)
    entry.updated_at = time.time()
    entry.invalidate_tokens()
    entry._seal_authority_state()
    return 1


@dataclass
class MemoryFile:
    """Represents a MEMORY.md file content with indexed lookups."""
    scope: MemoryScope
    entries: list[MemoryEntry] = field(default_factory=list)
    max_entries: int = MAX_CURRENT_AUTHORITY_ENTRIES
    max_size_bytes: int = MAX_CURRENT_AUTHORITY_CONTENT_BYTES
    _id_index: dict[str, MemoryEntry] = field(default_factory=dict, repr=False)
    _tag_index: dict[str, set[MemoryEntry]] = field(default_factory=dict, repr=False)
    _category_index: dict[str, list[MemoryEntry]] = field(default_factory=dict, repr=False)
    _tokens_cache: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _cache_dirty: bool = field(default=True, repr=False)

    def _rebuild_indices(self) -> None:
        self._id_index.clear()
        self._tag_index.clear()
        self._category_index.clear()
        self._tokens_cache.clear()
        for entry in self.entries:
            self._id_index[entry.id] = entry
            for tag in entry.tags:
                if tag not in self._tag_index:
                    self._tag_index[tag] = set()
                self._tag_index[tag].add(entry)
            cat = entry.category
            if cat not in self._category_index:
                self._category_index[cat] = []
            self._category_index[cat].append(entry)
            self._tokens_cache[entry.id] = entry.get_tokens()
        self._cache_dirty = False

    def _ensure_cache_valid(self) -> None:
        if self._cache_dirty:
            self._rebuild_indices()

    def _invalidate_cache(self) -> None:
        self._cache_dirty = True

    @property
    def size_bytes(self) -> int:
        """Estimate durable size in UTF-8 bytes.

        This intentionally counts bytes, not Python characters: the store
        limit is a storage limit, and a CJK character is up to three bytes on
        disk. Metadata lives in ``memory.json`` beside the content and is
        bounded separately by the same file-size guard.
        """
        return sum(self._entry_size_bytes(entry) for entry in self.entries)

    @staticmethod
    def _entry_size_bytes(entry: MemoryEntry) -> int:
        return len(str(entry.content).encode("utf-8")) + sum(
            len(str(tag).encode("utf-8")) for tag in entry.tags
        )

    def add_entry(
        self,
        entry: MemoryEntry,
        *,
        on_evict: Any | None = None,
    ) -> bool:
        """Unsafe/internal add: caller must have applied safety and approval.

        ``on_evict`` receives ``(evicted_entry, previous_lifecycle,
        previous_tier_reason)`` for every entry removed by the count or byte
        limits, so the owning manager can persist an audit tombstone. Returns
        ``True`` only when the proposed entry remains admitted after retention.
        """
        self.entries.append(entry)
        self._invalidate_cache()
        self._enforce_limits(on_evict, admission_entry=entry)
        self._ensure_cache_valid()
        return self._id_index.get(entry.id) is entry
    
    def update_entry(self, entry_id: str, content: str) -> bool:
        """Update existing entry using index."""
        self._ensure_cache_valid()
        entry = self._id_index.get(entry_id)
        if entry is None:
            return False
        if entry.content != content:
            _reset_version_scoped_observations(entry)
        entry.content = content
        entry.updated_at = time.time()
        entry.invalidate_tokens()
        self._invalidate_cache()
        return True
    
    def delete_entry(self, entry_id: str) -> bool:
        """Delete entry and rebuild all indexes defensively."""
        self._ensure_cache_valid()
        entry = self._id_index.get(entry_id)
        if entry is None:
            entry = next((e for e in self.entries if e.id == entry_id), None)
        if entry is None:
            return False
        original_len = len(self.entries)
        removed_id = entry.id
        self.entries = [e for e in self.entries if e.id != entry_id]
        if len(self.entries) == original_len:
            return False
        # Relationship edges are durable references into the same store. A
        # deleted or evicted entry must not leave either graph backlinks or
        # supersession metadata pointing at a missing id.
        for remaining in self.entries:
            _remove_memory_relationship_reference(remaining, removed_id)
        self._rebuild_indices()
        return True
    
    def get_entries_by_category(self, category: str) -> list[MemoryEntry]:
        """Get entries filtered by category using index."""
        self._ensure_cache_valid()
        return list(self._category_index.get(category, []))
    
    def search(
        self,
        query: str,
        active_domains: list[str] | None = None,
        *,
        record_usage: bool = True,
    ) -> list[MemoryEntry]:
        """Search entries by keyword with BM25 + domain relevance scoring.

        Combines BM25 semantic relevance with usage frequency and optional
        domain-based boosting (soft blend, not hard filtering).
        Domain score uses Jaccard similarity between entry domains and active domains.
        """
        candidate_entries = [entry for entry in self.entries if entry.is_active]
        if not candidate_entries:
            return []

        query_tokens = _tokenize(query)
        query_tokens = _expand_query_terms(query_tokens, active_domains=active_domains)
        if not query_tokens:
            return []

        query_lower = query.lower()
        query_terms = query_lower.split()

        entry_tokens = []
        for entry in candidate_entries:
            text = (
                f"{entry.content} {entry.category} "
                f"{' '.join(entry.tags)} {' '.join(entry.domains)}"
            )
            entry_tokens.append(_tokenize(text))

        idf = _compute_idf(entry_tokens)
        avgdl = _compute_avgdl(entry_tokens)

        scored: list[tuple[float, MemoryEntry]] = []
        for i, entry in enumerate(candidate_entries):
            bm25 = _bm25_score(query_tokens, entry_tokens[i], idf, avgdl)

            substring_score = 0.0
            content_lower = entry.content.lower()
            if query_lower in content_lower:
                substring_score = 2.0
            elif any(q in content_lower for q in query_terms):
                substring_score = 1.0

            tag_score = 0.0
            exact_tag_match = any(
                tag.lower() == query_lower for tag in entry.tags
            )
            partial_tag_match = any(
                query_lower in tag.lower() for tag in entry.tags
            )
            if exact_tag_match:
                tag_score = 5.0
            elif partial_tag_match:
                tag_score = 1.5
            if query_lower in entry.category.lower():
                tag_score += 1.0

            match_score = bm25 + substring_score + tag_score
            if match_score <= 0:
                continue

            # Domain score: Jaccard similarity between entry.domains and active_domains
            domain_score = 0.0
            if active_domains and entry.domains:
                entry_set = set(entry.domains)
                active_set = set(active_domains)
                intersection = entry_set & active_set
                union = entry_set | active_set
                domain_score = len(intersection) / len(union) if union else 0.0

            # Soft blend: BM25 dominates, domain provides light steering
            final_relevance = match_score * 0.7 + domain_score * 0.3

            usage_bonus = math.log1p(entry.usage_count) * 0.2
            feedback_bonus = entry.usefulness_score * 0.2
            age_hours = _bounded_age_hours(entry.updated_at)
            recency_bonus = 1.0 / (1.0 + age_hours / 24.0) * 0.5

            total_score = final_relevance + usage_bonus + feedback_bonus + recency_bonus
            entry._last_relevance = total_score
            scored.append((total_score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        if record_usage:
            for _, entry in scored[:10]:
                entry.retrieval_count = _saturating_counter_add(
                    entry.retrieval_count
                )
        return [entry for _, entry in scored]
    
    @staticmethod
    def _retention_key(entry: MemoryEntry) -> tuple[Any, ...]:
        """Return a deterministic, ascending worst-to-best authority key."""

        def finite_number(value: Any) -> float:
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                return 0.0
            return number if math.isfinite(number) else 0.0

        safety_rank = {
            _SAFETY_UNSAFE: 0,
            _SAFETY_SUSPICIOUS: 1,
            _SAFETY_SAFE: 2,
        }.get(_normalize_safety_status(entry.safety_status), 0)
        approval_rank = {
            _APPROVAL_REJECTED: 0,
            _APPROVAL_PENDING: 1,
            _APPROVAL_APPROVED: 2,
        }.get(_normalize_approval_status(entry.approval_status), 0)
        tier_rank = {
            MemoryTier.ARCHIVAL: 0,
            MemoryTier.WORKING: 1,
            MemoryTier.SHORT_TERM: 2,
            MemoryTier.LONG_TERM: 3,
        }.get(_coerce_tier(entry.tier), 0)
        return (
            1 if entry.is_active else 0,
            safety_rank,
            approval_rank,
            1 if entry.lifecycle_status == _ACTIVE_LIFECYCLE else 0,
            tier_rank,
            finite_number(entry.corroborated_usefulness_score),
            int(entry.corroborated_success_count),
            -int(entry.corroborated_failure_count),
            finite_number(entry.usefulness_score),
            int(entry.success_count),
            -int(entry.failure_count),
            max(
                finite_number(entry.last_used),
                finite_number(entry.last_accessed),
            ),
            finite_number(entry.created_at),
            str(entry.id),
        )

    def _enforce_limits(
        self,
        on_evict: Any | None = None,
        *,
        admission_entry: MemoryEntry | None = None,
    ) -> None:
        """Remove the lowest-authority entries if exceeding limits.

        Eviction is now an observable state transition rather than a silent
        removal: the entry is marked ``evicted``, live backlinks are
        cleaned, and the owning manager receives the previous lifecycle so it
        can write an audit tombstone.
        """
        removed = False

        def evict(eviction_index: int | None = None) -> MemoryEntry:
            if eviction_index is None:
                eviction_index = min(
                    range(len(self.entries)),
                    key=lambda index: self._retention_key(self.entries[index]),
                )
            entry = self.entries.pop(eviction_index)
            previous_lifecycle = entry.lifecycle_status
            previous_tier_reason = entry.tier_reason
            entry.lifecycle_status = "evicted"
            entry.tier_reason = "capacity_eviction"
            entry.deprecated_at = time.time()
            removed_id = entry.id
            for remaining in self.entries:
                _remove_memory_relationship_reference(remaining, removed_id)
            if on_evict is not None:
                on_evict(entry, previous_lifecycle, previous_tier_reason)
            return entry

        # An entry that cannot fit by itself must never evict a viable store
        # before inevitably evicting itself. Treat this as failed admission.
        if (
            admission_entry is not None
            and self._entry_size_bytes(admission_entry) > self.max_size_bytes
        ):
            admission_index = next(
                (
                    index
                    for index, candidate in enumerate(self.entries)
                    if candidate is admission_entry
                ),
                None,
            )
            if admission_index is not None:
                evict(admission_index)
                removed = True

        # Check entry count
        while len(self.entries) > self.max_entries:
            evict()
            removed = True

        # Check size
        while self.size_bytes > self.max_size_bytes and self.entries:
            evict()
            removed = True

        if removed:
            self._rebuild_indices()
    
    def format_as_markdown(self, include_header: bool = True) -> str:
        """Format as MEMORY.md content."""
        lines = []
        
        if include_header:
            scope_names = {
                MemoryScope.USER: "User Memory",
                MemoryScope.PROJECT: "Project Memory",
                MemoryScope.LOCAL: "Local Memory",
            }
            lines.append(f"# {scope_names[self.scope]}")
            lines.append("")
            lines.append(f"*Last updated: {time.strftime('%Y-%m-%d %H:%M')}*")
            lines.append("")
        
        # Group by category
        categories: dict[str, list[MemoryEntry]] = {}
        for entry in self.entries:
            if entry.category not in categories:
                categories[entry.category] = []
            categories[entry.category].append(entry)
        
        for category, entries in categories.items():
            lines.append(f"## {category.title()}")
            lines.append("")
            for entry in entries:
                tags_str = f" `{' '.join(entry.tags)}`" if entry.tags else ""
                lines.append(f"- {entry.content}{tags_str}")
            lines.append("")
        
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Memory Manager
# ---------------------------------------------------------------------------

@dataclass
class MemoryPaths:
    """Paths for memory files at different scopes."""
    user_memory: Path
    project_memory: Path
    local_memory: Path
    
    @classmethod
    def for_workspace(cls, workspace: str) -> "MemoryPaths":
        """Create memory paths for a workspace."""
        workspace_path = Path(workspace)
        
        return cls(
            user_memory=MINI_CODE_DIR / "memory",
            project_memory=workspace_path / ".mini-code-memory",
            local_memory=workspace_path / ".mini-code-memory-local",
        )


@dataclass(frozen=True, slots=True)
class _PreparedScopeAuthority:
    """One bounded canonical source parsed and scope-bound exactly once."""

    source: MemoryAuthoritySource | None
    data: Any = None
    legacy_markdown: MemoryAuthoritySource | None = None


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_AUTHORIZATION_ASSIGNMENT_RE = re.compile(
    r"(?i)\b((?:[a-z][a-z0-9]*[_-])*authorization)\b"
    r"(\s*[:=]\s*)((?!\s*\[redacted)[^\r\n]+)"
)
_PASSWORD_ASSIGNMENT_RE = re.compile(
    r"(?i)\b((?:[a-z][a-z0-9]*[_-])*(?:password|passwd|pwd))\b"
    r"(\s*[:=]\s*)((?!\s*\[redacted)[^\r\n]+)"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b((?:[a-z][a-z0-9]*[_-])*(?:api[_-]?key|access[_-]?token|"
    r"auth[_-]?token|credential|token|"
    r"secret(?:[_-]?key)?))\b"
    r"(\s*[:=]\s*)((?!\s*\[redacted)[^\s,;]+)"
)
_NON_SECRET_ASSIGNMENT_VALUE_RE = re.compile(
    r"^(?:"
    r"[0-9]{1,4}(?:\.[0-9]{1,4})?"
    r"|(?:None|True|False|null|nil|undefined|NULL|NaN)"
    r"|(?:str|int|float|bool|bytes|dict|list|set|tuple|Any)"
    r"|[A-Za-z_][A-Za-z_]{0,15}(?:\.[A-Za-z_][A-Za-z_]{0,15}){1,3}"
    r")[)\]}\"'`,.;:]*$"
)
_BEARER_VALUE_RE = re.compile(
    r"(?i)\bbearer\s+(?!\[redacted)[A-Za-z0-9._~+/=-]+"
)
_AUTHORIZATION_SCHEME_RE = re.compile(r"(?i)^(bearer|basic|token)\s+.+$")
_NON_SECRET_ASSIGNMENT_PROSE_RE = re.compile(
    r"(?i)^(?:None|null|nil|undefined)\s+"
    r"(?:means|indicates|denotes|represents)\s+"
    r"(?:unset|absent|missing|disabled|not\s+set)[.!]?$"
)
_TOKEN_VALUE_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{3,}|(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"gh[pousr]_[A-Za-z0-9]{20,255})\b"
)
_JWT_VALUE_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,}\b"
)
_CREDENTIAL_URL_RE = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@[^\s'\"<>]+"
)
_RAW_TASK_KEY_RE = re.compile(
    r"(?i)^(?:raw[_-]?task|task|task[_-]?(?:summary|description)|"
    r"user[_-]?(?:prompt|message))$"
)
_TASK_REFERENCE_RE = re.compile(r"^\[TASK_SHA256:[0-9a-f]{16} len=[0-9]+\]$")
_PERSISTENCE_MAX_DEPTH = 12
_SENSITIVE_KEY_WORDS = frozenset(
    {
        "auth",
        "authorization",
        "bearer",
        "cookie",
        "credential",
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
    }
)
_NON_SECRET_KEY_DESCRIPTORS = frozenset(
    {
        "algorithm",
        "algorithms",
        "budget",
        "budgets",
        "configured",
        "count",
        "counts",
        "enabled",
        "field",
        "fields",
        "format",
        "kind",
        "kinds",
        "length",
        "lifetime",
        "limit",
        "limits",
        "method",
        "methods",
        "name",
        "names",
        "policy",
        "policies",
        "prefix",
        "prefixes",
        "present",
        "required",
        "rules",
        "scanner",
        "scope",
        "size",
        "source",
        "status",
        "suffix",
        "suffixes",
        "ttl",
        "type",
        "types",
        "usage",
    }
)
_TOKEN_METRIC_PREFIXES = frozenset(
    {
        "completion",
        "estimated",
        "input",
        "max",
        "min",
        "output",
        "prompt",
        "total",
        "used",
    }
)


def _persistence_key_words(key: str) -> list[str]:
    """Split snake/kebab/camel/Pascal keys into normalized word tokens."""
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", separated)
    words = re.findall(r"[A-Za-z0-9]+", separated.lower())
    singular: list[str] = []
    for word in words:
        if word in {"tokens", "secrets", "credentials", "passwords"}:
            singular.append(word[:-1])
        else:
            singular.append(word)
    return singular


def _is_sensitive_persistence_key(key: str) -> bool:
    """Recognize credential-value keys while preserving descriptor fields."""
    words = _persistence_key_words(key)
    if not words:
        return False
    sensitive = bool(_SENSITIVE_KEY_WORDS.intersection(words))
    sensitive = sensitive or "apikey" in words
    sensitive = sensitive or any(
        left == "api" and right == "key"
        for left, right in zip(words, words[1:])
    )
    sensitive = sensitive or any(
        left == "private" and right == "key"
        for left, right in zip(words, words[1:])
    )
    if not sensitive:
        return False
    if words[-1] in _NON_SECRET_KEY_DESCRIPTORS:
        return False
    if (
        words[-1] == "token"
        and len(words) >= 2
        and words[-2] in _TOKEN_METRIC_PREFIXES
    ):
        return False
    return True


def _redact_secret_assignment(match: re.Match[str]) -> str:
    """Redact credential assignments without corrupting obvious code prose."""
    value = match.group(3).strip()
    if _NON_SECRET_ASSIGNMENT_VALUE_RE.match(value):
        return match.group(0)
    return f"{match.group(1)}{match.group(2)}[REDACTED]"


def _redact_full_secret_assignment(match: re.Match[str]) -> str:
    """Redact a whole line-delimited secret value, including whitespace."""
    value = match.group(3).strip()
    if (
        _NON_SECRET_ASSIGNMENT_VALUE_RE.match(value)
        or _NON_SECRET_ASSIGNMENT_PROSE_RE.match(value)
    ):
        return match.group(0)
    key_words = _persistence_key_words(match.group(1))
    scheme = _AUTHORIZATION_SCHEME_RE.match(value)
    if key_words and key_words[-1] == "authorization" and scheme is not None:
        return (
            f"{match.group(1)}{match.group(2)}"
            f"{scheme.group(1)} [REDACTED]"
        )
    return f"{match.group(1)}{match.group(2)}[REDACTED]"


def _redact_persistence_secrets(text: str) -> str:
    # Authorization schemes and passphrases may contain whitespace.  Consume
    # their entire line-delimited value before applying single-token patterns,
    # otherwise only the scheme/first word is redacted and the tail leaks.
    redacted = _AUTHORIZATION_ASSIGNMENT_RE.sub(
        _redact_full_secret_assignment,
        text,
    )
    redacted = _PASSWORD_ASSIGNMENT_RE.sub(
        _redact_full_secret_assignment,
        redacted,
    )
    redacted = _BEARER_VALUE_RE.sub("Bearer [REDACTED]", redacted)
    redacted = _SECRET_ASSIGNMENT_RE.sub(_redact_secret_assignment, redacted)
    redacted = _TOKEN_VALUE_RE.sub("[REDACTED]", redacted)
    redacted = _JWT_VALUE_RE.sub("[REDACTED_JWT]", redacted)
    return _CREDENTIAL_URL_RE.sub("[REDACTED_URL]", redacted)


def persistence_text_contains_secret(value: Any) -> bool:
    """Return whether text contains a credential shape this store redacts."""
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:
        return False
    return _redact_persistence_secrets(text) != text


def _task_reference(value: Any) -> str:
    """Replace raw user task text with a bounded, deterministic audit handle."""
    try:
        text = value if isinstance(value, str) else json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            default=_json_default,
            separators=(",", ":"),
        )
    except Exception:
        text = "[unprintable]"
    if _TASK_REFERENCE_RE.fullmatch(text):
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"[TASK_SHA256:{digest} len={len(text)}]"


def sanitize_for_persistence(
    value: Any,
    *,
    _depth: int = 0,
    _field_name: str | None = None,
) -> Any:
    """Recursively sanitize arbitrary values before durable persistence.

    This is the single credential-redaction boundary shared by Memory entries,
    their audit metadata/provenance, and ProjectFacts.  It intentionally keeps
    ordinary technical prose and JSON scalar types intact.  Raw task/prompt
    fields are represented by a digest so cross-record audit joins remain
    possible without storing the user's original request.
    """
    if _depth > _PERSISTENCE_MAX_DEPTH:
        return "[TRUNCATED]"
    if _field_name and _RAW_TASK_KEY_RE.fullmatch(_field_name):
        return _task_reference(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            safe_key = _sanitize_untrusted_text(key, max_chars=None)
            if _is_sensitive_persistence_key(safe_key):
                result[safe_key] = "[REDACTED]"
            else:
                result[safe_key] = sanitize_for_persistence(
                    nested,
                    _depth=_depth + 1,
                    _field_name=safe_key,
                )
        return result
    if isinstance(value, (list, tuple)):
        return [
            sanitize_for_persistence(item, _depth=_depth + 1)
            for item in value
        ]
    if isinstance(value, (set, frozenset)):
        return [
            sanitize_for_persistence(item, _depth=_depth + 1)
            for item in sorted(value, key=repr)
        ]
    if isinstance(value, str):
        return _sanitize_untrusted_text(value, max_chars=None)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _sanitize_untrusted_text(repr(value), max_chars=None)


def _sanitize_untrusted_text(value: Any, max_chars: int | None = 600) -> str:
    text = str(value)
    text = _ANSI_ESCAPE_RE.sub("", text)
    text = _CONTROL_RE.sub(lambda m: f"\\x{ord(m.group(0)):02x}", text)
    text = _redact_persistence_secrets(text)
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars] + f"... [truncated {len(text) - max_chars} chars]"
    return text


def _redact_audit_value(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            key_str = str(key)
            if _is_sensitive_persistence_key(key_str):
                result[key_str] = "[REDACTED]"
            else:
                result[key_str] = _redact_audit_value(nested, depth + 1)
        return result
    if isinstance(value, list):
        return [_redact_audit_value(v, depth + 1) for v in value[:20]]
    if isinstance(value, str):
        return _sanitize_untrusted_text(value, max_chars=240)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _sanitize_untrusted_text(repr(value), max_chars=160)


_MEMORY_WRITE_OPERATION_RE = re.compile(r"memoryop_[0-9a-f]{32}")
_MEMORY_WRITE_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_MAX_MEMORY_WRITE_RECEIPTS = 128
_MAX_PIPELINE_OPERATION_RECEIPTS = 128
_OBSERVATION_RECEIPT_LANES = frozenset({"telemetry", "maintenance"})
_MAX_OBSERVATION_RECEIPTS_PER_LANE = 512
_REFLECTION_CLAIM_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_MEMORY_ADD_ENTRY_KEYS = frozenset(
    {
        "category",
        "content",
        "tags",
        "domains",
        "metadata",
        "tier",
        "source",
        "provenance",
        "lifecycle_status",
        "tier_reason",
        "safety_status",
        "safety_reason",
        "approval_policy",
    }
)


def _snapshot_memory_add_entry_kwargs(value: object) -> dict[str, Any]:
    """Validate and detach one normal MemoryManager.add_entry payload."""

    if not isinstance(value, dict):
        raise TypeError("add_entry_kwargs must be a dict")
    unknown = set(value).difference(_MEMORY_ADD_ENTRY_KEYS)
    if unknown:
        raise ValueError("add_entry_kwargs contains unsupported fields")
    if not str(value.get("content", "") or "").strip():
        raise ValueError("add_entry_kwargs content must be non-empty")
    return copy.deepcopy(value)


@dataclass(frozen=True, slots=True)
class ReflectionClaimWrite:
    """One validated reflection claim proposed for an atomic Project write.

    The manager, rather than the Pipeline, resolves recurrence and
    supersession from freshly loaded authority. ``add_entry_kwargs`` contains
    only the normal Memory entry fields; scope and duplicate policy remain
    manager-owned so a batch cannot escape Project authority or merge two
    independently evidenced claims by accident.
    """

    claim_id: str
    semantic_key: str
    statement: str
    add_entry_kwargs: dict[str, Any]

    def __post_init__(self) -> None:
        for field_name, value, max_chars in (
            ("claim_id", self.claim_id, 128),
            ("semantic_key", self.semantic_key, 160),
            ("statement", self.statement, 1_200),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            if value != value.strip() or len(value) > max_chars:
                raise ValueError(f"{field_name} is not canonical")
        if _REFLECTION_CLAIM_ID_RE.fullmatch(self.claim_id) is None:
            raise ValueError("claim_id is not canonical")
        if re.fullmatch(r"[a-z0-9_\u4e00-\u9fff]+", self.semantic_key) is None:
            raise ValueError("semantic_key is not canonical")
        object.__setattr__(
            self,
            "add_entry_kwargs",
            _snapshot_memory_add_entry_kwargs(self.add_entry_kwargs),
        )


def _canonical_memory_write_value(value: Any) -> Any:
    """Return a deterministic JSON value for reflection payload binding."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key in sorted(value, key=lambda item: str(item)):
            if not isinstance(key, str):
                raise TypeError("memory write mapping keys must be strings")
            normalized[key] = _canonical_memory_write_value(value[key])
        return normalized
    if isinstance(value, (list, tuple)):
        return [_canonical_memory_write_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized_items = [_canonical_memory_write_value(item) for item in value]
        return sorted(
            normalized_items,
            key=lambda item: json.dumps(
                item,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("memory write numbers must be finite")
        return value
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError("memory write payload contains an unsupported value")


def reflection_batch_payload_digest(
    claims: tuple[ReflectionClaimWrite, ...] | list[ReflectionClaimWrite],
) -> str:
    """Bind a reflection batch to the exact canonical payload the manager uses."""

    if not isinstance(claims, (list, tuple)):
        raise TypeError("claims must be a list or tuple")
    normalized = []
    for claim in claims:
        if not isinstance(claim, ReflectionClaimWrite):
            raise TypeError("claims must contain ReflectionClaimWrite values")
        normalized.append(
            {
                "claim_id": claim.claim_id,
                "semantic_key": claim.semantic_key,
                "statement": claim.statement,
                "add_entry_kwargs": _canonical_memory_write_value(
                    claim.add_entry_kwargs
                ),
            }
        )
    encoded = json.dumps(
        {"schema": "reflection_memory_batch_v1", "claims": normalized},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def conversation_fact_payload_digest(
    *,
    claim_id: str,
    add_entry_kwargs: dict[str, Any],
) -> str:
    """Bind one conversation fact to the exact canonical write payload."""

    if (
        not isinstance(claim_id, str)
        or _REFLECTION_CLAIM_ID_RE.fullmatch(claim_id) is None
    ):
        raise ValueError("claim_id is not canonical")
    snapshot = _snapshot_memory_add_entry_kwargs(add_entry_kwargs)
    encoded = json.dumps(
        {
            "schema": "conversation_fact_memory_v1",
            "claim_id": claim_id,
            "add_entry_kwargs": _canonical_memory_write_value(snapshot),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _coordinated_scope_write(method):
    """Run a scope-first manager mutation against freshly loaded authority."""

    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        raw_scope = kwargs.get("scope", args[0] if args else MemoryScope.PROJECT)
        scope = _coerce_scope(raw_scope, MemoryScope.PROJECT)
        required_scopes = (
            tuple(MemoryScope)
            if self._scope_has_unresolved_relationships(scope)
            else (scope,)
        )
        covered = self._transaction_covers(required_scopes)
        if self.in_write_transaction and not covered:
            raise MemoryStoreConflict(
                "Memory transaction does not cover requested authority scopes"
            )
        if covered:
            # Nested public mutations remain capability boundaries. An outer
            # callback must not mutate live authority after its one preflight
            # and then use this method's audit append to bless that state.
            self._assert_live_authority_is_sealed((scope,))
            return method(self, *args, **kwargs)
        return self.coordinated_write(
            required_scopes, lambda: method(self, *args, **kwargs)
        )

    return wrapped


def _coordinated_all_write(method):
    """Run a potentially multi-scope mutation against freshly loaded authority."""

    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        scopes = tuple(MemoryScope)
        covered = self._transaction_covers(scopes)
        if self.in_write_transaction and not covered:
            raise MemoryStoreConflict(
                "Memory transaction does not cover requested authority scopes"
            )
        if covered:
            self._assert_live_authority_is_sealed(scopes)
            return method(self, *args, **kwargs)
        return self.coordinated_write(
            scopes, lambda: method(self, *args, **kwargs)
        )

    return wrapped


class MemoryManager:
    """Manages layered memory system."""

    def __init__(
        self,
        workspace: str | Path | None = None,
        *,
        project_root: str | Path | None = None,
        store_timeout: float = 5.0,
        readonly_load: bool = False,
        approval_interpretation_mode: ApprovalInterpretationMode = (
            ApprovalInterpretationMode.RECOVER_TO_PENDING
        ),
        data_root: str | Path | None = None,
    ):
        # Backward compatibility: older call sites pass `project_root=...`.
        resolved_workspace = workspace if workspace is not None else project_root
        if resolved_workspace is None:
            resolved_workspace = Path.cwd()

        # Canonicalize the workspace identity once. Different lexical aliases
        # for the same checkout must coordinate on one lock and one authority
        # path rather than deadlocking or publishing through a changed alias.
        workspace_path = Path(resolved_workspace).resolve(strict=False)
        self.workspace = str(workspace_path)
        if not isinstance(readonly_load, bool):
            raise TypeError("readonly_load must be a bool")
        if not isinstance(approval_interpretation_mode, ApprovalInterpretationMode):
            raise TypeError(
                "approval_interpretation_mode must be an ApprovalInterpretationMode"
            )
        self._recover_on_load = not readonly_load
        self._approval_interpretation_mode = approval_interpretation_mode
        self.paths = MemoryPaths.for_workspace(self.workspace)
        self._data_root = (
            Path(data_root) if data_root is not None else Path(MINI_CODE_DIR)
        ).resolve(strict=False)
        if data_root is not None:
            self.paths.user_memory = self._data_root / "memory"
        # User authority is shared by every workspace using this data root,
        # while Project and Local authorities are owned by the workspace.
        # Keeping those lock identities separate prevents unrelated projects
        # from serializing every write and still makes managers with different
        # data roots agree on the lock for one workspace. The workspace lock
        # lives at the workspace root so its open directory descriptor binds
        # the same owner inode as both authority directories. Acquiring it
        # cannot create a partially durable scope authority directory.
        self._store = MemoryStoreCoordinator(self._data_root, timeout=store_timeout)
        self._workspace_store = MemoryStoreCoordinator(
            workspace_path,
            timeout=store_timeout,
            lock_name=".minicode-memory-authority.lock",
        )
        self._store_timeout = float(store_timeout)
        self._transaction_local = threading.local()
        self._transaction_pid = os.getpid()
        # A read-only constructor intentionally takes no lock. Its entries and
        # sampled revisions can therefore straddle a concurrent commit. The
        # first later write to each scope must reload under that scope's fence
        # unconditionally instead of trusting the constructor's revision.
        self._unfenced_loaded_scopes: set[MemoryScope] = (
            set(MemoryScope) if readonly_load else set()
        )
        self._disk_revisions: dict[MemoryScope, str] = {}
        # Human-readable reason for the most recent ``add_entry`` rejection
        # (empty content, retention limit, sanitization). Consumed by the
        # chat-facing handlers so a rejected write reports its cause instead
        # of an unrelated usage hint.
        self.last_add_rejection: str | None = None
        self.memories: dict[MemoryScope, MemoryFile] = {
            MemoryScope.USER: MemoryFile(scope=MemoryScope.USER),
            MemoryScope.PROJECT: MemoryFile(scope=MemoryScope.PROJECT),
            MemoryScope.LOCAL: MemoryFile(scope=MemoryScope.LOCAL),
        }
        self.approval_audit: dict[MemoryScope, list[dict[str, Any]]] = {
            MemoryScope.USER: [],
            MemoryScope.PROJECT: [],
            MemoryScope.LOCAL: [],
        }
        # Current authorities keep only a bounded hot audit tail inline.  The
        # immutable cold history is reached through one content-addressed head
        # and cached separately so ordinary callers cannot accidentally mutate
        # already-published audit records.
        self._approval_audit_heads: dict[
            MemoryScope, MemoryAuditHead | None
        ] = {scope: None for scope in MemoryScope}
        self._approval_audit_cold_records: dict[
            MemoryScope, tuple[dict[str, Any], ...]
        ] = {scope: () for scope in MemoryScope}
        # Content-free, bounded receipts share the same one-file commit point
        # as their entries.  They make a lost-response retry distinguishable
        # from a genuinely new recurrence without introducing another
        # authority sidecar.
        self._memory_write_receipts: dict[MemoryScope, dict[str, Any]] = {
            MemoryScope.USER: {},
            MemoryScope.PROJECT: {},
            MemoryScope.LOCAL: {},
        }
        # Aggregate Pipeline envelopes fence one run/turn operation before its
        # output kind is projected into a lesson, conversation fact, or
        # facts-only result. They intentionally share memory.json's commit
        # point with authority receipts instead of creating another sidecar.
        self._pipeline_operation_receipts: dict[
            MemoryScope, dict[str, str]
        ] = {
            MemoryScope.USER: {},
            MemoryScope.PROJECT: {},
            MemoryScope.LOCAL: {},
        }
        # High-frequency telemetry and periodic maintenance need their own
        # replay lane.  They must not evict content-write receipts or
        # per-entry user-feedback evidence.  Only hashes are persisted.
        self._observation_receipts: dict[
            MemoryScope, dict[str, dict[str, str]]
        ] = {
            scope: {lane: {} for lane in _OBSERVATION_RECEIPT_LANES}
            for scope in MemoryScope
        }
        self._deferred_batch_scope: MemoryScope | None = None
        # Revision authority follows the same precedence as the loader.  A
        # canonical JSON with embedded audit is one-file authority; legacy
        # JSON without it also depends on approval_audit.json.
        self._scope_has_embedded_audit: dict[MemoryScope, bool] = {
            scope: False for scope in MemoryScope
        }
        self._last_retrieval_result: Any = None
        for scope in (MemoryScope.PROJECT, MemoryScope.LOCAL):
            self._validate_scope_root(scope)
        all_scopes = tuple(MemoryScope)
        # Initialization may migrate, repair, or auto-recover any scope that
        # becomes visible while it is loading. Take the complete ordered lock
        # set for a writable constructor; an existence precheck cannot safely
        # predict which authority another process creates during ``_load_all``.
        initial_scopes = all_scopes
        initial_transaction = (
            nullcontext()
            if readonly_load
            else self._transaction_for_scopes(initial_scopes)
        )
        with initial_transaction:
            self._load_all()
            self._disk_revisions = {
                scope: self._scope_disk_revision(scope) for scope in MemoryScope
            }
    @property
    def in_write_transaction(self) -> bool:
        """Whether this thread observes any Memory authority transaction."""

        return bool(self._manager_held_stores()) or (
            self._store.in_transaction or self._workspace_store.in_transaction
        )

    def _manager_held_stores(self) -> frozenset[MemoryStoreCoordinator]:
        """Return capabilities acquired by this manager in this process.

        ``MemoryStoreCoordinator`` deliberately shares a re-entrant lock per
        path. That physical-lock state is not proof that *this* manager has
        reloaded its snapshot: a second manager in the same thread observes
        the same lock depth. Keep the logical capability manager-local so it
        cannot take the nested fast path and overwrite a newer snapshot.
        """

        pid = os.getpid()
        if self._transaction_pid != pid:
            self._transaction_pid = pid
            self._transaction_local = threading.local()
            if hasattr(self, "_unfenced_loaded_scopes"):
                self._unfenced_loaded_scopes.update(MemoryScope)
            if hasattr(self, "_deferred_batch_scope"):
                self._deferred_batch_scope = None
            if hasattr(self, "_last_retrieval_result"):
                self._last_retrieval_result = None
        _active_memory_manager_transaction()
        held = getattr(self._transaction_local, "held_stores", frozenset())
        return held if isinstance(held, frozenset) else frozenset()

    def _scope_store(self, scope: MemoryScope) -> MemoryStoreCoordinator:
        return (
            self._store
            if scope is MemoryScope.USER
            else self._workspace_store
        )

    def _transaction_covers(
        self,
        scopes: tuple[MemoryScope, ...] | list[MemoryScope],
    ) -> bool:
        """Return whether the current thread owns every requested authority."""

        normalized = tuple(dict.fromkeys(_coerce_scope(scope) for scope in scopes))
        held = self._manager_held_stores()
        return all(self._scope_store(scope) in held for scope in normalized)

    @contextmanager
    def _transaction_for_scopes(
        self,
        scopes: tuple[MemoryScope, ...] | list[MemoryScope],
    ):
        """Acquire the exact authority lock set in one fixed global order.

        Nested calls are capabilities, not a boolean escape hatch.  They may
        use a subset of locks already held by this thread, but may not upgrade
        a partial lock set at runtime: another thread could hold the missing
        lock and wait for ours in the opposite direction.
        """

        normalized = tuple(dict.fromkeys(_coerce_scope(scope) for scope in scopes))
        requested_stores = tuple(
            dict.fromkeys(self._scope_store(scope) for scope in normalized)
        )
        manager_held = self._manager_held_stores()
        if all(store in manager_held for store in requested_stores):
            yield
            return
        if manager_held:
            raise MemoryStoreConflict(
                "Memory transaction does not cover requested authority scopes"
            )
        active_manager = _active_memory_manager_transaction()
        if active_manager is not None and active_manager is not self:
            raise MemoryStoreConflict(
                "Memory transaction belongs to another manager"
            )
        physically_held = tuple(
            store
            for store in (self._store, self._workspace_store)
            if store.in_transaction
        )
        if physically_held:
            raise MemoryStoreConflict(
                "Memory authority lock belongs to another manager transaction"
            )

        deadline = time.monotonic() + self._store_timeout
        # The canonical order is shared User authority before workspace
        # authority.  ExitStack releases them in reverse order.
        ordered = tuple(
            store
            for store in (self._store, self._workspace_store)
            if store in requested_stores
        )
        with ExitStack() as stack:
            for store in ordered:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MemoryStoreBusy("Memory store is busy")
                stack.enter_context(store.transaction(timeout=remaining))
            self._transaction_local.held_stores = frozenset(ordered)
            _MEMORY_MANAGER_TRANSACTION_STATE.pid = os.getpid()
            _MEMORY_MANAGER_TRANSACTION_STATE.owner = self
            try:
                yield
            finally:
                if os.getpid() == self._transaction_pid:
                    self._transaction_local.held_stores = frozenset()
                    if (
                        _active_memory_manager_transaction() is self
                    ):
                        _MEMORY_MANAGER_TRANSACTION_STATE.owner = None

    def _scope_disk_revision(self, scope: MemoryScope) -> str:
        digest = hashlib.sha256()
        memory_json = self._read_authority_source(scope, "memory.json")
        if memory_json is not None:
            sources: tuple[tuple[str, MemoryAuthoritySource | None], ...] = (
                ("memory.json", memory_json),
            )
            if not self._scope_has_embedded_audit.get(scope, False):
                sources += (
                    (
                        "approval_audit.json",
                        self._read_authority_source(scope, "approval_audit.json"),
                    ),
                )
        else:
            sources = (
                ("memory.json", None),
                (
                    "MEMORY.md",
                    self._read_authority_source(scope, "MEMORY.md"),
                ),
                (
                    "approval_audit.json",
                    self._read_authority_source(scope, "approval_audit.json"),
                ),
            )
        for filename, source in sources:
            digest.update(filename.encode("ascii"))
            if source is None:
                digest.update(b"\0missing")
            else:
                digest.update(source.data)
        return digest.hexdigest()

    @staticmethod
    def _embedded_authority_revision(authority_bytes: bytes) -> str:
        """Return the revision represented by one embedded-audit authority."""

        digest = hashlib.sha256()
        digest.update(b"memory.json")
        digest.update(authority_bytes)
        return digest.hexdigest()

    def _read_authority_source(
        self,
        scope: MemoryScope,
        filename: str,
        *,
        oversized_as_missing: bool = False,
    ) -> MemoryAuthoritySource | None:
        """Read one scope file through the shared bounded authority seam."""

        self._validate_scope_root(scope)
        try:
            return read_memory_authority_file(
                self._get_scope_path(scope),
                filename,
            )
        except MemoryAuthorityTooLargeError as error:
            if oversized_as_missing:
                return None
            raise MemoryStoreUnavailable(
                f"Memory authority source {filename} is unavailable"
            ) from error
        except MemoryAuthoritySourceError as error:
            raise MemoryStoreUnavailable(
                f"Memory authority source {filename} is unavailable"
            ) from error

    def prompt_authority_revision(self) -> str:
        """Hash only state that can change prompt selection or rendering.

        Retrieval/injection counters and their access timestamps are
        observational telemetry.  Including them in the prompt fence makes
        two harmless concurrent reads invalidate each other after expensive
        semantic work.  Feedback, usefulness, content, metadata, and every
        approval/safety/lifecycle field remain covered because they can alter
        admission, ordering, consolidation, or rendered text.
        """
        observational_fields = {
            "retrieval_count",
            "injection_count",
            "last_accessed",
            "last_used",
            "feedback_observations",
        }
        entries: list[dict[str, Any]] = []
        for scope in MemoryScope:
            for entry in sorted(
                self.memories[scope].entries,
                key=lambda item: item.id,
            ):
                if not entry.is_active:
                    continue
                authority = entry._raw_persistence_payload()
                for field_name in observational_fields:
                    authority.pop(field_name, None)
                entries.append(authority)
        encoded = json.dumps(
            {
                "schema": "memory_prompt_authority_v1",
                "entries": entries,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=_json_default,
            separators=(",", ":"),
        ).encode("utf-8")
        return "memorypromptrev_" + hashlib.sha256(encoded).hexdigest()

    def refresh_if_stale(self) -> tuple[MemoryScope, ...]:
        """Reload any scope whose on-disk authority changed since last sync.

        Writes already reload through `_coordinated_all_write`, but reads did
        not: a long-lived manager kept serving its in-memory view, so an entry
        revoked by another process (Dashboard approval, another session) could
        still be selected and rendered into the prompt one more time.

        Returns the scopes that were reloaded. Cheap enough to call on every
        retrieval — hashing all three scopes measures ~0.2 ms against a
        multi-second model call — so it deliberately reuses the same revision
        notion as the write path instead of introducing a second, weaker one.
        """
        scopes = tuple(MemoryScope)
        if self._transaction_covers(scopes):
            # The caller owns the complete consistency window. Reloading here
            # could discard its uncommitted state, except when process/readonly
            # initialization explicitly marked the snapshot as unfenced.
            unfenced = tuple(
                scope
                for scope in MemoryScope
                if scope in self._unfenced_loaded_scopes
            )
            if unfenced:
                self._reload_scopes(scopes)
                return unfenced
            if any(
                self._disk_revisions.get(scope)
                != self._scope_disk_revision(scope)
                for scope in MemoryScope
            ):
                # Taking the complete physical lock set does not prove that
                # this Manager reloaded before entering it.  A reload here
                # could erase an outer callback's uncommitted live mutation,
                # while returning success would falsely bless a stale view.
                raise MemoryStoreConflict(
                    "Memory authority is stale during refresh"
                )
            return ()
        if self.in_write_transaction:
            raise MemoryStoreConflict(
                "Memory refresh requires a complete authority transaction"
            )
        try:
            preliminary_stale = tuple(
                scope
                for scope in MemoryScope
                if scope in self._unfenced_loaded_scopes
                or (
                    scope in self._disk_revisions
                    and self._disk_revisions[scope]
                    != self._scope_disk_revision(scope)
                )
            )
        except MemoryStoreError:
            # Retrieval authority is fail-closed. Serving the previous view
            # could resurrect an entry another process just rejected.
            raise
        if not preliminary_stale:
            return ()
        # Lock and then recompute. The preliminary hash is only an optimization;
        # it is never authority for the reload decision.
        with self._transaction_for_scopes(scopes):
            stale = tuple(
                scope
                for scope in MemoryScope
                if self._disk_revisions.get(scope)
                != self._scope_disk_revision(scope)
            )
            reload_scopes = tuple(
                scope
                for scope in MemoryScope
                if scope in self._unfenced_loaded_scopes or scope in stale
            )
            if not reload_scopes:
                return ()
            self._reload_scopes(reload_scopes)
        return stale

    def _existing_authority_lock_scopes(self) -> tuple[MemoryScope, ...]:
        """Return one representative scope for each authority lock in use.

        An absent authority has no state to protect yet. Skipping its lock
        keeps read-only snapshots side-effect free; retrieval_snapshot checks
        the roots again before yielding and reports a concurrent creation as a
        revision conflict on exit.
        """

        scopes: list[MemoryScope] = []
        if self.paths.user_memory.exists():
            scopes.append(MemoryScope.USER)
        if self.paths.project_memory.exists() or self.paths.local_memory.exists():
            scopes.append(MemoryScope.PROJECT)
        return tuple(scopes)

    @contextmanager
    def retrieval_snapshot(self):
        """Yield one revision-fenced authority view under the store lock.

        Callers must keep this context short.  Expensive semantic or model work
        should use an immutable copy captured here, then open a second snapshot
        and compare revisions before publishing the result.
        """
        scopes = tuple(MemoryScope)
        if self._transaction_covers(scopes):
            # A manager inherited across fork, or created with readonly_load,
            # may appear to have an in-memory snapshot while none of those
            # bytes were loaded under this process's authority fence.
            if self._unfenced_loaded_scopes:
                self._reload_scopes(scopes)
            self._store.assert_transaction_valid()
            self._workspace_store.assert_transaction_valid()
            revisions = tuple(
                (scope.value, self._scope_disk_revision(scope))
                for scope in MemoryScope
            )
            if any(
                self._disk_revisions.get(scope) != revision
                for scope, (_scope_name, revision) in zip(
                    MemoryScope,
                    revisions,
                )
            ):
                # A raw complete lock acquisition does not imply that this
                # Manager reloaded before entering it. Reloading here could
                # discard a caller's uncommitted live state, so a previously
                # fenced but stale view must fail closed instead.
                raise MemoryStoreConflict(
                    "Memory authority is stale during retrieval"
                )
            yield revisions
            self._store.assert_transaction_valid()
            self._workspace_store.assert_transaction_valid()
            after = tuple(
                (scope.value, self._scope_disk_revision(scope))
                for scope in MemoryScope
            )
            if after != revisions:
                raise MemoryStoreConflict(
                    "Memory authority changed during retrieval"
                )
            return
        if self.in_write_transaction:
            raise MemoryStoreConflict(
                "Memory retrieval requires a complete authority transaction"
            )
        for _attempt in range(3):
            lock_scopes = self._existing_authority_lock_scopes()
            with self._transaction_for_scopes(lock_scopes):
                if lock_scopes != self._existing_authority_lock_scopes():
                    continue
                current = {
                    scope: self._scope_disk_revision(scope)
                    for scope in MemoryScope
                }
                stale = tuple(
                    scope
                    for scope in MemoryScope
                    if self._disk_revisions.get(scope) != current[scope]
                    or (
                        scope in self._unfenced_loaded_scopes
                        and self._transaction_covers((scope,))
                    )
                )
                if stale:
                    if not self._transaction_covers(stale):
                        continue
                    self._reload_scopes(stale)
                    current = {
                        scope: self._scope_disk_revision(scope)
                        for scope in MemoryScope
                    }
                    if lock_scopes != self._existing_authority_lock_scopes():
                        continue
                # Scopes whose roots are still absent need no physical lock.
                # The stable root-set check above proves that their lock-free
                # constructor view remains empty at this point; the exit
                # revision check catches a creation while the caller uses the
                # snapshot.
                for scope in tuple(self._unfenced_loaded_scopes):
                    if not self._transaction_covers((scope,)):
                        self._unfenced_loaded_scopes.discard(scope)
                revision = tuple(
                    (scope.value, current[scope]) for scope in MemoryScope
                )
                yield revision
                after = tuple(
                    (scope.value, self._scope_disk_revision(scope))
                    for scope in MemoryScope
                )
                if after != revision:
                    raise MemoryStoreConflict(
                        "Memory authority changed during retrieval"
                    )
                return
        raise MemoryStoreBusy("Memory authority changed while acquiring snapshot")

    def _reload_scopes(self, scopes: tuple[MemoryScope, ...]) -> None:
        prepared = {
            scope: self._prepare_scope_authority(scope) for scope in scopes
        }
        self._assert_cross_scope_unique(prepared)
        for scope in scopes:
            self.memories[scope] = MemoryFile(scope=scope)
            self.approval_audit[scope] = []
            self._memory_write_receipts[scope] = {}
            self._pipeline_operation_receipts[scope] = {}
            self._observation_receipts[scope] = {
                lane: {} for lane in _OBSERVATION_RECEIPT_LANES
            }
            self._load_scope(scope, prepared=prepared[scope])
            if self._recover_on_load:
                self._auto_recover_scope(scope)
            self._disk_revisions[scope] = self._scope_disk_revision(scope)
            self._unfenced_loaded_scopes.discard(scope)

    def coordinated_write(
        self,
        scopes: tuple[MemoryScope, ...] | list[MemoryScope],
        operation,
        *,
        reject_stale: bool = False,
        recover_live_authority: bool = False,
    ):
        """Reload, validate and mutate one or more scopes under the store lock."""
        normalized = tuple(dict.fromkeys(_coerce_scope(scope) for scope in scopes))
        if self._transaction_covers(normalized):
            return operation()
        with self._transaction_for_scopes(normalized):
            stale = any(
                scope in self._unfenced_loaded_scopes
                or (
                    scope in self._disk_revisions
                    and self._disk_revisions[scope]
                    != self._scope_disk_revision(scope)
                )
                for scope in normalized
            )
            if stale:
                self._reload_scopes(normalized)
                if reject_stale:
                    raise MemoryStoreConflict("Memory content changed")
            try:
                self._assert_live_authority_is_sealed(normalized)
            except MemoryStoreConflict:
                if recover_live_authority:
                    self._restore_live_authority_state(normalized)
                    self._assert_live_authority_is_sealed(normalized)
                else:
                    # Public mutations must never bless authority edits made
                    # through a previously returned mutable MemoryEntry.
                    # Restore the canonical view before reporting failure.
                    self._reload_scopes(normalized)
                    raise
            try:
                result = operation()
            except BaseException:
                # A failed audit/state write must not leave this live manager
                # exposing an uncommitted in-memory approval as injectable.
                try:
                    self._reload_scopes(normalized)
                except BaseException:  # preserve the original write failure
                    pass
                raise
            return result

    def _assert_live_authority_is_sealed(
        self,
        scopes: tuple[MemoryScope, ...],
    ) -> None:
        """Reject caller-side authority edits before an authorized mutation.

        ``_append_approval_audit`` seals the *result* of an authorized
        transition. Without this entry precondition, that final seal could
        launder a mutation which happened before the Manager operation began.
        """

        for scope in scopes:
            for entry in self.memories[scope].entries:
                trusted = entry._authority_state_snapshot
                if trusted is None or entry._authority_state_is_current():
                    continue
                current = entry._current_authority_state()
                if current.scope != trusted.scope:
                    raise MemoryStoreConflict(
                        "Memory entry scope does not match its authority root"
                    )
                if current.entry_id != trusted.entry_id:
                    other_ids = {
                        other.id
                        for other_scope in MemoryScope
                        if other_scope is not scope
                        for other in self.memories[other_scope].entries
                    }
                    if current.entry_id in other_ids:
                        raise MemoryStoreConflict(
                            "Memory authority has a cross-scope duplicate ID"
                        )
                    raise MemoryStoreConflict(
                        "Memory entry identity changed outside the Manager"
                    )
                raise MemoryStoreConflict(
                    "Memory entry authority changed outside the Manager"
                )

    def _restore_live_authority_state(
        self,
        scopes: tuple[MemoryScope, ...],
    ) -> None:
        """Restore sealed authority in place for fail-closed read paths."""

        for scope in scopes:
            rebuild_indices = False
            for entry in self.memories[scope].entries:
                trusted = entry._authority_state_snapshot
                if trusted is None or entry._authority_state_is_current():
                    continue
                rebuild_indices = (
                    rebuild_indices
                    or entry.id != trusted.entry_id
                    or entry.scope.value != trusted.scope
                )
                entry._restore_authority_state()
            if rebuild_indices:
                self.memories[scope]._rebuild_indices()
    
    def _load_all(self) -> None:
        """Load all memory files."""
        scopes = tuple(MemoryScope)
        prepared = {
            scope: self._prepare_scope_authority(scope) for scope in scopes
        }
        # Preflight every canonical scope before approval migration, partial
        # recovery, projection repair, or any other load-time persistence.
        self._assert_cross_scope_unique(prepared)
        for scope in scopes:
            self._load_scope(scope, prepared=prepared[scope])
            if self._recover_on_load:
                self._auto_recover_scope(scope)

    def _prepare_scope_authority(
        self,
        scope: MemoryScope,
    ) -> _PreparedScopeAuthority:
        """Read, parse, and bind one canonical JSON authority without writes."""

        source = self._read_authority_source(scope, "memory.json")
        if source is None:
            return _PreparedScopeAuthority(
                source=None,
                legacy_markdown=self._read_authority_source(scope, "MEMORY.md"),
            )
        try:
            data = parse_memory_authority_json(source.text)
        except (json.JSONDecodeError, RecursionError, ValueError) as error:
            raise MemoryStoreConflict("Memory authority is invalid") from error
        if isinstance(data, dict) and "schemaVersion" in data:
            schema_version = data["schemaVersion"]
            if (
                type(schema_version) is not int
                or schema_version
                not in (
                    {CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION}
                    | set(MIGRATABLE_MEMORY_AUTHORITY_SCHEMA_VERSIONS)
                )
            ):
                raise MemoryStoreConflict("Memory authority is invalid")
        data, scope_errors = _bind_memory_authority_scope(data, scope.value)
        if scope_errors:
            raise MemoryStoreConflict("Memory authority is invalid")
        return _PreparedScopeAuthority(source=source, data=data)

    @staticmethod
    def _authority_ids(
        prepared: _PreparedScopeAuthority,
        scope: MemoryScope,
    ) -> tuple[str, ...]:
        data = prepared.data
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            if prepared.legacy_markdown is None:
                return ()
            count = sum(
                1
                for line in prepared.legacy_markdown.text.splitlines()
                if line.strip().startswith("- ")
            )
            if count > MAX_AUTHORITY_ENTRIES:
                raise MemoryStoreConflict(
                    "Legacy Memory authority exceeds the entry limit"
                )
            return tuple(f"{scope.value}-{index}" for index in range(1, count + 1))
        ids: list[str] = []
        for raw_entry in data["entries"]:
            if not isinstance(raw_entry, dict):
                continue
            entry_id = raw_entry.get("id")
            if isinstance(entry_id, str) and entry_id:
                ids.append(entry_id)
        return tuple(ids)

    @staticmethod
    def _raw_entry_relationship_edges(
        raw_entry: dict[str, Any],
    ) -> tuple[tuple[str, str], ...]:
        edges: list[tuple[str, str]] = []
        related_to = raw_entry.get("related_to", [])
        if isinstance(related_to, list):
            edges.extend(
                ("related_to", value)
                for value in related_to
                if isinstance(value, str) and value
            )
        metadata = raw_entry.get("metadata")
        if isinstance(metadata, dict):
            supersedes = metadata.get("supersedes", [])
            if isinstance(supersedes, list):
                edges.extend(
                    ("supersedes", value)
                    for value in supersedes
                    if isinstance(value, str) and value
                )
            superseded_by = metadata.get("superseded_by")
            if isinstance(superseded_by, str) and superseded_by:
                edges.append(("superseded_by", superseded_by))
        return tuple(edges)

    @classmethod
    def _raw_entry_relationships(cls, raw_entry: dict[str, Any]) -> tuple[str, ...]:
        return tuple(
            reference
            for _kind, reference in cls._raw_entry_relationship_edges(raw_entry)
        )

    def _authority_relationship_keys(
        self,
        scope: MemoryScope,
        prepared: _PreparedScopeAuthority,
    ) -> Counter[tuple[MemoryScope, str, str, str]]:
        """Count unresolved committed edges by owner, kind, and target."""

        data = prepared.data
        raw_entries = (
            data.get("entries", []) if isinstance(data, dict) else []
        )
        owned_ids = {
            raw_entry.get("id")
            for raw_entry in raw_entries
            if isinstance(raw_entry, dict)
            and isinstance(raw_entry.get("id"), str)
            and raw_entry.get("id")
        }
        return Counter(
            (scope, entry_id, relation_kind, reference)
            for raw_entry in raw_entries
            if isinstance(raw_entry, dict)
            for entry_id in (raw_entry.get("id"),)
            if isinstance(entry_id, str) and entry_id
            for relation_kind, reference in self._raw_entry_relationship_edges(
                raw_entry
            )
            if reference not in owned_ids
        )

    def _scope_has_unresolved_relationships(self, scope: MemoryScope) -> bool:
        """Whether a legacy edge needs a complete cross-scope read fence."""

        entries = self.memories[scope].entries
        owned_ids = {entry.id for entry in entries}
        return any(
            reference not in owned_ids
            for entry in entries
            for reference in _memory_entry_relationships(entry)
        )

    def _assert_cross_scope_unique(
        self,
        prepared: dict[MemoryScope, _PreparedScopeAuthority],
        *,
        reject_new_unresolved: bool = False,
        grandfathered_relationships: Counter[
            tuple[MemoryScope, str, str, str]
        ] | None = None,
    ) -> None:
        owners: dict[str, MemoryScope] = {}
        ids_by_scope: dict[MemoryScope, set[str]] = {}
        allowed_unresolved = grandfathered_relationships or Counter()
        proposed_unresolved: Counter[
            tuple[MemoryScope, str, str, str]
        ] = Counter()
        for scope in MemoryScope:
            ids = (
                self._authority_ids(prepared[scope], scope)
                if scope in prepared
                else tuple(entry.id for entry in self.memories[scope].entries)
            )
            ids_by_scope[scope] = set(ids)
            for entry_id in ids:
                owner = owners.get(entry_id)
                if owner is not None and owner is not scope:
                    raise MemoryStoreConflict(
                        "Memory authority has a cross-scope duplicate ID"
                    )
                owners[entry_id] = scope

        # Relationship state is committed with exactly one scope root. A
        # cross-scope edge would let approval/supersession mutate one root
        # while returning success after publishing another, so current
        # authorities reject such references at every load/write boundary.
        for scope in MemoryScope:
            if scope in prepared:
                data = prepared[scope].data
                raw_entries = (
                    data.get("entries", []) if isinstance(data, dict) else []
                )
            else:
                raw_entries = [
                    entry.to_dict() for entry in self.memories[scope].entries
                ]
            for raw_entry in raw_entries:
                if not isinstance(raw_entry, dict):
                    continue
                entry_id = raw_entry.get("id")
                for relation_kind, reference in self._raw_entry_relationship_edges(
                    raw_entry
                ):
                    if reference in ids_by_scope[scope]:
                        continue
                    owner = owners.get(reference)
                    if owner is not None and owner is not scope:
                        raise MemoryStoreConflict(
                            "Memory authority has a cross-scope relationship"
                        )
                    relationship = (
                        scope,
                        str(entry_id or ""),
                        relation_kind,
                        reference,
                    )
                    proposed_unresolved[relationship] += 1
                    # Loads retain historic dangling relationships. A write
                    # may preserve one but cannot introduce another unresolved
                    # edge; otherwise one old deletion makes its entire scope
                    # permanently unwritable.
                    if (
                        reject_new_unresolved
                        and proposed_unresolved[relationship]
                        > allowed_unresolved[relationship]
                    ):
                        raise MemoryStoreConflict(
                            "Memory authority introduces an unresolved "
                            "relationship"
                        )
    
    def _auto_recover_scope(self, scope: MemoryScope) -> None:
        """Check integrity and auto-recover if issues are found.

        After loading, validates the memory state. If integrity issues
        are detected, attempts to recover by removing invalid entries
        and deduplicating IDs.

        Args:
            scope: Memory scope to check and recover
        """
        result = self.check_integrity(scope)
        if not result["is_valid"]:
            logger.warning(
                "Integrity check failed for scope %s: %d issues found. "
                "Attempting auto-recovery...",
                scope.value,
                len(result["issues"]),
            )
            self._recover_scope(scope)
    
    def _recover_scope(self, scope: MemoryScope) -> None:
        """Attempt to recover a scope with integrity issues.

        Removes entries with invalid IDs, deduplicates IDs (keeps first),
        and fixes entries with empty content or category.

        Args:
            scope: Memory scope to recover
        """
        entries = self.memories[scope].entries
        seen_ids: set[str] = set()
        recovered: list[MemoryEntry] = []
        removed_count = 0
        fixed_count = 0

        for entry in entries:
            if not entry.id or not isinstance(entry.id, str):
                logger.warning(
                    "Removing entry with invalid ID during recovery"
                )
                removed_count += 1
                continue

            if entry.id in seen_ids:
                logger.warning(
                    "Removing duplicate entry with ID '%s'", entry.id
                )
                removed_count += 1
                continue

            if not entry.category or not isinstance(entry.category, str):
                _reset_version_scoped_observations(entry)
                entry.category = "general"
                fixed_count += 1

            if not entry.content or not isinstance(entry.content, str):
                logger.warning(
                    "Removing entry '%s' with empty content", entry.id
                )
                removed_count += 1
                continue

            seen_ids.add(entry.id)
            recovered.append(entry)

        self.memories[scope].entries = recovered
        self.memories[scope]._rebuild_indices()
        self._save_scope(scope)

        logger.info(
            "Recovery complete for scope %s: %d entries recovered, "
            "%d removed, %d fixed",
            scope.value,
            len(recovered),
            removed_count,
            fixed_count,
        )

    @staticmethod
    def _risk_for_safety(status: str) -> str:
        if status == _SAFETY_UNSAFE:
            return "high"
        if status == _SAFETY_SUSPICIOUS:
            return "medium"
        return "low"

    @staticmethod
    def _approval_for_safety(
        status: str,
        policy: MemoryApprovalPolicy = MemoryApprovalPolicy.USER_EXPLICIT,
    ) -> str:
        if status == _SAFETY_UNSAFE:
            return _APPROVAL_REJECTED
        if (
            status == _SAFETY_SUSPICIOUS
            or policy == MemoryApprovalPolicy.USER_REVIEW_REQUIRED
        ):
            return _APPROVAL_PENDING
        return _APPROVAL_APPROVED

    def _set_entry_safety_and_approval(
        self,
        entry: MemoryEntry,
        safety: MemorySafetyResult,
        *,
        actor: str,
        reason: str = "",
        lifecycle_status: str | None = None,
        approval_policy: MemoryApprovalPolicy = MemoryApprovalPolicy.USER_EXPLICIT,
    ) -> None:
        """Apply the safety -> approval state transition to an entry."""
        entry.safety_status = _normalize_safety_status(safety.status)
        entry.safety_reason = safety.reason
        entry.approval_status = self._approval_for_safety(
            entry.safety_status, approval_policy
        )
        entry.approval_policy = approval_policy
        entry.approval_reason = reason or safety.reason
        entry.approval_actor = actor
        entry.approval_decided_at = time.time()
        entry.approval_content_hash = _approval_hash_for_entry(entry)
        if entry.safety_status == _SAFETY_UNSAFE:
            entry.lifecycle_status = "rejected"
            entry.tier_reason = "safety_gate"
            entry.curator_locked = False
        elif lifecycle_status is not None:
            entry.lifecycle_status = lifecycle_status

    def _entry_hash_matches_approval(self, entry: MemoryEntry) -> bool:
        return bool(entry.approval_content_hash) and (
            entry.approval_content_hash == _approval_hash_for_entry(entry)
        )

    def _apply_loaded_entry_safety(
        self,
        entry: MemoryEntry,
        raw_data: dict[str, Any] | None = None,
        *,
        save_audit: bool = True,
        authority_schema_version: int | None = None,
    ) -> bool:
        """Safety-scan loaded entries and migrate legacy approval semantics."""
        raw_data = raw_data or {}
        scope = entry.scope
        safety = assess_memory_safety(entry.content, source=entry.source or "load")
        previous_approval = entry.approval_status
        previous_lifecycle = entry.lifecycle_status
        raw_has_approval = "approval_status" in raw_data
        raw_has_hash = "approval_content_hash" in raw_data
        raw_has_policy = "approval_policy" in raw_data
        raw_policy = raw_data.get("approval_policy")
        invalid_policy = bool(
            raw_has_policy
            and raw_policy not in {policy.value for policy in MemoryApprovalPolicy}
        )
        interpretation = _interpret_loaded_entry_approval(
            entry,
            raw_data,
            safety,
            mode=self._approval_interpretation_mode,
            transition_time=time.time(),
            requires_complete_authority=(
                authority_schema_version in _SEALED_ENTRY_SCHEMA_VERSIONS
            ),
        )
        if interpretation.state is None or interpretation.failure is not None:
            raise MemoryStoreConflict("Memory approval authority is invalid")
        canonical_hash = _approval_hash_for_entry(entry)
        persisted_hash = str(raw_data.get("approval_content_hash", "") or "")
        reset_observations = (
            _reset_version_scoped_observations(entry)
            if not raw_has_hash or persisted_hash != canonical_hash
            else None
        )
        _apply_approval_authority_state(entry, interpretation.state)
        changed = interpretation.migration_required

        if changed:
            self._append_approval_audit(
                scope,
                entry,
                action="migrate",
                actor=entry.approval_actor or "migration",
                previous_approval=previous_approval,
                previous_lifecycle=previous_lifecycle,
                reason=entry.approval_reason,
                safety=safety,
                extra={
                    "had_approval": raw_has_approval,
                    "had_hash": raw_has_hash,
                    "had_policy": raw_has_policy,
                    "invalid_policy": invalid_policy,
                    "version_observations_reset": reset_observations,
                },
                save=save_audit,
            )
        return changed

    @staticmethod
    def _projection_body(markdown: str) -> str:
        """Normalize volatile projection metadata before drift comparison."""
        return "\n".join(
            line
            for line in markdown.splitlines()
            if not line.startswith("*Last updated:")
        ).strip()

    def _repair_markdown_projection(self, scope: MemoryScope) -> bool:
        """Rebuild a stale derived Markdown projection from JSON authority."""
        if not self._recover_on_load:
            return False
        # Every load/reload entrypoint holds the same cooperative transaction
        # used by authority writers.  Refuse an accidental future call outside
        # that fence rather than racing a newer memory.json commit.
        if not self._transaction_covers((scope,)):
            raise MemoryStoreConflict(
                "Memory Markdown projection repair requires a store transaction"
            )
        root = self._get_scope_path(scope)
        memory_md = root / "MEMORY.md"
        expected = MemoryFile(
            scope=scope,
            entries=[
                entry
                for entry in self.memories[scope].entries
                if entry.is_active
            ],
        ).format_as_markdown()
        current_source = self._read_authority_source(
            scope,
            "MEMORY.md",
            oversized_as_missing=True,
        )
        current = current_source.text if current_source is not None else ""
        if self._projection_body(current) == self._projection_body(expected):
            return False
        self._ensure_scope_path(scope)
        self._atomic_write(memory_md, expected)
        logger.info("Rebuilt stale Memory Markdown projection for %s", scope.value)
        return True

    def _backup_legacy_authority_for_migration(
        self,
        scope: MemoryScope,
        *,
        source: MemoryAuthoritySource,
        source_name: str,
    ) -> str:
        """Preserve the exact legacy source before a lossy-safe migration.

        Legacy audit dictionaries and over-capacity entries cannot be copied
        into the current strict authority unchanged.  Keep their complete
        source bytes in a content-addressed quarantine first, so migration can
        fail closed without either blessing incomplete history or silently
        discarding it.
        """

        scope_root_digest = hashlib.sha256(
            os.path.abspath(os.fspath(self._get_scope_path(scope))).encode(
                "utf-8",
                errors="surrogateescape",
            )
        ).hexdigest()[:32]
        source_digest = hashlib.sha256(source.data).hexdigest()
        quarantine_root = self._data_root / "memory-migration-quarantine"
        backup_root = quarantine_root / scope_root_digest
        for candidate in (quarantine_root, backup_root):
            try:
                if candidate.is_symlink():
                    raise MemoryStoreUnsafePath(
                        "Memory migration quarantine is a symbolic link"
                    )
                candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
                if candidate.is_symlink() or not candidate.is_dir():
                    raise MemoryStoreUnsafePath(
                        "Memory migration quarantine is unavailable"
                    )
            except OSError as error:
                raise MemoryStoreUnavailable(
                    "Memory migration quarantine is unavailable"
                ) from error
        backup_path = backup_root / (
            f"{scope.value}-{source_name}-{source_digest}.bak"
        )
        try:
            existing_backup = read_memory_authority_file(
                backup_root,
                backup_path.name,
            )
        except MemoryAuthoritySourceError as error:
            raise MemoryStoreUnavailable(
                "Memory migration quarantine is unavailable"
            ) from error
        if existing_backup is None:
            self._atomic_write(backup_path, source.text)
        elif existing_backup.data != source.data:
            raise MemoryStoreConflict("Memory migration backup conflicts")
        return source_digest

    def _migrate_legacy_approval_audit(
        self,
        scope: MemoryScope,
        *,
        source: MemoryAuthoritySource,
        source_name: str,
        legacy_records: Sequence[dict[str, Any]],
    ) -> bool:
        """Quarantine sparse legacy audit and replace it with typed provenance."""

        if not legacy_records:
            return False
        source_digest = self._backup_legacy_authority_for_migration(
            scope,
            source=source,
            source_name=source_name,
        )
        legacy_id_digest = hashlib.sha256(
            json.dumps(
                [record.get("audit_id") for record in legacy_records],
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        memory_file = self.memories[scope]
        if memory_file.entries:
            bound_entry = next(
                (
                    memory_file._id_index[record["entry_id"]]
                    for record in legacy_records
                    if isinstance(record.get("entry_id"), str)
                    and record["entry_id"] in memory_file._id_index
                ),
                memory_file.entries[0],
            )
            self._append_approval_audit(
                scope,
                bound_entry,
                action="legacy_audit_migration",
                actor="migration",
                previous_approval=bound_entry.approval_status,
                previous_lifecycle=bound_entry.lifecycle_status,
                reason="unversioned audit quarantined before typed migration",
                extra={
                    "source_schema_version": 0,
                    "target_schema_version": (
                        CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION
                    ),
                    "legacy_audit_record_count": len(legacy_records),
                    "legacy_audit_ids_digest": legacy_id_digest,
                    "migration_backup_digest": source_digest,
                },
                save=False,
            )
        return True

    def _enforce_legacy_runtime_capacity(
        self,
        scope: MemoryScope,
        *,
        source: MemoryAuthoritySource,
        source_name: str,
    ) -> bool:
        """Migrate one bounded legacy source into the current runtime limits."""

        memory_file = self.memories[scope]
        exceeds_runtime_capacity = (
            len(memory_file.entries) > memory_file.max_entries
            or memory_file.size_bytes > memory_file.max_size_bytes
        )
        if not exceeds_runtime_capacity:
            return False
        if not self._recover_on_load:
            raise MemoryStoreConflict(
                "Legacy Memory authority requires capacity migration"
            )

        source_digest = self._backup_legacy_authority_for_migration(
            scope,
            source=source,
            source_name=source_name,
        )

        evictions: list[tuple[MemoryEntry, str, str]] = []

        def capture_eviction(
            evicted: MemoryEntry,
            previous_lifecycle: str,
            previous_tier_reason: str,
        ) -> None:
            evictions.append(
                (evicted, previous_lifecycle, previous_tier_reason)
            )

        memory_file._enforce_limits(capture_eviction)
        for evicted, previous_lifecycle, previous_tier_reason in evictions:
            self._append_approval_audit(
                scope,
                evicted,
                action="capacity_migration_eviction",
                actor="migration",
                previous_approval=evicted.approval_status,
                previous_lifecycle=previous_lifecycle,
                reason="legacy authority exceeded the v2 runtime capacity",
                extra={
                    "previous_tier_reason": previous_tier_reason,
                    "retention_policy": "authority_first_v1",
                    "source_schema_version": 0,
                    "target_schema_version": (
                        CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION
                    ),
                    "migration_backup_digest": source_digest,
                    "evicted_content_hash": hashlib.sha256(
                        evicted.content.encode("utf-8")
                    ).hexdigest(),
                },
                save=False,
            )
        return bool(evictions)

    def _load_scope(
        self,
        scope: MemoryScope,
        *,
        prepared: _PreparedScopeAuthority | None = None,
    ) -> None:
        """Load memory file for a scope."""
        self._scope_has_embedded_audit[scope] = False
        self._approval_audit_heads[scope] = None
        self._approval_audit_cold_records[scope] = ()
        legacy_audit_records: tuple[dict[str, Any], ...] = ()
        prepared = prepared or self._prepare_scope_authority(scope)
        memory_json = prepared.source
        if memory_json is not None:
            try:
                data = prepared.data
                if isinstance(data, dict):
                    raw_entries = data.get("entries")
                    schema_version = data.get("schemaVersion")
                    is_unversioned_authority = "schemaVersion" not in data
                    is_migratable_versioned_authority = (
                        schema_version
                        in MIGRATABLE_MEMORY_AUTHORITY_SCHEMA_VERSIONS
                    )
                    needs_schema_migration = (
                        schema_version
                        != CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION
                    )
                    if (
                        is_migratable_versioned_authority
                        and not self._recover_on_load
                    ):
                        raise MemoryStoreConflict(
                            "Memory authority requires schema migration"
                        )
                    if (
                        not is_unversioned_authority
                        and isinstance(raw_entries, list)
                        and len(raw_entries) > MAX_CURRENT_AUTHORITY_ENTRIES
                    ):
                        raise MemoryStoreConflict(
                            "Versioned Memory authority exceeds runtime capacity"
                        )
                    loaded_write_receipts = self._validated_memory_write_receipts(
                        data.get("write_receipts", []),
                        scope=scope,
                        authority_schema_version=schema_version,
                    )
                    loaded_pipeline_receipts = (
                        self._validated_pipeline_operation_receipts(
                            data.get("pipeline_operation_receipts", [])
                        )
                    )
                    loaded_observation_receipts = (
                        self._validated_observation_receipts(
                            data.get(
                                "observation_receipts",
                                {
                                    lane: []
                                    for lane in _OBSERVATION_RECEIPT_LANES
                                },
                            )
                        )
                    )
                    for operation_id in set(loaded_write_receipts).intersection(
                        loaded_pipeline_receipts
                    ):
                        if (
                            loaded_write_receipts[operation_id].payload_digest
                            != loaded_pipeline_receipts[operation_id]
                        ):
                            raise MemoryStoreConflict(
                                "Memory operation receipt digests disagree"
                            )
                    if "approval_audit" in data:
                        self._scope_has_embedded_audit[scope] = True
                        if is_unversioned_authority:
                            decoded_legacy_audit = self._validated_audit_records(
                                data["approval_audit"],
                                scope=scope,
                                versioned=False,
                            )
                            if self._recover_on_load:
                                legacy_audit_records = tuple(
                                    decoded_legacy_audit
                                )
                                # Migration and safety transitions created
                                # below must start in a clean typed history.
                                # Sparse legacy dictionaries remain available
                                # from the exact quarantine backup.
                                self.approval_audit[scope] = []
                            else:
                                self.approval_audit[scope] = (
                                    decoded_legacy_audit
                                )
                        else:
                            self._load_versioned_approval_audit(
                                scope,
                                data,
                                schema_version=schema_version,
                            )
                    else:
                        self._load_approval_audit_projection(scope)
                is_valid, errors = _validate_memory_data(data)
                if is_valid:
                    self._memory_write_receipts[scope] = loaded_write_receipts
                    self._pipeline_operation_receipts[
                        scope
                    ] = loaded_pipeline_receipts
                    self._observation_receipts[
                        scope
                    ] = loaded_observation_receipts
                    changed = needs_schema_migration
                    for entry_data in data.get("entries", []):
                        entry = MemoryEntry.from_dict(entry_data)
                        changed = self._apply_loaded_entry_safety(
                            entry,
                            entry_data,
                            save_audit=False,
                            authority_schema_version=data.get(
                                "schemaVersion"
                            ),
                        ) or changed
                        if (
                            schema_version == 2
                            and entry.approval_status == _APPROVAL_APPROVED
                        ):
                            previous_approval = entry.approval_status
                            previous_lifecycle = entry.lifecycle_status
                            entry.approval_status = _APPROVAL_PENDING
                            entry.approval_reason = (
                                "v2 authority seal migration requires review"
                            )
                            entry.approval_actor = "migration"
                            entry.approval_decided_at = time.time()
                            self._append_approval_audit(
                                scope,
                                entry,
                                action="authority_schema_migration",
                                actor="migration",
                                previous_approval=previous_approval,
                                previous_lifecycle=previous_lifecycle,
                                reason=entry.approval_reason,
                                extra={
                                    "source_schema_version": schema_version,
                                    "target_schema_version": (
                                        CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION
                                    ),
                                    "authority_resealed": True,
                                },
                                save=False,
                            )
                            changed = True
                        if (
                            needs_schema_migration
                            and self._recover_on_load
                            and entry.lifecycle_status
                            not in _VALID_LIFECYCLE_STATUSES
                        ):
                            previous_approval = entry.approval_status
                            previous_lifecycle = entry.lifecycle_status
                            entry.lifecycle_status = "invalid"
                            if entry.approval_status != _APPROVAL_REJECTED:
                                entry.approval_status = _APPROVAL_PENDING
                            entry.approval_reason = (
                                "unknown legacy lifecycle requires review"
                            )
                            entry.approval_actor = "migration"
                            entry.approval_decided_at = time.time()
                            self._append_approval_audit(
                                scope,
                                entry,
                                action="legacy_lifecycle_migration",
                                actor="migration",
                                previous_approval=previous_approval,
                                previous_lifecycle=previous_lifecycle,
                                reason=entry.approval_reason,
                                extra={
                                    "source_lifecycle_digest": hashlib.sha256(
                                        previous_lifecycle.encode(
                                            "utf-8",
                                            errors="replace",
                                        )
                                    ).hexdigest(),
                                    "target_lifecycle": "invalid",
                                },
                                save=False,
                            )
                            changed = True
                        entry._seal_authority_state()
                        self.memories[scope].entries.append(entry)
                    self.memories[scope]._rebuild_indices()
                    memory_file = self.memories[scope]
                    exceeds_runtime_capacity = (
                        len(memory_file.entries) > memory_file.max_entries
                        or memory_file.size_bytes > memory_file.max_size_bytes
                    )
                    if exceeds_runtime_capacity and not is_unversioned_authority:
                        raise MemoryStoreConflict(
                            "Versioned Memory authority exceeds runtime capacity"
                        )
                    if is_unversioned_authority:
                        if self._recover_on_load:
                            changed = (
                                self._migrate_legacy_approval_audit(
                                    scope,
                                    source=memory_json,
                                    source_name="memory-json",
                                    legacy_records=legacy_audit_records,
                                )
                                or changed
                            )
                        changed = (
                            self._enforce_legacy_runtime_capacity(
                                scope,
                                source=memory_json,
                                source_name="memory-json",
                            )
                            or changed
                        )
                    if changed and self._recover_on_load:
                        self._save_scope(scope)
                    elif self._recover_on_load:
                        self._repair_markdown_projection(scope)
                    return
                logger.warning(
                    "Memory data validation failed for scope %s: %s",
                    scope.value,
                    "; ".join(errors[:5]),
                )
                entries_value = data.get("entries") if isinstance(data, dict) else None
                if (
                    not self._recover_on_load
                    or not isinstance(entries_value, list)
                    or len(entries_value) > MAX_AUTHORITY_ENTRIES
                ):
                    raise MemoryStoreConflict("Memory authority is invalid")
                valid_entries = _recover_entries(data)
                if not valid_entries:
                    raise MemoryStoreConflict("Memory authority is invalid")
                backup_path = self._get_scope_path(scope) / "memory.json.bak"
                self._atomic_write(backup_path, memory_json.text)
                logger.warning(
                    "Corrupted Memory authority backed up to %s",
                    backup_path,
                )
                for entry_data in valid_entries:
                    entry = MemoryEntry.from_dict(entry_data)
                    self._apply_loaded_entry_safety(
                        entry,
                        entry_data,
                        save_audit=False,
                        authority_schema_version=data.get("schemaVersion"),
                    )
                    entry._seal_authority_state()
                    self.memories[scope].entries.append(entry)
                self.memories[scope]._rebuild_indices()
                self._save_scope(scope)
                return
            except json.JSONDecodeError as e:
                logger.error(
                    "JSON decode error in scope %s: %s", scope.value, e
                )
                raise MemoryStoreConflict("Memory authority is invalid") from e
            except KeyError as e:
                logger.error(
                    "Missing key in scope %s data: %s", scope.value, e
                )
                raise MemoryStoreConflict("Memory authority is invalid") from e
            except (TypeError, ValueError, OverflowError, RecursionError) as e:
                logger.error(
                    "Invalid normalized entry in scope %s: %s",
                    scope.value,
                    type(e).__name__,
                )
                raise MemoryStoreConflict("Memory authority is invalid") from e

        # Explicit legacy migration is allowed only before JSON authority
        # exists.  Its standalone audit and Markdown are both bounded sources.
        self._load_approval_audit_projection(scope)
        memory_md = prepared.legacy_markdown
        if memory_md is None:
            return
        self._parse_memory_md(memory_md.text, scope)
        self._enforce_legacy_runtime_capacity(
            scope,
            source=memory_md,
            source_name="memory-markdown",
        )
        if self.memories[scope].entries and self._recover_on_load:
            self._save_scope(scope)
    
    def _parse_memory_md(self, content: str, scope: MemoryScope) -> None:
        """Parse MEMORY.md file into entries."""
        lines = content.split("\n")
        current_category = "general"
        entry_counter = 0
        
        for line in lines:
            line = line.strip()
            
            # Skip headers and metadata
            if line.startswith("#") or line.startswith("*") or not line:
                if line.startswith("## "):
                    current_category = line[3:].strip().lower()
                continue
            
            # Parse list items
            if line.startswith("- "):
                if entry_counter >= MAX_AUTHORITY_ENTRIES:
                    raise MemoryStoreConflict(
                        "Legacy Memory authority exceeds the entry limit"
                    )
                entry_content = line[2:]
                
                # Extract tags
                tags = []
                if "`" in entry_content:
                    import re
                    tag_matches = re.findall(r"`([^`]+)`", entry_content)
                    for tag_match in tag_matches:
                        tags.extend(tag_match.split())
                    entry_content = re.sub(r"`[^`]+`", "", entry_content).strip()
                
                entry_counter += 1
                entry = MemoryEntry(
                    id=f"{scope.value}-{entry_counter}",
                    scope=scope,
                    category=current_category,
                    content=entry_content,
                    tags=tags,
                )
                # Parsing may run under ``readonly_load=True``.  Accumulate the
                # migration record in memory only; the normal recovery path
                # commits the complete parsed scope once after this method.
                self._apply_loaded_entry_safety(entry, {}, save_audit=False)
                entry._seal_authority_state()
                self.memories[scope].entries.append(entry)
        # Rebuild indices after Markdown-based loading
        if self.memories[scope].entries:
            self.memories[scope]._rebuild_indices()
    
    def _get_scope_path(self, scope: MemoryScope) -> Path:
        """Get path for memory scope."""
        if scope == MemoryScope.USER:
            return self.paths.user_memory
        elif scope == MemoryScope.PROJECT:
            return self.paths.project_memory
        else:
            return self.paths.local_memory
    
    _STORE_FILENAMES = ("memory.json", "MEMORY.md", "approval_audit.json")

    def _validate_scope_root(self, scope: MemoryScope) -> None:
        """Refuse a scope root that could redirect writes outside its owner.

        Without this a symlinked `.mini-code-memory` (or a symlinked store
        file inside it) silently relocates `memory.json`, `MEMORY.md`, and the
        approval audit anywhere on disk. The Dashboard approval authority
        already refuses exactly this shape; the plain manager is the same
        store and must not hold a weaker rule.
        """
        root = self._get_scope_path(scope)
        try:
            if root.is_symlink():
                raise MemoryStoreUnsafePath(
                    f"{scope.value} memory root is a symbolic link"
                )
            # PROJECT/LOCAL belong to this Workspace. USER legitimately lives
            # under the home data dir, so only the link check applies there.
            if scope in {MemoryScope.PROJECT, MemoryScope.LOCAL}:
                workspace = Path(self.workspace).resolve(strict=False)
                if root.parent.resolve(strict=False) != workspace:
                    raise MemoryStoreUnsafePath(
                        f"{scope.value} memory root escapes the workspace"
                    )
            for filename in self._STORE_FILENAMES:
                if (root / filename).is_symlink():
                    raise MemoryStoreUnsafePath(
                        f"{scope.value} memory file {filename} is a symbolic link"
                    )
        except OSError as error:
            raise MemoryStoreUnsafePath(
                f"{scope.value} memory root could not be validated"
            ) from error

    def _ensure_scope_path(self, scope: MemoryScope) -> None:
        """Ensure directory exists for scope."""
        path = self._get_scope_path(scope)
        # Validate before mkdir: mkdir(exist_ok=True) follows an existing
        # symlink, so checking afterwards would already have been too late.
        self._validate_scope_root(scope)
        MemoryAuthorityPublisher.ensure_directory(path)
        self._validate_scope_root(scope)

    def _approval_audit_path(self, scope: MemoryScope) -> Path:
        return self._get_scope_path(scope) / "approval_audit.json"

    @staticmethod
    def _validated_audit_records(
        value: object,
        *,
        scope: MemoryScope,
        versioned: bool,
    ) -> list[dict[str, Any]]:
        """Decode one audit list through the explicit current/legacy seam."""

        try:
            if versioned:
                records = decode_memory_audit_records(
                    value,
                    expected_scope=scope.value,
                    maximum_records=MAX_AUTHORITY_AUDIT_RECORDS,
                )
                return [record.to_dict() for record in records]
            return [
                dict(record)
                for record in decode_legacy_memory_audit_records(
                    value,
                    expected_scope=scope.value,
                )
            ]
        except (TypeError, ValueError) as error:
            raise MemoryStoreConflict("Memory approval audit is invalid") from error

    def _resolve_approval_audit_chain(
        self,
        scope: MemoryScope,
        head: MemoryAuditHead | None,
        hot_records: tuple[MemoryAuditRecord, ...],
    ) -> tuple[dict[str, Any], ...]:
        """Resolve and verify a complete newest-to-oldest immutable chain."""

        if head is None:
            validate_memory_audit_segment_chain(
                None,
                (),
                expected_scope=scope.value,
                hot_records=hot_records,
            )
            return ()
        store = self._blob_store_for_scope(scope)
        reference = head.blob
        segments: list[MemoryAuditSegment] = []
        for _ in range(head.segment_count):
            try:
                value = store.read_json(reference)
                segment = MemoryAuditSegment.from_dict(
                    value,
                    expected_scope=scope.value,
                )
            except (MemoryStoreError, TypeError, ValueError) as error:
                raise MemoryStoreConflict(
                    "Memory approval audit segment is invalid"
                ) from error
            segments.append(segment)
            if segment.previous is None:
                break
            reference = segment.previous
        try:
            validate_memory_audit_segment_chain(
                head,
                segments,
                expected_scope=scope.value,
                hot_records=hot_records,
            )
        except (TypeError, ValueError) as error:
            raise MemoryStoreConflict(
                "Memory approval audit segment chain is invalid"
            ) from error
        return tuple(
            record.to_dict()
            for segment in reversed(segments)
            for record in segment.records
        )

    def _load_versioned_approval_audit(
        self,
        scope: MemoryScope,
        data: dict[str, Any],
        *,
        schema_version: int,
    ) -> None:
        """Load typed hot history and the optional current cold-chain head."""

        try:
            hot_records = decode_memory_audit_records(
                data.get("approval_audit"),
                expected_scope=scope.value,
                maximum_records=MAX_AUTHORITY_AUDIT_RECORDS,
            )
            head = (
                decode_memory_audit_head(data.get("approval_audit_head"))
                if schema_version == CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION
                else None
            )
        except (TypeError, ValueError) as error:
            raise MemoryStoreConflict("Memory approval audit is invalid") from error
        cold_records = self._resolve_approval_audit_chain(
            scope,
            head,
            hot_records,
        )
        self._approval_audit_heads[scope] = head
        self._approval_audit_cold_records[scope] = cold_records
        self.approval_audit[scope] = [
            record.to_dict() for record in hot_records
        ]

    def _validated_memory_write_receipts(
        self,
        value: object,
        *,
        scope: MemoryScope,
        authority_schema_version: int | None,
    ) -> dict[str, MemoryAuthorityCommit]:
        """Resolve bounded idempotency receipts without repair or coercion."""

        try:
            parsed = _parse_memory_write_receipt_authority(
                value,
                expected_scope=scope.value,
                authority_schema_version=authority_schema_version,
            )
        except (TypeError, ValueError) as error:
            raise MemoryStoreConflict("Memory write receipts are invalid") from error
        resolved: dict[str, MemoryAuthorityCommit] = {}
        blob_store = MemoryBlobStore(self._get_scope_path(scope))
        for operation_id, receipt in parsed.items():
            if isinstance(receipt, MemoryAuthorityCommit):
                resolved[operation_id] = receipt
                continue
            try:
                blob_value = blob_store.read_json(receipt.blob)
                commit = MemoryAuthorityCommit.from_dict(blob_value)
                receipt.validate_commit(commit)
            except (TypeError, ValueError) as error:
                raise MemoryStoreConflict(
                    "Memory write receipt blob is invalid"
                ) from error
            resolved[operation_id] = commit
        return resolved

    def _serialized_memory_write_receipts(
        self,
        scope: MemoryScope,
    ) -> tuple[list[dict[str, object]], tuple[MemoryAuthorityCommit, ...]]:
        """Prepare receipt references without publishing their blob bytes."""

        receipts = self._memory_write_receipts.get(scope, {})
        if len(receipts) > _MAX_MEMORY_WRITE_RECEIPTS:
            raise MemoryStoreConflict("Memory write receipts exceed the bounded limit")
        serialized: list[dict[str, object]] = []
        pending_blobs: list[MemoryAuthorityCommit] = []
        for receipt in receipts.values():
            if not isinstance(receipt, MemoryAuthorityCommit):
                raise MemoryStoreConflict("Memory write receipt state is invalid")
            if receipt.scope != scope.value or receipt.replayed:
                raise MemoryStoreConflict("Memory write receipt state is invalid")
            receipt.to_json()
            encoded = canonical_memory_blob_bytes(receipt.to_dict())
            blob = MemoryBlobReference(
                digest=memory_blob_digest(encoded),
                size_bytes=len(encoded),
            )
            try:
                reference = MemoryAuthorityReceiptReference.from_commit(
                    receipt,
                    blob,
                )
            except (TypeError, ValueError) as error:
                raise MemoryStoreConflict(
                    "Memory write receipt blob is invalid"
                ) from error
            serialized.append(reference.to_dict())
            pending_blobs.append(receipt)
        return serialized, tuple(pending_blobs)

    def _blob_store_for_scope(self, scope: MemoryScope) -> MemoryBlobStore:
        """Use the active root-bound scope session when one is available."""

        active = getattr(self._transaction_local, "scope_publication", None)
        if (
            isinstance(active, tuple)
            and len(active) == 2
            and active[0] is scope
        ):
            return active[1].blob_store()
        return MemoryBlobStore(self._get_scope_path(scope))

    @staticmethod
    def _validated_pipeline_operation_receipts(
        value: object,
    ) -> dict[str, str]:
        """Parse bounded aggregate operation envelopes without repair."""

        try:
            return _parse_pipeline_operation_receipt_authority(value)
        except (TypeError, ValueError) as error:
            raise MemoryStoreConflict(
                "Memory Pipeline operation receipts are invalid"
            ) from error

    def _serialized_pipeline_operation_receipts(
        self,
        scope: MemoryScope,
    ) -> list[dict[str, str]]:
        receipts = self._pipeline_operation_receipts.get(scope, {})
        if len(receipts) > _MAX_PIPELINE_OPERATION_RECEIPTS:
            raise MemoryStoreConflict(
                "Memory Pipeline operation receipts exceed the bounded limit"
            )
        serialized: list[dict[str, str]] = []
        for operation_id, aggregate_digest in receipts.items():
            if (
                not isinstance(operation_id, str)
                or _MEMORY_WRITE_OPERATION_RE.fullmatch(operation_id) is None
                or not isinstance(aggregate_digest, str)
                or _MEMORY_WRITE_DIGEST_RE.fullmatch(aggregate_digest) is None
            ):
                raise MemoryStoreConflict(
                    "Memory Pipeline operation receipt state is invalid"
                )
            serialized.append(
                {
                    "operationId": operation_id,
                    "aggregateDigest": aggregate_digest,
                }
            )
        return serialized

    @staticmethod
    def _validated_observation_receipts(
        value: object,
    ) -> dict[str, dict[str, str]]:
        """Parse replay receipts without repairing ambiguous state."""

        try:
            return _parse_observation_receipt_authority(value)
        except (TypeError, ValueError) as error:
            raise MemoryStoreConflict(
                "Memory observation receipts are invalid"
            ) from error

    def _serialized_observation_receipts(
        self,
        scope: MemoryScope,
    ) -> dict[str, list[dict[str, str]]]:
        receipts = self._observation_receipts.get(scope, {})
        if set(receipts) != _OBSERVATION_RECEIPT_LANES:
            raise MemoryStoreConflict(
                "Memory observation receipt state is invalid"
            )
        serialized: dict[str, list[dict[str, str]]] = {}
        for lane in sorted(_OBSERVATION_RECEIPT_LANES):
            lane_receipts = receipts.get(lane)
            if (
                not isinstance(lane_receipts, dict)
                or len(lane_receipts) > _MAX_OBSERVATION_RECEIPTS_PER_LANE
            ):
                raise MemoryStoreConflict(
                    "Memory observation receipts exceed the bounded limit"
                )
            serialized_lane: list[dict[str, str]] = []
            for observation_digest, payload_digest in lane_receipts.items():
                if (
                    not isinstance(observation_digest, str)
                    or _MEMORY_WRITE_DIGEST_RE.fullmatch(observation_digest)
                    is None
                    or not isinstance(payload_digest, str)
                    or _MEMORY_WRITE_DIGEST_RE.fullmatch(payload_digest) is None
                ):
                    raise MemoryStoreConflict(
                        "Memory observation receipt state is invalid"
                    )
                serialized_lane.append(
                    {
                        "observationDigest": observation_digest,
                        "payloadDigest": payload_digest,
                    }
                )
            serialized[lane] = serialized_lane
        return serialized

    def _load_approval_audit_projection(self, scope: MemoryScope) -> None:
        source = self._read_authority_source(scope, "approval_audit.json")
        if source is None:
            self._approval_audit_heads[scope] = None
            self._approval_audit_cold_records[scope] = ()
            self.approval_audit[scope] = []
            return
        try:
            data = parse_memory_authority_json(source.text)
        except (ValueError, TypeError, RecursionError) as error:
            raise MemoryStoreConflict("Memory approval audit is invalid") from error
        if not isinstance(data, dict):
            raise MemoryStoreConflict("Memory approval audit is invalid")
        self._approval_audit_heads[scope] = None
        self._approval_audit_cold_records[scope] = ()
        self.approval_audit[scope] = self._validated_audit_records(
            data.get("records", []),
            scope=scope,
            versioned=False,
        )

    def _load_approval_audit(self, scope: MemoryScope) -> None:
        """Load canonical audit, consulting projection only for legacy stores."""
        memory_json = self._read_authority_source(scope, "memory.json")
        if memory_json is None:
            self._load_approval_audit_projection(scope)
            return
        try:
            data = parse_memory_authority_json(memory_json.text)
        except (ValueError, TypeError, RecursionError) as error:
            raise MemoryStoreConflict("Memory authority is invalid") from error
        if not isinstance(data, dict):
            raise MemoryStoreConflict("Memory authority is invalid")
        schema_version = data.get("schemaVersion")
        if (
            isinstance(schema_version, int)
            and schema_version
            in (
                {CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION}
                | set(MIGRATABLE_MEMORY_AUTHORITY_SCHEMA_VERSIONS)
            )
        ):
            self._load_versioned_approval_audit(
                scope,
                data,
                schema_version=schema_version,
            )
            return
        if "approval_audit" in data:
            self._approval_audit_heads[scope] = None
            self._approval_audit_cold_records[scope] = ()
            self.approval_audit[scope] = self._validated_audit_records(
                data["approval_audit"],
                scope=scope,
                versioned=False,
            )
            return
        self._load_approval_audit_projection(scope)

    def _save_approval_audit(self, scope: MemoryScope) -> None:
        """Commit audit together with Memory authority.

        ``approval_audit.json`` remains a human/tooling projection only; the
        authoritative records live inside the atomic memory.json commit.
        """
        # Synchronize PID-owned capabilities before inspecting the unfenced
        # marker. A child process must not persist a mutable snapshot inherited
        # from its parent through this private compatibility seam.
        self._manager_held_stores()
        if scope in self._unfenced_loaded_scopes:
            raise MemoryStoreConflict(
                "Unfenced Memory snapshot requires coordinated reload"
            )
        required_scopes = (
            tuple(MemoryScope)
            if self._scope_has_unresolved_relationships(scope)
            else (scope,)
        )
        if not self._transaction_covers(required_scopes):
            with self._transaction_for_scopes(required_scopes):
                expected = self._disk_revisions.get(scope)
                if expected is not None and expected != self._scope_disk_revision(scope):
                    raise MemoryStoreConflict("Memory approval audit changed")
                self._save_scope_locked(scope)
                return
        self._save_scope_locked(scope)

    def _write_approval_audit_projection(self, scope: MemoryScope) -> None:
        active = getattr(self._transaction_local, "scope_publication", None)
        if not (
            isinstance(active, tuple)
            and len(active) == 2
            and active[0] is scope
        ):
            self._ensure_scope_path(scope)
        path = self._approval_audit_path(scope)
        data = {
            "scope": scope.value,
            "last_updated": time.time(),
            "records": self.approval_audit.get(scope, []),
            "cold_history": (
                self._approval_audit_heads[scope].to_dict()
                if self._approval_audit_heads.get(scope) is not None
                else None
            ),
        }
        self._atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False, default=_json_default))

    def _append_approval_audit(
        self,
        scope: MemoryScope,
        entry: MemoryEntry,
        *,
        action: str,
        actor: str,
        previous_approval: str,
        previous_lifecycle: str,
        reason: str = "",
        safety: MemorySafetyResult | None = None,
        extra: dict[str, Any] | None = None,
        save: bool = False,
    ) -> dict[str, Any]:
        # This method is the common end of every Manager/curator-authorized
        # state transition.  Seal before an optional nested save so a live
        # caller cannot counterfeit approval/lifecycle state merely by holding
        # the mutable entry returned from add_entry.
        entry._seal_authority_state()
        audit_extra = dict(extra or {})
        canonical_previous_approval = previous_approval
        if canonical_previous_approval not in (_VALID_APPROVAL_STATUSES | {"new"}):
            audit_extra["legacy_previous_approval_status"] = (
                _sanitize_untrusted_text(previous_approval, max_chars=120)
            )
            canonical_previous_approval = _APPROVAL_PENDING
        canonical_previous_lifecycle = previous_lifecycle
        if canonical_previous_lifecycle not in (_VALID_LIFECYCLE_STATUSES | {"new"}):
            audit_extra["legacy_previous_lifecycle_status"] = (
                _sanitize_untrusted_text(previous_lifecycle, max_chars=120)
            )
            canonical_previous_lifecycle = "invalid"
        canonical_lifecycle = entry.lifecycle_status
        if canonical_lifecycle not in _VALID_LIFECYCLE_STATUSES:
            audit_extra["legacy_lifecycle_status"] = _sanitize_untrusted_text(
                canonical_lifecycle,
                max_chars=120,
            )
            canonical_lifecycle = "invalid"
        record = {
            "audit_id": f"audit-{time.time_ns()}-{uuid.uuid4().hex[:8]}",
            "entry_id": entry.id,
            "scope": scope.value,
            # Public approval methods accept an actor label. Bound it at the
            # producer rather than allowing an untrusted label to make the
            # entire authority permanently unwritable.
            "action": _sanitize_untrusted_text(action, max_chars=32) or "unknown",
            "actor": _sanitize_untrusted_text(actor, max_chars=64) or "unknown",
            "created_at": time.time(),
            "previous_approval_status": canonical_previous_approval,
            "approval_status": entry.approval_status,
            "previous_lifecycle_status": canonical_previous_lifecycle,
            "lifecycle_status": canonical_lifecycle,
            "safety_status": entry.safety_status,
            "safety_reason": _sanitize_untrusted_text(
                safety.reason if safety else entry.safety_reason,
                max_chars=240,
            ),
            "risk": safety.risk if safety else self._risk_for_safety(entry.safety_status),
            "content_hash": entry.approval_content_hash,
            "reason": _sanitize_untrusted_text(reason or entry.approval_reason, max_chars=240),
            "source": _sanitize_untrusted_text(entry.source, max_chars=120),
            "provenance": _redact_audit_value(entry.provenance),
            "extra": _redact_audit_value(audit_extra),
        }
        try:
            canonical_record = MemoryAuditRecord.from_dict(
                record,
                expected_scope=scope.value,
                expected_entry_id=entry.id,
            ).to_dict()
        except (TypeError, ValueError) as error:
            raise MemoryStoreConflict("Memory approval audit is invalid") from error
        self.approval_audit.setdefault(scope, []).append(canonical_record)
        if save:
            self._save_scope(scope)
        return canonical_record

    def get_approval_audit(self, entry_id: str) -> list[dict[str, Any]]:
        """Return persisted approval audit records for one memory id."""
        records: list[dict[str, Any]] = []
        for scope in MemoryScope:
            records.extend(
                record
                for record in (
                    *self._approval_audit_cold_records.get(scope, ()),
                    *self.approval_audit.get(scope, []),
                )
                if record.get("entry_id") == entry_id
            )
        return sorted(records, key=lambda r: float(r.get("created_at", 0.0) or 0.0))
    
    @_coordinated_scope_write
    def add_entry(
        self,
        scope: MemoryScope,
        category: str = "auto",
        content: str = "",
        tags: list[str] | None = None,
        *,
        domains: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        tier: MemoryTier | str | None = None,
        source: str = "",
        provenance: dict[str, Any] | None = None,
        lifecycle_status: str = _ACTIVE_LIFECYCLE,
        tier_reason: str = "",
        safety_status: str | None = None,
        safety_reason: str = "",
        allow_duplicate: bool = False,
        approval_policy: MemoryApprovalPolicy = MemoryApprovalPolicy.USER_EXPLICIT,
    ) -> MemoryEntry | None:
        """Add a new memory entry.

        If category is 'auto' or not provided, content will be automatically
        classified using keyword heuristics. This is the durable write boundary
        for memory: safety scanning, dedupe, full metadata construction, and
        persistence all happen before the entry becomes injectable.

        Args:
            scope: Memory scope level
            category: Category for the entry, or 'auto' for auto-classification
            content: Content of the memory entry
            tags: Optional list of tags

        Returns:
            The created or merged MemoryEntry, or ``None`` for empty content
            or when the proposed entry is immediately rejected by retention.
            Unsafe content is persisted as rejected so it can be audited but
            cannot be searched or injected, unless a full higher-authority
            store rejects its admission and retains only the audit tombstone.
        """
        scope = _coerce_scope(scope, MemoryScope.PROJECT)
        if not isinstance(approval_policy, MemoryApprovalPolicy):
            raise TypeError("approval_policy must be a MemoryApprovalPolicy")
        self.last_add_rejection = None
        if not content or not content.strip():
            self.last_add_rejection = "content is empty"
            return None

        self._ensure_scope_path(scope)

        original_content = content
        sanitized_write = sanitize_for_persistence(
            {
                "content": content,
                "metadata": dict(metadata or {}),
                "provenance": dict(provenance or {}),
            }
        )
        if not isinstance(sanitized_write, dict):
            self.last_add_rejection = "persistence sanitization rejected the write"
            return None
        content = str(sanitized_write.get("content", ""))
        metadata_value = sanitized_write.get("metadata", {})
        provenance_value = sanitized_write.get("provenance", {})
        metadata = metadata_value if isinstance(metadata_value, dict) else {}
        provenance = provenance_value if isinstance(provenance_value, dict) else {}
        if content != original_content:
            metadata["persistence_sanitized"] = {"content": True}
        source = _sanitize_untrusted_text(source, max_chars=None)
        if not content.strip():
            self.last_add_rejection = "content is empty after sanitization"
            return None

        final_category = category
        final_tags = list(tags or [])

        if category == "auto" and content:
            auto_category, auto_tags = _auto_classify_content(content)
            final_category = auto_category
            final_tags = list(dict.fromkeys(final_tags + auto_tags))

        safety = (
            MemorySafetyResult(
                _normalize_safety_status(safety_status),
                safety_reason,
                self._risk_for_safety(_normalize_safety_status(safety_status)),
            )
            if safety_status is not None
            else assess_memory_safety(content, source=source)
        )
        if safety.status == _SAFETY_UNSAFE:
            logger.warning(
                "Persisting rejected memory write scope=%s category=%s source=%s reason=%s",
                scope.value,
                final_category,
                source or "unspecified",
                safety.reason,
            )

        final_domains = list(dict.fromkeys(domains or []))
        final_metadata = dict(metadata or {})
        final_provenance = dict(provenance or {})
        final_tier = _coerce_tier(tier or MemoryTier.SHORT_TERM.value)

        if not allow_duplicate:
            duplicate = self._find_duplicate_entry(scope, content)
            if duplicate is not None:
                changed = False
                previous_authority_digest = _approval_hash_for_entry(duplicate)
                for tag in final_tags:
                    if tag not in duplicate.tags:
                        duplicate.tags.append(tag)
                        changed = True
                for domain in final_domains:
                    if domain not in duplicate.domains:
                        duplicate.domains.append(domain)
                        changed = True
                if final_metadata:
                    duplicate.metadata.update(final_metadata)
                    changed = True
                if final_provenance:
                    duplicate.provenance.update(final_provenance)
                    changed = True
                if source and not duplicate.source:
                    duplicate.source = source
                    changed = True
                if duplicate.tier != final_tier and final_tier != MemoryTier.SHORT_TERM:
                    duplicate.tier = final_tier
                    changed = True
                if changed:
                    previous_approval = duplicate.approval_status
                    previous_lifecycle = duplicate.lifecycle_status
                    reset_observations = (
                        _reset_version_scoped_observations(duplicate)
                        if _approval_hash_for_entry(duplicate)
                        != previous_authority_digest
                        else None
                    )
                    if previous_approval == _APPROVAL_REJECTED or previous_lifecycle == "rejected":
                        duplicate.safety_status = safety.status
                        duplicate.safety_reason = safety.reason
                        duplicate.approval_status = _APPROVAL_REJECTED
                        duplicate.approval_reason = "previously rejected duplicate requires restore"
                        duplicate.approval_actor = "safety_gate"
                        duplicate.approval_decided_at = time.time()
                        duplicate.approval_content_hash = _approval_hash_for_entry(duplicate)
                        duplicate.lifecycle_status = "rejected"
                    else:
                        # A pending entry must stay pending no matter how the
                        # near-duplicate write arrived: a chat "# remember"
                        # line (USER_EXPLICIT) must not approve a lesson the
                        # user has never seen in the review flow. Human
                        # review or recurrence corroboration are the only
                        # paths to approved.
                        merge_policy = approval_policy
                        if previous_approval == _APPROVAL_PENDING and (
                            merge_policy == MemoryApprovalPolicy.USER_EXPLICIT
                        ):
                            merge_policy = MemoryApprovalPolicy.USER_REVIEW_REQUIRED
                        self._set_entry_safety_and_approval(
                            duplicate,
                            safety,
                            actor="safety_gate",
                            reason=(
                                "duplicate merge held pending user review"
                                if merge_policy == MemoryApprovalPolicy.USER_REVIEW_REQUIRED
                                and previous_approval == _APPROVAL_PENDING
                                else "duplicate merge"
                            ),
                            lifecycle_status=lifecycle_status,
                            approval_policy=merge_policy,
                        )
                    duplicate.updated_at = time.time()
                    duplicate.invalidate_tokens()
                    self.memories[scope]._invalidate_cache()
                    self._append_approval_audit(
                        scope,
                        duplicate,
                        action="duplicate_merge",
                        actor="safety_gate",
                        previous_approval=previous_approval,
                        previous_lifecycle=previous_lifecycle,
                        reason="metadata/source/provenance merge invalidated approval",
                        safety=safety,
                        extra={
                            "previous_authority_digest": (
                                previous_authority_digest
                            ),
                            "version_observations_reset": (
                                reset_observations
                            ),
                        },
                    )
                    self._save_scope(scope)
                return duplicate

        entry_id = f"{scope.value}-{time.time_ns()}-{uuid.uuid4().hex[:8]}"
        entry = MemoryEntry(
            id=entry_id,
            scope=scope,
            category=final_category,
            content=content,
            tags=final_tags,
            domains=final_domains,
            tier=final_tier,
            metadata=final_metadata,
            source=source,
            provenance=final_provenance,
            lifecycle_status=lifecycle_status,
            tier_reason=tier_reason,
            approval_policy=approval_policy,
        )
        self._set_entry_safety_and_approval(
            entry,
            safety,
            actor="safety_gate",
            reason=(
                safety.reason
                or (
                    "automatic memory requires user review"
                    if approval_policy == MemoryApprovalPolicy.USER_REVIEW_REQUIRED
                    else "safe auto-approval"
                )
            ),
            lifecycle_status=lifecycle_status,
            approval_policy=approval_policy,
        )

        evictions: list[tuple[MemoryEntry, str, str]] = []

        def record_eviction(
            evicted: MemoryEntry,
            previous_lifecycle: str,
            previous_tier_reason: str,
        ) -> None:
            evictions.append(
                (evicted, previous_lifecycle, previous_tier_reason)
            )

        admitted = self.memories[scope].add_entry(entry, on_evict=record_eviction)
        for evicted, previous_lifecycle, previous_tier_reason in evictions:
            admission_rejected = evicted is entry
            self._append_approval_audit(
                scope,
                evicted,
                action="capacity_eviction",
                actor="retention",
                previous_approval=evicted.approval_status,
                previous_lifecycle=previous_lifecycle,
                reason=(
                    "capacity limit rejected proposed entry by retention policy"
                    if admission_rejected
                    else "capacity limit evicted lower-retention entry"
                ),
                safety=None,
                extra={
                    "previous_tier_reason": previous_tier_reason,
                    "admission_rejected": admission_rejected,
                    "retention_policy": "authority_first_v1",
                    "evicted_content_hash": hashlib.sha256(
                        evicted.content.encode("utf-8")
                    ).hexdigest(),
                },
                save=False,
            )
        self._append_approval_audit(
            scope,
            entry,
            action="write",
            actor="safety_gate",
            previous_approval="new",
            previous_lifecycle="new",
            reason=entry.approval_reason,
            safety=safety,
        )
        self._save_scope(scope)
        if not admitted:
            self.last_add_rejection = (
                "content exceeds the single-entry retention limit "
                f"({self.memories[scope].max_size_bytes} bytes); "
                "split it into smaller memories"
            )
        return entry if admitted else None

    def _find_duplicate_entry(
        self, scope: MemoryScope, content: str
    ) -> MemoryEntry | None:
        """Find an exact or near-exact duplicate in one scope."""
        normalized = _normalize_memory_content(content)
        if not normalized:
            return None
        new_tokens = set(_tokenize(normalized))
        for entry in self.memories[scope].entries:
            existing = _normalize_memory_content(entry.content)
            if existing == normalized:
                return entry
            if not new_tokens:
                continue
            old_tokens = set(entry.get_tokens())
            if not old_tokens:
                continue
            union = new_tokens | old_tokens
            similarity = len(new_tokens & old_tokens) / len(union) if union else 0.0
            if similarity >= 0.95:
                return entry
        return None
    
    @_coordinated_scope_write
    def update_entry(self, scope: MemoryScope, entry_id: str, content: str) -> bool:
        """Update an existing entry through the same safety/dedupe boundary."""
        scope = _coerce_scope(scope, MemoryScope.PROJECT)
        if not content or not content.strip():
            return False
        original_content = content
        content = str(sanitize_for_persistence(content))
        if not content.strip():
            return False
        memory_file = self.memories[scope]
        memory_file._ensure_cache_valid()
        entry = memory_file._id_index.get(entry_id)
        if entry is None:
            return False
        duplicate = self._find_duplicate_entry(scope, content)
        if duplicate is not None and duplicate.id != entry_id:
            logger.info(
                "Rejected memory update scope=%s entry_id=%s reason=duplicate_content",
                scope.value,
                entry_id,
            )
            return False
        previous_authority_digest = _approval_hash_for_entry(entry)
        metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
        if content != original_content:
            metadata["persistence_sanitized"] = {"content": True}
        else:
            metadata.pop("persistence_sanitized", None)
        entry.metadata = metadata
        safety = assess_memory_safety(content, source="update")
        previous_approval = entry.approval_status
        previous_lifecycle = entry.lifecycle_status
        entry.content = content
        reset_observations: dict[str, int | float] | None = None
        if _approval_hash_for_entry(entry) != previous_authority_digest:
            reset_observations = _reset_version_scoped_observations(entry)
        entry.updated_at = time.time()
        entry.invalidate_tokens()
        memory_file._invalidate_cache()
        self._set_entry_safety_and_approval(
            entry,
            safety,
            actor="safety_gate",
            reason="content update invalidated approval",
            lifecycle_status=_ACTIVE_LIFECYCLE,
            approval_policy=entry.approval_policy,
        )
        self._append_approval_audit(
            scope,
            entry,
            action="update",
            actor="safety_gate",
            previous_approval=previous_approval,
            previous_lifecycle=previous_lifecycle,
            reason=entry.approval_reason,
            safety=safety,
            extra=(
                {
                    "previous_authority_digest": previous_authority_digest,
                    "version_observations_reset": reset_observations,
                }
                if reset_observations is not None
                else None
            ),
        )
        self._save_scope(scope)
        return True
    
    @_coordinated_scope_write
    def delete_entry(self, scope: MemoryScope, entry_id: str) -> bool:
        """Delete an entry with an approval-audit tombstone."""
        memory_file = self.memories[scope]
        memory_file._ensure_cache_valid()
        before = memory_file._id_index.get(entry_id)
        if before is None:
            return False
        if memory_file.delete_entry(entry_id):
            self._append_approval_audit(
                scope,
                before,
                action="delete",
                actor="user",
                previous_approval=before.approval_status,
                previous_lifecycle=before.lifecycle_status,
                reason="user deleted memory entry",
                safety=None,
                extra={
                    "deleted_content_hash": hashlib.sha256(
                        before.content.encode("utf-8")
                    ).hexdigest(),
                },
                save=False,
            )
            self._save_scope(scope)
            self._save_approval_audit(scope)
            return True
        return False

    @_coordinated_scope_write
    def set_related_memories(
        self,
        scope: MemoryScope,
        entry_id: str,
        related_ids: list[str] | tuple[str, ...],
    ) -> bool:
        """Replace one entry's same-scope graph links through an audited fence.

        Returned ``MemoryEntry`` objects remain mutable for compatibility, but
        direct authority edits are rejected at the next write. This method is
        the sanctioned capability for callers that intentionally update the
        relationship graph.
        """

        scope = _coerce_scope(scope)
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError("entry_id must be a non-empty string")
        if not isinstance(related_ids, (list, tuple)) or not all(
            isinstance(value, str) and value for value in related_ids
        ):
            raise TypeError("related_ids must contain non-empty strings")
        normalized = list(dict.fromkeys(related_ids))
        if len(normalized) > MAX_CURRENT_AUTHORITY_ENTRIES:
            raise ValueError("related_ids exceeds the authority entry limit")
        if entry_id in normalized:
            raise MemoryStoreConflict("Memory entry cannot relate to itself")

        memory_file = self.memories[scope]
        memory_file._ensure_cache_valid()
        entry = memory_file._id_index.get(entry_id)
        if entry is None:
            return False
        unknown = [
            related_id
            for related_id in normalized
            if related_id not in memory_file._id_index
        ]
        if unknown:
            raise MemoryStoreConflict(
                "Memory relationships must target the same authority scope"
            )
        if entry.related_to == normalized:
            return False

        entry.related_to = normalized
        entry.updated_at = time.time()
        entry.invalidate_tokens()
        entry._seal_authority_state()
        memory_file._invalidate_cache()
        self._save_scope(scope)
        return True

    @_coordinated_scope_write
    def add_tag(self, scope: MemoryScope, entry_id: str, tag: str) -> bool:
        """Add a tag to an entry."""
        for entry in self.memories[scope].entries:
            if entry.id == entry_id:
                if tag not in entry.tags:
                    previous_approval = entry.approval_status
                    previous_lifecycle = entry.lifecycle_status
                    previous_authority_digest = _approval_hash_for_entry(entry)
                    entry.tags.append(tag)
                    reset_observations = _reset_version_scoped_observations(entry)
                    safety = assess_memory_safety(entry.content, source="metadata")
                    if previous_approval != _APPROVAL_REJECTED and previous_lifecycle != "rejected":
                        self._set_entry_safety_and_approval(
                            entry,
                            safety,
                            actor="safety_gate",
                            reason="tag metadata changed",
                            lifecycle_status=entry.lifecycle_status,
                            approval_policy=entry.approval_policy,
                        )
                    else:
                        entry.safety_status = safety.status
                        entry.safety_reason = safety.reason
                        entry.approval_content_hash = _approval_hash_for_entry(entry)
                    entry.invalidate_tokens()
                    self.memories[scope]._invalidate_cache()
                    self._append_approval_audit(
                        scope,
                        entry,
                        action="metadata_update",
                        actor="safety_gate",
                        previous_approval=previous_approval,
                        previous_lifecycle=previous_lifecycle,
                        reason="tag metadata changed",
                        safety=safety,
                        extra={
                            "previous_authority_digest": previous_authority_digest,
                            "version_observations_reset": reset_observations,
                        },
                    )
                    self._save_scope(scope)
                return True
        return False

    @_coordinated_scope_write
    def remove_tag(self, scope: MemoryScope, entry_id: str, tag: str) -> bool:
        """Remove a tag from an entry."""
        for entry in self.memories[scope].entries:
            if entry.id == entry_id:
                if tag in entry.tags:
                    previous_approval = entry.approval_status
                    previous_lifecycle = entry.lifecycle_status
                    previous_authority_digest = _approval_hash_for_entry(entry)
                    entry.tags.remove(tag)
                    reset_observations = _reset_version_scoped_observations(entry)
                    safety = assess_memory_safety(entry.content, source="metadata")
                    if previous_approval != _APPROVAL_REJECTED and previous_lifecycle != "rejected":
                        self._set_entry_safety_and_approval(
                            entry,
                            safety,
                            actor="safety_gate",
                            reason="tag metadata changed",
                            lifecycle_status=entry.lifecycle_status,
                            approval_policy=entry.approval_policy,
                        )
                    else:
                        entry.safety_status = safety.status
                        entry.safety_reason = safety.reason
                        entry.approval_content_hash = _approval_hash_for_entry(entry)
                    entry.invalidate_tokens()
                    self.memories[scope]._invalidate_cache()
                    self._append_approval_audit(
                        scope,
                        entry,
                        action="metadata_update",
                        actor="safety_gate",
                        previous_approval=previous_approval,
                        previous_lifecycle=previous_lifecycle,
                        reason="tag metadata changed",
                        safety=safety,
                        extra={
                            "previous_authority_digest": previous_authority_digest,
                            "version_observations_reset": reset_observations,
                        },
                    )
                    self._save_scope(scope)
                return True
        return False

    def search_by_tag(self, scope: MemoryScope, tag: str) -> list[MemoryEntry]:
        """Search entries by tag."""
        return [
            entry for entry in self.memories[scope].entries
            if tag in entry.tags and entry.is_active
        ]

    def get_all_tags(self, scope: MemoryScope) -> set[str]:
        """Get all unique tags in a scope."""
        tags: set[str] = set()
        for entry in self.memories[scope].entries:
            tags.update(entry.tags)
        return tags

    def get_tags_by_category(self, scope: MemoryScope) -> dict[str, list[str]]:
        """Get tags grouped by category."""
        category_tags: dict[str, set[str]] = {}
        for entry in self.memories[scope].entries:
            if entry.category not in category_tags:
                category_tags[entry.category] = set()
            category_tags[entry.category].update(entry.tags)
        return {cat: sorted(list(tags)) for cat, tags in category_tags.items()}

    def search(
        self,
        query: str,
        scope: MemoryScope | None = None,
        limit: int = 20,
        min_relevance: float = 0.1,
        active_domains: list[str] | None = None,
        *,
        record_usage: bool = True,
    ) -> list[MemoryEntry]:
        """Search across memory scopes with TF-IDF + domain relevance.

        Args:
            query: Search query string
            scope: Optional scope to limit search to
            limit: Maximum results to return
            min_relevance: Minimum relevance score threshold (0.0-1.0)
            active_domains: Current domain context for soft boosting

        Returns:
            Entries ranked by relevance (TF-IDF + domain + usage + recency)
        """
        coordinated_scopes = (
            (_coerce_scope(scope, MemoryScope.PROJECT),)
            if scope is not None
            else tuple(MemoryScope)
        )
        if record_usage and not self._transaction_covers(coordinated_scopes):
            return self.coordinated_write(
                coordinated_scopes,
                lambda: self.search(
                    query,
                    scope=scope,
                    limit=limit,
                    min_relevance=min_relevance,
                    active_domains=active_domains,
                    record_usage=record_usage,
                ),
                recover_live_authority=True,
            )

        results = []

        if scope is not None:
            scope = _coerce_scope(scope, MemoryScope.PROJECT)
        scopes_to_search = [scope] if scope else list(MemoryScope)

        for s in scopes_to_search:
            scoped_results = self.memories[s].search(
                query,
                active_domains=active_domains,
                record_usage=record_usage,
            )
            if scoped_results and record_usage:
                self._save_scope(s)
            results.extend(scoped_results)

        # Apply minimum relevance threshold
        # (entries are already scored by MemoryFile.search)
        if min_relevance > 0:
            if results:
                max_score = max(getattr(e, "_last_relevance", 0.0) for e in results)
                if max_score > 0:
                    results = [
                        e for e in results
                        if getattr(e, "_last_relevance", 0.0) / max_score >= min_relevance
                    ]

        ranked = sorted(
            results,
            key=lambda e: (
                -self._global_rank(e, active_domains=active_domains),
                -e.updated_at,
                e.scope.value,
                e.id,
            ),
        )

        # Deduplicate by normalized content after global ranking.
        seen_content: set[str] = set()
        deduped = []
        for entry in ranked:
            content_key = _normalize_memory_content(entry.content)[:200]
            if content_key not in seen_content:
                seen_content.add(content_key)
                deduped.append(entry)

        return deduped[:limit]

    def _global_rank(
        self,
        entry: MemoryEntry,
        active_domains: list[str] | None = None,
    ) -> float:
        """Rank results across scopes deterministically after per-file scoring."""
        scope_priority = {
            MemoryScope.LOCAL: 0.25,
            MemoryScope.PROJECT: 0.18,
            MemoryScope.USER: 0.10,
        }.get(entry.scope, 0.0)
        tier_priority = {
            MemoryTier.WORKING: 0.05,
            MemoryTier.SHORT_TERM: 0.08,
            MemoryTier.LONG_TERM: 0.12,
            MemoryTier.ARCHIVAL: -1.0,
        }.get(entry.tier, 0.0)
        domain_boost = 0.0
        if active_domains and entry.domains:
            active = set(active_domains)
            entry_domains = set(entry.domains)
            union = active | entry_domains
            domain_boost = (len(active & entry_domains) / len(union)) * 0.25 if union else 0.0
        age_hours = _bounded_age_hours(entry.updated_at)
        recency = 1.0 / (1.0 + age_hours / 24.0) * 0.10
        feedback = entry.usefulness_score * 0.25
        raw_relevance = getattr(entry, "_last_relevance", 0.0)
        return raw_relevance + scope_priority + tier_priority + domain_boost + recency + feedback

    def _score_entry(self, entry: MemoryEntry, query_tokens: list[str]) -> float:
        """Compute relevance score for a memory entry."""
        if not query_tokens:
            return 0.0

        query_tokens_expanded = _expand_query_terms(query_tokens)
        entry_tokens = _tokenize(
            f"{entry.content} {entry.category} {' '.join(entry.tags)}"
        )
        idf = _compute_idf([entry_tokens])
        avgdl = len(entry_tokens)
        bm25 = _bm25_score(query_tokens_expanded, entry_tokens, idf, avgdl)

        query_lower = " ".join(query_tokens).lower()
        content_lower = entry.content.lower()
        substring_score = 0.0
        if query_lower in content_lower:
            substring_score = 2.0
        elif any(q in content_lower for q in query_tokens):
            substring_score = 1.0

        tag_score = 0.0
        exact_tag_match = any(tag.lower() == query_lower for tag in entry.tags)
        partial_tag_match = any(query_lower in tag.lower() for tag in entry.tags)
        if exact_tag_match:
            tag_score = 5.0
        elif partial_tag_match:
            tag_score = 1.5
        if query_lower in entry.category.lower():
            tag_score += 1.0

        usage_bonus = math.log1p(entry.usage_count) * 0.3

        age_hours = _bounded_age_hours(entry.updated_at)
        recency_bonus = 1.0 / (1.0 + age_hours / 24.0) * 0.5

        return bm25 + substring_score + tag_score + usage_bonus + recency_bonus
    
    def get_relevant_context(
        self,
        max_entries: int = 20,
        max_tokens: int = 8000,
        query: str | None = None,
        *,
        current_files: list[str] | None = None,
        active_domains: list[str] | None = None,
        context_usage: float = 0.0,
        max_tokens_per_memory: int | None = None,
        min_relevance: float = 0.0,
    ) -> str:
        """Get relevant memory context for system prompt injection.
        
        Returns formatted MEMORY.md content from all scopes,
        respecting token limits.
        """
        from minicode.context_manager import estimate_tokens

        query = (query or "").strip()
        if query:
            from minicode.memory_retrieval import (
                CanonicalMemoryRetriever,
                MemoryRetrievalRequest,
                RetrievalSource,
            )

            result = CanonicalMemoryRetriever(self).retrieve(
                MemoryRetrievalRequest(
                    query=query,
                    current_files=tuple(current_files or ()),
                    active_domains=tuple(active_domains or ()),
                    context_usage=context_usage,
                    max_memories=max_entries,
                    max_total_tokens=max_tokens,
                    max_tokens_per_memory=(
                        max_tokens_per_memory
                        if max_tokens_per_memory is not None
                        else max_tokens
                    ),
                    min_relevance=min_relevance,
                    source_entrypoint=RetrievalSource.MANAGER_CONTEXT,
                )
            )
            self._last_retrieval_result = result
            self.record_retrievals(
                list(result.selected_ids),
                observation_id=f"manager-context-{uuid.uuid4().hex}",
            )
            return result.prompt_text
        
        parts = []
        total_tokens = 0
        
        # Priority order: LOCAL > PROJECT > USER
        for scope in [MemoryScope.LOCAL, MemoryScope.PROJECT, MemoryScope.USER]:
            memory = self.memories[scope]
            active_entries = [entry for entry in memory.entries if entry.is_active]
            if not active_entries:
                continue
            
            formatted = MemoryFile(scope=scope, entries=active_entries).format_as_markdown(include_header=True)
            tokens = estimate_tokens(formatted)
            
            if total_tokens + tokens <= max_tokens:
                parts.append(formatted)
                total_tokens += tokens
            else:
                # Partial: include only recent entries
                remaining_tokens = max_tokens - total_tokens
                partial_entries = active_entries[-max_entries:]
                partial_memory = MemoryFile(scope=scope, entries=partial_entries)
                formatted = partial_memory.format_as_markdown(include_header=True)
                
                if estimate_tokens(formatted) <= remaining_tokens:
                    parts.append(formatted)
                break
        
        if not parts:
            return ""
        
        return "\n\n".join(parts)

    def _find_entry_by_id(
        self, entry_id: str
    ) -> tuple[MemoryScope, MemoryEntry] | tuple[None, None]:
        for scope in MemoryScope:
            memory = self.memories.get(scope)
            if not memory:
                continue
            memory._ensure_cache_valid()
            entry = memory._id_index.get(entry_id)
            if entry is not None:
                return scope, entry
        return None, None

    def _prepare_scope_effect_receipt(
        self,
        observation_id: str | None,
        *,
        lane: str,
        channel: str,
        payload: object,
    ) -> tuple[str, str] | None:
        """Validate one operation identity against every committed scope."""

        if lane not in _OBSERVATION_RECEIPT_LANES:
            raise ValueError("Memory observation receipt lane is invalid")
        receipt = _operation_observation_receipt(
            observation_id,
            channel=channel,
            payload=payload,
        )
        if receipt is None:
            return None
        observation_digest, payload_digest = receipt
        for scope in MemoryScope:
            previous = self._observation_receipts[scope][lane].get(
                observation_digest
            )
            if previous is not None and previous != payload_digest:
                raise MemoryStoreConflict(
                    "Memory observation operation payload changed"
                )
        return receipt

    def _claim_scope_effect_receipt(
        self,
        scope: MemoryScope,
        *,
        lane: str,
        receipt: tuple[str, str] | None,
    ) -> bool:
        """Claim a scope-local effect, returning false on an exact replay."""

        if receipt is None:
            return True
        observation_digest, payload_digest = receipt
        lane_receipts = self._observation_receipts[scope][lane]
        previous = lane_receipts.get(observation_digest)
        if previous is not None:
            if previous != payload_digest:
                raise MemoryStoreConflict(
                    "Memory observation operation payload changed"
                )
            return False
        lane_receipts[observation_digest] = payload_digest
        while len(lane_receipts) > _MAX_OBSERVATION_RECEIPTS_PER_LANE:
            lane_receipts.pop(next(iter(lane_receipts)))
        return True

    @staticmethod
    def _require_replay_identity_for_multiple_scopes(
        scopes: set[MemoryScope],
        receipt: tuple[str, str] | None,
    ) -> None:
        """Refuse an unidentifiable non-idempotent multi-root mutation."""

        if len(scopes) > 1 and receipt is None:
            raise MemoryStoreConflict(
                "A stable observation_id is required for multi-scope Memory accounting"
            )

    @_coordinated_all_write
    def record_injections(
        self,
        entry_ids: list[str],
        *,
        observation_id: str | None = None,
    ) -> None:
        """Persist actual injection, replay-safe when an observation ID is supplied."""

        normalized_ids = tuple(sorted(set(entry_ids)))
        receipt = self._prepare_scope_effect_receipt(
            observation_id,
            lane="telemetry",
            channel="injection",
            payload={"entry_ids": normalized_ids},
        )
        by_scope: dict[MemoryScope, list[MemoryEntry]] = {}
        for entry_id in normalized_ids:
            scope, entry = self._find_entry_by_id(entry_id)
            if scope is None or entry is None:
                logger.debug(
                    "Memory injection feedback skipped missing entry_id=%s",
                    entry_id,
                )
                continue
            by_scope.setdefault(scope, []).append(entry)
        self._require_replay_identity_for_multiple_scopes(set(by_scope), receipt)
        now = time.time()
        for scope in sorted(by_scope, key=lambda item: item.value):
            if not self._claim_scope_effect_receipt(
                scope,
                lane="telemetry",
                receipt=receipt,
            ):
                continue
            for entry in by_scope[scope]:
                entry.injection_count = _saturating_counter_add(
                    entry.injection_count
                )
                entry.last_used = now
                entry.last_accessed = now
            self._save_scope(scope)

    @_coordinated_all_write
    def reinforce_reflection_entry(
        self,
        entry_id: str,
        *,
        return_version: bool = False,
    ) -> bool | tuple[str, str]:
        """Record that a lesson recurred in a later trace.

        Recurrence, not repetition, is what makes a lesson trustworthy: the
        same semantic claim observed again strengthens the existing entry
        instead of queueing a near-duplicate for review.
        """
        scope, entry = self._find_entry_by_id(entry_id)
        if scope is None or entry is None:
            return False
        previous_approval = entry.approval_status
        previous_lifecycle = entry.lifecycle_status
        previous_authority_digest = _approval_hash_for_entry(entry)
        metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
        metadata["recurrence"] = int(metadata.get("recurrence", 1) or 1) + 1
        entry.metadata = metadata
        provenance = entry.provenance if isinstance(entry.provenance, dict) else {}
        provenance["last_recurred_at"] = time.time()
        entry.provenance = provenance
        # Each recurrence is an independent later trace re-observing the same
        # semantic claim. After enough independent observations a pending
        # lesson is corroborated evidence, not a one-off guess: promote it to
        # approved so the injection valve can actually use it. Safety class
        # is re-checked; anything not plainly safe stays pending.
        promoted_to_approved = False
        recurrence_count = int(metadata["recurrence"])
        draft = metadata.get("deposition_draft")
        draft_reasons = draft.get("reason_codes", ()) if isinstance(draft, dict) else ()
        reflection = metadata.get("structured_reflection")
        claims = reflection.get("claims", ()) if isinstance(reflection, dict) else ()
        # Reading the same declaration again never verifies that dependency.
        # Inspect the stored evidence, independent of today's experiment
        # mode, so rollback cannot turn an old declaration into approval.
        # The claim fallback recognizes the persisted manifest caveat;
        # unrelated inferred dependencies keep historical recurrence rules.
        declared_dependency = (
            isinstance(draft_reasons, (list, tuple))
            and "declared_dependency" in draft_reasons
        ) or (
            isinstance(claims, (list, tuple))
            and any(
                isinstance(claim, dict)
                and claim.get("claim_type") == "dependency"
                and claim.get("epistemic_status") == "inferred"
                and isinstance(claim.get("limitations"), (list, tuple))
                and "Declared in manifest; not yet exercised in this trace."
                in claim["limitations"]
                for claim in claims
            )
        )
        if (
            previous_approval == _APPROVAL_PENDING
            and entry.safety_status == _SAFETY_SAFE
            and recurrence_count >= RECURRENCE_AUTO_APPROVE_THRESHOLD
            and not declared_dependency
        ):
            entry.approval_status = _APPROVAL_APPROVED
            entry.approval_reason = (
                f"recurrence corroborated across {recurrence_count} independent traces"
            )
            entry.approval_actor = "reflection"
            entry.approval_decided_at = time.time()
            promoted_to_approved = True
        reset_observations = (
            _reset_version_scoped_observations(entry)
            if _approval_hash_for_entry(entry) != previous_authority_digest
            else None
        )
        entry.updated_at = time.time()
        entry.invalidate_tokens()
        self.memories[scope]._invalidate_cache()
        # Metadata and provenance are part of the approval hash. Recurrence
        # strengthens an already-approved lesson, so the hash must be
        # refreshed in the same write; otherwise the next process load sees a
        # mismatch and silently demotes the approved lesson back to pending.
        entry.approval_content_hash = _approval_hash_for_entry(entry)
        self._append_approval_audit(
            scope,
            entry,
            action=(
                "recurrence_promoted"
                if promoted_to_approved
                else "recurrence_reinforced"
            ),
            actor="reflection",
            previous_approval=previous_approval,
            previous_lifecycle=previous_lifecycle,
            reason=(
                "pending lesson corroborated by independent recurrence"
                if promoted_to_approved
                else "semantic lesson recurrence observed"
            ),
            extra={
                "previous_authority_digest": previous_authority_digest,
                "version_observations_reset": reset_observations,
                "recurrence_count": recurrence_count,
            },
        )
        self._save_scope(scope)
        if return_version:
            return entry.id, entry.approval_content_hash
        return True

    def record_user_facet_observation(
        self,
        facet_key: str,
        value: str,
        *,
        source: str = "conversation",
    ) -> tuple[str, str | None, int]:
        """Record one independent observation of a personal facet.

        Personal facets live in the USER scope so they can follow the user
        across projects, which is exactly why nothing here auto-approves:
        the first observation creates a *pending* candidate, repeats only
        accumulate an observation count, and a previously rejected facet is
        never re-created (negative memory).  A human confirms each facet
        exactly once via the normal review flow.
        """

        def _operation() -> tuple[str, str | None, int]:
            return self._record_user_facet_observation(
                facet_key, value, source=source
            )

        return self.coordinated_write((MemoryScope.USER,), _operation)

    def _record_user_facet_observation(
        self,
        facet_key: str,
        value: str,
        *,
        source: str = "conversation",
    ) -> tuple[str, str | None, int]:
        scope = MemoryScope.USER
        key = str(facet_key or "").strip().lower() or "background"
        text = str(value or "").strip()
        if not text:
            return ("invalid", None, 0)
        content = f"{key}: {text}"[:400]

        normalized_content = _normalize_memory_content(content)
        for entry in self.memories[scope].entries:
            if entry.category != USER_PROFILE_CATEGORY:
                continue
            if _normalize_memory_content(entry.content) != normalized_content:
                continue
            if entry.approval_status == _APPROVAL_APPROVED:
                return ("known", entry.id, int(
                    (entry.metadata or {}).get("facet_observations", 1)
                ))
            if entry.approval_status == _APPROVAL_REJECTED:
                # Negative memory: the user rejected this exact facet once;
                # do not re-observe it back into the review queue.
                return ("suppressed", entry.id, 0)

            previous_digest = _approval_hash_for_entry(entry)
            metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
            observations = int(metadata.get("facet_observations", 1) or 1) + 1
            metadata["facet_observations"] = observations
            metadata["facet"] = key
            entry.metadata = metadata
            provenance = entry.provenance if isinstance(entry.provenance, dict) else {}
            provenance["last_facet_observed_at"] = time.time()
            entry.provenance = provenance
            if observations >= USER_FACET_REVIEW_THRESHOLD:
                entry.approval_reason = (
                    f"{observations} independent observations; awaiting "
                    "one-time user confirmation"
                )
            # Metadata and provenance participate in the approval hash, so
            # the observation bump must refresh it inside this same write.
            entry.approval_content_hash = _approval_hash_for_entry(entry)
            entry.updated_at = time.time()
            entry.invalidate_tokens()
            self.memories[scope]._invalidate_cache()
            self._append_approval_audit(
                scope,
                entry,
                action="user_facet_observed",
                actor="pipeline",
                previous_approval=entry.approval_status,
                previous_lifecycle=entry.lifecycle_status,
                reason=(
                    f"independent facet observation #{observations} "
                    f"(facet: {key})"
                ),
                extra={
                    "previous_authority_digest": previous_digest,
                    "facet": key,
                    "facet_observations": observations,
                },
            )
            self._save_scope(scope)
            return ("observed", entry.id, observations)

        entry = self.add_entry(
            scope,
            USER_PROFILE_CATEGORY,
            content,
            tags=["user-facet"],
            source=source,
            metadata={"facet": key, "facet_observations": 1},
            approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
        )
        if entry is None:
            return ("rejected", None, 0)
        return ("created", entry.id, 1)

    def _apply_declared_supersession(self, replacement: MemoryEntry) -> int:
        """Retract older claim entries named by an approved replacement.

        A pending replacement may only retract pending predecessors — the
        review queue should not hold two contradictory candidates for one
        semantic key. Retracting an approved lesson still requires the
        replacement itself to be approved.
        """

        metadata = replacement.metadata if isinstance(replacement.metadata, dict) else {}
        supersedes = metadata.get("supersedes", [])
        if not isinstance(supersedes, list):
            return 0
        replacement_approved = replacement.is_active
        changed = 0
        for old_id in dict.fromkeys(str(value) for value in supersedes if str(value)):
            old_scope, old = self._find_entry_by_id(old_id)
            if old_scope is None or old is None or old.id == replacement.id:
                continue
            if old_scope is not replacement.scope:
                raise MemoryStoreConflict(
                    "Memory supersession cannot cross authority scopes"
                )
            if old.lifecycle_status == "superseded":
                continue
            if not replacement_approved and old.approval_status == _APPROVAL_APPROVED:
                # A pending replacement cannot retire an approved lesson;
                # only a reviewed replacement carries that authority.
                continue
            previous_approval = old.approval_status
            previous_lifecycle = old.lifecycle_status
            old.approval_status = _APPROVAL_REJECTED
            old.approval_reason = f"superseded by {replacement.id}"
            old.approval_actor = "claim_repository"
            old.approval_decided_at = time.time()
            old.lifecycle_status = "superseded"
            old.curator_locked = False
            old.updated_at = time.time()
            old_metadata = old.metadata if isinstance(old.metadata, dict) else {}
            previous_authority_digest = _approval_hash_for_entry(old)
            old_metadata["superseded_by"] = replacement.id
            old.metadata = old_metadata
            reset_observations = (
                _reset_version_scoped_observations(old)
                if _approval_hash_for_entry(old) != previous_authority_digest
                else None
            )
            old.approval_content_hash = _approval_hash_for_entry(old)
            self.memories[old_scope]._invalidate_cache()
            self._append_approval_audit(
                old_scope,
                old,
                action="superseded",
                actor="claim_repository",
                previous_approval=previous_approval,
                previous_lifecycle=previous_lifecycle,
                reason=old.approval_reason,
                extra={
                    "replacement_id": replacement.id,
                    "previous_authority_digest": previous_authority_digest,
                    "version_observations_reset": reset_observations,
                },
            )
            changed += 1
        return changed

    @_coordinated_all_write
    def apply_reflection_supersession(self, replacement_id: str) -> int:
        scope, replacement = self._find_entry_by_id(replacement_id)
        if scope is None or replacement is None:
            return 0
        changed = self._apply_declared_supersession(replacement)
        if changed:
            self._save_scope(scope)
        return changed

    @staticmethod
    def _reflection_identity(
        entry: MemoryEntry,
    ) -> tuple[str, str] | None:
        """Return the canonical semantic key and statement for one lesson."""

        metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
        identity = metadata.get("claim_identity")
        identity_key = ""
        identity_statement = ""
        if isinstance(identity, dict):
            identity_key = str(identity.get("semantic_key", "") or "")
            identity_statement = str(identity.get("statement", "") or "").strip()
            if identity_key and identity_statement:
                return identity_key, identity_statement

        reflection = metadata.get("structured_reflection")
        claims = reflection.get("claims") if isinstance(reflection, dict) else None
        if not isinstance(claims, list):
            return None
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            semantic_key = str(claim.get("semantic_key", "") or "")
            statement = str(claim.get("statement", "") or "").strip()
            if identity_key and semantic_key != identity_key:
                continue
            if semantic_key and statement:
                return semantic_key, statement
        return None

    def _find_reflection_entry_locked(
        self,
        semantic_key: str,
    ) -> tuple[MemoryEntry | None, str]:
        """Resolve a lesson identity from the freshly loaded Project scope."""

        memory_file = self.memories[MemoryScope.PROJECT]
        # Newer replacements appear later.  Searching in reverse avoids
        # reviving a superseded generation when legacy stores contain both.
        for entry in reversed(memory_file.entries):
            identity = self._reflection_identity(entry)
            if identity is not None and identity[0] == semantic_key:
                return entry, identity[1]
        return None, ""

    def _assert_memory_operation_scope_available(
        self,
        operation_id: str,
        *,
        target_scope: MemoryScope,
    ) -> None:
        """Fence one logical write to exactly one Memory authority scope."""

        for other_scope in (MemoryScope.PROJECT, MemoryScope.USER):
            if other_scope is target_scope:
                continue
            if (
                operation_id in self._memory_write_receipts[other_scope]
                or operation_id
                in self._pipeline_operation_receipts[other_scope]
            ):
                raise MemoryStoreConflict(
                    "Memory write operation already committed in another "
                    "authority scope"
                )

    def _upgrade_authority_operation_envelope_locked(
        self,
        *,
        scope: MemoryScope,
        operation_id: str,
        aggregate_digest: str,
        prior: MemoryAuthorityCommit,
    ) -> MemoryAuthorityCommit:
        """Atomically bind an existing authority receipt to its Pipeline digest.

        Authority receipts written before Pipeline-wide operation envelopes used
        the exact lesson/fact digest as ``payload_digest``.  A current Pipeline
        retry instead supplies its aggregate digest.  Once the caller has
        independently proved that the legacy receipt matches the exact payload,
        migrate both records in the same authority replace.  A newer aggregate
        receipt whose envelope is missing takes the same repair path without
        changing its materializations.
        """

        if not self._transaction_covers((MemoryScope.PROJECT, MemoryScope.USER)):
            raise MemoryStoreConflict(
                "Memory receipt migration requires a store transaction"
            )
        if self._deferred_batch_scope is not None:
            raise MemoryStoreConflict("Nested atomic Memory batches are not supported")

        original_memory = self.memories[scope]
        original_audit = self.approval_audit[scope]
        original_authority_receipts = self._memory_write_receipts[scope]
        original_pipeline_receipts = self._pipeline_operation_receipts[scope]
        self.memories[scope] = copy.deepcopy(original_memory)
        self.approval_audit[scope] = copy.deepcopy(original_audit)
        self._memory_write_receipts[scope] = dict(original_authority_receipts)
        self._pipeline_operation_receipts[scope] = dict(
            original_pipeline_receipts
        )
        committed = False
        try:
            migrated = MemoryAuthorityCommit(
                operation_id=prior.operation_id,
                payload_digest=aggregate_digest,
                scope=prior.scope,
                materializations=prior.materializations,
                rejected_claim_ids=prior.rejected_claim_ids,
                receipt_version=prior.receipt_version,
            )
            self._memory_write_receipts[scope][operation_id] = migrated
            pipeline_receipts = self._pipeline_operation_receipts[scope]
            pipeline_receipts[operation_id] = aggregate_digest
            while len(pipeline_receipts) > _MAX_PIPELINE_OPERATION_RECEIPTS:
                pipeline_receipts.pop(next(iter(pipeline_receipts)))
            self._save_scope_locked(scope)
            committed = True
            return migrated.as_replay()
        finally:
            if not committed:
                self.memories[scope] = original_memory
                self.approval_audit[scope] = original_audit
                self._memory_write_receipts[
                    scope
                ] = original_authority_receipts
                self._pipeline_operation_receipts[
                    scope
                ] = original_pipeline_receipts

    def _resolve_authority_operation_replay_locked(
        self,
        *,
        scope: MemoryScope,
        operation_id: str,
        aggregate_digest: str,
        exact_authority_digest: str,
        pipeline_envelope_requested: bool,
    ) -> MemoryAuthorityCommit | None:
        """Resolve, upgrade, or reject a prior single-scope authority write."""

        envelope_digest = self._pipeline_operation_receipts[scope].get(
            operation_id
        )
        if envelope_digest is not None and envelope_digest != aggregate_digest:
            raise MemoryStoreConflict("Memory write operation payload changed")

        prior = self._memory_write_receipts[scope].get(operation_id)
        if prior is None:
            if envelope_digest is not None:
                raise MemoryStoreConflict(
                    "Memory write operation already completed without authority"
                )
            return None
        if not isinstance(prior, MemoryAuthorityCommit):
            raise MemoryStoreConflict("Memory write receipt state is invalid")

        if envelope_digest is not None:
            # The strict loader (and canonical serializer) require these two
            # records to agree.  Keep the runtime guard for in-memory tampering.
            if prior.payload_digest != aggregate_digest:
                raise MemoryStoreConflict("Memory write operation payload changed")
            return prior.as_replay()

        if not pipeline_envelope_requested:
            # Compatibility for direct pre-envelope callers: exact replays are
            # byte-stable and do not manufacture a Pipeline provenance record.
            if prior.payload_digest != aggregate_digest:
                raise MemoryStoreConflict("Memory write operation payload changed")
            return prior.as_replay()

        if prior.payload_digest not in {
            aggregate_digest,
            exact_authority_digest,
        }:
            raise MemoryStoreConflict("Memory write operation payload changed")
        return self._upgrade_authority_operation_envelope_locked(
            scope=scope,
            operation_id=operation_id,
            aggregate_digest=aggregate_digest,
            prior=prior,
        )

    def _reconcile_pipeline_operation_locked(
        self,
        *,
        operation_id: str,
        aggregate_digest: str,
        authority_kind: str,
        authority_payload_digest: str | None,
        project_facts_payload_digest: str | None,
        project_facts_receipt: Any,
    ) -> str:
        """Reconcile one legacy operation while both authority locks are held."""

        if not self._transaction_covers((MemoryScope.PROJECT, MemoryScope.USER)):
            raise MemoryStoreConflict(
                "Memory Pipeline reconciliation requires a store transaction"
            )

        project_authority = self._memory_write_receipts[
            MemoryScope.PROJECT
        ].get(operation_id)
        user_authority = self._memory_write_receipts[MemoryScope.USER].get(
            operation_id
        )
        project_envelope = self._pipeline_operation_receipts[
            MemoryScope.PROJECT
        ].get(operation_id)
        user_envelope = self._pipeline_operation_receipts[
            MemoryScope.USER
        ].get(operation_id)

        project_state_exists = (
            project_authority is not None or project_envelope is not None
        )
        user_state_exists = user_authority is not None or user_envelope is not None
        if project_state_exists and user_state_exists:
            raise MemoryStoreConflict(
                "Memory write operation already spans multiple authority scopes"
            )

        facts_receipt_digest = None
        if project_facts_receipt is not None:
            receipt_operation_id = getattr(
                project_facts_receipt,
                "operation_id",
                None,
            )
            facts_receipt_digest = getattr(
                project_facts_receipt,
                "payload_digest",
                None,
            )
            if (
                receipt_operation_id != operation_id
                or not isinstance(facts_receipt_digest, str)
                or _MEMORY_WRITE_DIGEST_RE.fullmatch(facts_receipt_digest) is None
            ):
                raise MemoryStoreConflict(
                    "Project Facts operation receipt state is invalid"
                )
            if (
                project_facts_payload_digest is None
                or facts_receipt_digest != project_facts_payload_digest
            ):
                raise MemoryStoreConflict(
                    "Memory write operation Project Facts payload changed"
                )

        envelope_scope: MemoryScope | None = None
        envelope_digest: str | None = None
        if project_envelope is not None:
            envelope_scope = MemoryScope.PROJECT
            envelope_digest = project_envelope
        elif user_envelope is not None:
            envelope_scope = MemoryScope.USER
            envelope_digest = user_envelope

        if envelope_scope is not None:
            if envelope_digest != aggregate_digest:
                raise MemoryStoreConflict("Memory write operation payload changed")
            if authority_kind == "lesson":
                shape_matches = (
                    envelope_scope is MemoryScope.PROJECT
                    and isinstance(project_authority, MemoryAuthorityCommit)
                    and user_authority is None
                )
            elif authority_kind == "conversation_fact":
                shape_matches = (
                    envelope_scope is MemoryScope.USER
                    and isinstance(user_authority, MemoryAuthorityCommit)
                    and project_authority is None
                    and project_facts_payload_digest is None
                    and facts_receipt_digest is None
                )
            else:
                shape_matches = (
                    envelope_scope is MemoryScope.PROJECT
                    and project_authority is None
                    and user_authority is None
                )
            if not shape_matches:
                raise MemoryStoreConflict(
                    "Memory write operation output kind changed"
                )
            return "already_bound"

        if project_authority is not None:
            if (
                not isinstance(project_authority, MemoryAuthorityCommit)
                or authority_kind != "lesson"
                or authority_payload_digest is None
                or user_authority is not None
            ):
                raise MemoryStoreConflict(
                    "Memory write operation output kind changed"
                )
            if project_authority.payload_digest not in {
                aggregate_digest,
                authority_payload_digest,
            }:
                raise MemoryStoreConflict("Memory write operation payload changed")
            # An exact Project receipt plus current facts can only be proven to
            # be a legacy mixed operation when its second authority receipt is
            # still present.  Missing evidence is ambiguous, so fail closed.
            if (
                project_authority.payload_digest == authority_payload_digest
                and project_facts_payload_digest is not None
                and facts_receipt_digest is None
            ):
                raise MemoryStoreConflict(
                    "Legacy mixed Memory operation is missing Project Facts receipt"
                )
            self._upgrade_authority_operation_envelope_locked(
                scope=MemoryScope.PROJECT,
                operation_id=operation_id,
                aggregate_digest=aggregate_digest,
                prior=project_authority,
            )
            return "authority_migrated"

        if user_authority is not None:
            if (
                not isinstance(user_authority, MemoryAuthorityCommit)
                or authority_kind != "conversation_fact"
                or authority_payload_digest is None
                or project_facts_payload_digest is not None
                or facts_receipt_digest is not None
            ):
                raise MemoryStoreConflict(
                    "Memory write operation output kind changed"
                )
            if user_authority.payload_digest not in {
                aggregate_digest,
                authority_payload_digest,
            }:
                raise MemoryStoreConflict("Memory write operation payload changed")
            self._upgrade_authority_operation_envelope_locked(
                scope=MemoryScope.USER,
                operation_id=operation_id,
                aggregate_digest=aggregate_digest,
                prior=user_authority,
            )
            return "authority_migrated"

        if facts_receipt_digest is not None:
            if authority_kind != "none" or project_facts_payload_digest is None:
                raise MemoryStoreConflict(
                    "Memory write operation output kind changed"
                )
            self._bind_memory_write_operation_locked(
                operation_id=operation_id,
                payload_digest=aggregate_digest,
                scope=MemoryScope.PROJECT,
            )
            return "facts_only_migrated"

        return "new"

    def reconcile_pipeline_operation(
        self,
        *,
        project_facts_store: Any,
        operation_id: str,
        aggregate_digest: str,
        authority_kind: str,
        authority_payload_digest: str | None = None,
        project_facts_payload_digest: str | None = None,
    ) -> str:
        """Reconcile retained legacy receipts before a Pipeline write.

        Lock order is fixed as canonical Memory then Project Facts.  The facts
        guard is read-only and remains held through the sole possible migration
        commit in ``memory.json``.  This closes the old inspect-then-bind race
        without claiming an atomic transaction across two authority files.
        """

        if (
            not isinstance(operation_id, str)
            or _MEMORY_WRITE_OPERATION_RE.fullmatch(operation_id) is None
        ):
            raise ValueError("Memory operation ID is invalid")
        if (
            not isinstance(aggregate_digest, str)
            or _MEMORY_WRITE_DIGEST_RE.fullmatch(aggregate_digest) is None
        ):
            raise ValueError("Memory payload digest is invalid")
        if authority_kind not in {"none", "lesson", "conversation_fact"}:
            raise ValueError("Memory authority kind is invalid")
        if authority_kind == "none":
            if authority_payload_digest is not None:
                raise ValueError(
                    "Authority-free Memory operation cannot carry a payload digest"
                )
        elif (
            not isinstance(authority_payload_digest, str)
            or _MEMORY_WRITE_DIGEST_RE.fullmatch(authority_payload_digest) is None
        ):
            raise ValueError("Memory authority payload digest is invalid")
        if project_facts_payload_digest is not None and (
            not isinstance(project_facts_payload_digest, str)
            or _MEMORY_WRITE_DIGEST_RE.fullmatch(project_facts_payload_digest) is None
        ):
            raise ValueError("Project Facts payload digest is invalid")
        if (
            authority_kind == "conversation_fact"
            and project_facts_payload_digest is not None
        ):
            raise ValueError(
                "Conversation fact operations cannot include Project Facts"
            )

        receipt_guard = getattr(
            project_facts_store,
            "operation_receipt_guard",
            None,
        )
        if not callable(receipt_guard):
            raise TypeError("Project Facts store lacks the receipt guard")

        scopes = (MemoryScope.PROJECT, MemoryScope.USER)
        with self._transaction_for_scopes(scopes):
            # Reload unconditionally under the first lock.  A revision-only
            # fast path would leave a stale in-memory legacy receipt view.
            self._reload_scopes(scopes)
            try:
                with receipt_guard(operation_id) as project_facts_receipt:
                    return self._reconcile_pipeline_operation_locked(
                        operation_id=operation_id,
                        aggregate_digest=aggregate_digest,
                        authority_kind=authority_kind,
                        authority_payload_digest=authority_payload_digest,
                        project_facts_payload_digest=project_facts_payload_digest,
                        project_facts_receipt=project_facts_receipt,
                    )
            except BaseException:
                # Never retain an in-memory migration that did not reach its
                # one canonical Memory replace.
                try:
                    self._reload_scopes(scopes)
                except BaseException:
                    pass
                raise

    @staticmethod
    def _reflection_entry_is_rejected(entry: MemoryEntry) -> bool:
        return (
            entry.approval_status == _APPROVAL_REJECTED
            or entry.lifecycle_status in {"rejected", "superseded", "evicted"}
        )

    def _commit_reflection_batch_locked(
        self,
        *,
        operation_id: str,
        payload_digest: str,
        exact_authority_digest: str,
        pipeline_envelope_requested: bool,
        claims: tuple[ReflectionClaimWrite, ...],
    ) -> MemoryAuthorityCommit:
        """Stage and commit one already-validated Project batch under the lock."""

        if not self._transaction_covers((MemoryScope.PROJECT, MemoryScope.USER)):
            raise MemoryStoreConflict("Reflection batch requires a store transaction")
        scope = MemoryScope.PROJECT
        self._assert_memory_operation_scope_available(
            operation_id,
            target_scope=scope,
        )
        replay = self._resolve_authority_operation_replay_locked(
            scope=scope,
            operation_id=operation_id,
            aggregate_digest=payload_digest,
            exact_authority_digest=exact_authority_digest,
            pipeline_envelope_requested=pipeline_envelope_requested,
        )
        if replay is not None:
            return replay
        if self._deferred_batch_scope is not None:
            raise MemoryStoreConflict("Nested atomic Memory batches are not supported")

        original_memory = self.memories[scope]
        original_audit = self.approval_audit[scope]
        original_receipts = self._memory_write_receipts[scope]
        original_pipeline_receipts = self._pipeline_operation_receipts[scope]
        self.memories[scope] = copy.deepcopy(original_memory)
        self.approval_audit[scope] = copy.deepcopy(original_audit)
        self._memory_write_receipts[scope] = dict(original_receipts)
        self._pipeline_operation_receipts[scope] = dict(
            original_pipeline_receipts
        )
        self._deferred_batch_scope = scope
        committed = False
        staged_materializations: list[MemoryMaterialization] = []
        rejected_claim_ids: list[str] = []
        try:
            for claim in claims:
                existing, existing_statement = self._find_reflection_entry_locked(
                    claim.semantic_key
                )
                if existing is not None and existing_statement == claim.statement:
                    if self._reflection_entry_is_rejected(existing):
                        rejected_claim_ids.append(claim.claim_id)
                        continue
                    reinforced = self.reinforce_reflection_entry(
                        existing.id,
                        return_version=True,
                    )
                    if not (
                        isinstance(reinforced, tuple)
                        and len(reinforced) == 2
                        and all(isinstance(value, str) for value in reinforced)
                    ):
                        raise MemoryStoreConflict(
                            "Reflection recurrence disappeared during atomic write"
                        )
                    staged_materializations.append(
                        MemoryMaterialization(
                            claim_id=claim.claim_id,
                            entry_id=reinforced[0],
                            authority_digest=reinforced[1],
                            disposition="reinforced",
                        )
                    )
                    continue

                add_entry_kwargs = copy.deepcopy(claim.add_entry_kwargs)
                metadata_value = add_entry_kwargs.get("metadata", {})
                if not isinstance(metadata_value, dict):
                    raise TypeError("reflection entry metadata must be a dict")
                metadata = dict(metadata_value)
                metadata["claim_identity"] = {
                    "claim_id": claim.claim_id,
                    "semantic_key": claim.semantic_key,
                    "statement": claim.statement,
                }
                # Supersession is authority derived inside this lock.  A
                # caller-provided target could otherwise retract an unrelated
                # or cross-scope entry.
                metadata.pop("supersedes", None)
                if existing is not None:
                    metadata["supersedes"] = [existing.id]
                add_entry_kwargs["metadata"] = metadata

                entry = self.add_entry(
                    scope=scope,
                    allow_duplicate=True,
                    **add_entry_kwargs,
                )
                if entry is None:
                    rejected_claim_ids.append(claim.claim_id)
                    continue

                superseded_ids: tuple[str, ...] = ()
                if existing is not None:
                    # The supersession gate inside decides by authority:
                    # a pending replacement may retire pending predecessors
                    # but never an approved lesson.
                    changed = self._apply_declared_supersession(entry)
                    if changed and existing.lifecycle_status == "superseded":
                        superseded_ids = (existing.id,)
                staged_materializations.append(
                    MemoryMaterialization(
                        claim_id=claim.claim_id,
                        entry_id=entry.id,
                        authority_digest=entry.approval_content_hash,
                        disposition="created",
                        superseded_entry_ids=superseded_ids,
                    )
                )

            # Canonicalize before binding receipt versions.  ``add_entry``
            # already applies the write sanitizer to content/metadata/
            # provenance, but tags and domains are normalized at the final
            # persistence boundary.  Binding a receipt before that pass could
            # leave its digest referring to a pre-canonical in-memory shape
            # that never became authority.
            serialized_entries = self._canonicalize_scope_authority(scope)

            # A later claim can evict an earlier staged entry.  Only entries
            # present in the final authority may become written-version
            # provenance; every displaced claim is an explicit rejection.
            final_memory = self.memories[scope]
            final_memory._ensure_cache_valid()
            materializations: list[MemoryMaterialization] = []
            for materialization in staged_materializations:
                entry_id = materialization.entry_id
                current = (
                    final_memory._id_index.get(entry_id)
                    if entry_id is not None
                    else None
                )
                if current is None:
                    rejected_claim_ids.append(materialization.claim_id)
                    continue
                materializations.append(
                    MemoryMaterialization(
                        claim_id=materialization.claim_id,
                        entry_id=current.id,
                        authority_digest=_approval_hash_for_entry(current),
                        disposition=materialization.disposition,
                        superseded_entry_ids=materialization.superseded_entry_ids,
                    )
                )

            commit = MemoryAuthorityCommit(
                operation_id=operation_id,
                payload_digest=payload_digest,
                scope=scope.value,
                materializations=tuple(materializations),
                rejected_claim_ids=tuple(dict.fromkeys(rejected_claim_ids)),
            )
            receipts = self._memory_write_receipts[scope]
            receipts[operation_id] = commit
            while len(receipts) > _MAX_MEMORY_WRITE_RECEIPTS:
                receipts.pop(next(iter(receipts)))
            pipeline_receipts = self._pipeline_operation_receipts[scope]
            pipeline_receipts[operation_id] = payload_digest
            while (
                len(pipeline_receipts) > _MAX_PIPELINE_OPERATION_RECEIPTS
            ):
                pipeline_receipts.pop(next(iter(pipeline_receipts)))

            # All public mutators above were redirected to the staged snapshot.
            # This is the batch's only canonical authority replace.
            self._save_scope_locked(
                scope,
                serialized_entries=serialized_entries,
            )
            committed = True
            return commit
        finally:
            self._deferred_batch_scope = None
            if not committed:
                self.memories[scope] = original_memory
                self.approval_audit[scope] = original_audit
                self._memory_write_receipts[scope] = original_receipts
                self._pipeline_operation_receipts[
                    scope
                ] = original_pipeline_receipts

    def commit_reflection_batch(
        self,
        *,
        operation_id: str,
        payload_digest: str,
        claims: tuple[ReflectionClaimWrite, ...] | list[ReflectionClaimWrite],
        authority_payload_digest: str | None = None,
    ) -> MemoryAuthorityCommit:
        """Atomically create/reinforce one bounded batch of Project lessons.

        The payload digest is independently recomputed before entering the
        transaction, so an operation ID cannot be replayed against different
        content merely by reusing the old caller-supplied digest. Replay
        protection covers the 128 most recently committed operations in this
        scope; the bound prevents receipt metadata from growing without limit.
        """

        if (
            not isinstance(operation_id, str)
            or _MEMORY_WRITE_OPERATION_RE.fullmatch(operation_id) is None
        ):
            raise ValueError("Memory operation ID is invalid")
        if (
            not isinstance(payload_digest, str)
            or _MEMORY_WRITE_DIGEST_RE.fullmatch(payload_digest) is None
        ):
            raise ValueError("Memory payload digest is invalid")
        if not isinstance(claims, (list, tuple)):
            raise TypeError("claims must be a list or tuple")
        if not claims or len(claims) > MAX_MEMORY_CLAIMS_PER_BATCH:
            raise ValueError("reflection claim batch size is invalid")
        snapshot_items: list[ReflectionClaimWrite] = []
        for claim in claims:
            if not isinstance(claim, ReflectionClaimWrite):
                raise TypeError("claims must contain ReflectionClaimWrite values")
            snapshot_items.append(
                ReflectionClaimWrite(
                    claim_id=claim.claim_id,
                    semantic_key=claim.semantic_key,
                    statement=claim.statement,
                    add_entry_kwargs=claim.add_entry_kwargs,
                )
            )
        snapshot = tuple(snapshot_items)
        claim_ids = [claim.claim_id for claim in snapshot]
        semantic_keys = [claim.semantic_key for claim in snapshot]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("reflection batch contains duplicate claim IDs")
        if len(set(semantic_keys)) != len(semantic_keys):
            raise ValueError("reflection batch contains duplicate semantic keys")
        exact_authority_digest = (
            payload_digest
            if authority_payload_digest is None
            else authority_payload_digest
        )
        if (
            not isinstance(exact_authority_digest, str)
            or _MEMORY_WRITE_DIGEST_RE.fullmatch(exact_authority_digest) is None
        ):
            raise ValueError("Memory authority payload digest is invalid")
        if reflection_batch_payload_digest(snapshot) != exact_authority_digest:
            raise MemoryStoreConflict("Memory reflection payload digest does not match")

        return self.coordinated_write(
            (MemoryScope.PROJECT, MemoryScope.USER),
            lambda: self._commit_reflection_batch_locked(
                operation_id=operation_id,
                payload_digest=payload_digest,
                exact_authority_digest=exact_authority_digest,
                pipeline_envelope_requested=(
                    authority_payload_digest is not None
                ),
                claims=snapshot,
            ),
        )

    def _commit_conversation_fact_locked(
        self,
        *,
        operation_id: str,
        payload_digest: str,
        exact_authority_digest: str,
        pipeline_envelope_requested: bool,
        claim_id: str,
        add_entry_kwargs: dict[str, Any],
    ) -> MemoryAuthorityCommit:
        """Stage and commit one conversation fact under fresh User authority."""

        if not self._transaction_covers((MemoryScope.PROJECT, MemoryScope.USER)):
            raise MemoryStoreConflict(
                "Conversation fact commit requires a store transaction"
            )
        scope = MemoryScope.USER
        self._assert_memory_operation_scope_available(
            operation_id,
            target_scope=scope,
        )
        replay = self._resolve_authority_operation_replay_locked(
            scope=scope,
            operation_id=operation_id,
            aggregate_digest=payload_digest,
            exact_authority_digest=exact_authority_digest,
            pipeline_envelope_requested=pipeline_envelope_requested,
        )
        if replay is not None:
            return replay
        if self._deferred_batch_scope is not None:
            raise MemoryStoreConflict("Nested atomic Memory batches are not supported")

        original_memory = self.memories[scope]
        original_audit = self.approval_audit[scope]
        original_receipts = self._memory_write_receipts[scope]
        original_pipeline_receipts = self._pipeline_operation_receipts[scope]
        self.memories[scope] = copy.deepcopy(original_memory)
        self.approval_audit[scope] = copy.deepcopy(original_audit)
        self._memory_write_receipts[scope] = dict(original_receipts)
        self._pipeline_operation_receipts[scope] = dict(
            original_pipeline_receipts
        )
        self._deferred_batch_scope = scope
        committed = False
        try:
            staged_memory = self.memories[scope]
            staged_memory._ensure_cache_valid()
            existing_ids = set(staged_memory._id_index)
            entry = self.add_entry(
                scope=scope,
                allow_duplicate=False,
                **copy.deepcopy(add_entry_kwargs),
            )
            serialized_entries = self._canonicalize_scope_authority(scope)
            staged_memory._ensure_cache_valid()
            current = (
                staged_memory._id_index.get(entry.id)
                if entry is not None
                else None
            )
            rejected = (
                current is None
                or current.approval_status == _APPROVAL_REJECTED
                or current.lifecycle_status in {
                    "rejected",
                    "superseded",
                    "evicted",
                }
            )
            materializations = (
                ()
                if rejected
                else (
                    MemoryMaterialization(
                        claim_id=claim_id,
                        entry_id=current.id,
                        authority_digest=_approval_hash_for_entry(current),
                        disposition=(
                            "reinforced"
                            if current.id in existing_ids
                            else "created"
                        ),
                    ),
                )
            )
            commit = MemoryAuthorityCommit(
                operation_id=operation_id,
                payload_digest=payload_digest,
                scope=scope.value,
                materializations=materializations,
                rejected_claim_ids=(claim_id,) if rejected else (),
            )
            receipts = self._memory_write_receipts[scope]
            receipts[operation_id] = commit
            while len(receipts) > _MAX_MEMORY_WRITE_RECEIPTS:
                receipts.pop(next(iter(receipts)))
            pipeline_receipts = self._pipeline_operation_receipts[scope]
            pipeline_receipts[operation_id] = payload_digest
            while (
                len(pipeline_receipts) > _MAX_PIPELINE_OPERATION_RECEIPTS
            ):
                pipeline_receipts.pop(next(iter(pipeline_receipts)))
            self._save_scope_locked(
                scope,
                serialized_entries=serialized_entries,
            )
            committed = True
            return commit
        finally:
            self._deferred_batch_scope = None
            if not committed:
                self.memories[scope] = original_memory
                self.approval_audit[scope] = original_audit
                self._memory_write_receipts[scope] = original_receipts
                self._pipeline_operation_receipts[
                    scope
                ] = original_pipeline_receipts

    def commit_conversation_fact(
        self,
        *,
        operation_id: str,
        payload_digest: str,
        claim_id: str,
        add_entry_kwargs: dict[str, Any],
        authority_payload_digest: str | None = None,
    ) -> MemoryAuthorityCommit:
        """Materialize a User fact within the 128-operation replay window."""

        if (
            not isinstance(operation_id, str)
            or _MEMORY_WRITE_OPERATION_RE.fullmatch(operation_id) is None
        ):
            raise ValueError("Memory operation ID is invalid")
        if (
            not isinstance(payload_digest, str)
            or _MEMORY_WRITE_DIGEST_RE.fullmatch(payload_digest) is None
        ):
            raise ValueError("Memory payload digest is invalid")
        if (
            not isinstance(claim_id, str)
            or _REFLECTION_CLAIM_ID_RE.fullmatch(claim_id) is None
        ):
            raise ValueError("claim_id is not canonical")
        snapshot = _snapshot_memory_add_entry_kwargs(add_entry_kwargs)
        exact_authority_digest = (
            payload_digest
            if authority_payload_digest is None
            else authority_payload_digest
        )
        if (
            not isinstance(exact_authority_digest, str)
            or _MEMORY_WRITE_DIGEST_RE.fullmatch(exact_authority_digest) is None
        ):
            raise ValueError("Memory authority payload digest is invalid")
        if conversation_fact_payload_digest(
            claim_id=claim_id,
            add_entry_kwargs=snapshot,
        ) != exact_authority_digest:
            raise MemoryStoreConflict(
                "Memory conversation fact payload digest does not match"
            )
        return self.coordinated_write(
            (MemoryScope.PROJECT, MemoryScope.USER),
            lambda: self._commit_conversation_fact_locked(
                operation_id=operation_id,
                payload_digest=payload_digest,
                exact_authority_digest=exact_authority_digest,
                pipeline_envelope_requested=(
                    authority_payload_digest is not None
                ),
                claim_id=claim_id,
                add_entry_kwargs=snapshot,
            ),
        )

    def _bind_memory_write_operation_locked(
        self,
        *,
        operation_id: str,
        payload_digest: str,
        scope: MemoryScope,
    ) -> bool:
        """Atomically bind one authority-free Pipeline operation envelope."""

        if not self._transaction_covers((MemoryScope.PROJECT, MemoryScope.USER)):
            raise MemoryStoreConflict(
                "Memory operation binding requires a store transaction"
            )
        self._assert_memory_operation_scope_available(
            operation_id,
            target_scope=scope,
        )
        envelope_digest = self._pipeline_operation_receipts[scope].get(
            operation_id
        )
        authority_receipt = self._memory_write_receipts[scope].get(operation_id)
        if envelope_digest is not None:
            if envelope_digest != payload_digest:
                raise MemoryStoreConflict("Memory write operation payload changed")
            if authority_receipt is not None:
                raise MemoryStoreConflict(
                    "Memory write operation already produced authority"
                )
            return False
        if authority_receipt is not None:
            raise MemoryStoreConflict(
                "Memory write operation already produced authority"
            )

        original_memory = self.memories[scope]
        original_audit = self.approval_audit[scope]
        original_authority_receipts = self._memory_write_receipts[scope]
        original_pipeline_receipts = self._pipeline_operation_receipts[scope]
        self.memories[scope] = copy.deepcopy(original_memory)
        self.approval_audit[scope] = copy.deepcopy(original_audit)
        self._memory_write_receipts[scope] = dict(
            original_authority_receipts
        )
        self._pipeline_operation_receipts[scope] = dict(
            original_pipeline_receipts
        )
        committed = False
        try:
            pipeline_receipts = self._pipeline_operation_receipts[scope]
            pipeline_receipts[operation_id] = payload_digest
            while (
                len(pipeline_receipts) > _MAX_PIPELINE_OPERATION_RECEIPTS
            ):
                pipeline_receipts.pop(next(iter(pipeline_receipts)))
            self._save_scope_locked(scope)
            committed = True
            return True
        finally:
            if not committed:
                self.memories[scope] = original_memory
                self.approval_audit[scope] = original_audit
                self._memory_write_receipts[
                    scope
                ] = original_authority_receipts
                self._pipeline_operation_receipts[
                    scope
                ] = original_pipeline_receipts

    def bind_memory_write_operation(
        self,
        *,
        operation_id: str,
        payload_digest: str,
        scope: MemoryScope = MemoryScope.PROJECT,
    ) -> bool:
        """Bind one authority-free operation; return ``False`` on replay."""

        if (
            not isinstance(operation_id, str)
            or _MEMORY_WRITE_OPERATION_RE.fullmatch(operation_id) is None
        ):
            raise ValueError("Memory operation ID is invalid")
        if (
            not isinstance(payload_digest, str)
            or _MEMORY_WRITE_DIGEST_RE.fullmatch(payload_digest) is None
        ):
            raise ValueError("Memory payload digest is invalid")
        if not isinstance(scope, MemoryScope):
            try:
                scope = MemoryScope(scope)
            except (TypeError, ValueError) as error:
                raise ValueError("Memory operation scope is invalid") from error
        if scope not in {MemoryScope.PROJECT, MemoryScope.USER}:
            raise ValueError("Memory operation scope is invalid")
        return self.coordinated_write(
            (MemoryScope.PROJECT, MemoryScope.USER),
            lambda: self._bind_memory_write_operation_locked(
                operation_id=operation_id,
                payload_digest=payload_digest,
                scope=scope,
            ),
        )

    @_coordinated_all_write
    def record_retrievals(
        self,
        entry_ids: list[str],
        *,
        observation_id: str | None = None,
    ) -> None:
        """Persist retrieval, replay-safe when an observation ID is supplied."""

        normalized_ids = tuple(sorted(set(entry_ids)))
        receipt = self._prepare_scope_effect_receipt(
            observation_id,
            lane="telemetry",
            channel="retrieval",
            payload={"entry_ids": normalized_ids},
        )
        by_scope: dict[MemoryScope, list[MemoryEntry]] = {}
        for entry_id in normalized_ids:
            scope, entry = self._find_entry_by_id(entry_id)
            if scope is None or entry is None:
                logger.debug(
                    "Memory retrieval skipped missing entry_id=%s",
                    entry_id,
                )
                continue
            by_scope.setdefault(scope, []).append(entry)
        self._require_replay_identity_for_multiple_scopes(set(by_scope), receipt)
        now = time.time()
        for scope in sorted(by_scope, key=lambda item: item.value):
            if not self._claim_scope_effect_receipt(
                scope,
                lane="telemetry",
                receipt=receipt,
            ):
                continue
            for entry in by_scope[scope]:
                entry.retrieval_count = _saturating_counter_add(
                    entry.retrieval_count
                )
                entry.last_accessed = now
            self._save_scope(scope)

    @_coordinated_all_write
    def record_retrievals_and_injections(
        self,
        retrieved_entry_ids: list[str],
        injected_entry_ids: list[str],
        *,
        observation_id: str | None = None,
    ) -> None:
        """Batch task-start counters with one replay receipt per scope."""

        retrieved_ids = tuple(sorted(set(retrieved_entry_ids)))
        injected_ids = tuple(sorted(set(injected_entry_ids)))
        receipt = self._prepare_scope_effect_receipt(
            observation_id,
            lane="telemetry",
            channel="retrieval_and_injection",
            payload={
                "retrieved_entry_ids": retrieved_ids,
                "injected_entry_ids": injected_ids,
            },
        )
        retrieved_by_scope: dict[MemoryScope, list[MemoryEntry]] = {}
        injected_by_scope: dict[MemoryScope, list[MemoryEntry]] = {}
        touched: set[MemoryScope] = set()
        for entry_id in retrieved_ids:
            scope, entry = self._find_entry_by_id(entry_id)
            if scope is None or entry is None:
                logger.debug(
                    "Memory retrieval skipped missing entry_id=%s",
                    entry_id,
                )
                continue
            retrieved_by_scope.setdefault(scope, []).append(entry)
            touched.add(scope)
        for entry_id in injected_ids:
            scope, entry = self._find_entry_by_id(entry_id)
            if scope is None or entry is None:
                logger.debug(
                    "Memory injection skipped missing entry_id=%s",
                    entry_id,
                )
                continue
            injected_by_scope.setdefault(scope, []).append(entry)
            touched.add(scope)
        self._require_replay_identity_for_multiple_scopes(touched, receipt)
        now = time.time()
        for scope in sorted(touched, key=lambda item: item.value):
            if not self._claim_scope_effect_receipt(
                scope,
                lane="telemetry",
                receipt=receipt,
            ):
                continue
            for entry in retrieved_by_scope.get(scope, ()):
                entry.retrieval_count = _saturating_counter_add(
                    entry.retrieval_count
                )
                entry.last_accessed = now
            for entry in injected_by_scope.get(scope, ()):
                entry.injection_count = _saturating_counter_add(
                    entry.injection_count
                )
                entry.last_used = now
                entry.last_accessed = now
            self._save_scope(scope)

    @_coordinated_all_write
    def record_feedback(
        self,
        entry_ids: list[str],
        success: bool,
        *,
        observation_id: str | None = None,
        source: str = "task_outcome",
        expected_versions: list[tuple[str, str]]
        | tuple[tuple[str, str], ...]
        | None = None,
    ) -> None:
        """Persist task-outcome feedback for memories that were injected.

        A stable ``observation_id`` makes retries idempotent across manager
        instances. Receipts retain the most recent 256 observations per entry;
        omitting the ID preserves the legacy count-every-call API.
        """
        normalized_source = str(source).strip().lower()
        if normalized_source not in _ORDINARY_FEEDBACK_SOURCES:
            raise ValueError("invalid ordinary Memory feedback source")
        version_fences = _expected_memory_versions(expected_versions)
        observation_digest = _feedback_observation_digest(
            observation_id,
            channel=normalized_source,
        )
        candidate_scopes: set[MemoryScope] = set()
        for entry_id in dict.fromkeys(entry_ids):
            candidate_scope, candidate = self._find_entry_by_id(entry_id)
            if candidate_scope is None or candidate is None:
                continue
            if version_fences is not None and (
                entry_id not in version_fences
                or _approval_hash_for_entry(candidate)
                not in version_fences[entry_id]
            ):
                continue
            candidate_scopes.add(candidate_scope)
        if len(candidate_scopes) > 1 and observation_digest is None:
            raise MemoryStoreConflict(
                "A stable observation_id is required for multi-scope Memory feedback"
            )
        touched: set[MemoryScope] = set()
        now = time.time()
        for entry_id in dict.fromkeys(entry_ids):
            scope, entry = self._find_entry_by_id(entry_id)
            if scope is None or entry is None:
                logger.debug("Memory outcome feedback skipped missing entry_id=%s", entry_id)
                continue
            if version_fences is not None and (
                entry_id not in version_fences
                or _approval_hash_for_entry(entry)
                not in version_fences[entry_id]
            ):
                logger.debug(
                    "Memory outcome feedback skipped stale entry_id=%s",
                    entry_id,
                )
                continue
            if not _claim_feedback_observation(
                entry,
                observation_digest,
                success=success,
            ):
                continue
            if success:
                entry.success_count = _saturating_counter_add(
                    entry.success_count
                )
                entry.usage_count = _saturating_counter_add(
                    entry.usage_count
                )
            else:
                entry.failure_count = _saturating_counter_add(
                    entry.failure_count
                )
            entry.last_used = now
            entry.last_accessed = now
            total = entry.success_count + entry.failure_count
            entry.usefulness_score = (
                (entry.success_count - entry.failure_count) / total
                if total
                else 0.0
            )
            touched.add(scope)
        for scope in sorted(touched, key=lambda item: item.value):
            self._save_scope(scope)

    @_coordinated_all_write
    def record_corroborated_feedback(
        self,
        entry_ids: list[str],
        success: bool,
        *,
        source: str = "independent_verification",
        observation_id: str | None = None,
        expected_versions: list[tuple[str, str]]
        | tuple[tuple[str, str], ...]
        | None = None,
    ) -> None:
        """Persist feedback backed by independent verification or an explicit
        user accept/correct/reject signal, kept separate from whole-turn
        outcome feedback because it carries materially stronger evidence.

        Explicit correction/rejection quarantines the exact rendered Memory
        immediately. Automated verification is deliberately less absolute: a
        single failure records negative evidence, while two independently
        observed failures quarantine it for review. Ordinary whole-turn
        failures continue to use ``record_feedback`` and never revoke Memory.

        Independent verification requires a stable observation identity so a
        replay cannot masquerade as a second verifier. Receipts retain the most
        recent 256 observations per entry and contain only SHA-256 identities.
        """
        normalized_source = str(source).strip().lower()
        if normalized_source not in _CORROBORATED_FEEDBACK_SOURCES:
            raise ValueError("invalid corroborated Memory feedback source")
        if normalized_source == "independent_verification" and (
            not isinstance(observation_id, str) or not observation_id.strip()
        ):
            raise ValueError(
                "independent verification requires a non-empty observation_id"
            )
        if normalized_source == "explicit_user_accept" and not success:
            raise ValueError("explicit_user_accept requires successful feedback")
        if normalized_source in {
            "explicit_user_correction",
            "explicit_user_reject",
        } and success:
            raise ValueError("explicit negative user feedback cannot be successful")
        observation_digest = _feedback_observation_digest(
            observation_id,
            channel=f"corroborated:{normalized_source}",
        )
        version_fences = _expected_memory_versions(expected_versions)
        candidate_scopes: set[MemoryScope] = set()
        for entry_id in dict.fromkeys(entry_ids):
            candidate_scope, candidate = self._find_entry_by_id(entry_id)
            if candidate_scope is None or candidate is None:
                continue
            if version_fences is not None and (
                entry_id not in version_fences
                or _approval_hash_for_entry(candidate)
                not in version_fences[entry_id]
            ):
                continue
            candidate_scopes.add(candidate_scope)
        if len(candidate_scopes) > 1 and observation_digest is None:
            raise MemoryStoreConflict(
                "A stable observation_id is required for multi-scope Memory feedback"
            )
        touched: set[MemoryScope] = set()
        now = time.time()
        for entry_id in dict.fromkeys(entry_ids):
            scope, entry = self._find_entry_by_id(entry_id)
            if scope is None or entry is None:
                logger.debug(
                    "Memory corroborated feedback skipped missing entry_id=%s", entry_id
                )
                continue
            if version_fences is not None and (
                entry_id not in version_fences
                or _approval_hash_for_entry(entry)
                not in version_fences[entry_id]
            ):
                logger.debug(
                    "Memory corroborated feedback skipped stale entry_id=%s",
                    entry_id,
                )
                continue
            if not _claim_feedback_observation(
                entry,
                observation_digest,
                success=success,
            ):
                continue
            if success:
                entry.corroborated_success_count = _saturating_counter_add(
                    entry.corroborated_success_count
                )
            else:
                entry.corroborated_failure_count = _saturating_counter_add(
                    entry.corroborated_failure_count
                )
            entry.last_used = now
            entry.last_accessed = now
            total = entry.corroborated_success_count + entry.corroborated_failure_count
            entry.corroborated_usefulness_score = (
                (entry.corroborated_success_count - entry.corroborated_failure_count) / total
                if total
                else 0.0
            )
            explicit_negative = normalized_source in {
                "explicit_user_correction",
                "explicit_user_reject",
            }
            repeated_verification_failure = (
                normalized_source == "independent_verification"
                and entry.corroborated_failure_count
                >= _CORROBORATED_FAILURE_QUARANTINE_THRESHOLD
            )
            should_quarantine = (
                not success
                and entry.approval_status != _APPROVAL_REJECTED
                and entry.lifecycle_status != "rejected"
                and (explicit_negative or repeated_verification_failure)
            )
            if should_quarantine:
                previous_approval = entry.approval_status
                previous_lifecycle = entry.lifecycle_status
                entry.approval_status = _APPROVAL_REJECTED
                entry.lifecycle_status = "rejected"
                entry.curator_locked = False
                entry.approval_actor = normalized_source
                entry.approval_reason = (
                    "quarantined after explicit user correction"
                    if explicit_negative
                    else (
                        "quarantined after repeated independent "
                        "verification failures"
                    )
                )
                entry.approval_decided_at = now
                entry.updated_at = now
                entry.approval_content_hash = _approval_hash_for_entry(entry)
                self._append_approval_audit(
                    scope,
                    entry,
                    action="feedback_quarantine",
                    actor=normalized_source,
                    previous_approval=previous_approval,
                    previous_lifecycle=previous_lifecycle,
                    reason=entry.approval_reason,
                    extra={
                        "feedback_source": normalized_source,
                        "corroborated_failure_count": (
                            entry.corroborated_failure_count
                        ),
                    },
                )
            touched.add(scope)
        for scope in sorted(touched, key=lambda item: item.value):
            self._save_scope(scope)

    def _canonicalize_scope_authority(
        self,
        scope: MemoryScope,
    ) -> list[dict[str, Any]]:
        """Normalize the live authority before producing any projection.

        ``add_entry`` historically returned the live mutable MemoryEntry.  A
        caller could therefore change approval-bound fields behind the public
        update API.  Serialize-and-sanitize alone is insufficient because the
        Markdown projection would still read the original object.  Mutate the
        authoritative objects to their canonical safe form first, invalidate
        stale approvals, and only then let JSON and Markdown read that single
        snapshot.
        """
        scope_file = self.memories[scope]
        rebuild_indices = False
        serialized_entries: list[dict[str, Any]] = []
        for entry in scope_file.entries:
            previous_approval = entry.approval_status
            previous_lifecycle = entry.lifecycle_status
            authority_state_changed = not entry._authority_state_is_current()
            authority_state_expanded = (
                authority_state_changed
                and entry._authority_state_expands_privilege()
            )
            if authority_state_expanded:
                entry._restore_authority_state()
            previous_hash = entry.approval_content_hash
            before_bound_hash = _approval_hash_for_entry(entry)
            safe_payload = entry.to_dict()

            entry.category = str(safe_payload.get("category", "general") or "general")
            entry.content = str(safe_payload.get("content", "") or "")
            entry.tags = _safe_str_list(safe_payload.get("tags", []))
            entry.domains = _safe_str_list(safe_payload.get("domains", []))
            entry.related_to = _safe_str_list(safe_payload.get("related_to", []))
            entry.feedback_observations = _safe_feedback_observations(
                safe_payload.get("feedback_observations", {})
            )
            entry.metadata = _safe_dict(safe_payload.get("metadata", {}))
            entry.source = str(safe_payload.get("source", "") or "")
            entry.provenance = _safe_dict(safe_payload.get("provenance", {}))
            entry.tier_reason = str(safe_payload.get("tier_reason", "") or "")
            entry.approval_reason = str(
                safe_payload.get("approval_reason", "") or ""
            )
            entry.approval_actor = str(
                safe_payload.get("approval_actor", "") or ""
            )
            # Usefulness is persisted as a denormalized view of the feedback
            # counters, never as independently writable authority.  Public
            # feedback methods keep it current, but legacy/test integrations
            # can still construct a live ``MemoryEntry`` directly.  Repair
            # that live writer state at the canonical commit boundary while
            # retaining strict fail-closed validation for an inconsistent
            # score already present in a versioned on-disk authority.
            entry.usefulness_score = _derived_usefulness_score(
                entry.success_count,
                entry.failure_count,
            )
            entry.corroborated_usefulness_score = _derived_usefulness_score(
                entry.corroborated_success_count,
                entry.corroborated_failure_count,
            )
            entry.invalidate_tokens()

            canonical_hash = _approval_hash_for_entry(entry)
            canonicalized_bound_fields = before_bound_hash != canonical_hash
            # Hash-less in-memory entries are a supported legacy/test seam;
            # initialize their hash if their canonical fields are unchanged.
            # Real manager-created entries always have a non-empty hash, so a
            # live-object mutation still takes the strict mismatch path.
            approval_was_current = (
                not previous_hash or previous_hash == canonical_hash
            )
            reset_observations = (
                _reset_version_scoped_observations(entry)
                if canonicalized_bound_fields
                or bool(previous_hash and not approval_was_current)
                else None
            )
            safety = assess_memory_safety(
                entry.content,
                source=entry.source or "persistence_boundary",
            )
            entry.safety_status = _normalize_safety_status(safety.status)
            entry.safety_reason = safety.reason

            if safety.status == _SAFETY_UNSAFE:
                entry.approval_status = _APPROVAL_REJECTED
                entry.lifecycle_status = "rejected"
                entry.tier_reason = "safety_gate"
                entry.curator_locked = False
                entry.approval_reason = safety.reason
                entry.approval_actor = "persistence_boundary"
            elif entry.approval_status == _APPROVAL_REJECTED:
                entry.approval_status = _APPROVAL_REJECTED
                entry.curator_locked = False
            elif (
                canonicalized_bound_fields
                or not approval_was_current
            ):
                entry.approval_status = _APPROVAL_PENDING
                entry.approval_reason = (
                    "approval invalidated by persistence canonicalization"
                )
                entry.approval_actor = "persistence_boundary"
                entry.approval_decided_at = time.time()

            approval_invalidated_for_review = (
                safety.status != _SAFETY_UNSAFE
                and previous_hash
                and (canonicalized_bound_fields or not approval_was_current)
            )
            # Keep the prior hash stale until approve_entry rechecks the new
            # canonical content and explicitly reports that the review became
            # outdated.  Updating it here would let the very next approval
            # silently accept content the user never reviewed.
            entry.approval_content_hash = (
                previous_hash
                if approval_invalidated_for_review
                else canonical_hash
            )
            state_changed = (
                authority_state_changed
                or canonicalized_bound_fields
                or not approval_was_current
                or entry.approval_status != previous_approval
                or entry.lifecycle_status != previous_lifecycle
            )
            if state_changed:
                entry.updated_at = time.time()
                self._append_approval_audit(
                    scope,
                    entry,
                    action="persistence_canonicalized",
                    actor="persistence_boundary",
                    previous_approval=previous_approval,
                    previous_lifecycle=previous_lifecycle,
                    reason=(
                        "live authority state mutation rejected"
                        if authority_state_expanded
                        else "live authority restriction accepted"
                        if authority_state_changed
                        else entry.approval_reason
                    ),
                    safety=safety,
                    extra={
                        "authority_state_restored": authority_state_expanded,
                        "authority_restriction_accepted": (
                            authority_state_changed
                            and not authority_state_expanded
                        ),
                        "approval_hash_was_current": approval_was_current,
                        "bound_fields_sanitized": canonicalized_bound_fields,
                        "version_observations_reset": reset_observations,
                    },
                )
                rebuild_indices = True

            # ``safe_payload`` is the complete persistence-boundary result for
            # the entry as it entered this pass. Normal usage accounting is
            # already represented there, so writing it directly avoids a
            # second recursive secret scan for every unchanged entry. If any
            # canonicalization or authority transition changed the raw shape,
            # fall back to a fresh boundary pass. This equality check also
            # catches live mutation inside nested metadata/provenance values.
            final_raw_payload = entry._raw_persistence_payload()
            comparable_safe_payload = dict(safe_payload)
            comparable_safe_payload.pop("authority_state_digest", None)
            serialized_entries.append(
                safe_payload
                if final_raw_payload == comparable_safe_payload
                else entry.to_dict()
            )
            # The entry now exactly matches the payload proposed for this
            # canonical commit. Seal after canonicalization so safety-driven
            # fields (for example the persistence-boundary actor on an unsafe
            # entry) do not leave the live manager spuriously dirty. Any
            # caller-side drift that predates a public operation was already
            # rejected by ``coordinated_write``; direct saves still restore
            # privilege-expanding drift above before reaching this point.
            entry._seal_authority_state()

        # Audit records are immutable authority, not another content field to
        # repair in place. They were sanitized at creation and are decoded
        # strictly immediately before commit; silently rewriting an older
        # record here would invalidate the very history it is meant to prove.
        if rebuild_indices:
            scope_file._rebuild_indices()
        return serialized_entries

    def _save_scope(self, scope: MemoryScope) -> None:
        """Save memory to disk (atomic write to prevent corruption)."""
        # ``_manager_held_stores`` also notices a POSIX fork and marks every
        # inherited scope unfenced. Run that check before the private-save
        # guard below rather than only while testing transaction coverage.
        self._manager_held_stores()
        if self._deferred_batch_scope is not None:
            if scope is not self._deferred_batch_scope:
                raise MemoryStoreConflict(
                    "Atomic Memory batch attempted a cross-scope write"
            )
            return
        if scope in self._unfenced_loaded_scopes:
            raise MemoryStoreConflict(
                "Unfenced Memory snapshot requires coordinated reload"
            )
        required_scopes = (
            tuple(MemoryScope)
            if self._scope_has_unresolved_relationships(scope)
            else (scope,)
        )
        if not self._transaction_covers(required_scopes):
            with self._transaction_for_scopes(required_scopes):
                expected = self._disk_revisions.get(scope)
                if expected is not None and expected != self._scope_disk_revision(scope):
                    raise MemoryStoreConflict("Memory scope changed")
                # A direct/private save is a recovery seam, not an authority
                # grant. Restore the last Manager-sealed state in place before
                # entering the strict commit seam.
                self._restore_live_authority_state((scope,))
                self._save_scope_locked(scope)
                return
        self._save_scope_locked(scope)

    @staticmethod
    def _encode_memory_authority(data: dict[str, Any]) -> tuple[str, bytes]:
        """Encode the exact pretty JSON representation used by the root."""

        try:
            text = json.dumps(
                data,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            return text, text.encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError) as error:
            raise MemoryStoreConflict(
                "Memory authority contains invalid JSON values"
            ) from error

    def _build_memory_authority_data(
        self,
        scope: MemoryScope,
        *,
        serialized_entries: list[dict[str, Any]],
        hot_audit: list[dict[str, Any]],
        audit_head: MemoryAuditHead | None,
        write_receipts: list[dict[str, object]],
        pipeline_receipts: list[dict[str, str]],
        observation_receipts: dict[str, list[dict[str, str]]],
        last_updated: float,
    ) -> dict[str, Any]:
        return {
            "schemaVersion": CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION,
            "scope": scope.value,
            "last_updated": last_updated,
            "entries": serialized_entries,
            "approval_audit": hot_audit,
            "approval_audit_head": (
                audit_head.to_dict() if audit_head is not None else None
            ),
            "write_receipts": write_receipts,
            "pipeline_operation_receipts": pipeline_receipts,
            "observation_receipts": observation_receipts,
        }

    def _prepare_approval_audit_commit(
        self,
        scope: MemoryScope,
        *,
        build_root: Any,
    ) -> tuple[
        list[dict[str, Any]],
        MemoryAuditHead | None,
        tuple[dict[str, Any], ...],
        tuple[MemoryAuditSegment, ...],
    ]:
        """Validate the live audit and rotate its hot tail when required.

        Segment objects are prepared without filesystem mutation.  The caller
        publishes them before the root replace, then installs the returned
        in-memory state only after that replace succeeds.
        """

        try:
            hot_records = decode_memory_audit_records(
                list(self.approval_audit.get(scope, [])),
                expected_scope=scope.value,
                maximum_records=MAX_MEMORY_AUDIT_CHAIN_RECORDS,
            )
        except (TypeError, ValueError) as error:
            raise MemoryStoreConflict("Memory approval audit is invalid") from error

        head = self._approval_audit_heads.get(scope)
        cold_records = self._resolve_approval_audit_chain(
            scope,
            head,
            hot_records,
        )
        if cold_records != self._approval_audit_cold_records.get(scope, ()):
            raise MemoryStoreConflict("Memory approval audit cache is inconsistent")
        # Exceeding the chain history limit drops the oldest history by
        # rebasing instead of failing every future write: hot records are
        # rotated into a fresh genesis chain, orphaning the immutable cold
        # segments. A hard failure here would permanently wedge all memory
        # writes for a long-lived workspace.
        rebase_required = (
            len(cold_records) + len(hot_records) > MAX_MEMORY_AUDIT_CHAIN_RECORDS
        )

        hot_payload = [record.to_dict() for record in hot_records]
        provisional = build_root(hot_payload, head)
        _, provisional_bytes = self._encode_memory_authority(provisional)
        should_rotate = (
            len(hot_records) > MAX_AUTHORITY_AUDIT_RECORDS
            or len(provisional_bytes)
            > memory_authority_io_module.MAX_AUTHORITY_FILE_BYTES
            or rebase_required
        )
        if not should_rotate:
            return hot_payload, head, cold_records, ()
        if not hot_records:
            raise MemoryStoreConflict("Memory authority exceeds bounded limits")

        # Rotate the complete hot generation.  This deliberately favors a
        # simple immutable boundary over retaining a tiny inline prefix: every
        # record stays reachable, while the root immediately regains maximal
        # headroom.  Individual blobs are packed to the canonical 64 KiB cap.
        prepared_segments: list[MemoryAuditSegment] = []
        if rebase_required:
            previous_head = None
            previous_reference = None
            logger.warning(
                "Memory approval audit for scope %s exceeded %d records; "
                "rebased to a fresh chain, dropping %d oldest cold records",
                scope.value,
                MAX_MEMORY_AUDIT_CHAIN_RECORDS,
                len(cold_records),
            )
        else:
            previous_head = head
            previous_reference = head.blob if head is not None else None
        pending: list[MemoryAuditRecord] = []

        def finish_pending() -> None:
            nonlocal pending, previous_head, previous_reference
            if not pending:
                return
            segment = MemoryAuditSegment.from_records(
                pending,
                scope=scope.value,
                previous=previous_reference,
            )
            previous_head = MemoryAuditHead.from_segment(
                segment,
                previous_head=previous_head,
            )
            previous_reference = segment.blob_reference
            prepared_segments.append(segment)
            pending = []

        for record in hot_records:
            candidate = [*pending, record]
            if len(candidate) > MAX_MEMORY_AUDIT_RECORDS_PER_SEGMENT:
                finish_pending()
                pending = [record]
                continue
            try:
                MemoryAuditSegment.from_records(
                    candidate,
                    scope=scope.value,
                    previous=previous_reference,
                )
            except MemoryAuthorityLimitError:
                if not pending:
                    raise MemoryStoreConflict(
                        "Memory approval audit record exceeds the segment limit"
                    )
                finish_pending()
                pending = [record]
            else:
                pending = candidate
        finish_pending()
        if previous_head is None or not prepared_segments:
            raise MemoryStoreConflict("Memory approval audit rotation failed")

        rotated_root = build_root([], previous_head)
        _, rotated_bytes = self._encode_memory_authority(rotated_root)
        if len(rotated_bytes) > memory_authority_io_module.MAX_AUTHORITY_FILE_BYTES:
            raise MemoryStoreConflict("Memory authority exceeds bounded limits")
        if rebase_required:
            # The rebased chain's cold history is exactly the records just
            # rotated out of the hot tail; the dropped segments stay on disk
            # as unreferenced immutable blobs.
            rebased_cold = tuple(
                record.to_dict() for record in hot_records
            )
        else:
            rebased_cold = (
                *cold_records,
                *(record.to_dict() for record in hot_records),
            )
        return (
            [],
            previous_head,
            rebased_cold,
            tuple(prepared_segments),
        )

    @contextmanager
    def _bound_scope_publication(
        self,
        scope: MemoryScope,
        path: Path,
    ):
        """Keep one transaction-bound scope inode for an entire commit."""

        store = self._scope_store(scope)
        binding = store.transaction_root_binding()
        previous = getattr(self._transaction_local, "scope_publication", None)
        if previous is not None:
            raise MemoryStoreConflict("Nested Memory scope publication")
        if os.name != "posix" or binding.root_fd is None:
            # The portable publisher still validates the transaction-bound
            # root around every replace. POSIX additionally holds one scope fd
            # across blobs, the authority root, and derived projections.
            with MemoryAuthorityPublisher.bind_transaction_root(binding):
                yield None
            return
        with MemoryAuthorityPublisher.open_scope(
            binding,
            path.name,
            create=True,
        ) as publication:
            self._transaction_local.scope_publication = (scope, publication)
            try:
                yield publication
            finally:
                if previous is None:
                    try:
                        del self._transaction_local.scope_publication
                    except AttributeError:
                        pass
                else:
                    self._transaction_local.scope_publication = previous

    def _save_scope_locked(
        self,
        scope: MemoryScope,
        *,
        serialized_entries: list[dict[str, Any]] | None = None,
    ) -> None:
        """Write one scope while the caller owns the cooperative transaction."""
        self._manager_held_stores()
        if scope in self._unfenced_loaded_scopes:
            raise MemoryStoreConflict(
                "Unfenced Memory snapshot requires coordinated reload"
            )
        if not self._transaction_covers((scope,)):
            raise MemoryStoreConflict(
                "Memory transaction does not cover the saved authority scope"
            )
        self._scope_store(scope).assert_transaction_valid()
        # Defense in depth for arbitrary coordinated_write callbacks: a
        # callback may mutate a live object after the outer preflight and call
        # this private seam directly. Never let canonicalization or an audit
        # append turn that unsealed state into durable authority.
        self._assert_live_authority_is_sealed((scope,))
        path = self._get_scope_path(scope)
        if serialized_entries is None:
            serialized_entries = self._canonicalize_scope_authority(scope)
        memory_file = self.memories[scope]
        if (
            len(serialized_entries) > MAX_CURRENT_AUTHORITY_ENTRIES
            or memory_file.size_bytes > MAX_CURRENT_AUTHORITY_CONTENT_BYTES
        ):
            raise MemoryStoreConflict(
                "Memory authority exceeds runtime capacity"
            )
        
        # Prepare the one root payload. Receipts and audit segments publish
        # their immutable blobs first; memory.json remains the sole commit
        # point that makes those objects reachable.
        memory_json = path / "memory.json"
        last_updated = time.time()
        write_receipts, receipt_blobs = self._serialized_memory_write_receipts(
            scope
        )
        pipeline_receipts = self._serialized_pipeline_operation_receipts(scope)
        observation_receipts = self._serialized_observation_receipts(scope)

        def build_root(
            hot_audit: list[dict[str, Any]],
            audit_head: MemoryAuditHead | None,
        ) -> dict[str, Any]:
            return self._build_memory_authority_data(
                scope,
                serialized_entries=serialized_entries,
                hot_audit=hot_audit,
                audit_head=audit_head,
                write_receipts=write_receipts,
                pipeline_receipts=pipeline_receipts,
                observation_receipts=observation_receipts,
                last_updated=last_updated,
            )

        (
            hot_audit,
            audit_head,
            cold_audit,
            new_segments,
        ) = self._prepare_approval_audit_commit(
            scope,
            build_root=build_root,
        )
        data = build_root(hot_audit, audit_head)
        _, scope_errors = _bind_memory_authority_scope(data, scope.value)
        if scope_errors:
            raise MemoryStoreConflict(
                "Memory entry scope does not match its authority root"
            )
        # New IDs are created only by ``add_entry`` and are scope-qualified;
        # Manager APIs never accept or mutate an ID. Relationships are also
        # scope-local. That contract makes an ordinary one-scope commit safe
        # to validate against the Manager's already-vetted live snapshot and
        # avoids a racy, unlocked read of a different authority shard.
        # Initialization, migration and explicitly all-scope operations hold
        # both locks and retain the stronger canonical-file cross-check.
        if self._transaction_covers(tuple(MemoryScope)):
            prepared = {
                candidate_scope: (
                    _PreparedScopeAuthority(source=None, data=data)
                    if candidate_scope is scope
                    else self._prepare_scope_authority(candidate_scope)
                )
                for candidate_scope in MemoryScope
            }
        else:
            prepared = {
                scope: _PreparedScopeAuthority(source=None, data=data)
            }
        committed_scope = self._prepare_scope_authority(scope)
        self._assert_cross_scope_unique(
            prepared,
            reject_new_unresolved=True,
            grandfathered_relationships=self._authority_relationship_keys(
                scope,
                committed_scope,
            ),
        )
        is_valid, validation_errors = _validate_memory_data(data)
        if not is_valid:
            raise MemoryStoreConflict(
                "Memory authority contains invalid state: "
                + "; ".join(validation_errors[:3])
            )
        try:
            validate_memory_authority_value(data)
        except ValueError as error:
            raise MemoryStoreConflict(
                "Memory authority contains invalid JSON values"
            ) from error
        authority_text, authority_bytes = self._encode_memory_authority(data)
        try:
            validate_memory_authority_commit(
                authority_bytes,
                entry_count=len(serialized_entries),
                audit_count=len(data["approval_audit"]),
            )
        except (MemoryAuthorityLimitError, UnicodeEncodeError) as error:
            raise MemoryStoreConflict(
                "Memory authority exceeds bounded limits"
            ) from error
        # No filesystem mutation occurs before every authority invariant and
        # the transaction's bound root have both been revalidated. On POSIX,
        # one scope descriptor now spans receipt/audit blobs, memory.json, and
        # both projections so a path replacement cannot split the commit.
        self._scope_store(scope).assert_transaction_valid()
        with self._bound_scope_publication(scope, path):
            blob_store = self._blob_store_for_scope(scope)
            for receipt in receipt_blobs:
                encoded_receipt = canonical_memory_blob_bytes(receipt.to_dict())
                expected_receipt = MemoryBlobReference(
                    digest=memory_blob_digest(encoded_receipt),
                    size_bytes=len(encoded_receipt),
                )
                if blob_store.put_json(receipt.to_dict()) != expected_receipt:
                    raise MemoryStoreConflict(
                        "Memory write receipt publication changed"
                    )
            for segment in new_segments:
                published = blob_store.put_json(segment.to_dict())
                if published != segment.blob_reference:
                    raise MemoryStoreConflict(
                        "Memory approval audit segment publication changed"
                    )
            self._scope_store(scope).assert_transaction_valid()
            self._atomic_write(memory_json, authority_text)
            self._scope_store(scope).assert_transaction_valid()
            self._approval_audit_heads[scope] = audit_head
            self._approval_audit_cold_records[scope] = cold_audit
            self.approval_audit[scope] = hot_audit
            self._scope_has_embedded_audit[scope] = True
            # ``memory.json`` is the commit point. Derive the new revision from
            # the exact committed payload so a fallible post-replace read
            # cannot turn a durable entry+receipt into an apparent failure.
            self._disk_revisions[scope] = self._embedded_authority_revision(
                authority_bytes
            )

            # MEMORY.md is a human-facing projection, not audit authority.
            # Pending/rejected/unsafe/inactive/archival entries stay only in
            # the canonical JSON and audit projection.
            memory_md = path / "MEMORY.md"
            authoritative_projection = MemoryFile(
                scope=scope,
                entries=[
                    entry
                    for entry in self.memories[scope].entries
                    if entry.is_active
                ],
            )
            try:
                self._atomic_write(
                    memory_md,
                    authoritative_projection.format_as_markdown(),
                )
            except (MemoryStoreError, OSError) as error:
                logger.warning(
                    "Memory Markdown projection write failed for %s: %s",
                    scope.value,
                    error,
                )
            try:
                self._write_approval_audit_projection(scope)
            except (MemoryStoreError, OSError) as error:
                logger.warning(
                    "Memory approval audit projection write failed for %s: %s",
                    scope.value,
                    error,
                )

    def _atomic_write(self, target: Path, content: str) -> None:
        """Publish one authorized target through its bound transaction root."""

        candidate = Path(os.path.abspath(os.fspath(target)))
        allowed_scope_names = {*self._STORE_FILENAMES, "memory.json.bak"}
        selected_scope: MemoryScope | None = None
        selected_store: MemoryStoreCoordinator | None = None
        for scope in MemoryScope:
            root = Path(
                os.path.abspath(os.fspath(self._get_scope_path(scope)))
            )
            if candidate.parent == root and candidate.name in allowed_scope_names:
                selected_scope = scope
                selected_store = self._scope_store(scope)
                break

        if selected_store is None:
            quarantine_root = self._data_root / "memory-migration-quarantine"
            try:
                quarantine_relative = candidate.relative_to(quarantine_root)
            except ValueError:
                quarantine_relative = None
            if (
                quarantine_relative is not None
                and quarantine_relative.parts
                and candidate.suffix == ".bak"
            ):
                selected_scope = MemoryScope.USER
                selected_store = self._store

        if selected_scope is None or selected_store is None:
            raise MemoryStoreConflict(
                "Memory publication target is outside an authority root"
            )
        if not self._transaction_covers((selected_scope,)):
            raise MemoryStoreConflict(
                "Memory publication requires its authority transaction"
            )

        active = getattr(self._transaction_local, "scope_publication", None)
        if (
            isinstance(active, tuple)
            and len(active) == 2
            and active[0] is selected_scope
            and candidate.parent
            == Path(
                os.path.abspath(
                    os.fspath(self._get_scope_path(selected_scope))
                )
            )
        ):
            active[1].publish_text(candidate.name, content)
            return

        binding = selected_store.transaction_root_binding()
        # Keep the root binding active inside the final publisher seam. If a
        # hook or hostile local process replaces the workspace after this
        # method's preflight, publish_text opens the scope directory from the
        # already-held root fd and refuses the replacement pathname.
        with MemoryAuthorityPublisher.bind_transaction_root(binding):
            MemoryAuthorityPublisher.publish_text(candidate, content)
    
    def get_stats(self) -> dict[str, Any]:
        """Get memory statistics."""
        return {
            scope.value: {
                "entries": len(memory.entries),
                "size_bytes": memory.size_bytes,
                "categories": list(set(e.category for e in memory.entries)),
            }
            for scope, memory in self.memories.items()
        }
    
    def format_stats(self) -> str:
        """Format memory stats for display with tier and domain breakdown."""
        from collections import Counter

        lines = ["Memory System Status", "=" * 50, ""]
        tiers: Counter[str] = Counter()
        domains: Counter[str] = Counter()
        approvals: Counter[str] = Counter()
        total_entries = 0
        total_size = 0
        insight_count = 0

        for scope_name, scope_stats in self.get_stats().items():
            lines.append(f"{scope_name.title()}: {scope_stats['entries']} entries, "
                        f"{scope_stats['size_bytes'] / 1024:.1f} KB")
            total_entries += scope_stats["entries"]
            total_size += scope_stats["size_bytes"]

            # Collect tier and domain stats
            scope = MemoryScope(scope_name)
            if scope in self.memories:
                for e in self.memories[scope].entries:
                    tiers[e.tier.value] += 1
                    approvals[e.approval_status] += 1
                    for d in e.domains:
                        domains[d] += 1
                    if e.category == "insight":
                        insight_count += 1

        lines.append("")
        lines.append(f"Total: {total_entries} entries ({total_size / 1024:.1f} KB)")
        if approvals.get(_APPROVAL_PENDING, 0):
            lines.append(
                f"Pending Approval: {approvals[_APPROVAL_PENDING]} "
                "(use /memory pending)"
            )
        lines.append("")

        if approvals:
            lines.append("Approval Distribution:")
            for status in [_APPROVAL_APPROVED, _APPROVAL_PENDING, _APPROVAL_REJECTED]:
                lines.append(f"  {status:<12} {approvals.get(status, 0):>4}")
            lines.append("")

        if tiers:
            lines.append("Tier Distribution:")
            for tier_name in ["working", "short_term", "long_term", "archival"]:
                count = tiers.get(tier_name, 0)
                bar = "#" * (count // max(1, total_entries // 20))
                lines.append(f"  {tier_name:<12} {count:>4} {bar}")
            lines.append("")

        if domains:
            lines.append("Domain Distribution:")
            for domain, count in domains.most_common(6):
                lines.append(f"  {domain:<15} {count:>3}")
            lines.append("")

        if insight_count:
            lines.append(f"Curator Insights: {insight_count} synthesized")

        return "\n".join(lines)

    def pending_entries(self, scope: MemoryScope | None = None) -> list[MemoryEntry]:
        scopes = [_coerce_scope(scope)] if scope is not None else list(MemoryScope)
        entries: list[MemoryEntry] = []
        for scoped in scopes:
            entries.extend(
                entry
                for entry in self.memories[scoped].entries
                if entry.approval_status == _APPROVAL_PENDING
            )
        return sorted(entries, key=lambda e: (e.created_at, e.scope.value, e.id))

    def _format_pending_entries(self, scope: MemoryScope | None = None) -> str:
        entries = self.pending_entries(scope)
        if not entries:
            return "No pending memory approvals."
        lines = ["Pending Memory Approvals", "=" * 50]
        for entry in entries:
            risk = self._risk_for_safety(entry.safety_status)
            preview = _sanitize_untrusted_text(entry.content, max_chars=160).replace("\n", "\\n")
            lines.append(
                f"- {entry.id} [{entry.scope.value}] source={entry.source or 'unknown'} "
                f"created={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(entry.created_at))} "
                f"risk={risk} reason={_sanitize_untrusted_text(entry.safety_reason or entry.approval_reason, 120)}"
            )
            provenance = _redact_audit_value(entry.provenance)
            if provenance:
                lines.append(f"  provenance={_sanitize_untrusted_text(json.dumps(provenance, ensure_ascii=False, default=_json_default), 180)}")
            lines.append(f"  summary={preview}")
        return "\n".join(lines)

    def _format_memory_review(self, entry_id: str) -> str:
        scope, entry = self._find_entry_by_id(entry_id)
        if scope is None or entry is None:
            return f"Memory not found: {entry_id}"
        audit_count = len(self.get_approval_audit(entry.id))
        lines = [
            f"Memory Review: {entry.id}",
            "=" * 50,
            f"scope: {scope.value}",
            f"approval: {entry.approval_status}",
            f"safety: {entry.safety_status}",
            f"lifecycle: {entry.lifecycle_status}",
            f"tier: {entry.tier.value}",
            f"source: {entry.source or 'unknown'}",
            f"risk: {self._risk_for_safety(entry.safety_status)}",
            f"reason: {_sanitize_untrusted_text(entry.safety_reason or entry.approval_reason, 240)}",
            f"hash: {entry.approval_content_hash or '[missing]'}",
            f"audit_records: {audit_count}",
            "",
            "Untrusted Content:",
            _sanitize_untrusted_text(entry.content, max_chars=1200),
        ]
        return "\n".join(lines)

    def decide_pending_entry(
        self,
        entry_id: str,
        decision: str,
        *,
        actor: str,
        reason: str,
    ) -> MemoryApprovalMutation:
        """Apply one typed decision and return state, never a parsed message."""
        if decision not in {"approve", "reject"}:
            raise ValueError("invalid Memory decision")
        scope, before = self._find_entry_by_id(entry_id)
        if scope is None or before is None:
            raise KeyError(entry_id)
        if before.approval_status != _APPROVAL_PENDING:
            previous_status = before.approval_status
            if decision == "approve":
                message = (
                    f"Cannot approve memory {before.id}: "
                    f"status={before.approval_status}; only pending memories can be approved."
                )
            elif before.approval_status == _APPROVAL_REJECTED:
                message = f"Memory {before.id} is already rejected."
            else:
                message = self.reject_entry(entry_id, actor=actor, reason=reason)
            _, current = self._find_entry_by_id(entry_id)
            current = current or before
            return MemoryApprovalMutation(
                memory_id=before.id,
                scope=scope,
                status=current.approval_status,
                decision=decision,
                decision_accepted=(
                    decision == "reject"
                    and previous_status == _APPROVAL_APPROVED
                    and current.approval_status == _APPROVAL_REJECTED
                ),
                updated_at=current.updated_at,
                compatibility_message=message,
            )
        if decision == "approve":
            message = self.approve_entry(entry_id, actor=actor, reason=reason)
        else:
            message = self.reject_entry(entry_id, actor=actor, reason=reason)
        final_scope, final = self._find_entry_by_id(entry_id)
        if final_scope is None or final is None:
            raise MemoryStoreConflict("Memory disappeared during decision")
        return MemoryApprovalMutation(
            memory_id=final.id,
            scope=final_scope,
            status=final.approval_status,
            decision=decision,
            decision_accepted=(
                final.approval_status
                == (_APPROVAL_APPROVED if decision == "approve" else _APPROVAL_REJECTED)
            ),
            updated_at=final.updated_at,
            compatibility_message=message,
        )

    def approve_entry(
        self,
        entry_id: str,
        *,
        actor: str = "user",
        reason: str = "",
    ) -> str:
        if not self._transaction_covers(tuple(MemoryScope)):
            try:
                return self.coordinated_write(
                    tuple(MemoryScope),
                    lambda: self.approve_entry(entry_id, actor=actor, reason=reason),
                    reject_stale=True,
                )
            except MemoryStoreConflict:
                return (
                    f"Approval blocked for memory {entry_id}: content hash changed; "
                    "review it again."
                )
        scope, entry = self._find_entry_by_id(entry_id)
        if scope is None or entry is None:
            return f"Memory not found: {entry_id}"
        if entry.approval_status != _APPROVAL_PENDING:
            return (
                f"Cannot approve memory {entry.id}: status={entry.approval_status}; "
                "only pending memories can be approved."
            )
        if entry.curator_locked or entry.lifecycle_status != _ACTIVE_LIFECYCLE or entry.tier == MemoryTier.ARCHIVAL:
            return f"Cannot approve memory {entry.id} directly; use /memory restore {entry.id}."

        current_hash = _approval_hash_for_entry(entry)
        previous_approval = entry.approval_status
        previous_lifecycle = entry.lifecycle_status
        if entry.approval_content_hash != current_hash:
            reset_observations = _reset_version_scoped_observations(entry)
            safety = assess_memory_safety(entry.content, source="approve")
            self._set_entry_safety_and_approval(
                entry,
                safety,
                actor="safety_gate",
                reason="approval hash mismatch; review current content",
                lifecycle_status=_ACTIVE_LIFECYCLE,
            )
            if safety.status == _SAFETY_SAFE:
                entry.approval_status = _APPROVAL_PENDING
                entry.approval_reason = "approval hash mismatch; review current content"
                entry.approval_actor = "safety_gate"
                entry.approval_decided_at = time.time()
            entry.approval_content_hash = current_hash
            self._append_approval_audit(
                scope,
                entry,
                action="approval_hash_mismatch",
                actor=actor,
                previous_approval=previous_approval,
                previous_lifecycle=previous_lifecycle,
                reason=entry.approval_reason,
                safety=safety,
                extra={
                    "version_observations_reset": reset_observations,
                },
            )
            self._save_scope(scope)
            return f"Approval blocked for memory {entry.id}: content hash changed; review it again."

        safety = assess_memory_safety(entry.content, source="approve")
        entry.safety_status = safety.status
        entry.safety_reason = safety.reason
        if safety.status == _SAFETY_UNSAFE:
            entry.approval_status = _APPROVAL_REJECTED
            entry.lifecycle_status = "rejected"
            entry.approval_reason = safety.reason
            entry.approval_actor = "safety_gate"
            entry.approval_decided_at = time.time()
            entry.approval_content_hash = current_hash
            self._append_approval_audit(
                scope,
                entry,
                action="approve_rejected_by_safety",
                actor=actor,
                previous_approval=previous_approval,
                previous_lifecycle=previous_lifecycle,
                reason=safety.reason,
                safety=safety,
            )
            self._save_scope(scope)
            return f"Cannot approve memory {entry.id}: safety_status=unsafe reason={safety.reason}"

        entry.approval_status = _APPROVAL_APPROVED
        entry.approval_reason = reason or "approved by user"
        entry.approval_actor = actor
        entry.approval_decided_at = time.time()
        entry.approval_content_hash = current_hash
        entry.lifecycle_status = _ACTIVE_LIFECYCLE
        entry.updated_at = time.time()
        entry._seal_authority_state()
        self._apply_declared_supersession(entry)
        self._append_approval_audit(
            scope,
            entry,
            action="approve",
            actor=actor,
            previous_approval=previous_approval,
            previous_lifecycle=previous_lifecycle,
            reason=entry.approval_reason,
            safety=safety,
        )
        self._save_scope(scope)
        return f"Approved memory {entry.id}."

    def reject_entry(self, entry_id: str, *, actor: str = "user", reason: str = "") -> str:
        if not self._transaction_covers(tuple(MemoryScope)):
            try:
                return self.coordinated_write(
                    tuple(MemoryScope),
                    lambda: self.reject_entry(entry_id, actor=actor, reason=reason),
                    reject_stale=True,
                )
            except MemoryStoreConflict:
                return (
                    f"Rejection blocked for memory {entry_id}: content hash changed; "
                    "review it again."
                )
        scope, entry = self._find_entry_by_id(entry_id)
        if scope is None or entry is None:
            return f"Memory not found: {entry_id}"
        if entry.approval_status == _APPROVAL_REJECTED and entry.lifecycle_status == "rejected":
            return f"Memory {entry.id} is already rejected."
        previous_approval = entry.approval_status
        previous_lifecycle = entry.lifecycle_status
        current_hash = _approval_hash_for_entry(entry)
        reset_observations = (
            _reset_version_scoped_observations(entry)
            if entry.approval_content_hash != current_hash
            else None
        )
        safety = assess_memory_safety(entry.content, source="reject")
        entry.safety_status = safety.status
        entry.safety_reason = safety.reason
        entry.approval_status = _APPROVAL_REJECTED
        entry.approval_reason = reason or "rejected by user"
        entry.approval_actor = actor
        entry.approval_decided_at = time.time()
        entry.approval_content_hash = current_hash
        entry.lifecycle_status = "rejected"
        entry.curator_locked = False
        entry.updated_at = time.time()
        self._append_approval_audit(
            scope,
            entry,
            action="reject",
            actor=actor,
            previous_approval=previous_approval,
            previous_lifecycle=previous_lifecycle,
            reason=entry.approval_reason,
            safety=safety,
            extra={
                "version_observations_reset": reset_observations,
            },
        )
        self._save_scope(scope)
        return f"Rejected memory {entry.id}."

    def _entry_reference_warnings(self, entry: MemoryEntry) -> list[str]:
        candidates = re.findall(r"(?<![\w/.-])([A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,8})(?![\w/.-])", entry.content)
        warnings: list[str] = []
        workspace_path = Path(self.workspace)
        for candidate in dict.fromkeys(candidates):
            if candidate.startswith(("http://", "https://")):
                continue
            path = Path(candidate)
            if not path.is_absolute():
                path = workspace_path / candidate
            if not path.exists():
                warnings.append(candidate)
        return warnings[:10]

    @_coordinated_all_write
    def restore_entry(self, entry_id: str, *, actor: str = "user") -> str:
        scope, entry = self._find_entry_by_id(entry_id)
        if scope is None or entry is None:
            return f"Memory not found: {entry_id}"
        if entry.is_active:
            return f"Memory {entry.id} is already active and approved."
        if entry.lifecycle_status == "superseded":
            # A superseded lesson was retired because a replacement is live.
            # Restoring it while the replacement is still approved would
            # revive exactly the contradiction the supersession machinery
            # retired; once the replacement is itself retired, restoring the
            # old lesson is legitimate.
            replacement = next(
                (
                    candidate
                    for candidate in self.memories[scope].entries
                    if entry.id
                    in (
                        candidate.metadata.get("supersedes", [])
                        if isinstance(candidate.metadata, dict)
                        else []
                    )
                ),
                None,
            )
            if replacement is not None and replacement.is_active:
                return (
                    f"Memory {entry.id} is superseded by the approved "
                    f"replacement {replacement.id} and cannot be restored."
                )

        previous_approval = entry.approval_status
        previous_lifecycle = entry.lifecycle_status
        current_hash = _approval_hash_for_entry(entry)
        reset_observations = (
            _reset_version_scoped_observations(entry)
            if entry.approval_content_hash != current_hash
            else None
        )
        safety = assess_memory_safety(entry.content, source="restore")
        missing_refs = self._entry_reference_warnings(entry)
        if missing_refs and safety.status == _SAFETY_SAFE:
            safety = MemorySafetyResult(
                _SAFETY_SUSPICIOUS,
                "referenced files are missing: " + ", ".join(missing_refs),
                "medium",
            )

        self._set_entry_safety_and_approval(
            entry,
            safety,
            actor="safety_gate",
            reason="restore revalidation",
            lifecycle_status=_ACTIVE_LIFECYCLE,
        )
        if safety.status != _SAFETY_UNSAFE:
            entry.lifecycle_status = _ACTIVE_LIFECYCLE
            entry.curator_locked = False
            entry.deprecated_at = None
            if entry.tier == MemoryTier.ARCHIVAL:
                entry.tier = MemoryTier.SHORT_TERM
                entry.tier_reason = "restored"
        self._append_approval_audit(
            scope,
            entry,
            action="restore",
            actor=actor,
            previous_approval=previous_approval,
            previous_lifecycle=previous_lifecycle,
            reason=entry.approval_reason,
            safety=safety,
            extra={
                "missing_references": missing_refs,
                "version_observations_reset": reset_observations,
            },
        )
        self._save_scope(scope)
        if entry.approval_status == _APPROVAL_APPROVED:
            return f"Restored and approved memory {entry.id}."
        if entry.approval_status == _APPROVAL_PENDING:
            return f"Restored memory {entry.id} to pending approval: {entry.safety_reason}"
        return f"Restore rejected memory {entry.id}: {entry.safety_reason}"
    
    @_coordinated_scope_write
    def clear_scope(self, scope: MemoryScope) -> None:
        """Clear all entries in a scope."""
        self.memories[scope] = MemoryFile(scope=scope)
        self._save_scope(scope)

    def handle_user_memory_input(self, user_input: str) -> str | None:
        """Handle explicit memory inputs from the main chat path.

        Supported forms:
        - "# remember this project convention"
        - "/memory add remember this project convention"
        - "/memory add project: remember this shared project convention"
        - "/memory add local: remember this local-only note"
        - "/memory add user: remember this cross-project preference"
        """
        raw = user_input.strip()
        if not raw:
            return None

        content = ""
        scope = MemoryScope.PROJECT
        category = "note"

        if raw == "/memory":
            return self.format_stats()
        if raw.startswith("/memory pending"):
            scope_filter: MemoryScope | None = None
            scope_match = re.search(r"--scope\s+(\w+)", raw, flags=re.I)
            if scope_match:
                try:
                    scope_filter = MemoryScope(scope_match.group(1).lower())
                except ValueError:
                    return "Usage: /memory pending [--scope USER|PROJECT|LOCAL]"
            return self._format_pending_entries(scope_filter)
        if raw.startswith("/memory review "):
            entry_id = raw[len("/memory review ") :].strip()
            if not entry_id:
                return "Usage: /memory review <entry_id>"
            return self._format_memory_review(entry_id)
        if raw.startswith("/memory approve "):
            entry_id = raw[len("/memory approve ") :].strip()
            if not entry_id:
                return "Usage: /memory approve <entry_id>"
            try:
                return self.decide_pending_entry(
                    entry_id,
                    "approve",
                    actor="user",
                    reason="",
                ).compatibility_message
            except KeyError:
                return f"Memory not found: {entry_id}"
        if raw.startswith("/memory reject "):
            rest = raw[len("/memory reject ") :].strip()
            if not rest:
                return "Usage: /memory reject <entry_id>"
            entry_id, _, reason = rest.partition(" ")
            try:
                return self.decide_pending_entry(
                    entry_id,
                    "reject",
                    actor="user",
                    reason=reason.strip(),
                ).compatibility_message
            except KeyError:
                return f"Memory not found: {entry_id}"
        if raw.startswith("/memory restore "):
            entry_id = raw[len("/memory restore ") :].strip()
            if not entry_id:
                return "Usage: /memory restore <entry_id>"
            return self.restore_entry(entry_id)

        if raw.startswith("#"):
            content = raw[1:].strip()
            category = "directive"
        elif raw.startswith("/memory add "):
            content = raw[len("/memory add ") :].strip()
            scope_match = re.match(r"^(user|project|local)\s*:\s*(.+)$", content, flags=re.I)
            if scope_match:
                scope = MemoryScope(scope_match.group(1).lower())
                content = scope_match.group(2).strip()
                # USER-scope facet syntax ("role: 资深后端") files the entry
                # into the always-rendered User Profile block instead of the
                # query-gated retrieval lane.
                if scope == MemoryScope.USER:
                    facet_match = re.match(
                        r"^(role|skill|domain|concern|preference|compliance|"
                        r"background)\s*[:：]\s*(.+)$",
                        content,
                        flags=re.I,
                    )
                    if facet_match:
                        category = USER_PROFILE_CATEGORY
        else:
            return None

        if not content:
            return "Usage: # <memory> or /memory add [user|project|local:] <memory>"

        entry = self.add_entry(scope, category, content, tags=["chat"])
        if entry is None:
            rejection = self.last_add_rejection
            if rejection:
                return f"Memory not saved ({scope.value}): {rejection}"
            return "Usage: # <memory> or /memory add [user|project|local:] <memory>"
        if entry.approval_status == _APPROVAL_PENDING:
            reason = _sanitize_untrusted_text(entry.safety_reason or entry.approval_reason, max_chars=160)
            return (
                f"Memory pending approval ({entry.scope.value}): {entry.id} reason={reason}. "
                f"Use /memory review {entry.id} before /memory approve {entry.id} or /memory reject {entry.id}."
            )
        if entry.approval_status == _APPROVAL_REJECTED:
            reason = _sanitize_untrusted_text(entry.safety_reason or entry.approval_reason, max_chars=160)
            return f"Rejected memory ({entry.scope.value}): {entry.id} reason={reason}"
        return f"Saved memory ({entry.scope.value}): {entry.content}"

    def check_integrity(self, scope: MemoryScope) -> dict[str, Any]:
        """Validate all entries in a scope for integrity.

        Checks:
        - Valid IDs (non-empty strings)
        - Valid categories (non-empty strings)
        - Non-empty content
        - No duplicate IDs

        Args:
            scope: Memory scope to check

        Returns:
            Dictionary with {is_valid: bool, issues: list[str]}
        """
        issues: list[str] = []
        seen_ids: set[str] = set()
        entries = self.memories[scope].entries

        for idx, entry in enumerate(entries):
            if not entry.id or not isinstance(entry.id, str):
                issues.append(
                    f"Entry at index {idx} has invalid or empty ID"
                )

            if entry.id in seen_ids:
                issues.append(
                    f"Duplicate ID found: '{entry.id}' "
                    f"(entries {list(self._find_entry_indices(scope, entry.id))})"
                )
            else:
                seen_ids.add(entry.id)

            if not entry.category or not isinstance(entry.category, str):
                issues.append(
                    f"Entry '{entry.id}' has invalid or empty category"
                )

            if not entry.content or not isinstance(entry.content, str):
                issues.append(
                    f"Entry '{entry.id}' has empty or invalid content"
                )

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
        }

    @_coordinated_scope_write
    def compress_scope(
        self, scope: MemoryScope, similarity_threshold: float = 0.8
    ) -> dict[str, int]:
        """Compress memory entries by merging similar content.

        Merges entries with content similarity above the threshold.
        Removes duplicate entries (exact content matches).
        Updates timestamps and preserves usage counts only when the surviving
        entry's approval-bound authority version is unchanged.

        Args:
            scope: Memory scope to compress
            similarity_threshold: Jaccard similarity threshold for merging
                (default 0.8 = 80%)

        Returns:
            Stats dictionary with {merged_count, removed_count, remaining_count}
        """
        entries = self.memories[scope].entries
        if len(entries) <= 1:
            return {"merged_count": 0, "removed_count": 0, "remaining_count": len(entries)}

        seen_content: dict[str, int] = {}
        duplicates_removed = 0
        removed_to_survivor: dict[str, str] = {}

        unique_entries = []
        for entry in entries:
            content_key = entry.content.strip().lower()
            if content_key in seen_content:
                master_idx = seen_content[content_key]
                master = unique_entries[master_idx]
                removed_to_survivor[entry.id] = master.id
                previous_approval = master.approval_status
                previous_lifecycle = master.lifecycle_status
                previous_authority_digest = _approval_hash_for_entry(master)
                master.usage_count = _saturating_counter_add(
                    master.usage_count,
                    entry.usage_count,
                )
                master.updated_at = max(master.updated_at, entry.updated_at)
                master.tags = sorted(
                    list(set(master.tags + entry.tags))
                )
                if _approval_hash_for_entry(master) != previous_authority_digest:
                    reset_observations = _reset_version_scoped_observations(master)
                    safety = assess_memory_safety(
                        master.content,
                        source="compress",
                    )
                    if (
                        previous_approval != _APPROVAL_REJECTED
                        and previous_lifecycle != "rejected"
                    ):
                        self._set_entry_safety_and_approval(
                            master,
                            safety,
                            actor="safety_gate",
                            reason=(
                                "exact duplicate tag merge invalidated approval"
                            ),
                            lifecycle_status=master.lifecycle_status,
                            approval_policy=(
                                MemoryApprovalPolicy.USER_REVIEW_REQUIRED
                            ),
                        )
                    else:
                        master.safety_status = safety.status
                        master.safety_reason = safety.reason
                        master.approval_content_hash = _approval_hash_for_entry(
                            master
                        )
                    self._append_approval_audit(
                        scope,
                        master,
                        action="compress_exact_duplicate",
                        actor="safety_gate",
                        previous_approval=previous_approval,
                        previous_lifecycle=previous_lifecycle,
                        reason=(
                            "exact duplicate tag merge invalidated approval"
                        ),
                        safety=safety,
                        extra={
                            "merged_entry_id": entry.id,
                            "previous_authority_digest": (
                                previous_authority_digest
                            ),
                            "version_observations_reset": reset_observations,
                        },
                    )
                duplicates_removed += 1
            else:
                seen_content[content_key] = len(unique_entries)
                unique_entries.append(entry)

        merged_count = 0
        final_entries: list[MemoryEntry] = []
        merged_indices: set[int] = set()

        for i, entry_a in enumerate(unique_entries):
            if i in merged_indices:
                continue

            best_match_idx = None
            best_similarity = 0.0

            for j, entry_b in enumerate(unique_entries):
                # Earlier entries are deterministic survivors. Restricting
                # candidates to the suffix also ensures every replacement map
                # points from an actually removed entry to a retained one.
                if j <= i or j in merged_indices:
                    continue

                similarity = self._jaccard_similarity(
                    entry_a.content, entry_b.content
                )
                if similarity >= similarity_threshold and similarity > best_similarity:
                    best_similarity = similarity
                    best_match_idx = j

            if best_match_idx is not None:
                entry_b = unique_entries[best_match_idx]
                merged_content = self._merge_entry_content(
                    entry_a.content, entry_b.content
                )
                previous_approval = entry_a.approval_status
                previous_lifecycle = entry_a.lifecycle_status
                previous_authority_digest = _approval_hash_for_entry(entry_a)
                entry_a.content = merged_content
                entry_a.usage_count = _saturating_counter_add(
                    entry_a.usage_count,
                    entry_b.usage_count,
                )
                entry_a.updated_at = max(
                    entry_a.updated_at, entry_b.updated_at
                )
                entry_a.tags = sorted(
                    list(set(entry_a.tags + entry_b.tags))
                )
                reset_observations = (
                    _reset_version_scoped_observations(entry_a)
                    if _approval_hash_for_entry(entry_a)
                    != previous_authority_digest
                    else None
                )
                safety = assess_memory_safety(entry_a.content, source="compress")
                if previous_approval != _APPROVAL_REJECTED and previous_lifecycle != "rejected":
                    self._set_entry_safety_and_approval(
                        entry_a,
                        safety,
                        actor="safety_gate",
                        reason="compression merge invalidated approval",
                        lifecycle_status=entry_a.lifecycle_status,
                        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
                    )
                else:
                    entry_a.safety_status = safety.status
                    entry_a.safety_reason = safety.reason
                    entry_a.approval_content_hash = _approval_hash_for_entry(entry_a)
                self._append_approval_audit(
                    scope,
                    entry_a,
                    action="compress_merge",
                    actor="safety_gate",
                    previous_approval=previous_approval,
                    previous_lifecycle=previous_lifecycle,
                    reason="compression merge invalidated approval",
                    safety=safety,
                    extra={
                        "previous_authority_digest": previous_authority_digest,
                        "version_observations_reset": reset_observations,
                    },
                )
                removed_to_survivor[entry_b.id] = entry_a.id
                merged_indices.add(best_match_idx)
                merged_count += 1

            final_entries.append(entry_a)

        for entry in final_entries:
            _rewrite_memory_relationship_references(
                entry,
                removed_to_survivor,
            )
        self.memories[scope].entries = final_entries
        self.memories[scope]._rebuild_indices()
        self._save_scope(scope)

        return {
            "merged_count": merged_count,
            "removed_count": duplicates_removed,
            "remaining_count": len(final_entries),
        }

    @staticmethod
    def _jaccard_similarity(text_a: str, text_b: str) -> float:
        """Compute Jaccard similarity between two text strings.

        Uses token-based Jaccard similarity: |A ∩ B| / |A ∪ B|

        Args:
            text_a: First text string
            text_b: Second text string

        Returns:
            Similarity score between 0.0 and 1.0
        """
        tokens_a = set(_tokenize(text_a))
        tokens_b = set(_tokenize(text_b))

        if not tokens_a and not tokens_b:
            return 1.0
        if not tokens_a or not tokens_b:
            return 0.0

        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b

        return len(intersection) / len(union)

    @staticmethod
    def _merge_entry_content(content_a: str, content_b: str) -> str:
        """Merge two similar content strings.

        Keeps the longer version, appends unique parts from the shorter.

        Args:
            content_a: First content string
            content_b: Second content string

        Returns:
            Merged content string
        """
        if len(content_a) >= len(content_b):
            return content_a
        return content_b

    def detect_conflicts(self, content: str, scope: MemoryScope | None = None, threshold: float = 0.6) -> list[tuple[MemoryEntry, float]]:
        """Detect potential conflicts between new content and existing memories.

        Uses Jaccard similarity on token sets to identify entries that may
        contradict or overlap with the proposed new memory content.

        Args:
            content: New memory content to check for conflicts
            scope: Scope to check (None = all scopes)
            threshold: Similarity threshold for conflict flagging (0.0-1.0)

        Returns:
            List of (entry, similarity) tuples sorted by similarity descending
        """
        new_tokens = set(_tokenize(content))
        if not new_tokens:
            return []

        conflicts: list[tuple[MemoryEntry, float]] = []
        scopes = [scope] if scope else list(MemoryScope)

        for s in scopes:
            if s not in self.memories:
                continue
            for entry in self.memories[s].entries:
                old_tokens = set(entry.get_tokens())
                if not old_tokens:
                    continue
                intersection = new_tokens & old_tokens
                union = new_tokens | old_tokens
                similarity = len(intersection) / len(union) if union else 0.0
                if similarity >= threshold:
                    conflicts.append((entry, similarity))

        conflicts.sort(key=lambda x: x[1], reverse=True)
        return conflicts

    @_coordinated_all_write
    def decay_memories(
        self,
        max_age_days: float = 30.0,
        decay_factor: float = 0.5,
        *,
        operation_id: str | None = None,
        effective_at: float | None = None,
    ) -> int:
        """Apply time-based decay to memory usage_count.

        Entries older than max_age_days have their usage_count halved
        (multiplied by decay_factor), reducing their search ranking.
        Returns number of entries changed by this invocation. A stable
        ``operation_id`` makes partial multi-scope publication replay-safe;
        reusing it with a different policy fails closed. ``effective_at`` can
        freeze eligibility across a delayed retry.
        """
        if (
            isinstance(max_age_days, bool)
            or not isinstance(max_age_days, (int, float))
            or not math.isfinite(float(max_age_days))
            or float(max_age_days) < 0
        ):
            raise ValueError("max_age_days must be a finite non-negative number")
        if (
            isinstance(decay_factor, bool)
            or not isinstance(decay_factor, (int, float))
            or not math.isfinite(float(decay_factor))
            or not 0 <= float(decay_factor) <= 1
        ):
            raise ValueError("decay_factor must be between zero and one")
        if effective_at is None:
            now = time.time()
        elif (
            isinstance(effective_at, bool)
            or not isinstance(effective_at, (int, float))
            or not math.isfinite(float(effective_at))
            or not 0 <= float(effective_at) <= MAX_MEMORY_TIMESTAMP
        ):
            raise ValueError("effective_at must be a valid timestamp")
        else:
            now = float(effective_at)
        receipt = self._prepare_scope_effect_receipt(
            operation_id,
            lane="maintenance",
            channel="decay",
            payload={
                "max_age_days": float(max_age_days),
                "decay_factor": float(decay_factor),
                "effective_at": (
                    float(effective_at) if effective_at is not None else None
                ),
            },
        )
        candidates: dict[MemoryScope, list[MemoryEntry]] = {}
        for scope in MemoryScope:
            for entry in self.memories.get(scope, MemoryFile(scope=scope)).entries:
                age_days = (now - entry.updated_at) / 86400.0
                if age_days > float(max_age_days) and entry.usage_count > 0:
                    candidates.setdefault(scope, []).append(entry)
        self._require_replay_identity_for_multiple_scopes(
            set(candidates),
            receipt,
        )
        decayed = 0
        for scope in sorted(candidates, key=lambda item: item.value):
            if not self._claim_scope_effect_receipt(
                scope,
                lane="maintenance",
                receipt=receipt,
            ):
                continue
            for entry in candidates[scope]:
                entry.usage_count = max(
                    0,
                    int(entry.usage_count * float(decay_factor)),
                )
                decayed += 1
            self._save_scope(scope)
        return decayed

    @_coordinated_all_write
    def promote_memories(self) -> dict[str, int]:
        """Promote/demote memories across tiers based on usage and age.

        WORKING → SHORT_TERM → LONG_TERM → ARCHIVAL
        Returns counts per operation.
        """
        now = time.time()
        stats = {"promoted_to_long": 0, "demoted_to_archival": 0, "reactivated": 0}
        touched_scopes: set[MemoryScope] = set()
        for scope in MemoryScope:
            if scope not in self.memories:
                continue
            for entry in self.memories[scope].entries:
                if not entry.is_active:
                    continue
                age_days = (now - entry.updated_at) / 86400.0
                # A content/version rewrite is new activity without being a
                # retrieval.  Version resets therefore clear ``last_accessed``
                # but ``updated_at`` prevents that fresh version from looking
                # epoch-old and being archived immediately.
                activity_at = max(entry.updated_at, entry.last_accessed)
                accessed_days = (now - activity_at) / 86400.0
                if entry.tier == MemoryTier.SHORT_TERM and entry.usage_count >= 5 and age_days > 7:
                    entry.tier = MemoryTier.LONG_TERM
                    entry.tier_reason = "usage"
                    entry._seal_authority_state()
                    stats["promoted_to_long"] += 1
                    touched_scopes.add(scope)
                    continue
                if entry.tier == MemoryTier.LONG_TERM and accessed_days > 30:
                    previous_approval = entry.approval_status
                    previous_lifecycle = entry.lifecycle_status
                    previous_authority_digest = _approval_hash_for_entry(entry)
                    entry.tier = MemoryTier.ARCHIVAL
                    entry.tier_reason = "age_decay"
                    entry.content = self._summarize_content(entry.content)
                    reset_observations = (
                        _reset_version_scoped_observations(entry)
                        if _approval_hash_for_entry(entry)
                        != previous_authority_digest
                        else None
                    )
                    entry.invalidate_tokens()
                    safety = assess_memory_safety(entry.content, source="curator")
                    entry.safety_status = safety.status
                    entry.safety_reason = safety.reason
                    entry.approval_status = (
                        _APPROVAL_REJECTED
                        if safety.status == _SAFETY_UNSAFE
                        else _APPROVAL_PENDING
                    )
                    entry.approval_reason = "curator summary rewrite requires approval"
                    entry.approval_actor = "curator"
                    entry.approval_decided_at = time.time()
                    entry.approval_content_hash = _approval_hash_for_entry(entry)
                    if safety.status == _SAFETY_UNSAFE:
                        entry.lifecycle_status = "rejected"
                    self._append_approval_audit(
                        scope,
                        entry,
                        action="curator_rewrite",
                        actor="curator",
                        previous_approval=previous_approval,
                        previous_lifecycle=previous_lifecycle,
                        reason=entry.approval_reason,
                        safety=safety,
                        extra={
                            "previous_authority_digest": (
                                previous_authority_digest
                            ),
                            "version_observations_reset": (
                                reset_observations
                            ),
                        },
                    )
                    stats["demoted_to_archival"] += 1
                    touched_scopes.add(scope)
                    continue
                if entry.tier == MemoryTier.ARCHIVAL and accessed_days < 7:
                    entry.tier = MemoryTier.SHORT_TERM
                    entry.tier_reason = "recent_access"
                    entry._seal_authority_state()
                    stats["reactivated"] += 1
                    touched_scopes.add(scope)
        for scope in sorted(touched_scopes, key=lambda item: item.value):
            self._save_scope(scope)
        return stats

    @_coordinated_all_write
    def link_memories(self, similarity_threshold: float = 0.4) -> int:
        """Auto-link related memories by content similarity. Returns link count."""
        links = 0
        touched_scopes: set[MemoryScope] = set()
        for scope in MemoryScope:
            if scope not in self.memories:
                continue
            entries = self.memories[scope].entries
            for i, a in enumerate(entries):
                for j, b in enumerate(entries):
                    if i >= j:
                        continue
                    if b.id in a.related_to:
                        continue
                    if self._jaccard_similarity(a.content, b.content) >= similarity_threshold:
                        a.related_to.append(b.id)
                        b.related_to.append(a.id)
                        a.updated_at = time.time()
                        b.updated_at = time.time()
                        a._seal_authority_state()
                        b._seal_authority_state()
                        links += 2
                        touched_scopes.add(scope)
        for scope in sorted(touched_scopes, key=lambda item: item.value):
            self._save_scope(scope)
        return links

    def get_linked_memories(self, entry_id: str, depth: int = 1) -> list[MemoryEntry]:
        """Get memories linked to entry_id via related_to graph (BFS up to depth)."""
        entry = None
        found_scope = None
        for s in MemoryScope:
            if s in self.memories:
                entry = self.memories[s]._id_index.get(entry_id)
                if entry:
                    found_scope = s
                    break
        if not entry or not entry.related_to or not found_scope:
            return []
        visited = {entry_id}
        frontier = list(entry.related_to)
        results = []
        for _ in range(depth):
            nxt = []
            for rid in frontier:
                if rid in visited:
                    continue
                visited.add(rid)
                linked = self.memories[found_scope]._id_index.get(rid)
                if linked:
                    results.append(linked)
                    nxt.extend(linked.related_to)
            frontier = nxt
            if not frontier:
                break
        return results

    @staticmethod
    def _summarize_content(content: str, max_len: int = 150) -> str:
        if len(content) <= max_len:
            return content
        for sep in [". ", ".\n", "; ", ";\n", "\n"]:
            idx = content.find(sep)
            if 20 < idx < max_len:
                return content[:idx + 1]
        return content[:max_len] + "..."

    def _find_entry_indices(self, scope: MemoryScope, entry_id: str) -> list[int]:
        """Find all indices of entries with a given ID."""
        indices = []
        for idx, entry in enumerate(self.memories[scope].entries):
            if entry.id == entry_id:
                indices.append(idx)
        return indices


# ---------------------------------------------------------------------------
# System prompt integration
# ---------------------------------------------------------------------------

def inject_memory_into_prompt(
    system_prompt: str,
    memory_manager: MemoryManager,
    max_tokens: int = 8000,
    *,
    query: str | None = None,
    current_files: list[str] | None = None,
    active_domains: list[str] | None = None,
    management_mode: bool = False,
) -> str:
    """Compatibility injection facade backed by canonical retrieval.

    Production callers must provide a query. ``management_mode`` preserves an
    explicit human-facing all-active view but does not record injection usage.
    """
    normalized_query = (query or "").strip()
    if not normalized_query:
        if management_mode:
            memory_context = memory_manager.get_relevant_context(max_tokens=max_tokens)
            if not memory_context:
                return system_prompt
            return f"""{system_prompt}

## Project Memory & Context

The following information has been accumulated from previous sessions:

{memory_context}

Use this context to inform your decisions and follow established patterns."""
        return system_prompt

    from minicode.memory_retrieval import (
        CanonicalMemoryRetriever,
        MemoryRetrievalRequest,
        RetrievalSource,
    )

    result = CanonicalMemoryRetriever(memory_manager).retrieve(
        MemoryRetrievalRequest(
            query=normalized_query,
            current_files=tuple(current_files or ()),
            active_domains=tuple(active_domains or ()),
            max_total_tokens=max_tokens,
            source_entrypoint=RetrievalSource.COMPATIBILITY,
        )
    )
    memory_context = result.prompt_text
    
    if not memory_context:
        return system_prompt

    injected_prompt = f"""{system_prompt}

## Project Memory & Context

The following information has been accumulated from previous sessions:

{memory_context}

Use this context to inform your decisions and follow established patterns."""
    memory_manager.record_retrievals_and_injections(
        list(result.selected_ids),
        list(result.rendered_ids),
        observation_id=f"compatibility-prompt-{uuid.uuid4().hex}",
    )
    return injected_prompt


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def format_memory_list(memory_manager=None, scope: MemoryScope | None = None, category: str | None = None) -> str:
    """Format memory entries for CLI display."""
    if memory_manager is None:
        return "No MemoryManager available."

    # Collect entries from specified scope(s)
    scopes = [scope] if scope else list(MemoryScope)
    all_entries: list[MemoryEntry] = []
    for s in scopes:
        if s in memory_manager.memories:
            entries = memory_manager.memories[s].entries
            if category:
                entries = [e for e in entries if e.category == category]
            all_entries.extend(entries)

    if not all_entries:
        return "No memories found."

    lines = [f"{'=' * 60}"]
    for entry in all_entries[:20]:  # Limit to 20 entries
        scope_tag = f"[{entry.scope.value if hasattr(entry, 'scope') else '?'}]"
        cat_tag = f"[{entry.category}]"
        content_preview = entry.content[:100].replace('\n', ' ')
        lines.append(f"{scope_tag} {cat_tag} {content_preview}")
        if entry.tags:
            lines.append(f"     Tags: {', '.join(entry.tags[:5])}")
        lines.append(f"     Used: {entry.usage_count}x | Updated: {time.strftime('%Y-%m-%d %H:%M', time.localtime(entry.updated_at))}")
        lines.append("")
    lines.append(f"{'=' * 60}")
    lines.append(f"Total: {len(all_entries)} entries")
    return "\n".join(lines)
