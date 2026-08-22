"""Explicit note-passing tools for sub-agent collaboration.

These tools are the only structured communication channel between isolated
sub-agents. They write to the Turn's bounded :class:`SubagentMailbox` rather
than to the filesystem, and never expose another sub-agent's model context.
"""

from __future__ import annotations

import json
import re

from minicode.subagent_mailbox import (
    SubagentMailbox,
    SubagentMailboxError,
)
from minicode.tooling import ToolDefinition, ToolResult

_KEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,79}$")
_MAX_NOTE_CHARS = 20_000


def _mailbox(context) -> SubagentMailbox | None:
    return getattr(context, "_subagent_mailbox", None)


def _note_write(_input: dict, context) -> ToolResult:
    mailbox = _mailbox(context)
    if mailbox is None:
        return ToolResult(
            ok=False,
            output="error[subagent_mailbox_unavailable]: no shared mailbox in this turn",
        )
    try:
        note = mailbox.write(
            _input["key"],
            _input["content"],
            author=str(getattr(context, "_agent_depth", 0)),
        )
    except SubagentMailboxError as error:
        return ToolResult(ok=False, output=f"error[invalid_note]: {error}")
    return ToolResult(
        ok=True,
        output=json.dumps(
            {
                "key": note.key,
                "version": note.version,
                "chars": len(note.content),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def _note_read(_input: dict, context) -> ToolResult:
    mailbox = _mailbox(context)
    if mailbox is None:
        return ToolResult(
            ok=False,
            output="error[subagent_mailbox_unavailable]: no shared mailbox in this turn",
        )
    try:
        note = mailbox.read(_input["key"])
    except SubagentMailboxError as error:
        return ToolResult(ok=False, output=f"error[invalid_note]: {error}")
    if note is None:
        return ToolResult(
            ok=False,
            output=f"error[note_missing]: {_input['key']}",
        )
    return ToolResult(
        ok=True,
        output=(
            f"[note {note.key} v{note.version} by {note.author}]\n"
            + note.content
        ),
    )


def _note_list(_input: dict, context) -> ToolResult:
    del _input
    mailbox = _mailbox(context)
    if mailbox is None:
        return ToolResult(
            ok=False,
            output="error[subagent_mailbox_unavailable]: no shared mailbox in this turn",
        )
    return ToolResult(
        ok=True,
        output=json.dumps(list(mailbox.list_keys()), ensure_ascii=False),
    )


def _validate_write(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("input must be an object")
    key = value.get("key")
    if not isinstance(key, str) or _KEY_RE.fullmatch(key) is None:
        raise ValueError("key must match [A-Za-z0-9_][A-Za-z0-9_.:-]{0,79}")
    content = value.get("content")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if len(content) > _MAX_NOTE_CHARS:
        raise ValueError("content exceeds 20000 characters")
    return {"key": key, "content": content}


def _validate_read(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("input must be an object")
    key = value.get("key")
    if not isinstance(key, str) or _KEY_RE.fullmatch(key) is None:
        raise ValueError("key must match [A-Za-z0-9_][A-Za-z0-9_.:-]{0,79}")
    return {"key": key}


def _validate_list(value: object) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("input must be an object")
    return {}


subagent_note_write_tool = ToolDefinition(
    name="subagent_note_write",
    description=(
        "Write a bounded note to the current turn's shared sub-agent mailbox. "
        "Use this to hand a plan, finding, or result to another isolated "
        "sub-agent without putting raw context in a prompt."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Stable note key, e.g. workflow_plan or workflow_result",
            },
            "content": {
                "type": "string",
                "description": "Note content, at most 20000 characters",
            },
        },
        "required": ["key", "content"],
    },
    validator=_validate_write,
    run=_note_write,
)

subagent_note_read_tool = ToolDefinition(
    name="subagent_note_read",
    description=(
        "Read one note from the current turn's shared sub-agent mailbox."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Note key to read"},
        },
        "required": ["key"],
    },
    validator=_validate_read,
    run=_note_read,
)

subagent_note_list_tool = ToolDefinition(
    name="subagent_note_list",
    description="List keys in the current turn's shared sub-agent mailbox.",
    input_schema={"type": "object", "properties": {}},
    validator=_validate_list,
    run=_note_list,
)

__all__ = [
    "subagent_note_list_tool",
    "subagent_note_read_tool",
    "subagent_note_write_tool",
]
