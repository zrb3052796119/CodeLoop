"""Project-defined read-only sub-agent profiles loaded from Markdown files.

A profile is one ``.md`` file under ``<project>/.mini-code/agents``: a
closed-key frontmatter block (name, description, max_turns, allowed_tools)
followed by a body that becomes the agent type's system prompt. Loading is
fail-closed per file — any malformed file is rejected with a readable
error and never silently narrows the declared tool set or turns.

Custom types are always read-only and synchronous: ``allowed_tools`` must
be a non-empty subset of the built-in read-only whitelist, and reserved
built-in names cannot be overridden, so project files can never widen
write authority or weaken the workflow gate.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from minicode.subagent_types import AGENT_TYPES, READ_ONLY_TOOL_WHITELIST

AGENT_NAME_RE = re.compile(r"[a-z][a-z0-9-]{1,31}")
RESERVED_AGENT_NAMES = frozenset({"explore", "plan", "general", "workflow"})
_FRONTMATTER_KEYS = ("name", "description", "max_turns", "allowed_tools")
_MAX_TURNS_CEILING = 200
_MAX_DESCRIPTION_CHARS = 400
_PROFILE_DIR = (".mini-code", "agents")

_cache_lock = threading.Lock()
_cache: dict[tuple, tuple[dict[str, dict], list[str]]] = {}


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a closed-key frontmatter block from the prompt body.

    The block opens with a ``---`` line, continues with ``key: value``
    lines only, and closes with another ``---`` line. Anything else — a
    missing block, a line without a colon, an unknown key, a duplicated
    key — is a ``ValueError`` so the caller can fail that file closed.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("file must open with a '---' frontmatter block")
    fields: dict[str, str] = {}
    closing_index = None
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            closing_index = index
            break
        if ":" not in line:
            raise ValueError(f"malformed frontmatter line {index + 1}: {line!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        if key not in _FRONTMATTER_KEYS:
            raise ValueError(f"unknown frontmatter key {key!r}")
        if key in fields:
            raise ValueError(f"duplicate frontmatter key {key!r}")
        fields[key] = value.strip()
    if closing_index is None:
        raise ValueError("frontmatter block is not closed with '---'")
    missing = [key for key in _FRONTMATTER_KEYS if not fields.get(key)]
    if missing:
        raise ValueError("missing frontmatter keys: " + ", ".join(missing))
    body = "\n".join(lines[closing_index + 1 :]).strip()
    return fields, body


def _directory_signature(directory: Path) -> tuple:
    return tuple(
        (path.name, path.stat().st_mtime_ns, path.stat().st_size)
        for path in sorted(directory.glob("*.md"))
    )


def load_agent_profiles(
    project_root: str | Path,
) -> tuple[dict[str, dict], list[str]]:
    """Load valid project profiles plus per-file rejection reasons.

    The result is cached on the directory's (name, mtime_ns, size)
    signature so a long-running process picks up edits between turns
    without re-reading unchanged files on every dispatch.
    """
    root = Path(project_root)
    directory = root.joinpath(*_PROFILE_DIR)
    if not directory.is_dir():
        return ({}, [])
    key = (str(root), _directory_signature(directory))
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached

    profiles: dict[str, dict] = {}
    errors: list[str] = []
    for path in sorted(directory.glob("*.md")):
        try:
            fields, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            name = fields["name"]
            if AGENT_NAME_RE.fullmatch(name) is None:
                raise ValueError(f"invalid agent name {name!r}")
            if name in RESERVED_AGENT_NAMES:
                raise ValueError(
                    f"{name!r} is a reserved built-in and cannot be overridden"
                )
            tools = frozenset(
                tool
                for tool in (
                    token.strip()
                    for token in re.split(r"[,\s]+", fields["allowed_tools"])
                )
                if tool
            )
            if not tools:
                raise ValueError("allowed_tools must be non-empty")
            outside = tools - READ_ONLY_TOOL_WHITELIST
            if outside:
                raise ValueError(
                    "tools outside the read-only whitelist: "
                    + ", ".join(sorted(outside))
                )
            try:
                max_turns = int(fields["max_turns"], 10)
            except ValueError as error:
                raise ValueError(
                    f"max_turns must be an integer: {fields['max_turns']!r}"
                ) from error
            if not 1 <= max_turns <= _MAX_TURNS_CEILING:
                raise ValueError(
                    f"max_turns must be 1..{_MAX_TURNS_CEILING}"
                )
            if not body:
                raise ValueError("system prompt body is empty")
            description = fields["description"]
            if not description or len(description) > _MAX_DESCRIPTION_CHARS:
                raise ValueError(
                    f"description must be 1..{_MAX_DESCRIPTION_CHARS} chars"
                )
        except (OSError, ValueError) as error:
            errors.append(f"{path.name}: {error}")
            continue
        profiles[name] = {
            "name": name.replace("-", " ").title(),
            "description": description,
            "system_prompt": body,
            "allowed_tools": tools,
            "max_turns": max_turns,
            "source": "project-profile",
        }
    result = (profiles, errors)
    with _cache_lock:
        _cache[key] = result
    return result


def resolve_agent_types(project_root: str | Path) -> dict[str, dict[str, Any]]:
    """Built-in agent types merged with this project's valid profiles.

    Built-ins are authoritative: reserved names are rejected at load time,
    so the merge can never let a project file shadow a built-in type.
    """
    profiles, _errors = load_agent_profiles(project_root)
    return {**AGENT_TYPES, **profiles}


__all__ = (
    "AGENT_NAME_RE",
    "RESERVED_AGENT_NAMES",
    "load_agent_profiles",
    "resolve_agent_types",
)
