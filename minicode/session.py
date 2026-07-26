"""Session persistence and resume module.

Provides session data structures, autosave mechanism, and resume capabilities
to allow MiniCode to save and restore conversation state across restarts.

Uses incremental delta saves to reduce serialization overhead:
- Only new/changed messages are appended since last save
- Full save occurs periodically (every N deltas) for consistency
- Dirty tracking at field level avoids redundant serialization
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from minicode.config import MINI_CODE_DIR
from minicode.session_store import (
    SessionStoreBusyError as SessionStoreBusyError,
    SessionStoreLockError as SessionStoreLockError,
    SessionWriteConflictError as SessionWriteConflictError,
    session_store_transaction,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SESSIONS_DIR = MINI_CODE_DIR / "sessions"
AUTOSAVE_INTERVAL_SECONDS = 30  # Minimum seconds between autosaves

# Incremental save configuration
DELTA_DIR_NAME = "deltas"        # Subdirectory for delta files
FULL_SAVE_INTERVAL = 10          # Do a full save every N delta saves
MAX_DELTA_FILES = 50             # Maximum delta files before forced consolidation
MAX_PERSISTENCE_GENERATION = (2**31) - 1
MAX_DELTA_SEQUENCE = 99_999_999
_DELTA_FILE_RE = re.compile(r"delta_([0-9]{4,8})\.json")
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_TURN_ID_RE = re.compile(r"turn_[0-9a-f]{32}")
_TURN_COMMIT_FIELDS = frozenset(
    {"schemaVersion", "turnId", "userMessageIndex", "assistantMessageIndex"}
)
MAX_TURN_COMMITS = 10_000
_SESSION_STORAGE_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SessionMetadata:
    """Lightweight metadata for session listing."""
    session_id: str
    created_at: float  # Unix timestamp
    updated_at: float  # Unix timestamp
    first_message: str = ""  # Truncated first user message
    last_message: str = ""   # Truncated last message
    message_count: int = 0
    workspace: str = ""      # Working directory when session started


@dataclass
class SessionData:
    """Complete session state that can be persisted and restored."""
    session_id: str
    created_at: float
    updated_at: float
    workspace: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    transcript_entries: list[dict[str, Any]] = field(default_factory=list)
    history: list[str] = field(default_factory=list)
    permissions_summary: list[str] | dict[str, Any] = field(default_factory=dict)
    skills: list[dict[str, Any]] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    # Internal Dashboard recovery markers. Public Session projections never
    # expose these IDs or indexes.
    turn_commits: list[dict[str, Any]] = field(default_factory=list)
    metadata: SessionMetadata = field(default=None)
    
    # Incremental save tracking
    _last_saved_msg_count: int = field(default=0, repr=False)
    _last_saved_transcript_count: int = field(default=0, repr=False)
    _delta_save_count: int = field(default=0, repr=False)
    _last_full_save_hash: str = field(default="", repr=False)
    _persistence_generation: int = field(default=0, repr=False)
    _persistence_base_present: bool = field(default=False, repr=False)

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = SessionMetadata(
                session_id=self.session_id,
                created_at=self.created_at,
                updated_at=self.updated_at,
                message_count=len(self.messages),
                workspace=self.workspace,
            )

    def update_metadata(self) -> None:
        """Refresh metadata from current state."""
        self.updated_at = time.time()
        self.metadata.updated_at = self.updated_at
        self.metadata.message_count = len(self.messages)

        # Extract first user message (truncated)
        for msg in self.messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                self.metadata.first_message = content[:100]
                break

        # Extract last message (truncated) — avoid full reverse iteration
        if self.messages:
            for msg in reversed(self.messages):
                if msg.get("role") in ("user", "assistant"):
                    content = msg.get("content", "")
                    self.metadata.last_message = content[:100]
                    break
    
    @property
    def has_delta(self) -> bool:
        """Check if there are unsaved changes."""
        return (
            len(self.messages) != self._last_saved_msg_count
            or len(self.transcript_entries) != self._last_saved_transcript_count
        )
    
    def _compute_content_hash(self) -> str:
        """Compute a quick hash of message content for change detection."""
        h = hashlib.md5(usedforsecurity=False)
        for msg in self.messages[-20:]:  # Hash last 20 messages for speed
            h.update(msg.get("role", "").encode())
            content = msg.get("content", "")
            if isinstance(content, str):
                h.update(content[:500].encode())
        return h.hexdigest()


# ---------------------------------------------------------------------------
# Session file operations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DeltaCleanupResult:
    """Internal truth about a best-effort post-full-save cleanup."""

    remaining_files: tuple[str, ...]
    next_sequence: int

    @property
    def complete(self) -> bool:
        return not self.remaining_files


@dataclass(frozen=True)
class _SessionStorageRevision:
    """Caller/disk revision used to reject a stale Session writer."""

    base_present: bool
    generation: int
    next_delta_sequence: int


@dataclass(frozen=True)
class ValidatedSessionDelta:
    """Validated, generation-matching delta shared by Session/Web readers."""

    messages: list[dict[str, Any]]
    transcripts: list[dict[str, Any]]
    msg_offset: int
    transcript_offset: int
    session_state: dict[str, Any] | None
    metadata: SessionMetadata | None


@dataclass(frozen=True, slots=True)
class SessionDeletionSnapshot:
    """Content-free Session representations relevant to one deletion."""

    session: SessionData | None
    base_present: bool
    index_present: bool
    delta_count: int
    generation: int
    diagnostics: tuple[str, ...]

    @property
    def present(self) -> bool:
        return self.base_present or self.index_present or self.delta_count > 0


def validate_turn_commits(
    value: object,
    *,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate internal turn markers against exact persisted message indexes."""
    if not isinstance(value, list) or len(value) > MAX_TURN_COMMITS:
        raise TypeError("invalid Session turn commits")
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for marker in value:
        if not isinstance(marker, dict) or set(marker) != _TURN_COMMIT_FIELDS:
            raise TypeError("invalid Session turn commit")
        schema = marker.get("schemaVersion")
        turn_id = marker.get("turnId")
        user_index = marker.get("userMessageIndex")
        assistant_index = marker.get("assistantMessageIndex")
        if (
            isinstance(schema, bool)
            or schema != 1
            or not isinstance(turn_id, str)
            or _TURN_ID_RE.fullmatch(turn_id) is None
            or turn_id in seen
            or isinstance(user_index, bool)
            or not isinstance(user_index, int)
            or user_index < 0
            or isinstance(assistant_index, bool)
            or not isinstance(assistant_index, int)
            or assistant_index <= user_index
            or assistant_index >= len(messages)
            or messages[user_index].get("role") != "user"
            or not isinstance(messages[user_index].get("content"), str)
            or messages[assistant_index].get("role") != "assistant"
            or not isinstance(messages[assistant_index].get("content"), str)
            or not messages[assistant_index].get("content")
        ):
            raise TypeError("invalid Session turn commit")
        seen.add(turn_id)
        validated.append(
            {
                "schemaVersion": 1,
                "turnId": turn_id,
                "userMessageIndex": user_index,
                "assistantMessageIndex": assistant_index,
            }
        )
    return validated


def find_turn_commit(
    session: SessionData,
    turn_id: str,
) -> dict[str, Any] | None:
    """Return one already-validated internal commit marker, if present."""
    if not isinstance(turn_id, str) or _TURN_ID_RE.fullmatch(turn_id) is None:
        return None
    try:
        markers = validate_turn_commits(session.turn_commits, messages=session.messages)
    except TypeError:
        return None
    return next((marker for marker in markers if marker["turnId"] == turn_id), None)


def _prune_stale_turn_commits(session: SessionData) -> None:
    """Drop internal markers invalidated by legacy callers replacing messages."""
    coherent: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidates = session.turn_commits if isinstance(session.turn_commits, list) else []
    for candidate in candidates[:MAX_TURN_COMMITS]:
        try:
            marker = validate_turn_commits([candidate], messages=session.messages)[0]
        except (IndexError, TypeError):
            continue
        if marker["turnId"] in seen:
            continue
        seen.add(marker["turnId"])
        coherent.append(marker)
    session.turn_commits = coherent


def persistence_generation(record: dict[str, Any]) -> int:
    """Return one bounded persistence generation, treating legacy data as zero."""
    value = record.get("persistence_generation", 0)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > MAX_PERSISTENCE_GENERATION
    ):
        raise ValueError("invalid persistence generation")
    return value


def _finite_timestamp(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _validated_metadata(
    raw_metadata: object,
    *,
    session_id: str,
    workspace: str,
    created_at: float,
    updated_at: float,
    message_count: int,
) -> SessionMetadata:
    if not isinstance(raw_metadata, dict):
        raise TypeError("invalid session metadata")
    metadata = SessionMetadata(**raw_metadata)
    if (
        metadata.session_id != session_id
        or metadata.workspace != workspace
        or not _finite_timestamp(metadata.created_at)
        or not _finite_timestamp(metadata.updated_at)
        or float(metadata.created_at) != float(created_at)
        or float(metadata.updated_at) != float(updated_at)
        or not isinstance(metadata.first_message, str)
        or not isinstance(metadata.last_message, str)
        or isinstance(metadata.message_count, bool)
        or not isinstance(metadata.message_count, int)
        or metadata.message_count != message_count
    ):
        raise TypeError("invalid session metadata")
    return metadata


def validate_session_delta(
    delta: dict[str, Any],
    *,
    session_id: str,
    base_generation: int,
    current_message_count: int,
    current_transcript_count: int,
    workspace: str,
    created_at: float,
) -> ValidatedSessionDelta | None:
    """Validate one delta atomically or return None when its generation is stale."""
    if persistence_generation(delta) != base_generation:
        return None
    versioned_delta = "persistence_generation" in delta
    if (
        versioned_delta and delta.get("session_id") != session_id
    ):
        raise ValueError("delta session id mismatch")
    if "ts" in delta and not _finite_timestamp(delta.get("ts")):
        raise TypeError("invalid delta timestamp")
    if versioned_delta and "ts" not in delta:
        raise TypeError("missing delta timestamp")

    messages = delta.get("messages", [])
    transcripts = delta.get("transcripts", [])
    msg_offset = delta.get("msg_offset", current_message_count)
    transcript_offset = delta.get(
        "transcript_offset", current_transcript_count
    )
    if (
        not isinstance(messages, list)
        or any(not isinstance(item, dict) for item in messages)
        or not isinstance(transcripts, list)
        or any(not isinstance(item, dict) for item in transcripts)
        or isinstance(msg_offset, bool)
        or not isinstance(msg_offset, int)
        or msg_offset < 0
        or msg_offset > current_message_count
        or isinstance(transcript_offset, bool)
        or not isinstance(transcript_offset, int)
        or transcript_offset < 0
        or transcript_offset > current_transcript_count
    ):
        raise TypeError("invalid delta")

    raw_state = delta.get("session_state")
    metadata = None
    if versioned_delta and raw_state is None:
        raise TypeError("missing delta state")
    if raw_state is not None:
        if (
            not isinstance(raw_state, dict)
            or not isinstance(raw_state.get("history"), list)
            or not isinstance(raw_state.get("permissions_summary"), (dict, list))
            or not isinstance(raw_state.get("skills"), list)
            or not isinstance(raw_state.get("mcp_servers"), list)
            or not _finite_timestamp(raw_state.get("updated_at"))
        ):
            raise TypeError("invalid delta state")
        next_message_count = max(
            current_message_count, msg_offset + len(messages)
        )
        # The complete post-delta message list is not available to this shared
        # validator. Shape/index validation occurs below; role/content validation
        # is repeated by Session load after applying the delta.
        raw_turn_commits = raw_state.get("turn_commits", [])
        if not isinstance(raw_turn_commits, list) or len(raw_turn_commits) > MAX_TURN_COMMITS:
            raise TypeError("invalid delta state")
        seen_turn_ids: set[str] = set()
        for marker in raw_turn_commits:
            if not isinstance(marker, dict) or set(marker) != _TURN_COMMIT_FIELDS:
                raise TypeError("invalid delta state")
            schema = marker.get("schemaVersion")
            turn_id = marker.get("turnId")
            user_index = marker.get("userMessageIndex")
            assistant_index = marker.get("assistantMessageIndex")
            if (
                isinstance(schema, bool)
                or schema != 1
                or not isinstance(turn_id, str)
                or _TURN_ID_RE.fullmatch(turn_id) is None
                or turn_id in seen_turn_ids
                or isinstance(user_index, bool)
                or not isinstance(user_index, int)
                or user_index < 0
                or isinstance(assistant_index, bool)
                or not isinstance(assistant_index, int)
                or assistant_index <= user_index
                or assistant_index >= next_message_count
            ):
                raise TypeError("invalid delta state")
            seen_turn_ids.add(turn_id)
        metadata = _validated_metadata(
            raw_state.get("metadata"),
            session_id=session_id,
            workspace=workspace,
            created_at=created_at,
            updated_at=float(raw_state["updated_at"]),
            message_count=next_message_count,
        )

    return ValidatedSessionDelta(
        messages=list(messages),
        transcripts=list(transcripts),
        msg_offset=msg_offset,
        transcript_offset=transcript_offset,
        session_state=raw_state,
        metadata=metadata,
    )


def _delta_sequence(path: Path) -> int | None:
    match = _DELTA_FILE_RE.fullmatch(path.name)
    if match is None:
        return None
    sequence = int(match.group(1))
    if sequence > MAX_DELTA_SEQUENCE:
        return None
    return sequence


def _legal_delta_files(delta_dir: Path) -> list[tuple[int, Path]]:
    if not delta_dir.exists():
        return []
    files = []
    for path in delta_dir.iterdir():
        sequence = _delta_sequence(path)
        if sequence is not None and path.is_file():
            files.append((sequence, path))
    return sorted(files, key=lambda item: item[0])


def _caller_storage_revision(session: SessionData) -> _SessionStorageRevision:
    return _SessionStorageRevision(
        base_present=session._persistence_base_present,
        generation=persistence_generation(
            {"persistence_generation": session._persistence_generation}
        ),
        next_delta_sequence=session._delta_save_count,
    )


def _disk_storage_revision(session_id: str) -> _SessionStorageRevision:
    session_path = _session_file(session_id)
    try:
        base_present = session_path.exists()
        if base_present:
            parsed = json.loads(session_path.read_text(encoding="utf-8"))
            if (
                not isinstance(parsed, dict)
                or parsed.get("session_id") != session_id
            ):
                raise ValueError("invalid Session base")
            generation = persistence_generation(parsed)
        else:
            generation = 0
        delta_files = _legal_delta_files(_session_delta_dir(session_id))
        next_delta_sequence = (
            max((sequence for sequence, _ in delta_files), default=-1) + 1
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise SessionWriteConflictError("session write conflict") from error
    return _SessionStorageRevision(
        base_present=base_present,
        generation=generation,
        next_delta_sequence=next_delta_sequence,
    )


def _assert_current_storage_revision(session: SessionData) -> None:
    caller_revision = _caller_storage_revision(session)
    disk_revision = _disk_storage_revision(session.session_id)
    if caller_revision != disk_revision:
        raise SessionWriteConflictError("session write conflict")
    # The acquired-lock disk scan is authoritative even when the values match.
    session._persistence_generation = disk_revision.generation
    session._delta_save_count = disk_revision.next_delta_sequence

def _session_file(session_id: str) -> Path:
    """Return path to a session JSON file."""
    return SESSIONS_DIR / f"{session_id}.json"


def _session_delta_dir(session_id: str) -> Path:
    """Return path to a session's delta directory."""
    return SESSIONS_DIR / DELTA_DIR_NAME / session_id


def _session_index_file() -> Path:
    """Return path to the session index file."""
    return MINI_CODE_DIR / "sessions_index.json"


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically replace one UTF-8 text file under process-local coordination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _load_session_index() -> dict[str, SessionMetadata]:
    """Load the session index (lightweight metadata for all sessions)."""
    index_path = _session_index_file()
    if not index_path.exists():
        return {}
    try:
        raw = index_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return {
            sid: SessionMetadata(**meta)
            for sid, meta in data.items()
        }
    except (json.JSONDecodeError, TypeError, KeyError):
        return {}


def _save_session_index(index: dict[str, SessionMetadata]) -> None:
    """Save the session index."""
    MINI_CODE_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    serializable = {
        sid: {
            "session_id": meta.session_id,
            "created_at": meta.created_at,
            "updated_at": meta.updated_at,
            "first_message": meta.first_message,
            "last_message": meta.last_message,
            "message_count": meta.message_count,
            "workspace": meta.workspace,
        }
        for sid, meta in index.items()
    }
    _atomic_write_text(
        _session_index_file(),
        json.dumps(serializable, indent=2, allow_nan=False) + "\n",
    )


def _save_delta(session: SessionData) -> None:
    """Save only the incremental changes since last full save.
    
    Delta files contain new messages and transcript entries appended
    since the last save point. This is much cheaper than serializing
    the entire session on every autosave.
    """
    delta_dir = _session_delta_dir(session.session_id)
    delta_dir.mkdir(parents=True, exist_ok=True)
    
    # Collect new messages since last save
    new_messages = session.messages[session._last_saved_msg_count:]
    new_transcripts = session.transcript_entries[session._last_saved_transcript_count:]
    
    # Create delta entry
    delta_data: dict[str, Any] = {
        "persistence_generation": session._persistence_generation,
        "session_id": session.session_id,
        "ts": time.time(),
        "msg_offset": session._last_saved_msg_count,
        "transcript_offset": session._last_saved_transcript_count,
        "session_state": {
            "updated_at": session.updated_at,
            "history": session.history,
            "permissions_summary": session.permissions_summary,
            "skills": session.skills,
            "mcp_servers": session.mcp_servers,
            "turn_commits": session.turn_commits,
            "metadata": {
                "session_id": session.metadata.session_id,
                "created_at": session.metadata.created_at,
                "updated_at": session.metadata.updated_at,
                "first_message": session.metadata.first_message,
                "last_message": session.metadata.last_message,
                "message_count": session.metadata.message_count,
                "workspace": session.metadata.workspace,
            },
        },
    }
    if new_messages:
        delta_data["messages"] = new_messages
    if new_transcripts:
        delta_data["transcripts"] = new_transcripts
    
    # Write delta file with sequential numbering
    delta_num = session._delta_save_count
    delta_path = delta_dir / f"delta_{delta_num:04d}.json"
    _atomic_write_text(
        delta_path,
        json.dumps(delta_data, ensure_ascii=False, allow_nan=False) + "\n",
    )
    
    # Update tracking
    session._last_saved_msg_count = len(session.messages)
    session._last_saved_transcript_count = len(session.transcript_entries)
    session._delta_save_count += 1


def _consolidate_deltas(session: SessionData) -> _DeltaCleanupResult:
    """Merge all delta files into the full session file and clean up.
    
    This is called periodically to prevent unbounded delta file growth
    and to ensure the full session file stays consistent.
    """
    delta_dir = _session_delta_dir(session.session_id)
    if not delta_dir.exists():
        result = _DeltaCleanupResult(remaining_files=(), next_sequence=0)
        session._delta_save_count = result.next_sequence
        return result
    
    try:
        cleanup_candidates = _legal_delta_files(delta_dir)
    except OSError:
        result = _DeltaCleanupResult(
            remaining_files=("<delta-directory-unreadable>",),
            next_sequence=max(session._delta_save_count, FULL_SAVE_INTERVAL),
        )
        session._delta_save_count = result.next_sequence
        return result

    # Deltas are already included in the authoritative base, so clean up each
    # candidate independently and retain enough sequence state for safe retry.
    failed_files: list[str] = []
    for _, delta_file in cleanup_candidates:
        try:
            delta_file.unlink()
        except OSError:
            failed_files.append(delta_file.name)

    try:
        remaining = _legal_delta_files(delta_dir)
        remaining_files = tuple(path.name for _, path in remaining)
        next_sequence = max((sequence for sequence, _ in remaining), default=-1) + 1
    except OSError:
        remaining_files = tuple(failed_files) + ("<delta-directory-unreadable>",)
        next_sequence = max(
            (sequence for sequence, _ in cleanup_candidates),
            default=max(session._delta_save_count - 1, -1),
        ) + 1
    
    # Try to remove empty delta directory
    try:
        delta_dir.rmdir()
        # Also try to remove parent if empty
        parent = delta_dir.parent
        if parent.name == DELTA_DIR_NAME and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass
    
    result = _DeltaCleanupResult(
        remaining_files=remaining_files,
        next_sequence=next_sequence,
    )
    session._delta_save_count = result.next_sequence
    return result


def save_session(session: SessionData, force_full: bool = False) -> None:
    """Persist one Session under process-local and cross-process coordination."""
    from minicode.deletion_store import (
        DeletionFenceActive,
        DeletionStoreBusy,
        DeletionStoreUnavailable,
        conversation_write_guard,
    )

    try:
        with conversation_write_guard(
            session.workspace,
            session.session_id,
            data_dir=MINI_CODE_DIR,
        ):
            with _SESSION_STORAGE_LOCK:
                with session_store_transaction(MINI_CODE_DIR):
                    _save_session_locked(session, force_full=force_full)
    except DeletionFenceActive as error:
        raise SessionWriteConflictError("session deletion in progress") from error
    except DeletionStoreBusy as error:
        raise SessionStoreBusyError("session deletion coordination is busy") from error
    except DeletionStoreUnavailable as error:
        raise SessionStoreLockError("session store lock unavailable") from error


def _save_session_locked(session: SessionData, force_full: bool = False) -> None:
    """Persist session to disk with incremental delta support.
    
    Uses a hybrid strategy:
    - Delta saves: Only append new messages/transcripts (fast, small I/O)
    - Full saves: Serialize entire session (slower, but ensures consistency)
    - Consolidation: Merge deltas into full file periodically
    
    Args:
        session: The session to save
        force_full: Force a full save (e.g., on explicit save command)
    """
    _prune_stale_turn_commits(session)
    if (
        not isinstance(session.session_id, str)
        or not _SESSION_ID_RE.fullmatch(session.session_id)
        or session.metadata.session_id != session.session_id
    ):
        raise ValueError("invalid session id")
    _assert_current_storage_revision(session)
    session.update_metadata()
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    session_path = _session_file(session.session_id)
    
    # Decide whether to do a full save or delta save
    should_full_save = (
        force_full
        or not session_path.exists()  # First save is always full
        or session._delta_save_count >= FULL_SAVE_INTERVAL
        or session._delta_save_count >= MAX_DELTA_FILES  # Safety cap
    )
    
    if should_full_save:
        current_generation = persistence_generation(
            {"persistence_generation": session._persistence_generation}
        )
        next_generation = current_generation + 1
        if next_generation > MAX_PERSISTENCE_GENERATION:
            raise ValueError("persistence generation exhausted")
        # Full save: serialize everything
        serializable = {
            "persistence_generation": next_generation,
            "session_id": session.session_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "workspace": session.workspace,
            "messages": session.messages,
            "transcript_entries": session.transcript_entries,
            "history": session.history,
            "permissions_summary": session.permissions_summary,
            "skills": session.skills,
            "mcp_servers": session.mcp_servers,
            "turn_commits": session.turn_commits,
            "metadata": {
                "session_id": session.metadata.session_id,
                "created_at": session.metadata.created_at,
                "updated_at": session.metadata.updated_at,
                "first_message": session.metadata.first_message,
                "last_message": session.metadata.last_message,
                "message_count": session.metadata.message_count,
                "workspace": session.metadata.workspace,
            },
        }
        _atomic_write_text(
            session_path,
            json.dumps(
                serializable,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n",
        )

        # The in-memory generation changes only after the base replace succeeds.
        session._persistence_generation = next_generation
        session._persistence_base_present = True
        
        # Reset delta tracking
        session._last_saved_msg_count = len(session.messages)
        session._last_saved_transcript_count = len(session.transcript_entries)
        session._last_full_save_hash = session._compute_content_hash()
        
        # Consolidate and clean up delta files
        _consolidate_deltas(session)
    else:
        # Delta save: only append new data
        _save_delta(session)
    
    # Update index (always lightweight)
    index = _load_session_index()
    index[session.session_id] = session.metadata
    _save_session_index(index)


def load_session(session_id: str) -> SessionData | None:
    """Load a session from disk, applying any pending deltas.
    
    Loading process:
    1. Load the base session file
    2. Scan for delta files
    3. Apply deltas in order (append new messages/transcripts)
    4. Update tracking counters
    """
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        return None
    session_path = _session_file(session_id)
    if not session_path.exists():
        return None

    try:
        raw = session_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError("invalid session")
        base_generation = persistence_generation(data)
        if data.get("session_id") != session_id:
            raise ValueError("session id/path mismatch")
        created_at = data.get("created_at")
        updated_at = data.get("updated_at")
        workspace = data.get("workspace")
        messages = data.get("messages", [])
        transcript_entries = data.get("transcript_entries", [])
        history = data.get("history", [])
        permissions_summary = data.get("permissions_summary", {})
        skills = data.get("skills", [])
        mcp_servers = data.get("mcp_servers", [])
        turn_commits = data.get("turn_commits", [])
        if (
            not _finite_timestamp(created_at)
            or not _finite_timestamp(updated_at)
            or not isinstance(workspace, str)
            or not isinstance(messages, list)
            or any(not isinstance(item, dict) for item in messages)
            or not isinstance(transcript_entries, list)
            or any(not isinstance(item, dict) for item in transcript_entries)
            or not isinstance(history, list)
            or not isinstance(permissions_summary, (dict, list))
            or not isinstance(skills, list)
            or not isinstance(mcp_servers, list)
        ):
            raise TypeError("invalid session")
        turn_commits = validate_turn_commits(turn_commits, messages=messages)
        metadata = _validated_metadata(
            data.get("metadata"),
            session_id=session_id,
            workspace=workspace,
            created_at=float(created_at),
            updated_at=float(updated_at),
            message_count=len(messages),
        )
        session = SessionData(
            session_id=session_id,
            created_at=float(created_at),
            updated_at=float(updated_at),
            workspace=workspace,
            messages=list(messages),
            transcript_entries=list(transcript_entries),
            history=list(history),
            permissions_summary=(
                dict(permissions_summary)
                if isinstance(permissions_summary, dict)
                else list(permissions_summary)
            ),
            skills=list(skills),
            mcp_servers=list(mcp_servers),
            turn_commits=turn_commits,
            metadata=metadata,
            _persistence_generation=base_generation,
            _persistence_base_present=True,
        )
        
        # Apply any pending deltas
        delta_dir = _session_delta_dir(session_id)
        if delta_dir.exists():
            delta_files = _legal_delta_files(delta_dir)
            next_delta_number = 0
            for delta_number, delta_path in delta_files:
                next_delta_number = max(next_delta_number, delta_number + 1)
                try:
                    delta_raw = delta_path.read_text(encoding="utf-8")
                    delta = json.loads(delta_raw)
                    if not isinstance(delta, dict):
                        raise TypeError("invalid delta")
                    validated = validate_session_delta(
                        delta,
                        session_id=session_id,
                        base_generation=base_generation,
                        current_message_count=len(session.messages),
                        current_transcript_count=len(session.transcript_entries),
                        workspace=session.workspace,
                        created_at=session.created_at,
                    )
                    if validated is None:
                        continue
                    messages_delta = validated.messages
                    transcripts_delta = validated.transcripts
                    msg_offset = validated.msg_offset
                    transcript_offset = validated.transcript_offset
                    raw_state = validated.session_state
                    metadata = validated.metadata

                    prospective_messages = list(session.messages)
                    if messages_delta:
                        if msg_offset == len(prospective_messages):
                            prospective_messages.extend(messages_delta)
                        elif msg_offset + len(messages_delta) > len(prospective_messages):
                            overlap = len(prospective_messages) - msg_offset
                            prospective_messages.extend(messages_delta[overlap:])
                    next_turn_commits = None
                    if raw_state is not None:
                        next_turn_commits = validate_turn_commits(
                            raw_state.get("turn_commits", []),
                            messages=prospective_messages,
                        )

                    # Append delta messages at the correct offset
                    session.messages = prospective_messages
                    
                    # Append delta transcripts
                    if transcripts_delta:
                        if transcript_offset == len(session.transcript_entries):
                            session.transcript_entries.extend(transcripts_delta)
                        elif (
                            transcript_offset + len(transcripts_delta)
                            > len(session.transcript_entries)
                        ):
                            overlap = len(session.transcript_entries) - transcript_offset
                            session.transcript_entries.extend(
                                transcripts_delta[overlap:]
                            )

                    if raw_state is not None and metadata is not None:
                        session.updated_at = float(raw_state["updated_at"])
                        session.history = list(raw_state["history"])
                        permissions_summary = raw_state["permissions_summary"]
                        session.permissions_summary = (
                            dict(permissions_summary)
                            if isinstance(permissions_summary, dict)
                            else list(permissions_summary)
                        )
                        session.skills = list(raw_state["skills"])
                        session.mcp_servers = list(raw_state["mcp_servers"])
                        session.turn_commits = next_turn_commits or []
                        session.metadata = metadata
                    elif raw_state is None:
                        # Pre-Batch-6A deltas had no coherent state block. Keep
                        # the available base state but derive message metadata
                        # from the same accepted legacy delta sequence.
                        session.metadata.message_count = len(session.messages)
                        for message in session.messages:
                            if (
                                message.get("role") == "user"
                                and isinstance(message.get("content"), str)
                            ):
                                session.metadata.first_message = message["content"][:100]
                                break
                        for message in reversed(session.messages):
                            if (
                                message.get("role") in {"user", "assistant"}
                                and isinstance(message.get("content"), str)
                            ):
                                session.metadata.last_message = message["content"][:100]
                                break
                        if "ts" in delta:
                            session.updated_at = float(delta["ts"])
                            session.metadata.updated_at = session.updated_at
                except (json.JSONDecodeError, KeyError, TypeError):
                    # Skip corrupt delta files
                    continue
                except ValueError:
                    continue
            session._delta_save_count = next_delta_number
        
        # Update tracking counters
        session._last_saved_msg_count = len(session.messages)
        session._last_saved_transcript_count = len(session.transcript_entries)
        session._last_full_save_hash = session._compute_content_hash()
        
        return session
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def list_sessions() -> list[SessionMetadata]:
    """List all available sessions, newest first."""
    index = _load_session_index()
    sessions = list(index.values())
    sessions.sort(key=lambda s: s.updated_at, reverse=True)
    return sessions


def session_deletion_snapshot(
    session_id: str,
    workspace: str | Path,
    *,
    allow_unowned_orphans: bool = False,
) -> SessionDeletionSnapshot:
    """Return a bounded, content-free view of one Workspace-owned Session.

    The optional orphan allowance is reserved for resuming an already-fenced
    deletion.  A delta-only representation cannot otherwise prove Workspace
    ownership and therefore fails closed.
    """
    if not isinstance(session_id, str) or _SESSION_ID_RE.fullmatch(session_id) is None:
        raise ValueError("invalid session id")
    expected_workspace = Path(workspace).expanduser().resolve()
    diagnostics: set[str] = set()
    session: SessionData | None = None
    base_present = False
    index_present = False
    generation = 0
    owner_proven = False

    session_path = _session_file(session_id)
    try:
        base_info = os.lstat(session_path)
        if stat.S_ISLNK(base_info.st_mode) or not stat.S_ISREG(base_info.st_mode):
            diagnostics.add("session_record_invalid")
        elif base_info.st_size > 16 * 1024 * 1024:
            diagnostics.add("session_record_invalid")
        else:
            loaded = load_session(session_id)
            if loaded is None:
                diagnostics.add("session_record_invalid")
            else:
                try:
                    same_workspace = (
                        Path(loaded.workspace).expanduser().resolve()
                        == expected_workspace
                    )
                except (OSError, RuntimeError, ValueError):
                    same_workspace = False
                if same_workspace:
                    session = loaded
                    base_present = True
                    generation = loaded._persistence_generation
                    owner_proven = True
    except FileNotFoundError:
        pass
    except OSError:
        diagnostics.add("session_scan_unavailable")

    index_path = _session_index_file()
    try:
        index_info = os.lstat(index_path)
        if stat.S_ISLNK(index_info.st_mode) or not stat.S_ISREG(index_info.st_mode):
            diagnostics.add("session_index_invalid")
        elif index_info.st_size > 4 * 1024 * 1024:
            diagnostics.add("session_index_invalid")
        else:
            raw_index = json.loads(index_path.read_text(encoding="utf-8"))
            raw_metadata = raw_index.get(session_id) if isinstance(raw_index, dict) else None
            if raw_metadata is not None:
                if not isinstance(raw_metadata, dict):
                    diagnostics.add("session_index_invalid")
                else:
                    indexed_workspace = raw_metadata.get("workspace")
                    try:
                        same_workspace = (
                            isinstance(indexed_workspace, str)
                            and Path(indexed_workspace).expanduser().resolve()
                            == expected_workspace
                        )
                    except (OSError, RuntimeError, ValueError):
                        same_workspace = False
                    if same_workspace:
                        index_present = True
                        owner_proven = True
    except FileNotFoundError:
        pass
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        diagnostics.add("session_index_invalid")

    delta_count = 0
    delta_dir = _session_delta_dir(session_id)
    try:
        delta_info = os.lstat(delta_dir)
        if stat.S_ISLNK(delta_info.st_mode) or not stat.S_ISDIR(delta_info.st_mode):
            diagnostics.add("session_delta_invalid")
        else:
            with os.scandir(delta_dir) as scanner:
                for index, entry in enumerate(scanner):
                    if index >= 512:
                        diagnostics.add("session_delta_scan_limited")
                        break
                    if (
                        _DELTA_FILE_RE.fullmatch(entry.name) is None
                        or entry.is_symlink()
                        or not entry.is_file(follow_symlinks=False)
                    ):
                        diagnostics.add("session_delta_invalid")
                        continue
                    delta_count += 1
    except FileNotFoundError:
        pass
    except OSError:
        diagnostics.add("session_scan_unavailable")

    if delta_count and not owner_proven and not allow_unowned_orphans:
        diagnostics.add("session_ownership_unavailable")
        delta_count = 0
    return SessionDeletionSnapshot(
        session=session,
        base_present=base_present,
        index_present=index_present,
        delta_count=delta_count,
        generation=generation,
        diagnostics=tuple(sorted(diagnostics)),
    )


def delete_session(session_id: str) -> bool:
    """Delete a session from disk. Returns True if deleted."""
    if not isinstance(session_id, str) or _SESSION_ID_RE.fullmatch(session_id) is None:
        return False
    with _SESSION_STORAGE_LOCK:
        with session_store_transaction(MINI_CODE_DIR):
            return _delete_session_locked(session_id)


def _delete_session_locked(session_id: str) -> bool:
    """Delete a Session while the process-local storage lock is held."""
    session_path = _session_file(session_id)
    try:
        existed = False
        delta_dir = _session_delta_dir(session_id)
        try:
            delta_info = os.lstat(delta_dir)
            if stat.S_ISLNK(delta_info.st_mode) or not stat.S_ISDIR(delta_info.st_mode):
                return False
            with os.scandir(delta_dir) as scanner:
                entries = list(scanner)
            if len(entries) > 512:
                return False
            for entry in entries:
                if (
                    _DELTA_FILE_RE.fullmatch(entry.name) is None
                    or entry.is_symlink()
                    or not entry.is_file(follow_symlinks=False)
                ):
                    return False
            for entry in entries:
                Path(entry.path).unlink()
            delta_dir.rmdir()
            existed = True
        except FileNotFoundError:
            pass

        try:
            base_info = os.lstat(session_path)
            if stat.S_ISLNK(base_info.st_mode) or not stat.S_ISREG(base_info.st_mode):
                return False
            session_path.unlink()
            existed = True
        except FileNotFoundError:
            pass

        index = _load_session_index()
        if session_id in index:
            index.pop(session_id, None)
            _save_session_index(index)
            existed = True
        return existed
    except (OSError, TypeError, ValueError):
        return False


def cleanup_old_sessions(max_sessions: int = 50) -> int:
    """Remove oldest sessions beyond max_sessions limit. Returns count deleted."""
    with _SESSION_STORAGE_LOCK:
        with session_store_transaction(MINI_CODE_DIR):
            sessions = list_sessions()
            if len(sessions) <= max_sessions:
                return 0

            to_delete = sessions[max_sessions:]
            deleted = 0
            for meta in to_delete:
                if _delete_session_locked(meta.session_id):
                    deleted += 1
            return deleted


# ---------------------------------------------------------------------------
# Session creation helpers
# ---------------------------------------------------------------------------

def create_new_session(workspace: str) -> SessionData:
    """Create a new empty session."""
    now = time.time()
    session_id = uuid.uuid4().hex[:12]
    return SessionData(
        session_id=session_id,
        created_at=now,
        updated_at=now,
        workspace=workspace,
    )


def get_latest_session(workspace: str | None = None) -> SessionData | None:
    """Get the most recent session, optionally filtered by workspace."""
    sessions = list_sessions()
    for meta in sessions:
        if workspace is None or meta.workspace == workspace:
            return load_session(meta.session_id)
    return None


# ---------------------------------------------------------------------------
# Autosave manager
# ---------------------------------------------------------------------------

class AutosaveManager:
    """Manages automatic session saving with rate limiting and delta support.
    
    Uses incremental saves for autosave (fast) and full saves for
    explicit save commands (consistent).
    """

    def __init__(self, session: SessionData, interval: int = AUTOSAVE_INTERVAL_SECONDS):
        self.session = session
        self.interval = interval
        self._last_save_time = time.time()  # Initialize to current time
        self._dirty = False
        self._full_save_counter = 0

    def mark_dirty(self) -> None:
        """Mark session as needing save."""
        self._dirty = True

    def should_save(self) -> bool:
        """Check if autosave should trigger."""
        if not self._dirty:
            return False
        elapsed = time.time() - self._last_save_time
        return elapsed >= self.interval

    def save_if_needed(self) -> bool:
        """Save if dirty and interval elapsed. Uses delta saves for speed.
        
        Returns True if saved.
        """
        if self.should_save():
            return self.save_now(force_full=False)
        return False

    def save_now(self, *, force_full: bool = False) -> bool:
        """Persist dirty state immediately and retain dirty state on failure."""
        if not self._dirty:
            return False
        try:
            save_session(self.session, force_full=force_full)
        except Exception:  # persistence is retried by autosave/finalization
            return False
        self._last_save_time = time.time()
        self._dirty = False
        if force_full:
            self._full_save_counter = 0
        else:
            self._full_save_counter += 1
        return True

    def force_save(self) -> bool:
        """Attempt an immediate full save while preserving dirty on failure."""
        self._dirty = True
        return self.save_now(force_full=True)


# ---------------------------------------------------------------------------
# Session formatting for display
# ---------------------------------------------------------------------------

def _fmt_ts(ts: float, fmt: str) -> str:
    """Fast timestamp formatting using datetime (avoids repeated localtime)."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(fmt)


def format_session_list(sessions: list[SessionMetadata]) -> str:
    """Format sessions as a human-readable list."""
    if not sessions:
        return "No saved sessions found."

    lines = ["Saved sessions:", ""]
    for i, meta in enumerate(sessions, 1):
        created = _fmt_ts(meta.created_at, "%Y-%m-%d %H:%M")
        workspace = meta.workspace or "unknown"
        first_msg = meta.first_message or "(empty)"
        count = meta.message_count

        lines.append(
            f"  {i}. [{meta.session_id[:8]}] {created} - {workspace}"
        )
        lines.append(f"     Messages: {count} | First: {first_msg}")
        lines.append("")

    lines.append(f"Total: {len(sessions)} session(s)")
    return "\n".join(lines)


def format_session_resume(session: SessionData) -> str:
    """Format session info for resume confirmation."""
    created = _fmt_ts(session.created_at, "%Y-%m-%d %H:%M:%S")
    updated = _fmt_ts(session.updated_at, "%Y-%m-%d %H:%M:%S")
    return (
        f"Resuming session {session.session_id[:8]}\n"
        f"  Created: {created}\n"
        f"  Updated: {updated}\n"
        f"  Messages: {len(session.messages)}\n"
        f"  Workspace: {session.workspace}"
    )
