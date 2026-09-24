"""Turn-scoped continuation sessions for read-only sub-agents.

A continuation lets the parent model ask a follow-up question of an
'explore'/'plan' sub-agent that already finished, reusing the child's
stored conversation instead of paying for a fresh exploration. Sessions
are per user turn (they die with the turn, exactly like the sub-agent
mailbox), bounded in count, and never leave the process.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass

# Eight retained sessions covers one turn's parallel fan-out plus
# follow-ups; older sessions evict FIFO so repeated dispatch cannot grow
# the store without bound.
MAX_RETAINED_SESSIONS = 8


@dataclass(frozen=True)
class SubagentSession:
    subagent_id: str
    agent_type: str
    messages: list[dict]  # full child transcript, in order; treat as read-only
    partial: bool


class SubagentSessionStore:
    """Thread-safe, bounded map of subagent_id -> finished conversation."""

    def __init__(self, max_sessions: int = MAX_RETAINED_SESSIONS) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, SubagentSession] = OrderedDict()
        self._max_sessions = max_sessions

    def save(self, session: SubagentSession) -> None:
        with self._lock:
            self._sessions[session.subagent_id] = session
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)

    def get(self, subagent_id: str) -> SubagentSession | None:
        with self._lock:
            return self._sessions.get(subagent_id)

    def retained_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._sessions.keys())


__all__ = (
    "MAX_RETAINED_SESSIONS",
    "SubagentSession",
    "SubagentSessionStore",
)
