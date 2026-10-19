"""Project-defined read-only sub-agent profiles loaded from Markdown files.

A profile is one ``.md`` file under ``<project>/.mini-code/agents``: a
closed-key frontmatter block (name, description, allowed_tools, optional max_turns)
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
_MAX_DESCRIPTION_CHARS = 400
_PROFILE_DIR = (".mini-code", "agents")
_MAX_CACHED_ROOTS = 32

_cache_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[tuple, dict[str, dict], list[str]]] = {}


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
    missing = [key for key in _FRONTMATTER_KEYS
               if key != "max_turns" and not fields.get(key)]
    if missing:
        raise ValueError("missing frontmatter keys: " + ", ".join(missing))
    body = "\n".join(lines[closing_index + 1 :]).strip()
    return fields, body


def _directory_signature(directory: Path) -> tuple:
    signature = []
    try:
        paths = sorted(directory.glob("*.md"))
    except OSError as error:
        return (("directory_unreadable", error.errno),)
    for path in paths:
        try:
            metadata = path.stat()
        except OSError as error:
            # Parsing will report this individual file; a broken symlink
            # must not prevent the built-in task menu from being created.
            signature.append((path.name, "unreadable", error.errno))
            continue
        signature.append((
            path.name, metadata.st_dev, metadata.st_ino, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_ctime_ns,
        ))
    return tuple(signature)


def _parse_profile_file(path: Path, origin: str) -> tuple[str, dict]:
    """Parse and validate one profile file; raises on any malformation.

    Returns the dispatch key (slug name) and the agent-type entry.
    """
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
            "tools outside the read-only whitelist: " + ", ".join(sorted(outside))
        )
    max_turns = None
    if "max_turns" in fields:
        try:
            configured_turns = int(fields["max_turns"], 10)
        except ValueError as error:
            raise ValueError(
                f"max_turns must be a non-negative integer: {fields['max_turns']!r}"
            ) from error
        if configured_turns < 0:
            raise ValueError("max_turns must be a non-negative integer")
        max_turns = configured_turns or None
    if not body:
        raise ValueError("system prompt body is empty")
    description = fields["description"]
    if not description or len(description) > _MAX_DESCRIPTION_CHARS:
        raise ValueError(f"description must be 1..{_MAX_DESCRIPTION_CHARS} chars")
    return (
        name,
        {
            "name": name.replace("-", " ").title(),
            "description": description,
            "system_prompt": body,
            "allowed_tools": tools,
            "max_turns": max_turns,
            "source": f"{origin}-profile",
        },
    )


def load_agent_profiles(
    project_root: str | Path,
    *,
    user_root: str | Path | None = None,
) -> tuple[dict[str, dict], list[str]]:
    """Load valid profiles from the project and user directories.

    Project profiles (``<project>/.mini-code/agents``) win over user
    profiles (``~/.mini-code/agents``) on a name collision — the closer
    definition to the code being worked on is authoritative. Results are
    cached on each directory's file identity/change signature so a
    long-running process picks up edits between turns.
    """
    project_dir = Path(project_root).joinpath(*_PROFILE_DIR)
    user_home = Path(user_root) if user_root is not None else Path.home()
    user_dir = user_home.joinpath(*_PROFILE_DIR)
    roots = (
        (project_dir, "project"),
        (user_dir, "user"),
    )
    signatures = tuple(
        _directory_signature(directory) if directory.is_dir() else ()
        for directory, _origin in roots
    )
    key = (str(project_dir), str(user_dir))
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None and cached[0] == signatures:
        return cached[1], cached[2]

    profiles: dict[str, dict] = {}
    errors: list[str] = []
    for (directory, origin), _signature in zip(roots, signatures):
        if not directory.is_dir():
            continue
        try:
            paths = sorted(directory.glob("*.md"))
        except OSError as error:
            errors.append(f"{origin}:profile directory: {error}")
            continue
        for path in paths:
            try:
                slug, profile = _parse_profile_file(path, origin)
            except (OSError, ValueError) as error:
                errors.append(f"{origin}:{path.name}: {error}")
                continue
            # Project precedence: an earlier (project) definition wins.
            profiles.setdefault(slug, profile)
    with _cache_lock:
        if key not in _cache and len(_cache) >= _MAX_CACHED_ROOTS:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (signatures, profiles, errors)
    return profiles, errors


def resolve_agent_types(
    project_root: str | Path,
    *,
    user_root: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Built-in agent types merged with this project's valid profiles.

    Built-ins are authoritative: reserved names are rejected at load time,
    so the merge can never let a project file shadow a built-in type.
    """
    profiles, _errors = load_agent_profiles(project_root, user_root=user_root)
    return {**AGENT_TYPES, **profiles}


__all__ = (
    "AGENT_NAME_RE",
    "RESERVED_AGENT_NAMES",
    "load_agent_profiles",
    "resolve_agent_types",
)
