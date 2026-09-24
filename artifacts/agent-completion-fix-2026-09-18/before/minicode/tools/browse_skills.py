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
from itertools import zip_longest
from typing import Any

from minicode.skill_description_page import (
    DescriptionRow,
    render_description_page,
    shorten_text,
)
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
            output=(f"Skill discovery is closed for this turn ({policy.reason_code})."),
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
    budget: int,
) -> ToolResult:
    valid = ", ".join(repr(name) for name in sorted(by_directory))
    return ToolResult(
        ok=False,
        output=shorten_text(
            f"Unknown skill directory: {shorten_text(requested, 80)!r}. "
            'Use "" for root-level skills without a directory.'
            f" Valid directories: {valid or '(none)'}. ",
            budget,
            suffix=" [Directory list truncated; see system prompt.]",
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
                return _unknown_directory_error(
                    name, by_directory, _page_char_budget(_context)
                )

        # Policy filtering already happened: denied skills never appear and
        # every count below reflects only visible entries.
        ordered = [(name, entry) for name in requested for entry in by_directory[name]]
        rows = [
            DescriptionRow(
                name=_entry_text_key(entry),
                prefix=f"- {_entry_text_key(entry)}: ",
                description=_entry_text(entry, "description"),
                section=f"## {name or '(root)'} ({len(by_directory[name])} skills)",
            )
            for name, entry in ordered
        ]
        page = render_description_page(
            rows,
            offset=input_data["offset"],
            limit=input_data["limit"],
            budget=_page_char_budget(_context),
            tool_name="browse_skill_directory",
        )
        return ToolResult(ok=page.ok, output=page.output)

    return ToolDefinition(
        name="browse_skill_directory",
        description=(
            "List policy-visible Skill descriptions for one or more skill "
            "directories named in the 'Available skill directories:' section "
            'of the system prompt; batched expansion is allowed. Pass [""] '
            "for root-level skills without a directory. Returns descriptions "
            "only - call load_skill with the exact qualified name for the "
            "full skill body. Paged: on 'TRUNCATED: yes' call again with the "
            "given offset. A shortened description is marked explicitly; "
            "load_skill still accepts the complete name shown."
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
                    return _unknown_directory_error(
                        name, by_directory, _page_char_budget(_context)
                    )

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
                    f'No matching skills found for query "{shorten_text(query, 80)}". '
                    "Use browse_skill_directory to expand a directory, or "
                    "rephrase the query."
                ),
            )

        preferred_set = set(preferred_names or ())
        if preferred_names:
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
            # The complete order is independent of page size: neither a small
            # limit nor a later offset can discard the other directory's tail.
            # Alternation reaches a global match on the next page at limit=1.
            combined = [
                item
                for pair in zip_longest(preferred, others)
                for item in pair
                if item is not None
            ]
        else:
            combined = scored

        rows = []
        for _score, key, entry in combined:
            directory = _entry_text(entry, "directory")
            section = ""
            if preferred_names:
                section = (
                    "Matches in preferred directories:"
                    if directory in preferred_set
                    else "Other directories / global:"
                )
            rows.append(
                DescriptionRow(
                    name=key,
                    prefix=f"- {key} [{directory or 'root'}]: ",
                    description=_entry_text(entry, "description"),
                    section=section,
                )
            )
        page = render_description_page(
            rows,
            offset=input_data["offset"],
            limit=input_data["limit"],
            budget=_page_char_budget(_context),
            tool_name="search_skills",
            query=query,
        )
        return ToolResult(ok=page.ok, output=page.output)

    return ToolDefinition(
        name="search_skills",
        description=(
            "Lexically search all authorized Skills by name, description, "
            "and keywords. Optional directory_names prefer matches from those "
            "directories, but global candidates are always kept in a separate "
            "section. Returns descriptions only - call load_skill with the "
            "exact qualified name for the full skill body. Paged: on "
            "'TRUNCATED: yes' call again with the given offset. A shortened "
            "description is marked explicitly; load_skill still accepts the "
            "complete name shown."
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
