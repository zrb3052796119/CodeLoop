"""Closed, content-free Run event for optional Memory write failures."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

MEMORY_PERSISTENCE_FAILURE_EVENT_TYPE = "memory.persistence.failed"
MEMORY_PERSISTENCE_FAILURE_SCHEMA_VERSION = 1
MAX_MEMORY_PERSISTENCE_REASON_CODES = 8

_PHASES = frozenset({"committed_user_evidence"})
_OPERATION_ID_RE = re.compile(r"^memoryop_[0-9a-f]{32}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FIELDS = frozenset(
    {"schemaVersion", "phase", "operationId", "reasonCodes"}
)


def memory_persistence_failure_payload(
    *,
    phase: str,
    operation_id: str,
    reason_codes: Sequence[str],
) -> dict[str, object]:
    """Build one strict event payload without task, prompt, or error text."""
    selected_reasons: list[str] = []
    for reason in reason_codes:
        if (
            isinstance(reason, str)
            and _REASON_CODE_RE.fullmatch(reason) is not None
            and reason not in selected_reasons
        ):
            selected_reasons.append(reason)
        if len(selected_reasons) >= MAX_MEMORY_PERSISTENCE_REASON_CODES:
            break
    if not selected_reasons:
        selected_reasons.append("unavailable")
    payload = {
        "schemaVersion": MEMORY_PERSISTENCE_FAILURE_SCHEMA_VERSION,
        "phase": phase,
        "operationId": operation_id,
        "reasonCodes": selected_reasons,
    }
    normalized = normalize_memory_persistence_failure(payload)
    if normalized is None:
        raise ValueError("invalid Memory persistence failure payload")
    return normalized


def normalize_memory_persistence_failure(
    value: object,
) -> dict[str, object] | None:
    """Validate the exact durable schema; malformed payloads fail closed."""
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        return None
    schema = value.get("schemaVersion")
    phase = value.get("phase")
    operation_id = value.get("operationId")
    reason_codes = value.get("reasonCodes")
    if (
        isinstance(schema, bool)
        or schema != MEMORY_PERSISTENCE_FAILURE_SCHEMA_VERSION
        or phase not in _PHASES
        or not isinstance(operation_id, str)
        or _OPERATION_ID_RE.fullmatch(operation_id) is None
        or not isinstance(reason_codes, list)
        or not 0 < len(reason_codes) <= MAX_MEMORY_PERSISTENCE_REASON_CODES
        or any(
            not isinstance(reason, str)
            or _REASON_CODE_RE.fullmatch(reason) is None
            for reason in reason_codes
        )
        or len(set(reason_codes)) != len(reason_codes)
    ):
        return None
    return {
        "schemaVersion": MEMORY_PERSISTENCE_FAILURE_SCHEMA_VERSION,
        "phase": phase,
        "operationId": operation_id,
        "reasonCodes": list(reason_codes),
    }


__all__ = [
    "MAX_MEMORY_PERSISTENCE_REASON_CODES",
    "MEMORY_PERSISTENCE_FAILURE_EVENT_TYPE",
    "MEMORY_PERSISTENCE_FAILURE_SCHEMA_VERSION",
    "memory_persistence_failure_payload",
    "normalize_memory_persistence_failure",
]
