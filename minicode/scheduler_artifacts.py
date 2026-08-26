"""Canonical, append-only artifacts for centrally scheduled assignments.

The Scheduler owns the Store publish capability. Workers receive only an
``AssignmentArtifactChannel`` with exact immutable read keys and one reserved,
write-once result key. The legacy turn-mailbox adapter remains available while
the workflow integration migrates to the Store.
"""

from __future__ import annotations

import json
import math
import re
import threading
from dataclasses import dataclass

from minicode.subagent_mailbox import (
    SubagentMailbox,
    SubagentMailboxError,
    SubagentNote,
)


_ARTIFACT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,127}$")
MAX_SCHEDULER_ARTIFACT_BYTES = 20_000
MAX_SCHEDULER_ARTIFACTS_PER_WORKFLOW = 128
MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES = 4 * 1024 * 1024
_MAX_JSON_DEPTH = 64


class SchedulerArtifactError(SubagentMailboxError):
    """An artifact operation violated scheduler-owned authority or shape."""


@dataclass(frozen=True, slots=True)
class SchedulerArtifact:
    """Immutable canonical JSON stored under one scheduler artifact ID."""

    artifact_id: str
    content: str

    @property
    def key(self) -> str:
        return self.artifact_id

    @property
    def version(self) -> int:
        return 1

    @property
    def author(self) -> str:
        return "scheduler"


def _artifact_id(value: object) -> str:
    if not isinstance(value, str) or _ARTIFACT_ID_RE.fullmatch(value) is None:
        raise SchedulerArtifactError("artifact id is invalid")
    return value


def _validate_json_value(value: object, *, depth: int = 0) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise SchedulerArtifactError("artifact must be bounded JSON-safe data")
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise SchedulerArtifactError("artifact must be JSON-safe")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise SchedulerArtifactError("artifact JSON objects require string keys")
        for item in value.values():
            _validate_json_value(item, depth=depth + 1)
        return
    raise SchedulerArtifactError("artifact must be JSON-safe")


def _canonical_json(value: object) -> str:
    _validate_json_value(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SchedulerArtifactError("artifact must be JSON-safe") from exc
    if len(encoded.encode("utf-8")) > MAX_SCHEDULER_ARTIFACT_BYTES:
        raise SchedulerArtifactError("artifact exceeds the 20,000-byte limit")
    return encoded


def _parse_json_document(content: object) -> object:
    if not isinstance(content, str):
        raise SchedulerArtifactError("assignment artifact must be a JSON document")
    if len(content.encode("utf-8")) > MAX_SCHEDULER_ARTIFACT_BYTES:
        raise SchedulerArtifactError("artifact exceeds the 20,000-byte limit")

    def closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise SchedulerArtifactError("artifact JSON object has duplicate keys")
            result[key] = value
        return result

    def reject_constant(_value: str) -> object:
        raise SchedulerArtifactError("artifact must be JSON-safe")

    try:
        return json.loads(
            content,
            object_pairs_hook=closed_object,
            parse_constant=reject_constant,
        )
    except SchedulerArtifactError:
        raise
    except (TypeError, ValueError) as exc:
        raise SchedulerArtifactError("assignment artifact is not valid JSON") from exc


class SchedulerArtifactStore:
    """Append-only scheduler artifact authority with immutable reads."""

    def __init__(self, *, scheduler_authority: object) -> None:
        if type(scheduler_authority) is not object:
            raise ValueError("scheduler_authority must be a capability object")
        self._scheduler_authority = scheduler_authority
        self._artifacts: dict[str, SchedulerArtifact] = {}
        self._order: list[str] = []
        self._total_bytes = 0
        self._assignment_writes: dict[object, str] = {}
        self._lock = threading.RLock()

    def _append_locked(
        self,
        artifact: SchedulerArtifact,
        *,
        reservation_authority: object | None = None,
    ) -> None:
        """Append only when the final durable audit can still represent it."""
        artifact_bytes = len(artifact.content.encode("utf-8"))
        consumes_reservation = (
            reservation_authority is not None
            and self._assignment_writes.get(reservation_authority)
            == artifact.artifact_id
        )
        projected_slots = (
            len(self._order)
            + len(self._assignment_writes)
            + 1
            - int(consumes_reservation)
        )
        if projected_slots > MAX_SCHEDULER_ARTIFACTS_PER_WORKFLOW:
            raise SchedulerArtifactError(
                "workflow exceeds the 128-artifact audit limit"
            )
        if (
            self._total_bytes + artifact_bytes
            > MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES
        ):
            raise SchedulerArtifactError(
                "workflow exceeds the 4 MiB artifact audit limit"
            )
        self._artifacts[artifact.artifact_id] = artifact
        self._order.append(artifact.artifact_id)
        self._total_bytes += artifact_bytes
        if consumes_reservation:
            del self._assignment_writes[reservation_authority]

    def publish(
        self,
        artifact_id: str,
        value: object,
        *,
        authority: object,
    ) -> SchedulerArtifact:
        if authority is not self._scheduler_authority:
            raise SchedulerArtifactError("scheduler publish authority is required")
        key = _artifact_id(artifact_id)
        artifact = SchedulerArtifact(key, _canonical_json(value))
        with self._lock:
            if key in self._artifacts:
                raise SchedulerArtifactError("artifact id is write-once")
            if key in self._assignment_writes.values():
                raise SchedulerArtifactError(
                    "artifact id is reserved for an assignment write"
                )
            self._append_locked(artifact)
        return artifact

    def publish_stage_receipt(
        self,
        artifact_id: str,
        value: object,
        *,
        authority: object,
    ) -> SchedulerArtifact:
        """Seal a scheduler transition, consuming an unwritten result slot.

        This is intentionally separate from ``publish``: ordinary scheduler
        artifacts may not steal an Assignment's reserved result key. After a
        Worker returns, however, the Scheduler must be able to close a missing
        or invalid result with a canonical failure receipt so audit references
        never dangle and the late Worker capability becomes unusable.
        """
        if authority is not self._scheduler_authority:
            raise SchedulerArtifactError("scheduler publish authority is required")
        key = _artifact_id(artifact_id)
        artifact = SchedulerArtifact(key, _canonical_json(value))
        with self._lock:
            if key in self._artifacts:
                raise SchedulerArtifactError("artifact id is write-once")
            reserved_authorities = [
                write_authority
                for write_authority, reserved_key in self._assignment_writes.items()
                if reserved_key == key
            ]
            reservation_authority = (
                reserved_authorities[0] if reserved_authorities else None
            )
            self._append_locked(
                artifact,
                reservation_authority=reservation_authority,
            )
        return artifact

    def seal_stage_result(
        self,
        artifact_id: str,
        fallback: object,
        *,
        authority: object,
    ) -> SchedulerArtifact:
        """Atomically keep a returned result or seal its exact reserved key."""
        if authority is not self._scheduler_authority:
            raise SchedulerArtifactError("scheduler publish authority is required")
        key = _artifact_id(artifact_id)
        fallback_artifact = SchedulerArtifact(key, _canonical_json(fallback))
        with self._lock:
            existing = self._artifacts.get(key)
            if existing is not None:
                return existing
            reserved_authorities = [
                write_authority
                for write_authority, reserved_key in self._assignment_writes.items()
                if reserved_key == key
            ]
            reservation_authority = (
                reserved_authorities[0] if reserved_authorities else None
            )
            self._append_locked(
                fallback_artifact,
                reservation_authority=reservation_authority,
            )
            return fallback_artifact

    def read(self, artifact_id: str) -> SchedulerArtifact | None:
        key = _artifact_id(artifact_id)
        with self._lock:
            return self._artifacts.get(key)

    def list_artifact_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._order)

    def revoke_pending_assignment_writes(
        self,
        *,
        authority: object,
    ) -> tuple[str, ...]:
        """Spend all unconsumed Worker write capabilities without publishing."""
        if authority is not self._scheduler_authority:
            raise SchedulerArtifactError("scheduler publish authority is required")
        with self._lock:
            revoked = tuple(self._assignment_writes.values())
            self._assignment_writes.clear()
            return revoked

    def _revoke_assignment_write(
        self,
        artifact_id: str,
        *,
        authority: object,
    ) -> None:
        """Spend one exact Worker capability at the Worker-return boundary."""
        key = _artifact_id(artifact_id)
        with self._lock:
            reserved_key = self._assignment_writes.get(authority)
            if reserved_key is None:
                return
            if reserved_key != key:
                raise SchedulerArtifactError(
                    "assignment write authority does not match its result key"
                )
            del self._assignment_writes[authority]

    def create_assignment_channel(
        self,
        *,
        readable_keys: tuple[str, ...],
        write_key: str,
    ) -> AssignmentArtifactChannel:
        if (
            not isinstance(readable_keys, tuple)
            or any(not isinstance(key, str) for key in readable_keys)
            or len(set(readable_keys)) != len(readable_keys)
        ):
            raise SchedulerArtifactError("readable_keys must be a unique tuple")
        readable = tuple(_artifact_id(key) for key in readable_keys)
        result_key = _artifact_id(write_key)
        if result_key in readable:
            raise SchedulerArtifactError("write key cannot also be readable")
        with self._lock:
            missing = [key for key in readable if key not in self._artifacts]
            if missing:
                raise SchedulerArtifactError("readable artifact is not published")
            if result_key in self._artifacts or result_key in (
                self._assignment_writes.values()
            ):
                raise SchedulerArtifactError("write key is already reserved")
            if (
                len(self._order) + len(self._assignment_writes) + 1
                > MAX_SCHEDULER_ARTIFACTS_PER_WORKFLOW
            ):
                raise SchedulerArtifactError(
                    "workflow exceeds the 128-artifact audit limit"
                )
            write_authority = object()
            self._assignment_writes[write_authority] = result_key
        return AssignmentArtifactChannel._for_store(
            self,
            readable_keys=readable,
            write_key=result_key,
            write_authority=write_authority,
        )

    def _publish_assignment(
        self,
        artifact_id: str,
        content: object,
        *,
        authority: object,
    ) -> SchedulerArtifact:
        key = _artifact_id(artifact_id)
        with self._lock:
            if self._assignment_writes.get(authority) != key:
                raise SchedulerArtifactError("assignment write authority is invalid")
        artifact = SchedulerArtifact(key, _canonical_json(_parse_json_document(content)))
        with self._lock:
            if self._assignment_writes.get(authority) != key:
                raise SchedulerArtifactError("assignment write capability is spent")
            if key in self._artifacts:
                raise SchedulerArtifactError("artifact id is write-once")
            self._append_locked(
                artifact,
                reservation_authority=authority,
            )
        return artifact


class AssignmentArtifactChannel:
    """Least-authority artifact view for exactly one Assignment."""

    def __init__(
        self,
        mailbox: SubagentMailbox,
        *,
        readable_keys: tuple[str, ...],
        result_key: str,
        author: str,
    ) -> None:
        if not isinstance(mailbox, SubagentMailbox):
            raise TypeError("mailbox must be a SubagentMailbox")
        if (
            not isinstance(readable_keys, tuple)
            or any(not isinstance(key, str) or not key for key in readable_keys)
            or len(set(readable_keys)) != len(readable_keys)
        ):
            raise ValueError("readable_keys must be a unique tuple")
        if not isinstance(result_key, str) or not result_key:
            raise ValueError("result_key must be non-empty")
        if result_key in readable_keys:
            raise ValueError("result_key cannot also be a readable input")
        if not isinstance(author, str) or not author:
            raise ValueError("author must be non-empty")
        self._mailbox = mailbox
        self._store: SchedulerArtifactStore | None = None
        self._readable_keys = readable_keys
        self._result_key = result_key
        self._author = author
        self._write_authority: object | None = None
        self._written = False
        self._lock = threading.Lock()

    @classmethod
    def _for_store(
        cls,
        store: SchedulerArtifactStore,
        *,
        readable_keys: tuple[str, ...],
        write_key: str,
        write_authority: object,
    ) -> AssignmentArtifactChannel:
        channel = cls.__new__(cls)
        channel._mailbox = None
        channel._store = store
        channel._readable_keys = readable_keys
        channel._result_key = write_key
        channel._author = "scheduler"
        channel._write_authority = write_authority
        channel._written = False
        channel._lock = threading.Lock()
        return channel

    @property
    def readable_keys(self) -> tuple[str, ...]:
        return self._readable_keys

    @property
    def write_key(self) -> str:
        return self._result_key

    def write(
        self,
        key: str,
        content: str,
        *,
        author: str,
    ) -> SubagentNote | SchedulerArtifact:
        del author  # Identity is scheduler-owned, never caller supplied.
        if key != self._result_key:
            if self._store is not None:
                raise SchedulerArtifactError(
                    "Assignment may write only its exact write key"
                )
            raise SubagentMailboxError(
                "Assignment may write only its scheduler-owned result key"
            )
        with self._lock:
            if self._store is not None:
                if self._written:
                    raise SchedulerArtifactError("assignment write key is write-once")
                artifact = self._store._publish_assignment(
                    key,
                    content,
                    authority=self._write_authority,
                )
                self._written = True
                return artifact
            if self._written or self._mailbox is None or self._mailbox.read(key) is not None:
                raise SubagentMailboxError("Assignment result is immutable")
            note = self._mailbox.write(key, content, author=self._author)
            self._written = True
            return note

    def read(self, key: str) -> SubagentNote | SchedulerArtifact | None:
        if key not in self._readable_keys:
            if self._store is not None:
                raise SchedulerArtifactError("Artifact key is not readable")
            raise SubagentMailboxError("Artifact key is not readable")
        if self._store is not None:
            return self._store.read(key)
        if self._mailbox is None:
            return None
        return self._mailbox.read(key)

    def close(self) -> None:
        """Revoke this exact result capability once the Worker call returns."""
        store = self._store
        write_authority = self._write_authority
        if store is None or write_authority is None:
            return
        # Do not wait on the channel lock. The Store lock is the write
        # linearization point: a write still parsing must lose to this revoke,
        # while a write already appended before close is a valid result.
        store._revoke_assignment_write(
            self._result_key,
            authority=write_authority,
        )
        self._write_authority = None

    def list_keys(self) -> tuple[str, ...]:
        if self._store is not None:
            return self._readable_keys
        if self._mailbox is None:
            return ()
        return tuple(
            key for key in self._readable_keys if self._mailbox.read(key) is not None
        )

    def snapshot(self) -> tuple[SubagentNote | SchedulerArtifact, ...]:
        if self._store is not None:
            return tuple(
                artifact
                for key in self._readable_keys
                if (artifact := self._store.read(key)) is not None
            )
        if self._mailbox is None:
            return ()
        return tuple(
            note
            for key in self._readable_keys
            if (note := self._mailbox.read(key)) is not None
        )


__all__ = [
    "AssignmentArtifactChannel",
    "MAX_SCHEDULER_ARTIFACT_BYTES",
    "MAX_SCHEDULER_ARTIFACTS_PER_WORKFLOW",
    "MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES",
    "SchedulerArtifact",
    "SchedulerArtifactError",
    "SchedulerArtifactStore",
]
