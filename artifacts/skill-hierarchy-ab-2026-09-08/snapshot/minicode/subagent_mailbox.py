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

    def write(self, key: str, content: str, *, author: str) -> SubagentNote:
        if not isinstance(key, str) or _KEY_RE.fullmatch(key) is None:
            raise SubagentMailboxError("note key is invalid")
        text = str(content or "")
        if len(text) > _MAX_VALUE_CHARS:
            raise SubagentMailboxError("note content exceeds limit")
        with self._lock:
            previous = self._notes.get(key)
            version = (previous.version + 1) if previous is not None else 1
            note = SubagentNote(
                key=key,
                content=text,
                author=str(author or "subagent")[:80],
                version=version,
                written_at=time.time(),
            )
            self._notes[key] = note
            if previous is None:
                self._order.append(key)
            while len(self._order) > _MAX_NOTES:
                oldest = self._order.pop(0)
                if oldest != key:
                    self._notes.pop(oldest, None)
            return note

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


__all__ = [
    "SubagentMailbox",
    "SubagentMailboxError",
    "SubagentNote",
]
