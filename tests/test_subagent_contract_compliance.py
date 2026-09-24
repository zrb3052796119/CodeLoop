"""Live-model contract compliance: closing reminder recovers the typed note.

The 2026-09-25 live A/B showed real sub-agent models never call
``subagent_note_write`` before finishing (20/20 fallback). These tests pin
the two-pronged fix: an imperative result instruction, and one bounded
closing-reminder pass that gives the model a final chance to submit the
note before the result is synthesized from free text.
"""

from __future__ import annotations

import json
import re

import minicode.tools.task as task_module
from minicode.subagent_mailbox import SubagentMailbox
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext
from minicode.tools.task import task_tool

_SYSTEM_KEY_RE = re.compile(r"mailbox key `(subagent_result_[a-z0-9_]+)`")

_NOTE_JSON = json.dumps(
    {
        "resultVersion": 1,
        "summary": "billing/invoices.py owns invoice creation.",
        "files": [{"path": "billing/invoices.py", "action": "read"}],
        "risks": [],
        "verification": {"status": "not_run", "checks": []},
    }
)


# ---------------------------------------------------------------------------
# Task 1: imperative result instruction
# ---------------------------------------------------------------------------


def test_result_instruction_is_imperative_and_shape_stable() -> None:
    text = task_module._subagent_result_instruction("sub_result_x")
    assert "MANDATORY" in text
    assert "Before your final response" in text
    assert "non-compliant" in text
    # The JSON shape block is frozen; only the framing around it changed.
    assert '"resultVersion":1,"summary":' in text
    assert '"files":[{"path":"relative/or/absolute/path","action":' in text


# ---------------------------------------------------------------------------
# Task 2: closing-reminder remediation pass
# ---------------------------------------------------------------------------


def _submit_note_from_system(kwargs) -> None:
    match = _SYSTEM_KEY_RE.search(kwargs["messages"][0]["content"])
    assert match is not None, kwargs["messages"][0]["content"][:400]
    kwargs["subagent_mailbox"].write(
        match.group(1), _NOTE_JSON, author="explore"
    )


def _make_dispatch(calls: list[dict], final_text, *, submit=False, partial=False):
    def dispatch(**kwargs):
        calls.append(kwargs)
        if submit:
            _submit_note_from_system(kwargs)
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        closing = {"role": "assistant", "content": final_text}
        if partial:
            closing["_partial"] = True
        return [*kwargs["messages"], closing]

    return dispatch


def _run_task(tmp_path, mailbox):
    context = ToolContext(
        cwd=str(tmp_path),
        _runtime={"model": "fake"},
        _subagent_mailbox=mailbox,
    )
    return task_tool.run(
        {
            "description": "map billing",
            "prompt": "Map the billing module.",
            "agent_type": "explore",
        },
        context,
    )


def test_missing_note_triggers_reminder_and_recovers(monkeypatch, tmp_path):
    mailbox = SubagentMailbox()
    calls: list[dict] = []
    state = {"submitted": False}

    def dispatch(**kwargs):
        calls.append(kwargs)
        is_remediation = len(calls) == 2
        if is_remediation:
            _submit_note_from_system(kwargs)
            state["submitted"] = True
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        closing_text = (
            "<final> note submitted" if is_remediation else "<final> survey done"
        )
        return [*kwargs["messages"], {"role": "assistant", "content": closing_text}]

    monkeypatch.setattr(task_module, "run_agent_turn", dispatch)
    result = _run_task(tmp_path, mailbox)

    assert result.ok, result.output
    assert state["submitted"] is True
    assert len(calls) == 2
    reminder_messages = calls[1]["messages"]
    assert any(
        message.get("role") == "user"
        and "subagent_note_write" in message.get("content", "")
        for message in reminder_messages
    )
    # First-round history is preserved in the remediation seeding.
    assert any(
        message.get("content") == "Map the billing module."
        for message in reminder_messages
    )
    assert "=== SUBAGENT RESULT v1 ===" in result.output
    assert "recovered via closing reminder" in result.output


def test_model_refuses_reminder(monkeypatch, tmp_path):
    mailbox = SubagentMailbox()
    calls: list[dict] = []

    def dispatch(**kwargs):
        calls.append(kwargs)
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [
            *kwargs["messages"],
            {"role": "assistant", "content": "<final> no note"},
        ]

    monkeypatch.setattr(task_module, "run_agent_turn", dispatch)
    result = _run_task(tmp_path, mailbox)

    assert result.ok
    assert len(calls) == 2
    assert "fallback" in result.output
    assert "despite one closing reminder" in result.output


def test_partial_run_skips_remediation(monkeypatch, tmp_path):
    mailbox = SubagentMailbox()
    calls: list[dict] = []
    dispatch = _make_dispatch(
        calls, "PARTIAL SURVEY: ran out of turns.", partial=True
    )
    monkeypatch.setattr(task_module, "run_agent_turn", dispatch)
    result = _run_task(tmp_path, mailbox)

    assert result.ok
    assert len(calls) == 1
    assert "PARTIAL" in result.output


def test_existing_note_skips_remediation(monkeypatch, tmp_path):
    mailbox = SubagentMailbox()
    calls: list[dict] = []
    dispatch = _make_dispatch(calls, "<final> done with note", submit=True)
    monkeypatch.setattr(task_module, "run_agent_turn", dispatch)
    result = _run_task(tmp_path, mailbox)

    assert result.ok
    assert len(calls) == 1
    assert "reminder" not in result.output


def test_no_mailbox_skips_remediation(monkeypatch, tmp_path):
    calls: list[dict] = []
    dispatch = _make_dispatch(calls, "<final> done")
    monkeypatch.setattr(task_module, "run_agent_turn", dispatch)
    context = ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"})
    result = task_tool.run(
        {
            "description": "map billing",
            "prompt": "Map the billing module.",
            "agent_type": "explore",
        },
        context,
    )

    assert result.ok
    assert len(calls) == 1
