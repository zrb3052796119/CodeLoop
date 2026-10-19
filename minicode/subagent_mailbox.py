"""Bounded shared note mailbox for sub-agent collaboration.

One mailbox is created per top-level Agent turn and passed through every
``ToolContext``. Direct ``task`` sub-agents and workflow phases therefore read
and write the same process-local notes without sharing raw model context.

The mailbox is intentionally ephemeral: notes live for exactly one Turn and
are bounded in count and bytes. Durable cross-Run collaboration belongs in
Memory or the sub-agent Run journal, not here.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any


_MAX_NOTES = 100
_MAX_KEY_CHARS = 80
_MAX_VALUE_CHARS = 20_000
_KEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,79}$")


class SubagentMailboxError(RuntimeError):
    """Base error for bounded sub-agent note operations."""


@dataclass(frozen=True, slots=True)
class SubagentNote:
    key: str
    content: str
    author: str
    version: int
    written_at: float

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "content": self.content,
            "author": self.author,
            "version": self.version,
            "writtenAt": self.written_at,
        }


@dataclass
class SubagentMailbox:
    """Thread-safe, process-local, bounded note space shared by one Turn."""

    _notes: dict[str, SubagentNote] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)
    _lock: Any = field(default_factory=threading.RLock)
    # The host reserves result keys with an unforgeable process-local token.
    # None means sealed: shared-note writes must never reclaim that key.
    _result_slots: dict[str, object | None] = field(default_factory=dict)

    @staticmethod
    def _validated_content(key: str, content: str) -> str:
        if not isinstance(key, str) or _KEY_RE.fullmatch(key) is None:
            raise SubagentMailboxError("note key is invalid")
        text = str(content or "")
        if len(text) > _MAX_VALUE_CHARS:
            raise SubagentMailboxError("note content exceeds limit")
        return text

    def _write_locked(self, key: str, text: str, *, author: str) -> SubagentNote:
        previous = self._notes.get(key)
        if previous is None and len(self._order) >= _MAX_NOTES:
            # A sibling can churn shared notes, but cannot evict an in-flight
            # result or a sealed result awaiting a parent read.
            oldest = next(
                (item for item in self._order if item not in self._result_slots),
                None,
            )
            if oldest is None:
                raise SubagentMailboxError("mailbox is full of protected results")
            self._order.remove(oldest)
            self._notes.pop(oldest, None)
        note = SubagentNote(
            key=key,
            content=text,
            author=str(author or "subagent")[:80],
            version=(previous.version + 1) if previous is not None else 1,
            written_at=time.time(),
        )
        self._notes[key] = note
        if previous is None:
            self._order.append(key)
        return note

    def write(self, key: str, content: str, *, author: str) -> SubagentNote:
        text = self._validated_content(key, content)
        with self._lock:
            if key in self._result_slots:
                raise SubagentMailboxError(
                    "formal result key requires its owning channel"
                )
            return self._write_locked(key, text, author=author)

    def create_result_channel(
        self, result_key: str, *, author: str,
    ) -> SubagentResultChannel:
        """Reserve one child result and its detail slot for the host-owned run."""
        self._validated_content(result_key, "")
        detail_key = result_key + "_detail"
        self._validated_content(detail_key, "")
        if not isinstance(author, str) or not author.strip():
            raise SubagentMailboxError("result channel author is invalid")
        with self._lock:
            keys = (result_key, detail_key)
            if any(key in self._result_slots or key in self._notes for key in keys):
                raise SubagentMailboxError("formal result key is already in use")
            if len(self._result_slots) + len(keys) > _MAX_NOTES:
                raise SubagentMailboxError("formal result slot limit exceeded")
            authority = object()
            for key in keys:
                self._result_slots[key] = authority
            return SubagentResultChannel(
                self, keys=keys, authority=authority, author=author,
            )

    def read(self, key: str) -> SubagentNote | None:
        if not isinstance(key, str) or _KEY_RE.fullmatch(key) is None:
            raise SubagentMailboxError("note key is invalid")
        with self._lock:
            return self._notes.get(key)

    def list_keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._order)

    def snapshot(self) -> tuple[SubagentNote, ...]:
        with self._lock:
            return tuple(self._notes[key] for key in self._order if key in self._notes)


class SubagentResultChannel:
    """Own result writes plus ordinary shared collaboration notes.

    The child may correct its own result until close. Authorship comes from
    the host reservation, never from the author argument on a model tool.
    Closing seals both result keys and prevents late shared-note writes.
    """

    def __init__(
        self,
        mailbox: SubagentMailbox,
        *,
        keys: tuple[str, str],
        authority: object,
        author: str,
    ) -> None:
        self._mailbox = mailbox
        self._keys = keys
        self._authority = authority
        self._author = author
        self._closed = False

    def write(self, key: str, content: str, *, author: str) -> SubagentNote:
        del author
        text = self._mailbox._validated_content(key, content)
        with self._mailbox._lock:
            if self._closed:
                raise SubagentMailboxError("result channel is closed")
            if key in self._keys:
                if self._mailbox._result_slots.get(key) is not self._authority:
                    raise SubagentMailboxError("result channel authority is revoked")
                return self._mailbox._write_locked(key, text, author=self._author)
            return self._mailbox.write(key, content, author=self._author)

    def read(self, key: str) -> SubagentNote | None:
        return self._mailbox.read(key)

    def list_keys(self) -> tuple[str, ...]:
        return self._mailbox.list_keys()

    def snapshot(self) -> tuple[SubagentNote, ...]:
        return self._mailbox.snapshot()

    def close(self) -> None:
        with self._mailbox._lock:
            if self._closed:
                return
            self._closed = True
            for key in self._keys:
                if self._mailbox._result_slots.get(key) is self._authority:
                    self._mailbox._result_slots[key] = None


__all__ = [
    "SubagentMailbox",
    "SubagentMailboxError",
    "SubagentNote",
    "SubagentResultChannel",
]
