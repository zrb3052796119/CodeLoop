"""Continuation sessions for read-only sub-agents (task tool)."""

import pytest

from minicode.subagent_sessions import (
    MAX_RETAINED_SESSIONS,
    SubagentSession,
    SubagentSessionStore,
)


def _session(n: int, agent_type: str = "explore") -> SubagentSession:
    return SubagentSession(
        subagent_id=f"sub_{n:032x}",
        agent_type=agent_type,
        messages=[
            {"role": "user", "content": f"q{n}"},
            {"role": "assistant", "content": f"a{n}"},
        ],
        partial=False,
    )


def test_store_roundtrip() -> None:
    store = SubagentSessionStore()
    session = _session(1)
    store.save(session)
    assert store.get(session.subagent_id) is session
    assert store.retained_ids() == (session.subagent_id,)


def test_store_evicts_oldest_beyond_cap() -> None:
    store = SubagentSessionStore()
    first = _session(1)
    for n in range(1, MAX_RETAINED_SESSIONS + 2):
        store.save(_session(n))
    assert store.get(first.subagent_id) is None
    assert len(store.retained_ids()) == MAX_RETAINED_SESSIONS


def test_store_rejects_non_positive_cap() -> None:
    with pytest.raises(ValueError):
        SubagentSessionStore(max_sessions=0)
