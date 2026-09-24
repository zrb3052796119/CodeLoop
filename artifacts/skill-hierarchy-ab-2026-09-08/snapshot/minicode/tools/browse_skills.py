"""Hierarchical Skill discovery tools: directory browsing and lexical search.

Both tools render policy-visible Skill descriptions only; bodies still load
exclusively through ``load_skill``. Each factory binds the same immutable
catalog snapshot used by routing/prompt composition and re-applies the turn's
``SkillLoadPolicy`` inside ``_run``, so the discovery view can never widen the
granted set. Paging mirrors ``read_file``: an offset/limit pair plus a
``TRUNCATED: yes - call again with offset N`` line, with server-enforced caps.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from minicode.skill_semantics import AliasSemanticMatcher, _semantic_tokens
from minicode.skills import SkillSummary, discover_skills
from minicode.tooling import (
    SkillLoadPolicy,
    ToolDefinition,
    ToolResult,
    skill_load_binding_from_value,
)

# Mirror the skillSelection.skillPageCharBudget contract from config.py; the
# tool re-validates the runtime value so a stale/foreign runtime cannot
# unbound a page.
_DEFAULT_PAGE_CHAR_BUDGET = 4000
_MIN_PAGE_CHAR_BUDGET = 500
_MAX_PAGE_CHAR_BUDGET = 50000
_DEFAULT_PAGE_LIMIT = 50
_MAX_PAGE_LIMIT = 100


def _summary_value(
    summary: SkillSummary | dict[str, Any],
    key: str,
) -> Any:
    if isinstance(summary, dict):
        return summary.get(key)
    return getattr(summary, key, None)


def _entry_text(entry: SkillSummary | dict[str, Any], key: str) -> str:
    return str(_summary_value(entry, key) or "")


def _entry_keywords(entry: SkillSummary | dict[str, Any]) -> list[str]:
    keywords = _summary_value(entry, "keywords") or []
    return [str(keyword) for keyword in keywords]


def _page_char_budget(context: Any) -> int:
    runtime = getattr(context, "_runtime", None)
    if isinstance(runtime, Mapping):
        selection = runtime.get("skillSelection")
        if isinstance(selection, Mapping):
            try:
                parsed = int(selection.get("skillPageCharBudget"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return _DEFAULT_PAGE_CHAR_BUDGET
            if _MIN_PAGE_CHAR_BUDGET <= parsed <= _MAX_PAGE_CHAR_BUDGET:
                return parsed
    return _DEFAULT_PAGE_CHAR_BUDGET


def _visible_catalog(
    bound_catalog: list[SkillSummary | dict[str, Any]],
    context: Any,
) -> list[SkillSummary | dict[str, Any]] | ToolResult:
    """Filter the snapshot through the turn policy, or explain why not."""
    policy = getattr(context, "_skill_load_policy", None)
    if policy is None:
        # Legacy no-routing path: load_skill performs no policy check either.
        return list(bound_catalog)
    if not isinstance(policy, SkillLoadPolicy):
        return ToolResult(
            ok=False,
            output="Skill discovery is unavailable: invalid turn policy.",
        )
    visible = [
        entry
        for entry in bound_catalog
        if (binding := skill_load_binding_from_value(entry)) is not None
        and policy.permits(binding)
    ]
    if not visible:
        return ToolResult(
            ok=False,
            output=(
                "Skill discovery is closed for this turn "
                f"({policy.reason_code})."
            ),
        )
    return visible


def _group_by_directory(
    visible: list[SkillSummary | dict[str, Any]],
) -> dict[str, list[SkillSummary | dict[str, Any]]]:
    by_directory: dict[str, list[SkillSummary | dict[str, Any]]] = {}
    for entry in visible:
        by_directory.setdefault(_entry_text(entry, "directory"), []).append(entry)
    for entries in by_directory.values():
        entries.sort(key=_entry_text_key)
    return by_directory


def _entry_text_key(entry: SkillSummary | dict[str, Any]) -> str:
    return _entry_text(entry, "qualified_name") or _entry_text(entry, "name")


def _unknown_directory_error(
    requested: str,
    by_directory: dict[str, list[SkillSummary | dict[str, Any]]],
) -> ToolResult:
    valid = ", ".join(
        repr(name) for name in sorted(by_directory)
    )
    return ToolResult(
        ok=False,
        output=(
            f"Unknown skill directory: {requested!r}. "
            f"Valid directories: {valid or '(none)'}. "
            'Use "" for root-level skills without a directory.'
        ),
    )


def _validate_paging(input_data: dict) -> dict[str, int]:
    offset = int(input_data.get("offset", 0))
    limit = int(input_data.get("limit", _DEFAULT_PAGE_LIMIT))
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if limit < 1 or limit > _MAX_PAGE_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_PAGE_LIMIT}")
    return {"offset": offset, "limit": limit}


def _validate_directory_names(value: Any, *, required: bool) -> list[str] | None:
    if value is None:
        if required:
            raise ValueError(
                "directory_names is required: a non-empty array of directory "
                'names; use [""] for root-level skills'
            )
        return None
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(name, str) for name in value)
    ):
        raise ValueError(
            "directory_names must be a non-empty array of strings; "
            'use [""] for root-level skills'
        )
    # Preserve request order but never expand one directory twice.
    return list(dict.fromkeys(name.strip() for name in value))


def create_browse_skill_directory_tool(
    cwd: str,
    catalog: list[SkillSummary | dict] | None = None,
) -> ToolDefinition:
    # Same immutable snapshot discipline as load_skill: a live rescan here
    # would let the discovery view drift from routing/prompt composition.
    bound_catalog = copy.deepcopy(
        list(catalog) if catalog is not None else discover_skills(cwd)
    )

    def _validate(input_data: dict) -> dict:
        return {
            "directory_names": _validate_directory_names(
                input_data.get("directory_names"), required=True
            ),
            **_validate_paging(input_data),
        }

    def _run(input_data: dict, _context) -> ToolResult:
        visible = _visible_catalog(bound_catalog, _context)
        if isinstance(visible, ToolResult):
            return visible
        by_directory = _group_by_directory(visible)
        requested = input_data["directory_names"]
        for name in requested:
            if name not in by_directory:
                return _unknown_directory_error(name, by_directory)

        # Policy filtering already happened: denied skills never appear and
        # every count below reflects only visible entries.
        ordered = [
            (name, entry) for name in requested for entry in by_directory[name]
        ]
        offset = input_data["offset"]
        limit = input_data["limit"]
        window = ordered[offset : offset + limit]
        budget = _page_char_budget(_context)

        lines: list[str] = []
        used = 0
        rendered = 0
        current_directory: str | None = None
        for name, entry in window:
            entry_line = (
                f"- {_entry_text_key(entry)}: {_entry_text(entry, 'description')}"
            )
            parts = []
            if name != current_directory:
                label = name or "(root)"
                parts.append(f"## {label} ({len(by_directory[name])} skills)")
            parts.append(entry_line)
            block_chars = sum(len(part) + 1 for part in parts)
            # Always render at least one skill so the next offset advances.
            if rendered > 0 and used + block_chars > budget:
                break
            lines.extend(parts)
            used += block_chars
            current_directory = name
            rendered += 1

        next_offset = offset + rendered
        truncated = next_offset < len(ordered)
        header = [
            f"OFFSET: {offset}",
            "TRUNCATED: "
            + (
                f"yes - call browse_skill_directory again with offset {next_offset}"
                if truncated
                else "no"
            ),
            "",
        ]
        if not lines:
            header.append("(no skills on this page)")
        return ToolResult(ok=True, output="\n".join(header + lines))

    return ToolDefinition(
        name="browse_skill_directory",
        description=(
            "List policy-visible Skill descriptions for one or more skill "
            "directories named in the 'Available skill directories:' section "
            "of the system prompt; batched expansion is allowed. Pass [\"\"] "
            "for root-level skills without a directory. Returns descriptions "
            "only - call load_skill with the exact qualified name for the "
            "full skill body. Paged: on 'TRUNCATED: yes' call again with the "
            "given offset."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "directory_names": {"type": "array", "items": {"type": "string"}},
                "offset": {"type": "number"},
                "limit": {"type": "number"},
            },
            "required": ["directory_names"],
        },
        validator=_validate,
        run=_run,
    )


def create_search_skills_tool(
    cwd: str,
    catalog: list[SkillSummary | dict] | None = None,
) -> ToolDefinition:
    bound_catalog = copy.deepcopy(
        list(catalog) if catalog is not None else discover_skills(cwd)
    )
    matcher = AliasSemanticMatcher()

    def _validate(input_data: dict) -> dict:
        query = input_data.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")
        return {
            "query": query.strip(),
            "directory_names": _validate_directory_names(
                input_data.get("directory_names"), required=False
            ),
            **_validate_paging(input_data),
        }

    def _run(input_data: dict, _context) -> ToolResult:
        visible = _visible_catalog(bound_catalog, _context)
        if isinstance(visible, ToolResult):
            return visible
        by_directory = _group_by_directory(visible)
        query = input_data["query"]
        preferred_names = input_data["directory_names"]
        if preferred_names:
            for name in preferred_names:
                if name not in by_directory:
                    return _unknown_directory_error(name, by_directory)

        # Lexical token overlap is the base score; alias-concept coverage is
        # the boost so e.g. "auth" also credits "authentication".
        query_tokens = _semantic_tokens(query)
        scored: list[tuple[float, str, SkillSummary | dict[str, Any]]] = []
        for entry in visible:
            skill_text = " ".join(
                [
                    _entry_text_key(entry),
                    _entry_text(entry, "description"),
                    " ".join(_entry_keywords(entry)),
                ]
            )
            overlap = len(query_tokens & _semantic_tokens(skill_text))
            coverage = matcher.query_coverage(query, skill_text)
            score = float(overlap) + coverage
            if score > 0:
                scored.append((score, _entry_text_key(entry), entry))
        scored.sort(key=lambda item: (-item[0], item[1]))

        if not scored:
            return ToolResult(
                ok=True,
                output=(
                    f'No matching skills found for query "{query}". '
                    "Use browse_skill_directory to expand a directory, or "
                    "rephrase the query."
                ),
            )

        offset = input_data["offset"]
        limit = input_data["limit"]
        if preferred_names:
            preferred_set = set(preferred_names)
            preferred = [
                item
                for item in scored
                if _entry_text(item[2], "directory") in preferred_set
            ]
            others = [
                item
                for item in scored
                if _entry_text(item[2], "directory") not in preferred_set
            ]
            # Reserve a quota per section so in-directory scores cannot crowd
            # out global candidates.
            other_quota = limit // 2
            preferred = preferred[: limit - other_quota]
            combined = preferred + others[:other_quota]
            preferred_ids = {id(item[2]) for item in preferred}
        else:
            combined = scored
            preferred_ids = set()

        window = combined[offset : offset + limit]
        budget = _page_char_budget(_context)

        lines: list[str] = []
        used = 0
        rendered = 0
        in_preferred_section = False
        in_other_section = False
        for score, _key, entry in window:
            entry_line = (
                f"- {_entry_text_key(entry)} "
                f"[{_entry_text(entry, 'directory') or 'root'}]: "
                f"{_entry_text(entry, 'description')}"
            )
            parts = []
            if preferred_names:
                if id(entry) in preferred_ids and not in_preferred_section:
                    parts.append("Matches in preferred directories:")
                    in_preferred_section = True
                elif id(entry) not in preferred_ids and not in_other_section:
                    parts.append("Other directories / global:")
                    in_other_section = True
            parts.append(entry_line)
            block_chars = sum(len(part) + 1 for part in parts)
            if rendered > 0 and used + block_chars > budget:
                break
            lines.extend(parts)
            used += block_chars
            rendered += 1

        next_offset = offset + rendered
        truncated = next_offset < len(combined)
        header = [
            f"QUERY: {query}",
            f"OFFSET: {offset}",
            "TRUNCATED: "
            + (
                f"yes - call search_skills again with offset {next_offset}"
                if truncated
                else "no"
            ),
            "",
        ]
        if not lines:
            header.append("(no skills on this page)")
        return ToolResult(ok=True, output="\n".join(header + lines))

    return ToolDefinition(
        name="search_skills",
        description=(
            "Lexically search all authorized Skills by name, description, "
            "and keywords. Optional directory_names prefer matches from those "
            "directories, but global candidates are always kept in a separate "
            "section. Returns descriptions only - call load_skill with the "
            "exact qualified name for the full skill body. Paged: on "
            "'TRUNCATED: yes' call again with the given offset."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "directory_names": {"type": "array", "items": {"type": "string"}},
                "offset": {"type": "number"},
                "limit": {"type": "number"},
            },
            "required": ["query"],
        },
        validator=_validate,
        run=_run,
    )
