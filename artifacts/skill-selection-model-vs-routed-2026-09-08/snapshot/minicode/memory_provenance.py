"""Strict, content-free Memory provenance transport contract.

The contract crosses subprocess and Run-observation boundaries, so malformed
input is rejected instead of repaired.  It carries only opaque Memory entry
identities and their exact approval-content authority digests; prompts,
queries, paths, sub-agent identities, and other model-controlled content are
deliberately outside this payload.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from minicode.memory_authority_io import (
    MEMORY_ENTRY_ID_PATTERN,
    is_memory_entry_id,
)

MEMORY_PROVENANCE_SCHEMA_VERSION = 1
MAX_MEMORY_PROVENANCE_VERSIONS = 64
MAX_MEMORY_PROVENANCE_BATCHES = 128

MEMORY_PROVENANCE_EFFECTS = frozenset({"rendered", "written"})
MEMORY_PROVENANCE_ORIGINS = frozenset(
    {"task_start", "context_compaction", "reflection"}
)
MEMORY_PROVENANCE_COVERAGE = frozenset({"complete", "partial", "unknown"})

_AUTHORITY_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_CONTEXT_OPERATION_ID_RE = re.compile(r"^ctxop_[0-9a-f]{32}$")

_REQUIRED_FIELDS = frozenset(
    {
        "schemaVersion",
        "effect",
        "origin",
        "authorityVersions",
        "coverage",
    }
)
_OPTIONAL_FIELDS = frozenset({"receiptId", "contextOperationId"})

AuthorityVersion = tuple[str, str]


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryProvenanceBatch:
    """One bounded provenance observation from a single Memory operation."""

    schema_version: int = MEMORY_PROVENANCE_SCHEMA_VERSION
    effect: str
    origin: str
    authority_versions: tuple[AuthorityVersion, ...] = ()
    receipt_id: str | None = None
    context_operation_id: str | None = None
    coverage: str = "complete"

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(
            self.schema_version, int
        ):
            raise TypeError("Memory provenance schema_version must be an integer")
        if self.schema_version != MEMORY_PROVENANCE_SCHEMA_VERSION:
            raise ValueError("Memory provenance schema_version is unsupported")
        if not isinstance(self.effect, str):
            raise TypeError("Memory provenance effect must be a string")
        if self.effect not in MEMORY_PROVENANCE_EFFECTS:
            raise ValueError("Memory provenance effect is invalid")
        if not isinstance(self.origin, str):
            raise TypeError("Memory provenance origin must be a string")
        if self.origin not in MEMORY_PROVENANCE_ORIGINS:
            raise ValueError("Memory provenance origin is invalid")
        if not isinstance(self.coverage, str):
            raise TypeError("Memory provenance coverage must be a string")
        if self.coverage not in MEMORY_PROVENANCE_COVERAGE:
            raise ValueError("Memory provenance coverage is invalid")
        if not isinstance(self.authority_versions, tuple):
            raise TypeError("Memory provenance authority_versions must be a tuple")
        if len(self.authority_versions) > MAX_MEMORY_PROVENANCE_VERSIONS:
            raise ValueError("Memory provenance contains too many authority versions")

        seen: set[AuthorityVersion] = set()
        for version in self.authority_versions:
            if not isinstance(version, tuple) or len(version) != 2:
                raise TypeError("Memory provenance authority version must be a pair")
            entry_id, digest = version
            if not isinstance(entry_id, str) or not isinstance(digest, str):
                raise TypeError(
                    "Memory provenance authority version values must be strings"
                )
            if not is_memory_entry_id(entry_id):
                raise ValueError("Memory provenance entry ID is invalid")
            if _AUTHORITY_DIGEST_RE.fullmatch(digest) is None:
                raise ValueError("Memory provenance authority digest is invalid")
            if version in seen:
                raise ValueError(
                    "Memory provenance contains a duplicate authority version"
                )
            seen.add(version)

        if self.coverage == "unknown":
            if self.authority_versions:
                raise ValueError("Unknown Memory provenance cannot contain versions")
        elif not self.authority_versions:
            raise ValueError(
                "Complete or partial Memory provenance requires an authority version"
            )

        if self.receipt_id is not None:
            if not isinstance(self.receipt_id, str):
                raise TypeError("Memory provenance receipt_id must be a string or None")
            if _RECEIPT_ID_RE.fullmatch(self.receipt_id) is None:
                raise ValueError("Memory provenance receipt_id is invalid")
        if self.context_operation_id is not None:
            if not isinstance(self.context_operation_id, str):
                raise TypeError(
                    "Memory provenance context_operation_id must be a string or None"
                )
            if _CONTEXT_OPERATION_ID_RE.fullmatch(self.context_operation_id) is None:
                raise ValueError("Memory provenance context_operation_id is invalid")

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-safe, content-free representation."""
        payload: dict[str, object] = {
            "schemaVersion": self.schema_version,
            "effect": self.effect,
            "origin": self.origin,
            "authorityVersions": [
                [entry_id, digest] for entry_id, digest in self.authority_versions
            ],
            "coverage": self.coverage,
        }
        if self.receipt_id is not None:
            payload["receiptId"] = self.receipt_id
        if self.context_operation_id is not None:
            payload["contextOperationId"] = self.context_operation_id
        return payload

    @classmethod
    def from_dict(cls, value: object) -> MemoryProvenanceBatch:
        """Parse one canonical payload, rejecting unknown or missing fields."""
        if not isinstance(value, Mapping):
            raise TypeError("Memory provenance payload must be a mapping")
        fields = set(value)
        if not _REQUIRED_FIELDS.issubset(fields):
            raise ValueError("Memory provenance payload is missing required fields")
        if not fields.issubset(_REQUIRED_FIELDS | _OPTIONAL_FIELDS):
            raise ValueError("Memory provenance payload contains unknown fields")

        raw_versions = value.get("authorityVersions")
        if not isinstance(raw_versions, (list, tuple)):
            raise TypeError("Memory provenance authorityVersions must be a list")
        versions: list[AuthorityVersion] = []
        for raw_version in raw_versions:
            if not isinstance(raw_version, (list, tuple)) or len(raw_version) != 2:
                raise TypeError("Memory provenance authorityVersion must be a pair")
            entry_id, digest = raw_version
            if not isinstance(entry_id, str) or not isinstance(digest, str):
                raise TypeError(
                    "Memory provenance authorityVersion values must be strings"
                )
            versions.append((entry_id, digest))

        return cls(
            schema_version=value.get("schemaVersion"),  # type: ignore[arg-type]
            effect=value.get("effect"),  # type: ignore[arg-type]
            origin=value.get("origin"),  # type: ignore[arg-type]
            authority_versions=tuple(versions),
            receipt_id=value.get("receiptId"),  # type: ignore[arg-type]
            context_operation_id=value.get(  # type: ignore[arg-type]
                "contextOperationId"
            ),
            coverage=value.get("coverage"),  # type: ignore[arg-type]
        )


def normalize_memory_provenance_batches(
    value: object,
    *,
    max_batches: int = MAX_MEMORY_PROVENANCE_BATCHES,
) -> tuple[MemoryProvenanceBatch, ...]:
    """Validate a hostile batch collection with all-or-nothing semantics.

    Subprocess payloads must never be partially trusted: if the collection or
    any member is malformed, the whole observation degrades to an empty tuple.
    ``max_batches`` is trusted caller configuration and therefore raises when
    its own type or value is invalid.
    """
    if isinstance(max_batches, bool) or not isinstance(max_batches, int):
        raise TypeError("max_batches must be an integer")
    if max_batches < 0:
        raise ValueError("max_batches must be non-negative")
    if not isinstance(value, (list, tuple)) or len(value) > max_batches:
        return ()
    try:
        return tuple(MemoryProvenanceBatch.from_dict(item) for item in value)
    except (TypeError, ValueError):
        return ()


__all__ = [
    "MAX_MEMORY_PROVENANCE_BATCHES",
    "MAX_MEMORY_PROVENANCE_VERSIONS",
    "MEMORY_ENTRY_ID_PATTERN",
    "MEMORY_PROVENANCE_COVERAGE",
    "MEMORY_PROVENANCE_EFFECTS",
    "MEMORY_PROVENANCE_ORIGINS",
    "MEMORY_PROVENANCE_SCHEMA_VERSION",
    "AuthorityVersion",
    "MemoryProvenanceBatch",
    "is_memory_entry_id",
    "normalize_memory_provenance_batches",
]
