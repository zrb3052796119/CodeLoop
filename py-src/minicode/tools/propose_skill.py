from __future__ import annotations

from minicode.capability_registry import get_registry
from minicode.skill_authoring import format_skill_proposal, propose_skill
from minicode.tooling import ToolCapability, ToolDefinition, ToolMetadata, ToolResult


def _validate(input_data: dict) -> dict:
    if not isinstance(input_data, dict):
        raise ValueError("input must be an object")
    name = input_data.get("name")
    description = input_data.get("description")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description is required")
    parsed = {
        "name": name.strip(),
        "description": description.strip(),
        "keywords": _optional_string_list(input_data, "keywords"),
        "domains": _optional_string_list(input_data, "domains"),
        "scopes": _optional_string_list(input_data, "scopes"),
        "tools": _optional_string_list(input_data, "tools"),
        "examples": _optional_string_list(input_data, "examples"),
        "content_outline": str(input_data.get("content_outline", "") or "").strip(),
    }
    top_k = input_data.get("top_k")
    if top_k is not None:
        if not isinstance(top_k, int) or top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        parsed["top_k"] = top_k
    return parsed


def _optional_string_list(input_data: dict, key: str) -> list[str]:
    value = input_data.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list of strings")
    result = [str(item).strip() for item in value if str(item).strip()]
    return result


def create_propose_skill_tool(cwd: str) -> ToolDefinition:
    def _run(input_data: dict, _context) -> ToolResult:
        proposal = propose_skill(cwd, input_data, get_registry())
        return ToolResult(ok=True, output=format_skill_proposal(proposal))

    return ToolDefinition(
        name="propose_skill",
        description=(
            "Use before creating a new Skill or SKILL.md. Recommend where the Skill should live, "
            "generate frontmatter, check duplicate risks, and validate declared tools without writing files."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "domains": {"type": "array", "items": {"type": "string"}},
                "scopes": {"type": "array", "items": {"type": "string"}},
                "tools": {"type": "array", "items": {"type": "string"}},
                "examples": {"type": "array", "items": {"type": "string"}},
                "content_outline": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1},
            },
            "required": ["name", "description"],
        },
        validator=_validate,
        run=_run,
        metadata=ToolMetadata(
            name="propose_skill",
            description="Read-only Skill authoring proposal tool.",
            capabilities={ToolCapability.READ_ONLY, ToolCapability.CONCURRENCY_SAFE},
            tags=["skill", "authoring", "routing"],
        ),
    )
