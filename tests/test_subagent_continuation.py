"""Continuation sessions for read-only sub-agents (task tool)."""

from dataclasses import fields
import re

import pytest

import minicode.tools.task as task_module
from minicode.agent_budget import AgentTurnBudget
from minicode.permissions import PermissionManager
from minicode.subagent_lifecycle import AsyncSubagentLifecycle
from minicode.subagent_mailbox import SubagentMailbox
from minicode.turn_cancellation import TurnCancellationToken
from minicode.subagent_sessions import (
    MAX_RETAINED_SESSIONS,
    SubagentSession,
    SubagentSessionStore,
)
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext
from minicode.tools.task import task_tool


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


def test_context_session_binding_is_read_only_and_preserves_other_capabilities(tmp_path):
    original_store = SubagentSessionStore()
    next_store = SubagentSessionStore()
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    context = ToolContext(
        cwd=str(tmp_path), permissions=PermissionManager(str(tmp_path)),
        _agent_budget=AgentTurnBudget(max_model_calls=4, max_total_tokens=1000),
        _cancellation_token=TurnCancellationToken("turn_" + "c" * 32),
        _subagent_mailbox=SubagentMailbox(), _subagent_lifecycle=lifecycle,
        _subagent_sessions=original_store,
    )
    try:
        bound = context.with_subagent_sessions(next_store)
        assert bound is not context
        assert context.subagent_sessions is original_store
        assert bound.subagent_sessions is next_store
        assert not hasattr(bound, "__dict__"), "ToolContext must remain slotted"
        for item in fields(context):
            if item.name != "_subagent_sessions":
                assert getattr(bound, item.name) is getattr(context, item.name), item.name
        reservation = bound._agent_budget.reserve_model_call(7)
        bound._agent_budget.record_model_call(
            input_tokens=7, output_tokens=3, reservation=reservation,
        )
        assert context._agent_budget.snapshot().used_total_tokens == 10
        assert context._agent_budget.snapshot().used_model_calls == 1
        disabled = bound.with_subagent_sessions(None)
        assert disabled.subagent_sessions is None
        assert bound.subagent_sessions is next_store
        with pytest.raises(AttributeError):
            bound.subagent_sessions = original_store
    finally:
        lifecycle.shutdown()


# ---------------------------------------------------------------------------
# Dispatch: validation and fail-closed error paths
# ---------------------------------------------------------------------------


def test_validate_rejects_continue_with_spawn_action() -> None:
    with pytest.raises(ValueError, match="action='run'"):
        task_module._validate(
            {
                "action": "spawn",
                "description": "auth follow-up",
                "prompt": "which function issues the token?",
                "agent_type": "explore",
                "continue_subagent_id": "sub_" + "1" * 32,
            }
        )


def test_validate_rejects_continue_with_workflow_type() -> None:
    with pytest.raises(ValueError, match="read-only"):
        task_module._validate(
            {
                "description": "change things",
                "prompt": "edit the file",
                "agent_type": "workflow",
                "write_paths": ["a.py"],
                "verification_commands": [["python3", "-m", "compileall", "a.py"]],
                "continue_subagent_id": "sub_" + "1" * 32,
            }
        )


def test_validate_rejects_malformed_continue_id() -> None:
    with pytest.raises(ValueError, match="sub_ id"):
        task_module._validate(
            {
                "description": "auth follow-up",
                "prompt": "which function issues the token?",
                "agent_type": "explore",
                "continue_subagent_id": "not-an-id",
            }
        )


def test_run_continuation_error_lists_retained_sessions(tmp_path):
    store = SubagentSessionStore()
    result = task_module._run(
        {
            "description": "auth follow-up",
            "prompt": "which function issues the token?",
            "agent_type": "explore",
            "continue_subagent_id": "sub_" + "9" * 32,
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _subagent_sessions=store,
        ),
    )
    assert not result.ok
    assert "unknown_continue_subagent_id" in result.output
    assert "none" in result.output


def test_run_type_mismatch_fails_closed(tmp_path):
    store = SubagentSessionStore()
    store.save(
        SubagentSession(
            subagent_id="sub_" + "1" * 32,
            agent_type="explore",
            messages=[{"role": "user", "content": "find auth code"}],
            partial=False,
        )
    )
    result = task_module._run(
        {
            "description": "auth follow-up",
            "prompt": "which function issues the token?",
            "agent_type": "plan",
            "continue_subagent_id": "sub_" + "1" * 32,
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _subagent_sessions=store,
        ),
    )
    assert not result.ok
    assert "subagent_type_mismatch" in result.output


# ---------------------------------------------------------------------------
# Dispatch: seeding, saving, and result advertisement
# ---------------------------------------------------------------------------


def _fake_successful_turn(captured):
    def fake_run_agent_turn(**kwargs):
        captured["messages"] = kwargs["messages"]
        kwargs["outcome_capture"].record(
            canonicalize_task_outcome("success", 0)
        )
        return list(kwargs["messages"]) + [
            {"role": "assistant", "content": "follow-up answer"}
        ]

    return fake_run_agent_turn


def test_run_seeds_history_from_stored_session(monkeypatch, tmp_path):
    store = SubagentSessionStore()
    store.save(
        SubagentSession(
            subagent_id="sub_" + "1" * 32,
            agent_type="explore",
            messages=[
                {"role": "system", "content": "old-system"},
                {"role": "user", "content": "find auth code"},
                {"role": "assistant", "content": "auth.py handles it"},
            ],
            partial=False,
        )
    )
    captured: dict = {}
    monkeypatch.setattr(
        task_module, "run_agent_turn", _fake_successful_turn(captured)
    )

    result = task_tool.run(
        {
            "description": "auth follow-up",
            "prompt": "which function issues the token?",
            "agent_type": "explore",
            "continue_subagent_id": "sub_" + "1" * 32,
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _subagent_sessions=store,
        ),
    )
    assert result.ok, result.output
    msgs = captured["messages"]
    # New system prompt (fresh result key) replaces the stored one.
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] != "old-system"
    # Prior conversation is preserved.
    assert {"role": "user", "content": "find auth code"} in msgs
    assert {"role": "assistant", "content": "auth.py handles it"} in msgs
    # The follow-up is the last user message and carries the marker.
    assert msgs[-1]["role"] == "user"
    assert "continuation of sub-agent" in msgs[-1]["content"]
    assert "which function issues the token?" in msgs[-1]["content"]


def test_successful_run_saves_new_session_and_advertises_it(
    monkeypatch, tmp_path
):
    store = SubagentSessionStore()
    captured: dict = {}
    monkeypatch.setattr(
        task_module, "run_agent_turn", _fake_successful_turn(captured)
    )

    result = task_tool.run(
        {
            "description": "explore auth",
            "prompt": "find auth code",
            "agent_type": "explore",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _subagent_sessions=store,
        ),
    )
    assert result.ok, result.output
    match = re.search(r"continue_subagent_id='(sub_[0-9a-f]{32})'", result.output)
    assert match is not None, result.output
    new_id = match.group(1)
    saved = store.get(new_id)
    assert saved is not None
    assert saved.agent_type == "explore"
    assert saved.partial is False
    assert {"role": "user", "content": "find auth code"} in saved.messages
    assert {"role": "assistant", "content": "follow-up answer"} in saved.messages


def test_continuation_without_store_fails_closed(monkeypatch, tmp_path):
    result = task_module._run(
        {
            "description": "auth follow-up",
            "prompt": "which function issues the token?",
            "agent_type": "explore",
            "continue_subagent_id": "sub_" + "1" * 32,
        },
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
    )
    assert not result.ok
    assert "subagent_continuation_unavailable" in result.output
