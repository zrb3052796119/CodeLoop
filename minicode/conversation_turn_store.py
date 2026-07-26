"""Durable identity and status facts for synchronous Dashboard Chat turns.

The store deliberately contains no conversation content.  It coordinates
threads in one Gateway process, persists enough state for restart recovery, and
uses an internal owner token only to distinguish a live claim from an execution
left by an older process.  Session remains the authoritative content store.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal

from minicode.run_journal import stable_workspace_id
from minicode.turn_cancellation import (
    TurnCancellationRegistry,
    TurnCancellationToken,
)


TURN_STORE_SCHEMA_VERSION = 1
TURN_ID_PATTERN = re.compile(r"turn_[0-9a-f]{32}")
_FINGERPRINT_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_WORKSPACE_ID_PATTERN = re.compile(r"ws_[0-9a-f]{16}")
_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_RUN_ID_PATTERN = re.compile(r"run_[0-9a-f]{32}")
_OWNER_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z"
)
TURN_STATUSES = frozenset(
    {
        "accepted",
        "running",
        "cancel_requested",
        "committing",
        "completed",
        "failed",
        "interrupted",
        "cancelled",
    }
)
TERMINAL_TURN_STATUSES = frozenset(
    {"completed", "failed", "interrupted", "cancelled"}
)
TURN_ERROR_CODES = frozenset(
    {
        "session_not_found",
        "session_conflict",
        "session_busy",
        "runtime_unavailable",
        "turn_failed",
        "turn_interrupted",
        "turn_cancelled",
    }
)
_RECORD_FIELDS = frozenset(
    {
        "schemaVersion",
        "turnId",
        "workspaceId",
        "requestFingerprint",
        "status",
        "sessionId",
        "createdSession",
        "runId",
        "createdAt",
        "updatedAt",
        "completedAt",
        "errorCode",
        "commitMarker",
        "ownerId",
    }
)
_MARKER_FIELDS = frozenset(
    {"schemaVersion", "userMessageIndex", "assistantMessageIndex"}
)
_MAX_RECORD_BYTES = 16 * 1024
_DEFAULT_MAX_RECORDS = 10_000
_DEFAULT_SCAN_LIMIT = 20_000
_DEFAULT_TERMINAL_MAX_AGE = timedelta(days=90)
_TEMP_MAX_AGE = timedelta(days=1)

ClaimDisposition = Literal[
    "claimed", "in_progress", "recover", "terminal", "conflict"
]


class TurnStoreError(RuntimeError):
    """Low-information storage or state-machine failure."""


class TurnStoreCorruptError(TurnStoreError):
    """One record is malformed, oversized, or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class TurnRecord:
    schema_version: int
    turn_id: str
    workspace_id: str
    request_fingerprint: str
    status: str
    session_id: str | None
    created_session: bool | None
    run_id: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    error_code: str | None
    commit_marker: dict[str, int] | None
    owner_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "turnId": self.turn_id,
            "workspaceId": self.workspace_id,
            "requestFingerprint": self.request_fingerprint,
            "status": self.status,
            "sessionId": self.session_id,
            "createdSession": self.created_session,
            "runId": self.run_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "completedAt": self.completed_at,
            "errorCode": self.error_code,
            "commitMarker": self.commit_marker,
            "ownerId": self.owner_id,
        }


@dataclass(frozen=True, slots=True)
class TurnClaim:
    disposition: ClaimDisposition
    record: TurnRecord


@dataclass(frozen=True, slots=True)
class TurnCancellationDecision:
    record: TurnRecord
    cancellation_accepted: bool


@dataclass(frozen=True, slots=True)
class TurnStartDecision:
    record: TurnRecord
    execution_started: bool


@dataclass(frozen=True, slots=True)
class TurnCommitDecision:
    record: TurnRecord
    commit_allowed: bool


@dataclass(frozen=True, slots=True)
class TurnFailureDecision:
    record: TurnRecord
    failure_recorded: bool


@dataclass(frozen=True, slots=True)
class TurnDeletionSnapshot:
    """Content-free records relevant to one Session deletion plan."""

    terminal: tuple[TurnRecord, ...]
    active: tuple[TurnRecord, ...]
    diagnostics: tuple[str, ...]


_ROOT_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[str, threading.RLock] = {}


def _root_lock(path: Path) -> threading.RLock:
    key = str(path)
    with _ROOT_LOCKS_GUARD:
        lock = _ROOT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _ROOT_LOCKS[key] = lock
        return lock


def create_turn_id() -> str:
    """Create a cryptographically strong closed-format turn identifier."""
    return f"turn_{secrets.token_hex(16)}"


def validate_turn_id(turn_id: object) -> str:
    if not isinstance(turn_id, str) or TURN_ID_PATTERN.fullmatch(turn_id) is None:
        raise ValueError("invalid turn id")
    return turn_id


def request_fingerprint(
    *,
    workspace_id: str,
    session_id: str | None,
    message: str,
) -> str:
    """Hash the complete normalized request identity without retaining content."""
    if not isinstance(workspace_id, str) or _WORKSPACE_ID_PATTERN.fullmatch(workspace_id) is None:
        raise ValueError("invalid workspace id")
    if session_id is not None and (
        not isinstance(session_id, str)
        or _SESSION_ID_PATTERN.fullmatch(session_id) is None
    ):
        raise ValueError("invalid session id")
    if not isinstance(message, str):
        raise ValueError("invalid message")
    session_identity = session_id if session_id is not None else "<new-session>"
    payload = (
        "minicode.dashboard.chat-turn-fingerprint.v1\0"
        f"{workspace_id}\0{session_identity}\0{message}"
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise TurnStoreError("turn store clock unavailable")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise TurnStoreCorruptError("turn record is invalid")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise TurnStoreCorruptError("turn record is invalid") from error
    if parsed.utcoffset() != timedelta(0):
        raise TurnStoreCorruptError("turn record is invalid")
    return parsed


def _validate_marker(value: object) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _MARKER_FIELDS:
        raise TurnStoreCorruptError("turn record is invalid")
    schema = value.get("schemaVersion")
    user_index = value.get("userMessageIndex")
    assistant_index = value.get("assistantMessageIndex")
    if (
        isinstance(schema, bool)
        or schema != 1
        or isinstance(user_index, bool)
        or not isinstance(user_index, int)
        or user_index < 0
        or isinstance(assistant_index, bool)
        or not isinstance(assistant_index, int)
        or assistant_index <= user_index
    ):
        raise TurnStoreCorruptError("turn record is invalid")
    return {
        "schemaVersion": 1,
        "userMessageIndex": user_index,
        "assistantMessageIndex": assistant_index,
    }


def _record_from_dict(value: object, *, expected_turn_id: str) -> TurnRecord:
    if not isinstance(value, dict) or set(value) != _RECORD_FIELDS:
        raise TurnStoreCorruptError("turn record is invalid")
    schema_version = value.get("schemaVersion")
    turn_id = value.get("turnId")
    workspace_id = value.get("workspaceId")
    fingerprint = value.get("requestFingerprint")
    status_value = value.get("status")
    session_id = value.get("sessionId")
    created_session = value.get("createdSession")
    run_id = value.get("runId")
    completed_at = value.get("completedAt")
    error_code = value.get("errorCode")
    owner_id = value.get("ownerId")
    if (
        isinstance(schema_version, bool)
        or schema_version != TURN_STORE_SCHEMA_VERSION
        or turn_id != expected_turn_id
        or not isinstance(turn_id, str)
        or TURN_ID_PATTERN.fullmatch(turn_id) is None
        or not isinstance(workspace_id, str)
        or _WORKSPACE_ID_PATTERN.fullmatch(workspace_id) is None
        or not isinstance(fingerprint, str)
        or _FINGERPRINT_PATTERN.fullmatch(fingerprint) is None
        or not isinstance(status_value, str)
        or status_value not in TURN_STATUSES
        or session_id is not None
        and (
            not isinstance(session_id, str)
            or _SESSION_ID_PATTERN.fullmatch(session_id) is None
        )
        or created_session is not None
        and not isinstance(created_session, bool)
        or run_id is not None
        and (not isinstance(run_id, str) or _RUN_ID_PATTERN.fullmatch(run_id) is None)
        or completed_at is not None
        and not isinstance(completed_at, str)
        or error_code is not None
        and (not isinstance(error_code, str) or error_code not in TURN_ERROR_CODES)
        or not isinstance(owner_id, str)
        or _OWNER_ID_PATTERN.fullmatch(owner_id) is None
    ):
        raise TurnStoreCorruptError("turn record is invalid")
    created_at = value.get("createdAt")
    updated_at = value.get("updatedAt")
    created_time = _parse_timestamp(created_at)
    updated_time = _parse_timestamp(updated_at)
    if updated_time < created_time:
        raise TurnStoreCorruptError("turn record is invalid")
    if completed_at is not None:
        completed_time = _parse_timestamp(completed_at)
        if completed_time < created_time:
            raise TurnStoreCorruptError("turn record is invalid")
    marker = _validate_marker(value.get("commitMarker"))
    if status_value == "completed":
        if (
            session_id is None
            or created_session is None
            or completed_at is None
            or error_code is not None
            or marker is None
        ):
            raise TurnStoreCorruptError("turn record is invalid")
    elif marker is not None or completed_at is not None:
        raise TurnStoreCorruptError("turn record is invalid")
    if status_value in {"accepted", "running", "cancel_requested", "committing"} and error_code is not None:
        raise TurnStoreCorruptError("turn record is invalid")
    if status_value in {"failed", "interrupted"} and error_code is None:
        raise TurnStoreCorruptError("turn record is invalid")
    if status_value == "cancelled" and error_code != "turn_cancelled":
        raise TurnStoreCorruptError("turn record is invalid")
    if (session_id is None) != (created_session is None):
        raise TurnStoreCorruptError("turn record is invalid")
    return TurnRecord(
        schema_version=TURN_STORE_SCHEMA_VERSION,
        turn_id=turn_id,
        workspace_id=workspace_id,
        request_fingerprint=fingerprint,
        status=status_value,
        session_id=session_id,
        created_session=created_session,
        run_id=run_id,
        created_at=created_at,
        updated_at=updated_at,
        completed_at=completed_at,
        error_code=error_code,
        commit_marker=marker,
        owner_id=owner_id,
    )


class ConversationTurnStore:
    """One-workspace durable turn registry with bounded local operations."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        data_dir: str | Path,
        owner_id: str | None = None,
        clock: Callable[[], datetime] = _utc_now,
        max_records: int = _DEFAULT_MAX_RECORDS,
        scan_limit: int = _DEFAULT_SCAN_LIMIT,
        terminal_max_age: timedelta = _DEFAULT_TERMINAL_MAX_AGE,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace_id = stable_workspace_id(self.workspace)
        self.data_dir = Path(data_dir).expanduser()
        self._data_root = self.data_dir.resolve(strict=False)
        self._turns_root = (
            self._data_root
            / "dashboard"
            / "workspaces"
            / self.workspace_id
            / "turns"
        )
        self.owner_id = secrets.token_hex(16) if owner_id is None else owner_id
        if _OWNER_ID_PATTERN.fullmatch(self.owner_id) is None:
            raise ValueError("invalid turn owner")
        if (
            isinstance(max_records, bool)
            or not isinstance(max_records, int)
            or max_records < 1
            or isinstance(scan_limit, bool)
            or not isinstance(scan_limit, int)
            or scan_limit < max_records
            or not isinstance(terminal_max_age, timedelta)
            or terminal_max_age.total_seconds() < 0
        ):
            raise ValueError("invalid turn retention")
        self._clock = clock
        self.max_records = max_records
        self.scan_limit = scan_limit
        self.terminal_max_age = terminal_max_age
        self._lock = _root_lock(self._turns_root)
        self._active_turns: set[str] = set()
        self._cancellations = TurnCancellationRegistry()

    def record_path(self, turn_id: str) -> Path:
        return self._turns_root / f"{validate_turn_id(turn_id)}.json"

    def _ensure_storage_root(self) -> None:
        try:
            self._data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not self._data_root.is_dir():
                raise OSError("data root is not a directory")
            current = self._data_root
            for name in ("dashboard", "workspaces", self.workspace_id, "turns"):
                current = current / name
                current.mkdir(exist_ok=True, mode=0o700)
                info = os.lstat(current)
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise OSError("unsafe turn store directory")
                if not current.resolve(strict=True).is_relative_to(self._data_root):
                    raise OSError("unsafe turn store directory")
        except OSError as error:
            raise TurnStoreError("turn store unavailable") from error

    def _read(self, turn_id: str) -> TurnRecord | None:
        path = self.record_path(turn_id)
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise TurnStoreError("turn store unavailable") from error
        try:
            descriptor_stat = os.fstat(descriptor)
            path_stat = os.lstat(path)
            if (
                not stat.S_ISREG(descriptor_stat.st_mode)
                or not stat.S_ISREG(path_stat.st_mode)
                or descriptor_stat.st_dev != path_stat.st_dev
                or descriptor_stat.st_ino != path_stat.st_ino
                or descriptor_stat.st_size > _MAX_RECORD_BYTES
            ):
                raise TurnStoreCorruptError("turn record is invalid")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                raw = handle.read(_MAX_RECORD_BYTES + 1)
            if len(raw) > _MAX_RECORD_BYTES:
                raise TurnStoreCorruptError("turn record is invalid")
            try:
                value = json.loads(raw.decode("utf-8", errors="strict"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise TurnStoreCorruptError("turn record is invalid") from error
            return _record_from_dict(value, expected_turn_id=turn_id)
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def _write(self, record: TurnRecord) -> None:
        if record.workspace_id != self.workspace_id:
            raise TurnStoreError("turn store unavailable")
        self._ensure_storage_root()
        path = self.record_path(record.turn_id)
        encoded = (
            json.dumps(
                record.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_RECORD_BYTES:
            raise TurnStoreError("turn store unavailable")
        descriptor = -1
        temporary = ""
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".turn-", suffix=".tmp", dir=self._turns_root
            )
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(descriptor)
            if path.exists() and path.is_symlink():
                raise OSError("unsafe turn record")
            os.replace(temporary, path)
            temporary = ""
            try:
                directory_fd = os.open(self._turns_root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        except (OSError, TypeError, ValueError) as error:
            raise TurnStoreError("turn store unavailable") from error
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary:
                try:
                    Path(temporary).unlink()
                except OSError:
                    pass

    def get(self, turn_id: str) -> TurnRecord | None:
        validated = validate_turn_id(turn_id)
        with self._lock:
            self._ensure_storage_root()
            record = self._read(validated)
            if record is not None and record.workspace_id != self.workspace_id:
                return None
            return record

    def deletion_snapshot(self, session_id: str) -> TurnDeletionSnapshot:
        """Read a bounded, content-free view of Turns linked to one Session."""
        if (
            not isinstance(session_id, str)
            or _SESSION_ID_PATTERN.fullmatch(session_id) is None
        ):
            raise ValueError("invalid session id")
        terminal: list[TurnRecord] = []
        active: list[TurnRecord] = []
        diagnostics: set[str] = set()
        with self._lock:
            if not self._turns_root.exists():
                return TurnDeletionSnapshot((), (), ())
            try:
                if self._turns_root.is_symlink():
                    raise OSError("unsafe turn root")
                root = self._turns_root.resolve(strict=True)
                if not root.is_relative_to(self._data_root):
                    raise OSError("turn root escapes data directory")
                with os.scandir(root) as scanner:
                    entries = []
                    for index, entry in enumerate(scanner):
                        if index >= self.scan_limit:
                            diagnostics.add("turn_scan_limited")
                            break
                        entries.append(entry.name)
            except OSError:
                return TurnDeletionSnapshot((), (), ("turn_scan_unavailable",))
            for name in sorted(entries):
                match = re.fullmatch(r"(turn_[0-9a-f]{32})\.json", name)
                if match is None:
                    continue
                try:
                    record = self._read(match.group(1))
                except TurnStoreCorruptError:
                    diagnostics.add("turn_record_invalid")
                    continue
                except TurnStoreError:
                    diagnostics.add("turn_scan_unavailable")
                    continue
                if record is None or record.session_id != session_id:
                    continue
                if record.status in TERMINAL_TURN_STATUSES:
                    terminal.append(record)
                else:
                    active.append(record)
        return TurnDeletionSnapshot(
            tuple(sorted(terminal, key=lambda item: item.turn_id)),
            tuple(sorted(active, key=lambda item: item.turn_id)),
            tuple(sorted(diagnostics)),
        )

    def delete_terminal_for_session(self, session_id: str) -> int:
        """Delete exactly the currently terminal Turns linked to one Session."""
        with self._lock:
            snapshot = self.deletion_snapshot(session_id)
            if snapshot.active or snapshot.diagnostics:
                raise TurnStoreError("turn deletion unavailable")
            deleted = 0
            for record in snapshot.terminal:
                path = self.record_path(record.turn_id)
                try:
                    info = os.lstat(path)
                    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                        raise OSError("unsafe turn record")
                    current = self._read(record.turn_id)
                    if (
                        current is None
                        or current.session_id != session_id
                        or current.status not in TERMINAL_TURN_STATUSES
                    ):
                        raise OSError("turn changed during deletion")
                    path.unlink()
                    self._active_turns.discard(record.turn_id)
                    self._cancellations.release(record.turn_id)
                    deleted += 1
                except FileNotFoundError:
                    continue
                except OSError as error:
                    raise TurnStoreError("turn deletion unavailable") from error
            return deleted

    def claim(self, *, turn_id: str, fingerprint: str) -> TurnClaim:
        validated = validate_turn_id(turn_id)
        if not isinstance(fingerprint, str) or _FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
            raise ValueError("invalid request fingerprint")
        with self._lock:
            self._ensure_storage_root()
            existing = self._read(validated)
            if existing is not None:
                if existing.workspace_id != self.workspace_id:
                    raise TurnStoreError("turn store unavailable")
                if existing.request_fingerprint != fingerprint:
                    return TurnClaim("conflict", existing)
                if existing.status in TERMINAL_TURN_STATUSES:
                    return TurnClaim("terminal", existing)
                if (
                    existing.owner_id == self.owner_id
                    and validated in self._active_turns
                ):
                    return TurnClaim("in_progress", existing)
                return TurnClaim("recover", existing)
            now = _timestamp(self._clock())
            record = TurnRecord(
                schema_version=TURN_STORE_SCHEMA_VERSION,
                turn_id=validated,
                workspace_id=self.workspace_id,
                request_fingerprint=fingerprint,
                status="accepted",
                session_id=None,
                created_session=None,
                run_id=None,
                created_at=now,
                updated_at=now,
                completed_at=None,
                error_code=None,
                commit_marker=None,
                owner_id=self.owner_id,
            )
            self._write(record)
            self._active_turns.add(validated)
            self._enforce_retention(current_turn_id=validated)
            return TurnClaim("claimed", record)

    def is_active(self, turn_id: str) -> bool:
        validated = validate_turn_id(turn_id)
        with self._lock:
            return validated in self._active_turns

    def release_claim(self, turn_id: str) -> None:
        validated = validate_turn_id(turn_id)
        with self._lock:
            self._active_turns.discard(validated)
            self._cancellations.release(validated)

    def cancellation_token(self, turn_id: str) -> TurnCancellationToken:
        """Return the single process-local token for one live owned Turn."""
        validated = validate_turn_id(turn_id)
        with self._lock:
            record = self._owned_record(validated)
            if validated not in self._active_turns or record.status in TERMINAL_TURN_STATUSES:
                raise TurnStoreError("turn is not active")
            return self._cancellations.acquire(
                validated,
                requested=record.status == "cancel_requested",
            )

    def _owned_record(self, turn_id: str) -> TurnRecord:
        record = self._read(validate_turn_id(turn_id))
        if record is None or record.workspace_id != self.workspace_id:
            raise TurnStoreError("turn store unavailable")
        if record.owner_id != self.owner_id:
            raise TurnStoreError("turn store unavailable")
        return record

    def _mark_cancelled_locked(self, record: TurnRecord) -> TurnRecord:
        updated = replace(
            record,
            owner_id=self.owner_id,
            status="cancelled",
            updated_at=_timestamp(self._clock()),
            error_code="turn_cancelled",
        )
        self._write(updated)
        self._active_turns.discard(record.turn_id)
        self._cancellations.release(record.turn_id)
        return updated

    def mark_running(self, turn_id: str) -> TurnStartDecision:
        """Atomically start execution or finish a cancellation that won first."""
        with self._lock:
            record = self._owned_record(turn_id)
            if record.status == "cancel_requested":
                return TurnStartDecision(
                    self._mark_cancelled_locked(record),
                    False,
                )
            if record.status != "accepted":
                raise TurnStoreError("invalid turn transition")
            updated = replace(
                record, status="running", updated_at=_timestamp(self._clock())
            )
            self._write(updated)
            return TurnStartDecision(updated, True)

    def request_cancel(self, turn_id: str) -> TurnCancellationDecision:
        """Persist an idempotent request; committing and terminal states win."""
        validated = validate_turn_id(turn_id)
        with self._lock:
            record = self._read(validated)
            if record is None or record.workspace_id != self.workspace_id:
                raise TurnStoreError("turn store unavailable")
            if record.status == "cancel_requested":
                self._cancellations.request(validated)
                return TurnCancellationDecision(record, True)
            if record.status not in {"accepted", "running"}:
                return TurnCancellationDecision(record, False)
            updated = replace(
                record,
                status="cancel_requested",
                updated_at=_timestamp(self._clock()),
            )
            self._write(updated)
            self._cancellations.request(validated)
            return TurnCancellationDecision(updated, True)

    def begin_commit(self, turn_id: str) -> TurnCommitDecision:
        """Atomically decide whether completion or cancellation owns the Turn."""
        with self._lock:
            record = self._owned_record(turn_id)
            if record.status == "cancel_requested":
                return TurnCommitDecision(record, False)
            if record.status != "running" or record.session_id is None:
                raise TurnStoreError("invalid turn transition")
            updated = replace(
                record,
                status="committing",
                updated_at=_timestamp(self._clock()),
            )
            self._write(updated)
            return TurnCommitDecision(updated, True)

    def attach_session(
        self,
        turn_id: str,
        *,
        session_id: str,
        created_session: bool,
    ) -> TurnRecord:
        if (
            not isinstance(session_id, str)
            or _SESSION_ID_PATTERN.fullmatch(session_id) is None
            or not isinstance(created_session, bool)
        ):
            raise ValueError("invalid Session reference")
        from minicode.deletion_store import DeletionStoreError, conversation_write_guard

        try:
            with conversation_write_guard(
                self.workspace,
                session_id,
                data_dir=self.data_dir,
            ):
                with self._lock:
                    record = self._owned_record(turn_id)
                    if (
                        record.status not in {"running", "cancel_requested"}
                        or record.session_id is not None
                    ):
                        raise TurnStoreError("invalid turn transition")
                    updated = replace(
                        record,
                        session_id=session_id,
                        created_session=created_session,
                        updated_at=_timestamp(self._clock()),
                    )
                    self._write(updated)
                    return updated
        except DeletionStoreError as error:
            raise TurnStoreError("turn association unavailable") from error

    def attach_run(self, turn_id: str, *, run_id: str | None) -> TurnRecord:
        if run_id is not None and (
            not isinstance(run_id, str) or _RUN_ID_PATTERN.fullmatch(run_id) is None
        ):
            raise ValueError("invalid Run reference")
        with self._lock:
            record = self._owned_record(turn_id)
            if record.status not in {"running", "cancel_requested"} or record.session_id is None:
                raise TurnStoreError("invalid turn transition")
            updated = replace(
                record, run_id=run_id, updated_at=_timestamp(self._clock())
            )
            self._write(updated)
            return updated

    def mark_completed(
        self,
        turn_id: str,
        *,
        commit_marker: dict[str, int],
    ) -> TurnRecord:
        marker = _validate_marker(commit_marker)
        if marker is None:
            raise ValueError("invalid commit marker")
        with self._lock:
            record = self._owned_record(turn_id)
            if record.status != "committing" or record.session_id is None:
                raise TurnStoreError("invalid turn transition")
            now = _timestamp(self._clock())
            updated = replace(
                record,
                status="completed",
                updated_at=now,
                completed_at=now,
                error_code=None,
                commit_marker=marker,
            )
            self._write(updated)
            self._active_turns.discard(turn_id)
            self._cancellations.release(turn_id)
            return updated

    def recover_completed(
        self,
        turn_id: str,
        *,
        commit_marker: dict[str, int],
    ) -> TurnRecord:
        marker = _validate_marker(commit_marker)
        if marker is None:
            raise ValueError("invalid commit marker")
        with self._lock:
            record = self._read(validate_turn_id(turn_id))
            if (
                record is None
                or record.workspace_id != self.workspace_id
                or record.status not in {"accepted", "running", "cancel_requested", "committing"}
                or record.session_id is None
            ):
                raise TurnStoreError("invalid turn transition")
            now = _timestamp(self._clock())
            updated = replace(
                record,
                owner_id=self.owner_id,
                status="completed",
                updated_at=now,
                completed_at=now,
                error_code=None,
                commit_marker=marker,
            )
            self._write(updated)
            self._active_turns.discard(turn_id)
            self._cancellations.release(turn_id)
            return updated

    def mark_failed(self, turn_id: str, *, error_code: str) -> TurnFailureDecision:
        """Atomically record failure unless a persisted cancellation won first."""
        if error_code not in TURN_ERROR_CODES or error_code == "turn_interrupted":
            raise ValueError("invalid turn error code")
        with self._lock:
            record = self._owned_record(turn_id)
            if record.status == "cancel_requested":
                return TurnFailureDecision(
                    self._mark_cancelled_locked(record),
                    False,
                )
            if record.status not in {"accepted", "running", "committing"}:
                raise TurnStoreError("invalid turn transition")
            updated = replace(
                record,
                status="failed",
                updated_at=_timestamp(self._clock()),
                error_code=error_code,
            )
            self._write(updated)
            self._active_turns.discard(turn_id)
            self._cancellations.release(turn_id)
            return TurnFailureDecision(updated, True)

    def mark_interrupted(self, turn_id: str) -> TurnRecord:
        with self._lock:
            record = self._read(validate_turn_id(turn_id))
            if (
                record is None
                or record.workspace_id != self.workspace_id
                or record.status not in {"accepted", "running", "committing"}
            ):
                raise TurnStoreError("invalid turn transition")
            updated = replace(
                record,
                owner_id=self.owner_id,
                status="interrupted",
                updated_at=_timestamp(self._clock()),
                error_code="turn_interrupted",
            )
            self._write(updated)
            self._active_turns.discard(turn_id)
            self._cancellations.release(turn_id)
            return updated

    def mark_cancelled(self, turn_id: str) -> TurnRecord:
        with self._lock:
            record = self._read(validate_turn_id(turn_id))
            if (
                record is None
                or record.workspace_id != self.workspace_id
                or record.status != "cancel_requested"
            ):
                raise TurnStoreError("invalid turn transition")
            return self._mark_cancelled_locked(record)

    def _enforce_retention(self, *, current_turn_id: str) -> None:
        """Best-effort bounded terminal/temp cleanup; active records are retained."""
        try:
            entries: list[os.DirEntry[str]] = []
            with os.scandir(self._turns_root) as scanner:
                for index, entry in enumerate(scanner):
                    if index >= self.scan_limit:
                        return
                    entries.append(entry)
            now = self._clock()
            records: list[tuple[datetime, Path, TurnRecord]] = []
            for entry in entries:
                path = Path(entry.path)
                if entry.name.startswith(".turn-") and entry.name.endswith(".tmp"):
                    try:
                        modified = datetime.fromtimestamp(
                            entry.stat(follow_symlinks=False).st_mtime,
                            tz=timezone.utc,
                        )
                        if now - modified > _TEMP_MAX_AGE and not entry.is_symlink():
                            path.unlink()
                    except OSError:
                        pass
                    continue
                match = re.fullmatch(r"(turn_[0-9a-f]{32})\.json", entry.name)
                if match is None or entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    record = self._read(match.group(1))
                    if record is not None and record.status in TERMINAL_TURN_STATUSES:
                        records.append((_parse_timestamp(record.updated_at), path, record))
                except TurnStoreError:
                    continue
            records.sort(key=lambda item: item[0])
            excess = max(0, len(entries) - self.max_records)
            cutoff = now - self.terminal_max_age
            for updated, path, record in records:
                if record.turn_id == current_turn_id:
                    continue
                if updated < cutoff or excess > 0:
                    try:
                        path.unlink()
                        excess = max(0, excess - 1)
                    except OSError:
                        pass
        except (OSError, TurnStoreError, ValueError):
            return


__all__ = [
    "ConversationTurnStore",
    "TERMINAL_TURN_STATUSES",
    "TURN_ERROR_CODES",
    "TURN_ID_PATTERN",
    "TURN_STATUSES",
    "TurnClaim",
    "TurnCancellationDecision",
    "TurnCommitDecision",
    "TurnFailureDecision",
    "TurnDeletionSnapshot",
    "TurnStartDecision",
    "TurnRecord",
    "TurnStoreCorruptError",
    "TurnStoreError",
    "create_turn_id",
    "request_fingerprint",
    "validate_turn_id",
]
