"""Durable, bounded, workspace-isolated Run records and event journals.

The module owns storage layout, writer ownership, validation, redaction,
recovery, paging, and retention.  Callers never provide filesystem paths.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from minicode.config import MINI_CODE_DIR
from minicode.mcp_event_contract import normalize_mcp_runtime_payload
from minicode.permission_event_contract import (
    PERMISSION_EVENT_TYPES,
    normalize_permission_event_payload,
)
from minicode.subagent_journal import (
    SubagentRunJournal,
    SubagentRunSummary,
)
from minicode.subagent_observation import (
    SUBAGENT_EVENT_TYPE,
    normalize_subagent_payload,
)
from minicode.verification_observation import (
    VERIFICATION_EVENT_TYPE,
    normalize_verification_payload,
)


def _conversation_fenced_run_create(method):
    """Keep linked Run creation outside an active conversation deletion."""

    def wrapped(self, *args, **kwargs):
        session_id = kwargs.get("session_id")
        if session_id is None:
            return method(self, *args, **kwargs)
        from minicode.deletion_store import (
            DeletionStoreError,
            conversation_write_guard,
        )

        try:
            with conversation_write_guard(
                self.workspace,
                session_id,
                data_dir=self.data_dir,
            ):
                return method(self, *args, **kwargs)
        except DeletionStoreError as error:
            raise RunJournalStorageError(
                "Conversation deletion coordination is unavailable."
            ) from error

    return wrapped


SCHEMA_VERSION = 1
RUN_STATUSES = (
    "queued",
    "running",
    "completed",
    "failed",
    "interrupted",
    "cancel_requested",
    "cancelled",
)
RUN_SOURCES = ("tui", "headless", "gateway", "unknown")
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted", "cancelled"})
LIFECYCLE_EVENT_FOR_STATUS = {
    "queued": "run.queued",
    "running": "run.started",
    "completed": "run.completed",
    "failed": "run.failed",
    "interrupted": "run.interrupted",
    "cancel_requested": "run.cancel_requested",
    "cancelled": "run.cancelled",
}
_STATUS_FOR_LIFECYCLE_EVENT = {
    event_type: status for status, event_type in LIFECYCLE_EVENT_FOR_STATUS.items()
}
EVENT_TYPES = frozenset(
    {
        *LIFECYCLE_EVENT_FOR_STATUS.values(),
        "model.started",
        "model.completed",
        "model.costed",
        "model.failed",
        "tool.started",
        "tool.finished",
        "assistant.completed",
        "task.outcome",
        "execution.stopped",
        VERIFICATION_EVENT_TYPE,
        "skill.routed",
        "skill.loaded",
        "skill.attributed",
        "memory.retrieved",
        "memory.rendered",
        "working_memory.observed",
        "context.compacted",
        "context.compaction.failed",
        "recovery.started",
        "recovery.completed",
        "mcp.runtime.observed",
        SUBAGENT_EVENT_TYPE,
        *PERMISSION_EVENT_TYPES,
    }
)

_ALLOWED_TRANSITIONS = {
    "queued": frozenset({"running", "failed", "interrupted"}),
    "running": frozenset({"completed", "failed", "interrupted"}),
    "cancel_requested": frozenset({"cancelled", "failed", "interrupted"}),
}
_RUN_ID_RE = re.compile(r"^run_[0-9a-f]{32}$")
_EVENT_ID_RE = re.compile(r"^evt_[0-9a-f]{32}$")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_CURSOR_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MCP_SERVER_KEY_RE = re.compile(r"^mcpsrv_[0-9a-f]{32}$")
_ISO_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$"
)
_SENSITIVE_KEYS = {
    "apikey",
    "accesstoken",
    "authtoken",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "env",
    "environment",
    "password",
    "privatekey",
    "providerconfig",
    "providercredential",
    "providercredentials",
    "secret",
    "systemmessage",
    "systemprompt",
    "token",
    "toolinput",
    "tooloutput",
}
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_TOKEN_RE = re.compile(r"(?i)\bsk-[A-Za-z0-9][A-Za-z0-9_-]{2,}")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|password|secret|"
    r"credential|authorization|cookie)\b(\s*[:=]\s*)[^\s,;/'\"]+"
)
_MAX_TITLE_CHARS = 240
_MAX_METADATA_ITEMS = 8
_MAX_METADATA_VALUE_CHARS = 128
_ALLOWED_METADATA_KEYS = frozenset({"origin", "retryOf", "correlationId"})
_MAX_PAYLOAD_DEPTH = 6
_MAX_PAYLOAD_STRING_CHARS = 4_096
_MAX_PAYLOAD_ITEMS = 100
_MAX_EVENT_BYTES = 32 * 1024
_MAX_METADATA_BYTES = 1024 * 1024
_MAX_RUN_EVENT_FILE_BYTES = 16 * 1024 * 1024
_MAX_EVENTS_PER_RUN = 100_000
_MAX_RUNS_SCANNED = 10_000
_MAX_DIAGNOSTICS = 20
_MAX_DIAGNOSTIC_CHARS = 240
_DEFAULT_RUN_LIMIT = 20
_DEFAULT_EVENT_LIMIT = 50
_MAX_PAGE_LIMIT = 100
_MAX_CURSOR_CHARS = 512
_MAX_USER_SIGNAL_BYTES = 1_024
_USER_SIGNAL_FILE = "user_signal.json"
_USER_SIGNAL_LOCK = ".user-signal.lock"
_USER_SIGNALS = frozenset({"accept", "correct", "reject"})
_USER_SIGNAL_SOURCE = "explicit_user_action"
_MAX_RENDERED_MEMORY_IDS = 20
_MAX_RENDERED_MEMORY_BYTES = 4_096
_RENDERED_MEMORY_FILE = "memory_rendered.json"
_WRITTEN_MEMORY_FILE = "memory_written.json"
_MEMORY_ENTRY_ID_RE = re.compile(r"^(user|project|local)-[0-9]{1,20}-[0-9a-f]{8}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _platform_name() -> str:
    return os.name


def _iso_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _parse_time(value: str) -> datetime:
    if not isinstance(value, str) or not _ISO_UTC_RE.fullmatch(value):
        raise ValueError("invalid UTC timestamp")
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("invalid UTC timestamp")
    return parsed


def stable_workspace_id(workspace: str | Path) -> str:
    """Return the stable workspace ID shared with Dashboard projections."""
    resolved = Path(workspace).expanduser().resolve()
    return "ws_" + hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]


def _redact_text(value: str, *, max_chars: int) -> str:
    redacted = _BEARER_RE.sub("Bearer [REDACTED]", value)
    redacted = _KEY_TOKEN_RE.sub("[REDACTED]", redacted)
    redacted = _ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", redacted
    )
    if len(redacted) > max_chars:
        return redacted[:max_chars].rstrip() + "…"
    return redacted


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _sanitize_payload_value(value: Any, *, depth: int = 0) -> Any:
    if depth > _MAX_PAYLOAD_DEPTH:
        raise RunJournalValidationError("Event payload is too deeply nested.")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RunJournalValidationError("Event payload contains a non-finite number.")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_PAYLOAD_STRING_CHARS:
            raise RunJournalValidationError("Event payload contains an oversized string.")
        return _redact_text(value, max_chars=_MAX_PAYLOAD_STRING_CHARS)
    if isinstance(value, dict):
        if len(value) > _MAX_PAYLOAD_ITEMS:
            raise RunJournalValidationError("Event payload contains too many fields.")
        sanitized: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str) or not key or len(key) > 64:
                raise RunJournalValidationError("Event payload contains an invalid key.")
            safe_key = _redact_text(key, max_chars=64)
            sanitized[safe_key] = (
                "[REDACTED]"
                if _normalized_key(key) in _SENSITIVE_KEYS
                else _sanitize_payload_value(nested, depth=depth + 1)
            )
        return sanitized
    if isinstance(value, list):
        if len(value) > _MAX_PAYLOAD_ITEMS:
            raise RunJournalValidationError("Event payload contains too many items.")
        return [
            _sanitize_payload_value(item, depth=depth + 1) for item in value
        ]
    raise RunJournalValidationError("Event payload contains an unsupported value.")


def _sanitize_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise RunJournalValidationError("Event payload must be a JSON object.")
    sanitized = _sanitize_payload_value(payload)
    encoded = json.dumps(
        sanitized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _MAX_EVENT_BYTES:
        raise RunJournalValidationError("Event payload exceeds the byte limit.")
    return sanitized


def _sanitize_event_payload(event_type: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    sanitized = _sanitize_payload(payload)
    if event_type == "mcp.runtime.observed":
        normalized = normalize_mcp_runtime_payload(sanitized)
        if normalized is None:
            raise RunJournalValidationError("MCP runtime payload is invalid.")
        return normalized
    if event_type in PERMISSION_EVENT_TYPES:
        normalized_permission = normalize_permission_event_payload(
            event_type, sanitized
        )
        if normalized_permission is None:
            raise RunJournalValidationError("Permission event payload is invalid.")
        return normalized_permission
    if event_type == SUBAGENT_EVENT_TYPE:
        normalized_subagent = normalize_subagent_payload(sanitized)
        if normalized_subagent is None:
            raise RunJournalValidationError("Sub-agent event payload is invalid.")
        return normalized_subagent
    if event_type == VERIFICATION_EVENT_TYPE:
        normalized_verification = normalize_verification_payload(sanitized)
        if normalized_verification is None:
            raise RunJournalValidationError(
                "Verification event payload is invalid."
            )
        return normalized_verification
    return sanitized


def _sanitize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    if metadata is None:
        return {}
    if not isinstance(metadata, dict) or len(metadata) > _MAX_METADATA_ITEMS:
        raise RunJournalValidationError("Run metadata is invalid.")
    sanitized: dict[str, str] = {}
    for key, value in metadata.items():
        if key not in _ALLOWED_METADATA_KEYS or not isinstance(value, str):
            raise RunJournalValidationError("Run metadata contains an unsupported field.")
        if len(value) > _MAX_METADATA_VALUE_CHARS:
            raise RunJournalValidationError("Run metadata value is too long.")
        sanitized[key] = _redact_text(value, max_chars=_MAX_METADATA_VALUE_CHARS)
    return sanitized


def _normalize_rendered_memory_ids(entry_ids: object) -> tuple[str, ...]:
    """Validate a bounded set of opaque Memory entry IDs, content-free."""
    if not isinstance(entry_ids, (list, tuple)):
        raise RunJournalValidationError("Rendered Memory IDs must be a list.")
    deduped = tuple(dict.fromkeys(entry_ids))
    if len(deduped) > _MAX_RENDERED_MEMORY_IDS:
        raise RunJournalValidationError("Too many rendered Memory IDs.")
    for entry_id in deduped:
        if not isinstance(entry_id, str) or not _MEMORY_ENTRY_ID_RE.fullmatch(entry_id):
            raise RunJournalValidationError("Rendered Memory ID is invalid.")
    return deduped


class RunJournalError(RuntimeError):
    """Base domain error for RunJournal operations."""


class RunJournalValidationError(RunJournalError, ValueError):
    """Raised when caller input violates the public RunJournal contract."""


class RunJournalTransitionError(RunJournalError):
    """Raised for an illegal Run status transition."""


class RunJournalOwnershipError(RunJournalError):
    """Raised when a process does not own the requested Run writer."""


class RunJournalNotFoundError(RunJournalError):
    """Raised when a Run is absent or not visible in this workspace."""


class RunJournalStorageError(RunJournalError):
    """Raised when a durable operation cannot be completed safely."""


class RunJournalUserSignalConflictError(RunJournalError):
    """Raised when an immutable Run already has a different user signal."""


@dataclass(frozen=True, slots=True)
class RunUserSignal:
    schema_version: int
    signal: str
    source: str
    recorded_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "signal": self.signal,
            "source": self.source,
            "recordedAt": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, value: object) -> RunUserSignal:
        if not isinstance(value, dict) or set(value) != {
            "schemaVersion",
            "signal",
            "source",
            "recordedAt",
        }:
            raise ValueError("invalid user signal record")
        if value.get("schemaVersion") != SCHEMA_VERSION:
            raise ValueError("invalid user signal schema")
        signal = value.get("signal")
        if signal not in _USER_SIGNALS:
            raise ValueError("invalid user signal")
        if value.get("source") != _USER_SIGNAL_SOURCE:
            raise ValueError("invalid user signal source")
        recorded_at = value.get("recordedAt")
        if not isinstance(recorded_at, str):
            raise ValueError("invalid user signal timestamp")
        _parse_time(recorded_at)
        return cls(
            schema_version=SCHEMA_VERSION,
            signal=signal,
            source=_USER_SIGNAL_SOURCE,
            recorded_at=recorded_at,
        )


@dataclass(frozen=True, slots=True)
class RunRecord:
    schema_version: int
    id: str
    workspace_id: str
    session_id: str | None
    status: str
    source: str
    title: str
    created_at: str
    started_at: str | None
    completed_at: str | None
    updated_at: str
    last_sequence: int
    event_count: int
    error_count: int
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "id": self.id,
            "workspaceId": self.workspace_id,
            "sessionId": self.session_id,
            "status": self.status,
            "source": self.source,
            "title": self.title,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "updatedAt": self.updated_at,
            "lastSequence": self.last_sequence,
            "eventCount": self.event_count,
            "errorCount": self.error_count,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RunRecord":
        if not isinstance(raw, dict) or raw.get("schemaVersion") != SCHEMA_VERSION:
            raise ValueError("invalid RunRecord schema")
        run_id = raw.get("id")
        workspace_id = raw.get("workspaceId")
        session_id = raw.get("sessionId")
        status = raw.get("status")
        source = raw.get("source")
        title = raw.get("title")
        timestamps = (
            raw.get("createdAt"),
            raw.get("updatedAt"),
        )
        optional_times = (raw.get("startedAt"), raw.get("completedAt"))
        counts = (raw.get("lastSequence"), raw.get("eventCount"), raw.get("errorCount"))
        if (
            not isinstance(run_id, str)
            or not _RUN_ID_RE.fullmatch(run_id)
            or not isinstance(workspace_id, str)
            or not re.fullmatch(r"ws_[0-9a-f]{16}", workspace_id)
            or (session_id is not None and (not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id)))
            or status not in RUN_STATUSES
            or source not in RUN_SOURCES
            or not isinstance(title, str)
            or len(title) > _MAX_TITLE_CHARS + 1
            or any(not isinstance(item, str) for item in timestamps)
            or any(item is not None and not isinstance(item, str) for item in optional_times)
            or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in counts)
        ):
            raise ValueError("invalid RunRecord")
        for timestamp in (*timestamps, *(item for item in optional_times if item is not None)):
            _parse_time(timestamp)
        metadata = _sanitize_metadata(raw.get("metadata"))
        return cls(
            schema_version=SCHEMA_VERSION,
            id=run_id,
            workspace_id=workspace_id,
            session_id=session_id,
            status=status,
            source=source,
            title=_redact_text(title, max_chars=_MAX_TITLE_CHARS),
            created_at=timestamps[0],
            started_at=optional_times[0],
            completed_at=optional_times[1],
            updated_at=timestamps[1],
            last_sequence=counts[0],
            event_count=counts[1],
            error_count=counts[2],
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class RunEvent:
    schema_version: int
    event_id: str
    sequence: int
    timestamp: str
    workspace_id: str
    session_id: str | None
    run_id: str
    type: str
    step: int | None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "eventId": self.event_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "workspaceId": self.workspace_id,
            "sessionId": self.session_id,
            "runId": self.run_id,
            "type": self.type,
            "step": self.step,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RunEvent":
        if not isinstance(raw, dict) or raw.get("schemaVersion") != SCHEMA_VERSION:
            raise ValueError("invalid RunEvent schema")
        event_id = raw.get("eventId")
        sequence = raw.get("sequence")
        timestamp = raw.get("timestamp")
        workspace_id = raw.get("workspaceId")
        session_id = raw.get("sessionId")
        run_id = raw.get("runId")
        event_type = raw.get("type")
        step = raw.get("step")
        if (
            not isinstance(event_id, str)
            or not _EVENT_ID_RE.fullmatch(event_id)
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
            or not isinstance(timestamp, str)
            or not isinstance(workspace_id, str)
            or not re.fullmatch(r"ws_[0-9a-f]{16}", workspace_id)
            or (session_id is not None and (not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id)))
            or not isinstance(run_id, str)
            or not _RUN_ID_RE.fullmatch(run_id)
            or event_type not in EVENT_TYPES
            or (step is not None and (isinstance(step, bool) or not isinstance(step, int) or step < 0 or step > 1_000_000))
        ):
            raise ValueError("invalid RunEvent")
        _parse_time(timestamp)
        payload = _sanitize_event_payload(event_type, raw.get("payload"))
        return cls(
            schema_version=SCHEMA_VERSION,
            event_id=event_id,
            sequence=sequence,
            timestamp=timestamp,
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=run_id,
            type=event_type,
            step=step,
            payload=payload,
        )


@dataclass(frozen=True, slots=True)
class RunPage:
    items: tuple[RunRecord, ...]
    limit: int
    has_more: bool
    next_cursor: str | None
    known_total: int
    by_status: dict[str, int]
    diagnostics: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class EventPage:
    items: tuple[RunEvent, ...]
    limit: int
    has_more: bool
    next_cursor: str | None
    diagnostics: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RetentionResult:
    deleted_count: int
    diagnostics: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RunDeletionSnapshot:
    """Content-free records relevant to one Session deletion plan."""

    terminal: tuple[RunRecord, ...]
    active: tuple[RunRecord, ...]
    diagnostics: tuple[str, ...]


class RunJournal:
    """Own durable Run creation, append, transition, reads, and retention."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        data_dir: str | Path | None = None,
        clock: Callable[[], datetime] = _utc_now,
        max_runs: int = 1_000,
        terminal_max_age: timedelta | None = timedelta(days=90),
        max_total_bytes: int | None = 256 * 1024 * 1024,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace_id = stable_workspace_id(self.workspace)
        self.data_dir = Path(data_dir if data_dir is not None else MINI_CODE_DIR).expanduser()
        self._data_root = self.data_dir.resolve(strict=False)
        self._runs_root = (
            self._data_root
            / "dashboard"
            / "workspaces"
            / self.workspace_id
            / "runs"
        )
        if isinstance(max_runs, bool) or not isinstance(max_runs, int) or max_runs < 1:
            raise RunJournalValidationError("max_runs is invalid.")
        if max_total_bytes is not None and (
            isinstance(max_total_bytes, bool)
            or not isinstance(max_total_bytes, int)
            or max_total_bytes < 1
        ):
            raise RunJournalValidationError("max_total_bytes is invalid.")
        if terminal_max_age is not None and (
            not isinstance(terminal_max_age, timedelta)
            or terminal_max_age.total_seconds() < 0
        ):
            raise RunJournalValidationError("terminal_max_age is invalid.")
        self.max_runs = max_runs
        self.terminal_max_age = terminal_max_age
        self.max_total_bytes = max_total_bytes
        self._clock = clock
        self._writers: dict[str, tuple[str, int]] = {}
        self._writer_mutexes: dict[str, threading.Lock] = {}

    @_conversation_fenced_run_create
    def create_run(
        self,
        *,
        title: str,
        source: str = "unknown",
        session_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RunRecord:
        if not isinstance(title, str) or not title.strip():
            raise RunJournalValidationError("Run title is invalid.")
        if source not in RUN_SOURCES:
            raise RunJournalValidationError("Run source is invalid.")
        if session_id is not None and (
            not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id)
        ):
            raise RunJournalValidationError("Session ID is invalid.")
        safe_title = _redact_text(title.strip(), max_chars=_MAX_TITLE_CHARS)
        safe_metadata = _sanitize_metadata(metadata)
        self._ensure_storage_root()

        run_id = ""
        run_dir: Path | None = None
        for _ in range(20):
            candidate = f"run_{uuid.uuid4().hex}"
            candidate_dir = self._runs_root / candidate
            try:
                candidate_dir.mkdir(mode=0o700)
            except FileExistsError:
                continue
            run_id = candidate
            run_dir = candidate_dir
            break
        if run_dir is None:
            raise RunJournalStorageError("A unique Run ID could not be allocated.")

        writer_token = uuid.uuid4().hex
        try:
            self._create_writer_lock(run_dir, writer_token)
            self._writers[run_id] = (writer_token, os.getpid())
            self._writer_mutexes[run_id] = threading.Lock()
            now = _iso_time(self._clock())
            initial = RunRecord(
                schema_version=SCHEMA_VERSION,
                id=run_id,
                workspace_id=self.workspace_id,
                session_id=session_id,
                status="queued",
                source=source,
                title=safe_title,
                created_at=now,
                started_at=None,
                completed_at=None,
                updated_at=now,
                last_sequence=0,
                event_count=0,
                error_count=0,
                metadata=safe_metadata,
            )
            self._write_metadata(run_dir, initial)
            event = self._new_event(initial, "run.queued", sequence=1, timestamp=now)
            self._append_event_line(run_dir, event)
            record = replace(initial, last_sequence=1, event_count=1)
            self._write_metadata(run_dir, record)
        except Exception:
            self._writers.pop(run_id, None)
            self._writer_mutexes.pop(run_id, None)
            raise
        self._refresh_index_best_effort()
        return record

    def get_run(self, run_id: str) -> RunRecord | None:
        self._validate_run_id(run_id)
        record, _ = self._read_run(run_id)
        return record

    def get_user_signal(self, run_id: str) -> RunUserSignal | None:
        """Read one immutable content-free post-terminal user action."""
        self._validate_run_id(run_id)
        record, _ = self._read_run(run_id)
        if record is None:
            raise RunJournalNotFoundError("Run was not found.")
        run_dir = self._run_directory(run_id, must_exist=True)
        return self._read_user_signal(run_dir)

    def record_user_signal(
        self,
        run_id: str,
        signal: str,
    ) -> RunUserSignal:
        """Record one explicit completed-Run signal, idempotently."""
        self._validate_run_id(run_id)
        if signal not in _USER_SIGNALS:
            raise RunJournalValidationError("User signal is invalid.")
        record, _ = self._read_run(run_id)
        if record is None:
            raise RunJournalNotFoundError("Run was not found.")
        if record.status != "completed":
            raise RunJournalTransitionError(
                "User signal requires a completed Run."
            )
        if record.session_id is None:
            return self._record_user_signal_locked(run_id, signal)
        from minicode.deletion_store import (
            DeletionStoreError,
            conversation_write_guard,
        )

        try:
            with conversation_write_guard(
                self.workspace,
                record.session_id,
                data_dir=self.data_dir,
            ):
                return self._record_user_signal_locked(run_id, signal)
        except DeletionStoreError as error:
            raise RunJournalStorageError(
                "User signal storage is unavailable."
            ) from error

    def _record_user_signal_locked(
        self,
        run_id: str,
        signal: str,
    ) -> RunUserSignal:
        run_dir = self._run_directory(run_id, must_exist=True)
        self._acquire_run_mutation_lock(run_dir)
        try:
            record, _ = self._read_run(run_id)
            if record is None:
                raise RunJournalNotFoundError("Run was not found.")
            if record.status != "completed":
                raise RunJournalTransitionError(
                    "User signal requires a completed Run."
                )
            existing = self._read_user_signal(run_dir)
            if existing is not None:
                if existing.signal != signal:
                    raise RunJournalUserSignalConflictError(
                        "Run user signal is immutable."
                    )
                return existing
            created = RunUserSignal(
                schema_version=SCHEMA_VERSION,
                signal=signal,
                source=_USER_SIGNAL_SOURCE,
                recorded_at=_iso_time(self._clock()),
            )
            self._write_user_signal(run_dir, created)
            return created
        finally:
            self._release_run_mutation_lock(run_dir)

    def deletion_snapshot(self, session_id: str) -> RunDeletionSnapshot:
        """Read a bounded view of Runs linked to one Session."""
        if (
            not isinstance(session_id, str)
            or _SESSION_ID_RE.fullmatch(session_id) is None
        ):
            raise RunJournalValidationError("Session ID is invalid.")
        terminal: list[RunRecord] = []
        active: list[RunRecord] = []
        diagnostics: set[str] = set()
        if not self._runs_root.exists():
            return RunDeletionSnapshot((), (), ())
        try:
            if self._runs_root.is_symlink():
                raise OSError("unsafe Run root")
            root = self._runs_root.resolve(strict=True)
            if not root.is_relative_to(self._data_root):
                raise OSError("Run root escapes data directory")
            entries, limited = self._bounded_run_entries(root)
            if limited:
                diagnostics.add("run_scan_limited")
        except Exception:
            return RunDeletionSnapshot((), (), ("run_scan_unavailable",))
        for entry in entries:
            if entry.is_symlink():
                diagnostics.add("run_record_invalid")
                continue
            record, record_diagnostics = self._read_run(entry.name)
            if record_diagnostics:
                diagnostics.add("run_record_invalid")
            if record is None or record.session_id != session_id:
                continue
            writer_path = entry / ".writer.lock"
            writer_present = False
            try:
                writer_info = os.lstat(writer_path)
                writer_present = True
                if stat.S_ISLNK(writer_info.st_mode) or not stat.S_ISREG(
                    writer_info.st_mode
                ):
                    diagnostics.add("run_writer_invalid")
            except FileNotFoundError:
                pass
            except OSError:
                diagnostics.add("run_writer_invalid")
            if record.status in TERMINAL_STATUSES and not writer_present:
                terminal.append(record)
            else:
                active.append(record)
        return RunDeletionSnapshot(
            tuple(sorted(terminal, key=lambda item: item.id)),
            tuple(sorted(active, key=lambda item: item.id)),
            tuple(sorted(diagnostics)),
        )

    def delete_terminal_for_session(self, session_id: str) -> int:
        """Delete exactly the currently terminal Runs linked to one Session."""
        snapshot = self.deletion_snapshot(session_id)
        if snapshot.active or snapshot.diagnostics:
            raise RunJournalStorageError("Run deletion is unavailable.")
        if not snapshot.terminal:
            return 0
        root = self._runs_root.resolve(strict=True)
        deleted = 0
        for record in snapshot.terminal:
            directory = self._run_directory(record.id, must_exist=True)
            self._acquire_run_mutation_lock(directory)
            try:
                current, current_diagnostics = self._read_run(record.id)
                if (
                    current_diagnostics
                    or current is None
                    or current.session_id != session_id
                    or current.status not in TERMINAL_STATUSES
                    or (directory / ".writer.lock").exists()
                ):
                    raise RunJournalStorageError(
                        "Run changed during deletion."
                    )
                self._validate_retention_directory(
                    record.id, directory, root
                )
                shutil.rmtree(directory)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise RunJournalStorageError("Run deletion is unavailable.") from error
            finally:
                if directory.exists():
                    self._release_run_mutation_lock(directory)
            self._writers.pop(record.id, None)
            self._writer_mutexes.pop(record.id, None)
            deleted += 1
        if deleted:
            self._refresh_index_best_effort()
        return deleted

    def list_runs(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
        limit: int | str | None = None,
        cursor: str | None = None,
    ) -> RunPage:
        """Scan canonical Run directories and return a stable filtered page."""
        if status is not None and status not in RUN_STATUSES:
            raise RunJournalValidationError("Run status is invalid.")
        if source is not None and source not in RUN_SOURCES:
            raise RunJournalValidationError("Run source is invalid.")
        page_limit = self._page_limit(limit, default=_DEFAULT_RUN_LIMIT)
        records: list[RunRecord] = []
        diagnostics: list[dict[str, str]] = []
        if self._runs_root.exists():
            try:
                if self._runs_root.is_symlink():
                    raise OSError("unsafe RunJournal root")
                resolved_root = self._runs_root.resolve(strict=True)
                if not resolved_root.is_relative_to(self._data_root):
                    raise OSError("RunJournal root escapes data directory")
                entries, entries_limited = self._bounded_run_entries(resolved_root)
            except Exception:
                entries = []
                entries_limited = False
                diagnostics.append(
                    self._diagnostic(
                        "journal_read_failed", "RunJournal storage could not be read."
                    )
                )
            for entry in entries:
                if not _RUN_ID_RE.fullmatch(entry.name):
                    continue
                if entry.is_symlink():
                    diagnostics.append(
                        self._diagnostic(
                            "run_read_failed", "A Run record could not be read."
                        )
                    )
                    continue
                record, record_diagnostics = self._read_run(entry.name)
                diagnostics.extend(record_diagnostics)
                if record is not None:
                    records.append(record)
            if entries_limited:
                diagnostics.append(
                    self._diagnostic(
                        "runs_limited", "Run discovery reached the configured limit."
                    )
                )

        by_status = {name: 0 for name in RUN_STATUSES}
        for record in records:
            by_status[record.status] += 1

        def sort_key(record: RunRecord) -> tuple[float, float, str]:
            return (
                -_parse_time(record.updated_at).timestamp(),
                -_parse_time(record.created_at).timestamp(),
                record.id,
            )

        filtered = [
            record
            for record in records
            if (status is None or record.status == status)
            and (source is None or record.source == source)
        ]
        filtered.sort(key=sort_key)
        if cursor not in (None, ""):
            try:
                values = self._decode_cursor("runs", cursor)
                if (
                    len(values) != 5
                    or values[0] != (status or "")
                    or values[1] != (source or "")
                    or isinstance(values[2], bool)
                    or isinstance(values[3], bool)
                    or not isinstance(values[2], (int, float))
                    or not isinstance(values[3], (int, float))
                    or not math.isfinite(float(values[2]))
                    or not math.isfinite(float(values[3]))
                    or not isinstance(values[4], str)
                    or not _RUN_ID_RE.fullmatch(values[4])
                ):
                    raise ValueError("invalid Runs cursor")
                cursor_key = (-float(values[2]), -float(values[3]), values[4])
            except ValueError as exc:
                raise RunJournalValidationError("Cursor is invalid.") from exc
            filtered = [record for record in filtered if sort_key(record) > cursor_key]

        page_records = filtered[:page_limit]
        has_more = len(filtered) > page_limit
        last = page_records[-1] if page_records else None
        return RunPage(
            items=tuple(page_records),
            limit=page_limit,
            has_more=has_more,
            next_cursor=(
                self._encode_cursor(
                    "runs",
                    [
                        status or "",
                        source or "",
                        _parse_time(last.updated_at).timestamp(),
                        _parse_time(last.created_at).timestamp(),
                        last.id,
                    ],
                )
                if has_more and last is not None
                else None
            ),
            known_total=len(records),
            by_status=by_status,
            diagnostics=tuple(diagnostics[:_MAX_DIAGNOSTICS]),
        )

    def append_event(
        self,
        run_id: str,
        event_type: str,
        *,
        step: int | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> RunEvent:
        """Append one non-lifecycle event for a Run owned by this process."""
        self._validate_run_id(run_id)
        if event_type in LIFECYCLE_EVENT_FOR_STATUS.values():
            raise RunJournalValidationError(
                "Lifecycle events must be recorded through transition()."
            )
        if event_type not in EVENT_TYPES:
            raise RunJournalValidationError("Event type is invalid.")
        mutex = self._writer_mutexes.get(run_id)
        if mutex is None:
            raise RunJournalOwnershipError("This process does not own the Run writer.")
        with mutex:
            run_dir = self._run_directory(run_id, must_exist=True)
            self._require_writer(run_id, run_dir)
            record, _ = self._read_run(run_id)
            if record is None:
                raise RunJournalNotFoundError("Run was not found.")
            if record.status in TERMINAL_STATUSES:
                raise RunJournalTransitionError("A terminal Run cannot accept events.")
            event = self._new_event(
                record,
                event_type,
                sequence=record.last_sequence + 1,
                step=step,
                payload=payload,
            )
            self._append_event_line(run_dir, event)
            checkpoint = replace(
                record,
                updated_at=event.timestamp,
                last_sequence=event.sequence,
                event_count=record.event_count + 1,
                error_count=record.error_count + int(event.type.endswith(".failed")),
            )
            self._write_metadata(run_dir, checkpoint)
            return event

    def record_rendered_memory_ids(
        self,
        run_id: str,
        entry_ids: list[str],
    ) -> None:
        """Persist this Run's rendered Memory entry IDs once, content-free,
        so a later explicit user signal can bind corroborated feedback to
        the exact entries this turn actually rendered.
        """
        self._validate_run_id(run_id)
        normalized = _normalize_rendered_memory_ids(entry_ids)
        if not normalized:
            return
        mutex = self._writer_mutexes.get(run_id)
        if mutex is None:
            raise RunJournalOwnershipError("This process does not own the Run writer.")
        with mutex:
            run_dir = self._run_directory(run_id, must_exist=True)
            self._require_writer(run_id, run_dir)
            record, _ = self._read_run(run_id)
            if record is None:
                raise RunJournalNotFoundError("Run was not found.")
            if record.status in TERMINAL_STATUSES:
                raise RunJournalTransitionError(
                    "A terminal Run cannot accept rendered Memory IDs."
                )
            self._write_memory_id_sidecar(
                run_dir, normalized, filename=_RENDERED_MEMORY_FILE, label="Rendered"
            )

    def get_rendered_memory_ids(self, run_id: str) -> tuple[str, ...] | None:
        """Read the content-free rendered Memory entry IDs for this Run."""
        self._validate_run_id(run_id)
        record, _ = self._read_run(run_id)
        if record is None:
            raise RunJournalNotFoundError("Run was not found.")
        run_dir = self._run_directory(run_id, must_exist=True)
        return self._read_memory_id_sidecar(
            run_dir, _RENDERED_MEMORY_FILE, "Rendered"
        )

    def record_written_memory_ids(
        self,
        run_id: str,
        entry_ids: list[str],
    ) -> None:
        """Persist the Memory entries this Run *produced*, content-free.

        The rendered sidecar records what was shown to the turn; this records
        what the turn concluded. Without it an explicit "this was wrong" from
        the user cannot reach the lesson the wrong turn just wrote, and that
        lesson stays eligible for approval as if nothing had been said.
        """
        self._validate_run_id(run_id)
        normalized = _normalize_rendered_memory_ids(entry_ids)
        if not normalized:
            return
        mutex = self._writer_mutexes.get(run_id)
        if mutex is None:
            raise RunJournalOwnershipError("This process does not own the Run writer.")
        with mutex:
            run_dir = self._run_directory(run_id, must_exist=True)
            self._require_writer(run_id, run_dir)
            record, _ = self._read_run(run_id)
            if record is None:
                raise RunJournalNotFoundError("Run was not found.")
            if record.status in TERMINAL_STATUSES:
                raise RunJournalTransitionError(
                    "A terminal Run cannot accept written Memory IDs."
                )
            self._write_memory_id_sidecar(
                run_dir, normalized, filename=_WRITTEN_MEMORY_FILE, label="Written"
            )

    def get_written_memory_ids(self, run_id: str) -> tuple[str, ...] | None:
        """Read the content-free Memory entry IDs this Run produced."""
        self._validate_run_id(run_id)
        record, _ = self._read_run(run_id)
        if record is None:
            raise RunJournalNotFoundError("Run was not found.")
        run_dir = self._run_directory(run_id, must_exist=True)
        return self._read_memory_id_sidecar(run_dir, _WRITTEN_MEMORY_FILE, "Written")

    def open_subagent_journal(self, run_id: str) -> SubagentRunJournal:
        """Open the bounded sidecar journal owned by one Run directory.

        The journal is intentionally outside the parent event stream: readers
        that require exactly one ``task.outcome`` per Run must not see nested
        loop lifecycle events. Retention removes it together with the Run.
        """
        self._validate_run_id(run_id)
        record, _ = self._read_run(run_id)
        if record is None:
            raise RunJournalNotFoundError("Run was not found.")
        run_dir = self._run_directory(run_id, must_exist=True)
        return SubagentRunJournal(run_dir)

    def list_subagent_runs(
        self, run_id: str
    ) -> tuple[SubagentRunSummary, ...]:
        """Read bounded sub-agent Run summaries for one parent Run."""
        return self.open_subagent_journal(run_id).list_runs()

    def list_subagent_events(self, run_id: str, subagent_id: str):
        """Read one sub-agent's bounded event stream."""
        return self.open_subagent_journal(run_id).list_events(subagent_id)

    def transition(
        self,
        run_id: str,
        status: str,
        *,
        reason: str | None = None,
    ) -> RunRecord:
        """Apply a legal lifecycle transition; repeated terminal state is idempotent."""
        self._validate_run_id(run_id)
        if status not in RUN_STATUSES:
            raise RunJournalValidationError("Run status is invalid.")
        record, _ = self._read_run(run_id)
        if record is None:
            raise RunJournalNotFoundError("Run was not found.")
        if record.status == status and status in TERMINAL_STATUSES:
            return record
        if status not in _ALLOWED_TRANSITIONS.get(record.status, frozenset()):
            raise RunJournalTransitionError(
                f"Run cannot transition from {record.status} to {status}."
            )
        mutex = self._writer_mutexes.get(run_id)
        if mutex is None:
            raise RunJournalOwnershipError("This process does not own the Run writer.")
        with mutex:
            run_dir = self._run_directory(run_id, must_exist=True)
            self._require_writer(run_id, run_dir)
            current, _ = self._read_run(run_id)
            if current is None:
                raise RunJournalNotFoundError("Run was not found.")
            if current.status == status and status in TERMINAL_STATUSES:
                return current
            if status not in _ALLOWED_TRANSITIONS.get(current.status, frozenset()):
                raise RunJournalTransitionError(
                    f"Run cannot transition from {current.status} to {status}."
                )
            payload = {"summary": reason} if reason else None
            event = self._new_event(
                current,
                LIFECYCLE_EVENT_FOR_STATUS[status],
                sequence=current.last_sequence + 1,
                payload=payload,
            )
            # Event first, then an atomic metadata checkpoint.  If checkpointing
            # fails, readers still recover the transition from the durable event.
            self._append_event_line(run_dir, event)
            checkpoint = replace(
                current,
                status=status,
                started_at=(
                    event.timestamp if status == "running" else current.started_at
                ),
                completed_at=(
                    event.timestamp
                    if status in TERMINAL_STATUSES
                    else current.completed_at
                ),
                updated_at=event.timestamp,
                last_sequence=event.sequence,
                event_count=current.event_count + 1,
                error_count=current.error_count + int(status == "failed"),
            )
            self._write_metadata(run_dir, checkpoint)
            if status in TERMINAL_STATUSES:
                self._release_writer(run_id, run_dir)
            self._refresh_index_best_effort()
            return checkpoint

    def list_events(
        self,
        run_id: str,
        *,
        limit: int | str | None = None,
        cursor: str | None = None,
    ) -> EventPage:
        self._validate_run_id(run_id)
        page_limit = self._page_limit(limit, default=_DEFAULT_EVENT_LIMIT)
        record, _ = self._read_run(run_id)
        if record is None:
            raise RunJournalNotFoundError("Run was not found.")
        events, event_diagnostics = self._read_events(run_id)
        after_sequence = 0
        if cursor not in (None, ""):
            try:
                values = self._decode_cursor("run_events", cursor)
                if (
                    len(values) != 2
                    or values[0] != run_id
                    or isinstance(values[1], bool)
                    or not isinstance(values[1], int)
                    or values[1] < 0
                ):
                    raise ValueError("invalid event cursor")
                after_sequence = values[1]
            except ValueError as exc:
                raise RunJournalValidationError("Cursor is invalid.") from exc
        remaining = [event for event in events if event.sequence > after_sequence]
        items = remaining[:page_limit]
        has_more = len(remaining) > page_limit
        diagnostics = tuple(event_diagnostics[:_MAX_DIAGNOSTICS])
        return EventPage(
            items=tuple(items),
            limit=page_limit,
            has_more=has_more,
            next_cursor=(
                self._encode_cursor("run_events", [run_id, items[-1].sequence])
                if has_more and items
                else None
            ),
            diagnostics=diagnostics,
        )

    def enforce_retention(self, *, now: datetime | None = None) -> RetentionResult:
        """Explicitly remove eligible terminal Runs under configured bounds."""
        if not self._runs_root.exists():
            return RetentionResult(deleted_count=0)
        reference = now if now is not None else self._clock()
        if not isinstance(reference, datetime):
            raise RunJournalValidationError("Retention time is invalid.")
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        reference = reference.astimezone(timezone.utc)
        diagnostics: list[dict[str, str]] = []
        candidates: list[tuple[RunRecord, Path, int]] = []
        try:
            root = self._runs_root.resolve(strict=True)
            if self._runs_root.is_symlink() or not root.is_relative_to(self._data_root):
                raise OSError("unsafe retention root")
            entries, entries_limited = self._bounded_run_entries(root)
        except Exception:
            return RetentionResult(
                deleted_count=0,
                diagnostics=(
                    self._diagnostic(
                        "retention_root_unsafe", "Run retention storage is unsafe."
                    ),
                ),
            )

        if entries_limited:
            diagnostics.append(
                self._diagnostic(
                    "retention_scan_limited",
                    "Run retention reached the configured scan limit.",
                )
            )
        for entry in entries:
            if not _RUN_ID_RE.fullmatch(entry.name):
                continue
            if entry.is_symlink():
                diagnostics.append(
                    self._diagnostic(
                        "retention_path_unsafe",
                        "A Run retention path was rejected.",
                    )
                )
                continue
            record, _ = self._read_run(entry.name)
            if record is None:
                continue
            try:
                size = self._directory_size(entry)
            except OSError:
                diagnostics.append(
                    self._diagnostic(
                        "retention_path_unsafe",
                        "A Run retention path was rejected.",
                    )
                )
                continue
            candidates.append((record, entry, size))

        terminal = sorted(
            (item for item in candidates if item[0].status in TERMINAL_STATUSES),
            key=lambda item: (
                _parse_time(item[0].completed_at or item[0].updated_at),
                item[0].id,
            ),
        )
        selected: dict[str, tuple[RunRecord, Path, int]] = {}
        if self.terminal_max_age is not None:
            cutoff = reference - self.terminal_max_age
            for item in terminal:
                if _parse_time(item[0].completed_at or item[0].updated_at) < cutoff:
                    selected[item[0].id] = item

        remaining_count = len(candidates) - len(selected)
        if remaining_count > self.max_runs:
            for item in terminal:
                if remaining_count <= self.max_runs:
                    break
                if item[0].id not in selected:
                    selected[item[0].id] = item
                    remaining_count -= 1

        if self.max_total_bytes is not None:
            remaining_bytes = sum(item[2] for item in candidates) - sum(
                item[2] for item in selected.values()
            )
            if remaining_bytes > self.max_total_bytes:
                for item in terminal:
                    if remaining_bytes <= self.max_total_bytes:
                        break
                    if item[0].id not in selected:
                        selected[item[0].id] = item
                        remaining_bytes -= item[2]

        deleted = 0
        for record, directory, _ in sorted(
            selected.values(),
            key=lambda item: (
                _parse_time(item[0].completed_at or item[0].updated_at),
                item[0].id,
            ),
        ):
            mutation_lock_acquired = False
            try:
                self._acquire_run_mutation_lock(directory)
                mutation_lock_acquired = True
                self._validate_retention_directory(record.id, directory, root)
                shutil.rmtree(directory)
                deleted += 1
            except Exception:
                diagnostics.append(
                    self._diagnostic(
                        "retention_delete_failed",
                        "An eligible Run could not be removed.",
                    )
                )
            finally:
                if mutation_lock_acquired and directory.exists():
                    self._release_run_mutation_lock(directory)
        if deleted:
            self._refresh_index_best_effort()
        return RetentionResult(
            deleted_count=deleted,
            diagnostics=tuple(diagnostics[:_MAX_DIAGNOSTICS]),
        )

    def _ensure_storage_root(self) -> None:
        self._data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        current = self._data_root.resolve(strict=True)
        if current != self._data_root or not current.is_dir():
            raise RunJournalStorageError("RunJournal storage root is unsafe.")
        for name in ("dashboard", "workspaces", self.workspace_id, "runs"):
            target = current / name
            if target.is_symlink():
                raise RunJournalStorageError("RunJournal storage root is unsafe.")
            try:
                target.mkdir(mode=0o700, exist_ok=True)
            except OSError as exc:
                raise RunJournalStorageError(
                    "RunJournal storage root is unsafe."
                ) from exc
            resolved = target.resolve(strict=True)
            if (
                resolved.parent != current
                or not resolved.is_relative_to(self._data_root)
                or not resolved.is_dir()
            ):
                raise RunJournalStorageError("RunJournal storage root is unsafe.")
            current = resolved

    @staticmethod
    def _directory_size(directory: Path) -> int:
        if directory.is_symlink():
            raise OSError("symlinked Run directory")
        total = 0
        for root, directory_names, filenames in os.walk(directory, followlinks=False):
            root_path = Path(root)
            directory_names[:] = [
                name for name in directory_names if not (root_path / name).is_symlink()
            ]
            for filename in filenames:
                path = root_path / filename
                if path.is_symlink():
                    continue
                total += path.stat().st_size
        return total

    @staticmethod
    def _bounded_run_entries(root: Path) -> tuple[list[Path], bool]:
        entries: list[Path] = []
        limited = False
        for entry in root.iterdir():
            if not _RUN_ID_RE.fullmatch(entry.name):
                continue
            if len(entries) >= _MAX_RUNS_SCANNED:
                limited = True
                break
            entries.append(entry)
        entries.sort(key=lambda item: item.name)
        return entries, limited

    @staticmethod
    def _validate_retention_directory(run_id: str, directory: Path, root: Path) -> None:
        if (
            not _RUN_ID_RE.fullmatch(run_id)
            or directory.name != run_id
            or directory.parent != root
            or directory.is_symlink()
        ):
            raise OSError("unsafe retention path")
        resolved = directory.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_dir():
            raise OSError("unsafe retention path")

    def _run_directory(self, run_id: str, *, must_exist: bool) -> Path:
        candidate = self._runs_root / run_id
        if not must_exist:
            return candidate
        if not self._runs_root.exists() or self._runs_root.is_symlink():
            raise RunJournalStorageError("RunJournal storage root is unsafe.")
        root = self._runs_root.resolve(strict=True)
        if candidate.is_symlink():
            raise RunJournalStorageError("Run directory is unsafe.")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_dir():
            raise RunJournalStorageError("Run directory is unsafe.")
        return resolved

    def _create_writer_lock(self, run_dir: Path, token: str) -> None:
        path = run_dir / ".writer.lock"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            payload = json.dumps(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "writerId": token,
                    "pid": os.getpid(),
                    "createdAt": _iso_time(self._clock()),
                },
                separators=(",", ":"),
            ).encode("utf-8")
            if os.write(fd, payload) != len(payload):
                raise OSError("short writer lock write")
            os.fsync(fd)
        finally:
            os.close(fd)

    def _require_writer(self, run_id: str, run_dir: Path) -> None:
        owner = self._writers.get(run_id)
        if owner is None or owner[1] != os.getpid():
            raise RunJournalOwnershipError("This process does not own the Run writer.")
        path = run_dir / ".writer.lock"
        try:
            if path.is_symlink() or path.stat().st_size > _MAX_METADATA_BYTES:
                raise OSError("unsafe writer lock")
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RunJournalOwnershipError("Run writer ownership could not be verified.") from exc
        if (
            not isinstance(parsed, dict)
            or parsed.get("writerId") != owner[0]
            or parsed.get("pid") != os.getpid()
        ):
            raise RunJournalOwnershipError("This process does not own the Run writer.")

    def _release_writer(self, run_id: str, run_dir: Path) -> None:
        self._require_writer(run_id, run_dir)
        try:
            (run_dir / ".writer.lock").unlink()
            self._fsync_directory(run_dir)
        finally:
            self._writers.pop(run_id, None)
            self._writer_mutexes.pop(run_id, None)

    def _new_event(
        self,
        record: RunRecord,
        event_type: str,
        *,
        sequence: int,
        timestamp: str | None = None,
        step: int | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> RunEvent:
        if event_type not in EVENT_TYPES:
            raise RunJournalValidationError("Event type is invalid.")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise RunJournalValidationError("Event sequence is invalid.")
        if step is not None and (
            isinstance(step, bool)
            or not isinstance(step, int)
            or step < 0
            or step > 1_000_000
        ):
            raise RunJournalValidationError("Event step is invalid.")
        return RunEvent(
            schema_version=SCHEMA_VERSION,
            event_id=f"evt_{uuid.uuid4().hex}",
            sequence=sequence,
            timestamp=timestamp or _iso_time(self._clock()),
            workspace_id=record.workspace_id,
            session_id=record.session_id,
            run_id=record.id,
            type=event_type,
            step=step,
            payload=_sanitize_event_payload(event_type, payload),
        )

    def _append_event_line(self, run_dir: Path, event: RunEvent) -> None:
        encoded = (
            json.dumps(
                event.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_EVENT_BYTES:
            raise RunJournalValidationError("Event exceeds the byte limit.")
        path = run_dir / "events.ndjson"
        if path.exists():
            if path.is_symlink() or path.stat().st_size + len(encoded) > _MAX_RUN_EVENT_FILE_BYTES:
                raise RunJournalStorageError("Run event storage limit was reached.")
            if path.stat().st_size:
                with path.open("rb") as handle:
                    handle.seek(-1, os.SEEK_END)
                    if handle.read(1) != b"\n":
                        raise RunJournalStorageError(
                            "Run event file has an incomplete final record."
                        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            if os.write(fd, encoded) != len(encoded):
                raise OSError("short event write")
            os.fsync(fd)
        finally:
            os.close(fd)

    def _write_metadata(self, run_dir: Path, record: RunRecord) -> None:
        encoded = (
            json.dumps(
                record.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_METADATA_BYTES:
            raise RunJournalStorageError("Run metadata exceeds the byte limit.")
        fd, temporary = tempfile.mkstemp(prefix=".metadata-", suffix=".tmp", dir=run_dir)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, run_dir / "metadata.json")
            self._fsync_directory(run_dir)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def _write_user_signal(
        self,
        run_dir: Path,
        record: RunUserSignal,
    ) -> None:
        encoded = (
            json.dumps(
                record.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_USER_SIGNAL_BYTES:
            raise RunJournalStorageError("User signal exceeds the byte limit.")
        fd, temporary = tempfile.mkstemp(
            prefix=".user-signal-",
            suffix=".tmp",
            dir=run_dir,
        )
        target = run_dir / _USER_SIGNAL_FILE
        try:
            with os.fdopen(fd, "wb") as handle:
                if _platform_name() == "posix":
                    os.fchmod(handle.fileno(), 0o600)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, target, follow_symlinks=False)
            os.unlink(temporary)
            self._fsync_directory(run_dir)
        except FileExistsError as error:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise RunJournalStorageError(
                "User signal storage changed during write."
            ) from error
        except OSError as error:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise RunJournalStorageError(
                "User signal storage is unavailable."
            ) from error

    @staticmethod
    def _acquire_run_mutation_lock(run_dir: Path) -> None:
        try:
            (run_dir / _USER_SIGNAL_LOCK).mkdir(mode=0o700)
        except FileExistsError as error:
            raise RunJournalStorageError(
                "Run mutation storage is busy."
            ) from error
        except OSError as error:
            raise RunJournalStorageError(
                "Run mutation storage is unavailable."
            ) from error

    @staticmethod
    def _release_run_mutation_lock(run_dir: Path) -> None:
        try:
            (run_dir / _USER_SIGNAL_LOCK).rmdir()
        except OSError:
            pass

    def _read_user_signal(self, run_dir: Path) -> RunUserSignal | None:
        path = run_dir / _USER_SIGNAL_FILE
        try:
            before = os.lstat(path)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise RunJournalStorageError(
                "User signal storage is unavailable."
            ) from error
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > _MAX_USER_SIGNAL_BYTES
        ):
            raise RunJournalStorageError("User signal storage is unsafe.")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags)
            try:
                after = os.fstat(fd)
                if (
                    after.st_dev != before.st_dev
                    or after.st_ino != before.st_ino
                    or not stat.S_ISREG(after.st_mode)
                    or after.st_size != before.st_size
                ):
                    raise OSError("user signal changed during read")
                raw = os.read(fd, _MAX_USER_SIGNAL_BYTES + 1)
            finally:
                os.close(fd)
            if len(raw) != before.st_size:
                raise OSError("short user signal read")
            return RunUserSignal.from_dict(json.loads(raw.decode("utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise RunJournalStorageError(
                "User signal storage is unsafe."
            ) from error

    def _write_memory_id_sidecar(
        self,
        run_dir: Path,
        entry_ids: tuple[str, ...],
        *,
        filename: str,
        label: str,
    ) -> None:
        encoded = (
            json.dumps(
                {"schemaVersion": SCHEMA_VERSION, "entryIds": list(entry_ids)},
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_RENDERED_MEMORY_BYTES:
            raise RunJournalStorageError(f"{label} Memory IDs exceed the byte limit.")
        fd, temporary = tempfile.mkstemp(
            prefix=f".memory-{filename}-",
            suffix=".tmp",
            dir=run_dir,
        )
        target = run_dir / filename
        try:
            with os.fdopen(fd, "wb") as handle:
                if _platform_name() == "posix":
                    os.fchmod(handle.fileno(), 0o600)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, target, follow_symlinks=False)
            os.unlink(temporary)
            self._fsync_directory(run_dir)
        except FileExistsError:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            # Only one retrieval happens per turn, so a second write attempt
            # is tolerated only when the existing target is a safe file with
            # identical content; anything else (a symlink, a mismatch) is a
            # genuine storage problem, not a benign retry.
            if self._read_memory_id_sidecar(run_dir, filename, label) != entry_ids:
                raise RunJournalStorageError(
                    f"{label} Memory ID storage changed during write."
                )
        except OSError as error:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise RunJournalStorageError(
                f"{label} Memory ID storage is unavailable."
            ) from error

    def _read_memory_id_sidecar(
        self, run_dir: Path, filename: str, label: str
    ) -> tuple[str, ...] | None:
        path = run_dir / filename
        try:
            before = os.lstat(path)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise RunJournalStorageError(
                f"{label} Memory ID storage is unavailable."
            ) from error
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > _MAX_RENDERED_MEMORY_BYTES
        ):
            raise RunJournalStorageError(f"{label} Memory ID storage is unsafe.")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags)
            try:
                after = os.fstat(fd)
                if (
                    after.st_dev != before.st_dev
                    or after.st_ino != before.st_ino
                    or not stat.S_ISREG(after.st_mode)
                    or after.st_size != before.st_size
                ):
                    raise OSError("memory ID sidecar changed during read")
                raw = os.read(fd, _MAX_RENDERED_MEMORY_BYTES + 1)
            finally:
                os.close(fd)
            if len(raw) != before.st_size:
                raise OSError("short memory ID sidecar read")
            decoded = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RunJournalStorageError(
                f"{label} Memory ID storage is unsafe."
            ) from error
        if (
            not isinstance(decoded, dict)
            or set(decoded) != {"schemaVersion", "entryIds"}
            or decoded.get("schemaVersion") != SCHEMA_VERSION
        ):
            raise RunJournalStorageError(f"{label} Memory ID storage is unsafe.")
        try:
            return _normalize_rendered_memory_ids(decoded.get("entryIds"))
        except RunJournalValidationError as error:
            raise RunJournalStorageError(
                f"{label} Memory ID storage is unsafe."
            ) from error

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        try:
            fd = os.open(directory, flags)
        except OSError:
            return
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _read_run(self, run_id: str) -> tuple[RunRecord | None, list[dict[str, str]]]:
        if not self._runs_root.exists():
            return None, []
        try:
            run_dir = self._run_directory(run_id, must_exist=True)
            record = self._read_metadata(run_dir)
            if record.id != run_id or record.workspace_id != self.workspace_id:
                raise ValueError("Run identity mismatch")
        except FileNotFoundError:
            return None, []
        except Exception:
            return None, [self._diagnostic("run_read_failed", "A Run record could not be read.")]
        events, diagnostics = self._read_events(run_id)
        return self._reconcile_record(record, events), diagnostics

    def _read_metadata(self, run_dir: Path) -> RunRecord:
        path = run_dir / "metadata.json"
        if path.is_symlink():
            raise RunJournalStorageError("Run metadata is unsafe.")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(run_dir) or resolved.stat().st_size > _MAX_METADATA_BYTES:
            raise RunJournalStorageError("Run metadata is unsafe.")
        parsed = json.loads(resolved.read_text(encoding="utf-8"))
        return RunRecord.from_dict(parsed)

    def _read_events(self, run_id: str) -> tuple[list[RunEvent], list[dict[str, str]]]:
        try:
            run_dir = self._run_directory(run_id, must_exist=True)
        except FileNotFoundError:
            return [], []
        path = run_dir / "events.ndjson"
        if not path.exists():
            return [], [self._diagnostic("events_missing", "Run events are unavailable.")]
        if path.is_symlink():
            return [], [self._diagnostic("events_unsafe", "Run events could not be read safely.")]
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(run_dir):
                raise OSError("event file escapes Run directory")
        except Exception:
            return [], [self._diagnostic("events_unsafe", "Run events could not be read safely.")]

        events: list[RunEvent] = []
        diagnostics: list[dict[str, str]] = []
        total = 0
        last_sequence = 0
        lifecycle_status: str | None = None
        try:
            with resolved.open("rb") as handle:
                while True:
                    raw_line = handle.readline(_MAX_EVENT_BYTES + 1)
                    if not raw_line:
                        break
                    total += len(raw_line)
                    if len(raw_line) > _MAX_EVENT_BYTES:
                        diagnostics.append(
                            self._diagnostic(
                                "event_oversized",
                                "An oversized Run event was ignored.",
                            )
                        )
                        break
                    if (
                        total > _MAX_RUN_EVENT_FILE_BYTES
                        or len(events) >= _MAX_EVENTS_PER_RUN
                    ):
                        diagnostics.append(self._diagnostic("events_limited", "Run events reached the read limit."))
                        break
                    if not raw_line.endswith(b"\n"):
                        diagnostics.append(self._diagnostic("partial_final_event", "An incomplete final Run event was ignored."))
                        break
                    try:
                        parsed = json.loads(raw_line.decode("utf-8"))
                        event = RunEvent.from_dict(parsed)
                        if (
                            event.run_id != run_id
                            or event.workspace_id != self.workspace_id
                            or event.sequence <= last_sequence
                        ):
                            raise ValueError("event identity or sequence mismatch")
                        candidate_status = _STATUS_FOR_LIFECYCLE_EVENT.get(event.type)
                        if not events and (
                            event.type != "run.queued" or event.sequence != 1
                        ):
                            raise ValueError("Run must start with run.queued")
                        if candidate_status is not None:
                            if lifecycle_status is None:
                                if candidate_status != "queued":
                                    raise ValueError("invalid initial lifecycle event")
                            elif candidate_status not in _ALLOWED_TRANSITIONS.get(
                                lifecycle_status, frozenset()
                            ):
                                raise ValueError("invalid lifecycle transition")
                    except Exception:
                        diagnostics.append(self._diagnostic("event_invalid", "A malformed Run event was skipped."))
                        continue
                    events.append(event)
                    last_sequence = event.sequence
                    if candidate_status is not None:
                        lifecycle_status = candidate_status
        except OSError:
            return [], [self._diagnostic("events_read_failed", "Run events could not be read.")]
        return events, diagnostics[:_MAX_DIAGNOSTICS]

    @staticmethod
    def _reconcile_record(record: RunRecord, events: list[RunEvent]) -> RunRecord:
        if not events:
            return replace(record, last_sequence=0, event_count=0, error_count=0)
        status = record.status
        started_at: str | None = None
        completed_at: str | None = None
        lifecycle_status: str | None = None
        for event in events:
            for candidate_status, candidate_type in LIFECYCLE_EVENT_FOR_STATUS.items():
                if event.type == candidate_type:
                    lifecycle_status = candidate_status
                    if candidate_status == "running":
                        started_at = event.timestamp
                    if candidate_status in TERMINAL_STATUSES:
                        completed_at = event.timestamp
                    break
        if lifecycle_status is not None:
            status = lifecycle_status
        return replace(
            record,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            updated_at=events[-1].timestamp,
            last_sequence=events[-1].sequence,
            event_count=len(events),
            error_count=sum(1 for event in events if event.type.endswith(".failed")),
        )

    def _refresh_index_best_effort(self) -> None:
        lock = self._runs_root / ".index.lock"
        acquired = False
        try:
            deadline = time.monotonic() + 0.5
            while True:
                try:
                    lock.mkdir(mode=0o700)
                    acquired = True
                    break
                except FileExistsError:
                    if time.monotonic() >= deadline:
                        return
                    time.sleep(0.01)
            entries, _ = self._bounded_run_entries(self._runs_root)
            run_ids = [
                entry.name
                for entry in entries
                if not entry.is_symlink() and entry.is_dir()
            ]
            payload = {
                "schemaVersion": SCHEMA_VERSION,
                "workspaceId": self.workspace_id,
                "updatedAt": _iso_time(self._clock()),
                "runIds": run_ids,
            }
            fd, temporary = tempfile.mkstemp(prefix=".index-", suffix=".tmp", dir=self._runs_root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, separators=(",", ":"))
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self._runs_root / "index.json")
                self._fsync_directory(self._runs_root)
            except Exception:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                raise
        except Exception:
            return
        finally:
            if acquired:
                try:
                    lock.rmdir()
                except OSError:
                    pass

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
            raise RunJournalValidationError("Run ID is invalid.")

    @staticmethod
    def _page_limit(value: int | str | None, *, default: int) -> int:
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            raise RunJournalValidationError("Page limit is invalid.")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise RunJournalValidationError("Page limit is invalid.") from exc
        if parsed < 1 or parsed > _MAX_PAGE_LIMIT:
            raise RunJournalValidationError("Page limit is invalid.")
        return parsed

    @staticmethod
    def _encode_cursor(kind: str, values: list[object]) -> str:
        raw = json.dumps([kind, *values], separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(kind: str, cursor: str) -> list[object]:
        if (
            not isinstance(cursor, str)
            or not 1 <= len(cursor) <= _MAX_CURSOR_CHARS
            or not _CURSOR_RE.fullmatch(cursor)
        ):
            raise ValueError("invalid cursor")
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
            payload = json.loads(decoded.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid cursor") from exc
        if not isinstance(payload, list) or not payload or payload[0] != kind or len(payload) > 8:
            raise ValueError("invalid cursor")
        return payload[1:]

    @staticmethod
    def _diagnostic(code: str, message: str) -> dict[str, str]:
        return {
            "source": "runs",
            "code": code,
            "message": _redact_text(message, max_chars=_MAX_DIAGNOSTIC_CHARS),
        }
