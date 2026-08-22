from __future__ import annotations

from minicode.run_events import (
    emit_skill_loaded_safely,
    record_skill_loaded_safely,
)
from minicode.skills import SkillSummary, discover_skills, load_skill_from_catalog
from minicode.tooling import ToolDefinition, ToolResult


def _validate(input_data: dict) -> dict:
    name = input_data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")
    return {"name": name.strip()}


def create_load_skill_tool(
    cwd: str,
    catalog: list[SkillSummary | dict] | None = None,
) -> ToolDefinition:
    # Bind the loader to the same immutable snapshot used by routing/prompt
    # composition. A live rescan here would permit route-old/load-new drift.
    bound_catalog = list(catalog) if catalog is not None else discover_skills(cwd)

    def _run(input_data: dict, _context) -> ToolResult:
        skill = load_skill_from_catalog(cwd, input_data["name"], bound_catalog)
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
