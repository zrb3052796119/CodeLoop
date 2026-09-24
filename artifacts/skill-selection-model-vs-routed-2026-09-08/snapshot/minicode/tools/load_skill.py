from __future__ import annotations

import copy
from typing import Any

from minicode.run_events import (
    emit_skill_loaded_safely,
    record_skill_loaded_safely,
)
from minicode.skills import (
    SkillSummary,
    discover_skills,
    load_skill_from_catalog,
    resolve_skill_catalog_binding,
)
from minicode.tooling import (
    SkillLoadBinding,
    SkillLoadPolicy,
    ToolDefinition,
    ToolResult,
    skill_load_binding_from_value,
)


def _validate(input_data: dict) -> dict:
    name = input_data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")
    return {"name": name.strip()}


def _summary_value(
    summary: SkillSummary | dict[str, Any],
    key: str,
) -> Any:
    if isinstance(summary, dict):
        return summary.get(key)
    return getattr(summary, key, None)


def _bound_binding(
    requested_name: str,
    catalog: list[SkillSummary | dict[str, Any]],
) -> SkillLoadBinding | None:
    """Resolve an advertised identity without opening the Skill file."""
    resolved = resolve_skill_catalog_binding(requested_name, catalog)
    return skill_load_binding_from_value(resolved) if resolved is not None else None


def create_load_skill_tool(
    cwd: str,
    catalog: list[SkillSummary | dict] | None = None,
) -> ToolDefinition:
    # Bind the loader to the same immutable snapshot used by routing/prompt
    # composition. A live rescan here would permit route-old/load-new drift.
    bound_catalog = copy.deepcopy(
        list(catalog) if catalog is not None else discover_skills(cwd)
    )

    def _run(input_data: dict, _context) -> ToolResult:
        policy = getattr(_context, "_skill_load_policy", None)
        binding: SkillLoadBinding | None = None
        if policy is not None:
            if not isinstance(policy, SkillLoadPolicy):
                return ToolResult(
                    ok=False,
                    output="Skill loading is unavailable: invalid turn policy.",
                )
            binding = _bound_binding(input_data["name"], bound_catalog)
            if binding is None or not policy.permits(binding):
                return ToolResult(
                    ok=False,
                    output=(
                        "Skill load denied by the current turn routing policy "
                        f"({policy.reason_code})."
                    ),
                )
        skill = load_skill_from_catalog(
            cwd,
            input_data["name"],
            bound_catalog,
            expected_binding=binding,
        )
        if skill is None:
            return ToolResult(
                ok=False,
                output=(
                    f"Unknown or stale skill binding: {input_data['name']}. "
                    "Refresh the Skill catalog and retry."
                ),
            )
        if len(skill.content) > 180_000:
            return ToolResult(
                ok=False,
                output=(
                    f"Skill is too large to load safely without truncation: "
                    f"{input_data['name']} ({len(skill.content)} characters)."
                ),
            )
        record_skill_loaded_safely(_context._skill_usage_tracker, skill)
        emit_skill_loaded_safely(
            _context._event_sink,
            skill,
            step=_context._step,
        )
        return ToolResult(
            ok=True,
            output="\n".join(
                [
                    f"SKILL: {skill.name}",
                    f"SOURCE: {skill.source}",
                    f"PATH: {skill.path}",
                    "",
                    skill.content,
                ]
            ),
        )

    return ToolDefinition(
        name="load_skill",
        description="Load a local SKILL.md by name or qualified directory/name.",
        input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        validator=_validate,
        run=_run,
    )
