"""Refresh executable Skill context at an owning conversation's turn boundary."""

from __future__ import annotations

import hashlib
from typing import Any

from minicode.skill_versions import observe_skill_catalog_safely
from minicode.types import ChatMessage


def _body_is_current(content: str, catalog: list[dict[str, Any]]) -> bool:
    header, separator, body = content.partition("\n\n")
    lines = header.splitlines()
    if not separator or len(lines) != 3 or not lines[0].startswith("SKILL: "):
        return False
    source = lines[1].removeprefix("SOURCE: ")
    path = lines[2].removeprefix("PATH: ")
    expected = next(
        (
            skill.get("content_digest")
            for skill in catalog
            if skill.get("source") == source and str(skill.get("path")) == path
        ),
        None,
    )
    if not expected:
        return False
    if hashlib.sha256(body.encode("utf-8")).hexdigest() == expected:
        return True
    # The Agent Loop may append its own pressure/retry nudge after the exact
    # loader output. Accept that suffix only when the original body matches.
    original, note, suffix = body.rpartition("\n\n[System note: ")
    return bool(
        note
        and suffix.endswith("]")
        and hashlib.sha256(original.encode("utf-8")).hexdigest() == expected
    )


def refresh_skill_catalog_for_turn(
    cwd: str,
    tools: Any,
    messages: list[ChatMessage] | None = None,
) -> list[ChatMessage]:
    """Publish one catalog/tool snapshot and retire outdated loaded bodies.

    Call before routing/compaction, never from an in-flight tool invocation.
    Old call/result identifiers and assistant batch grouping are retained.
    The historical load did succeed, but an invalidated result is no longer
    successful executable context for this turn or the compactor's pin set.
    Custom registries without a catalog refresher retain their own semantics.
    """
    prepared = list(messages or [])
    refresh = getattr(tools, "refresh_skill_catalog", None)
    if not callable(refresh):
        return prepared
    changed = refresh()
    if changed is None:
        return prepared
    if changed:
        observe_skill_catalog_safely(cwd, tools.get_skills())
    return retire_stale_skill_context(prepared, tools.get_skills())


def retire_stale_skill_context(
    messages: list[ChatMessage], catalog: list[dict[str, Any]]
) -> list[ChatMessage]:
    """Retire historical bodies using an already routed snapshot, with no I/O.

    Fresh per-turn runtimes use this after routing and before model execution
    or compaction; interactive owners use refresh_skill_catalog_for_turn.
    """
    prepared = list(messages)
    for index, message in enumerate(prepared):
        if (
            message.get("role") == "tool_result"
            and message.get("toolName") == "load_skill"
            and not message.get("isError")
            and not _body_is_current(str(message.get("content", "")), catalog)
        ):
            prepared[index] = {
                **message,
                "isError": True,
                "content": (
                    "[skill_context_invalidated] This historical Skill load "
                    "succeeded, but its body has been removed from current context "
                    "because the Skill changed or is no longer in the catalog. "
                    "Consult the current catalog and load the Skill again if needed."
                ),
            }
    return prepared
