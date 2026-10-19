"""Explicit note-passing tools for sub-agent collaboration.

These tools are the only structured communication channel between isolated
sub-agents. They write to the Turn's bounded :class:`SubagentMailbox` rather
than to the filesystem, and never expose another sub-agent's model context.
"""

from __future__ import annotations

import hashlib
import json
import re

from minicode.subagent_mailbox import (
    SubagentMailbox,
    SubagentMailboxError,
)
from minicode.task_deadline import raise_task_deadline
from minicode.tooling import ToolDefinition, ToolExecutionAbandoned, ToolResult

_KEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,79}$")
_MAX_NOTE_CHARS = 20_000
_MAX_PAGE_CHARS = 4_000


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
    from minicode.executor_result_submission import ExecutorResultChannel

    return ToolResult(
        ok=True,
        _implementation_submitted=isinstance(mailbox, ExecutorResultChannel),
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


def _read_checkpoint(context, mailbox) -> None:
    abandoned = getattr(context, "_tool_abandoned", None)
    if abandoned is not None and abandoned.is_set():
        raise ToolExecutionAbandoned("note reader owner was abandoned")
    raise_task_deadline(context)
    if getattr(mailbox, "_closed", False):
        raise SubagentMailboxError("note reader channel is closed")
    if getattr(mailbox, "_store", None) is not None:
        # AssignmentArtifactChannel.read is also used by host audit after the
        # worker returns. The model-facing tool must not reuse that host path
        # once the exact Assignment reservation has been closed or revoked.
        from minicode.reviewer_result_submission import _channel_live

        if not _channel_live(mailbox):
            raise SubagentMailboxError("note reader Assignment is closed or revoked")


def _note_read(_input: dict, context) -> ToolResult:
    mailbox = _mailbox(context)
    if mailbox is None:
        return ToolResult(
            ok=False,
            output="error[subagent_mailbox_unavailable]: no shared mailbox in this turn",
        )
    try:
        _read_checkpoint(context, mailbox)
        _input = _validate_read(_input)
        note = mailbox.read(_input["key"])
    except SubagentMailboxError as error:
        return ToolResult(ok=False, output=f"error[invalid_note]: {error}")
    if note is None:
        return ToolResult(
            ok=False,
            output=f"error[note_missing]: {_input['key']}",
        )
    explicit_page = "offset" in _input or "limit" in _input
    if not explicit_page and len(note.content) <= _MAX_NOTE_CHARS:
        output = (
            f"[note {note.key} v{note.version} by {note.author}]\n"
            + note.content
        )
    else:
        offset, limit = _input.get("offset", 0), _input.get("limit", _MAX_PAGE_CHARS)
        if offset > len(note.content):
            return ToolResult(ok=False, output="error[invalid_note_page]: offset exceeds note length")
        end = min(offset + limit, len(note.content))
        output = json.dumps({"resultType": "SubagentNotePage.v1", "key": note.key,
            "version": note.version, "author": note.author, "offset": offset,
            "offsetUnit": "unicode_characters", "totalChars": len(note.content),
            "digest": "sha256:" + hashlib.sha256(note.content.encode("utf-8")).hexdigest(),
            "text": note.content[offset:end], "nextOffset": end if end < len(note.content) else None,
            "nextRead": {"key": note.key, "offset": end, "limit": limit} if end < len(note.content) else None},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        _read_checkpoint(context, mailbox)
    except SubagentMailboxError as error:
        return ToolResult(ok=False, output=f"error[invalid_note]: {error}")
    return ToolResult(ok=True, output=output)


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
    if not set(value) <= {"key", "offset", "limit"}:
        raise ValueError("read accepts only key, offset and limit")
    key = value.get("key")
    if not isinstance(key, str) or _KEY_RE.fullmatch(key) is None:
        raise ValueError("key must match [A-Za-z0-9_][A-Za-z0-9_.:-]{0,79}")
    for field, minimum in (("offset", 0), ("limit", 1)):
        if field in value and (type(value[field]) is not int or value[field] < minimum):
            raise ValueError(f"{field} must be an integer >= {minimum}")
    if value.get("limit", 1) > _MAX_PAGE_CHARS:
        raise ValueError("limit must be at most 4000 Unicode characters")
    return dict(value)


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
        "Read one already-authorized note from the current turn's shared sub-agent mailbox. "
        "Optional offset and limit select Unicode characters (limit at most 4000). "
        "Small notes without paging arguments retain their original text format. "
        "Long notes automatically return a JSON first page with key, version, totalChars, digest, "
        "text, nextOffset and nextRead. Follow nextRead until null, concatenate text, and check "
        "the version/digest stay unchanged. Paging does not grant access to other keys or closed Assignments."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "key": {"type": "string", "description": "Note key to read"},
            "offset": {"type": "integer", "minimum": 0, "description": "Unicode-character offset; defaults to zero."},
            "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_PAGE_CHARS,
                      "description": "Maximum Unicode characters in one page; defaults to 4000 when paging."},
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
