from __future__ import annotations

from collections.abc import Callable

from minicode.capability_registry import CapabilityRegistry
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


def _presel_matcher(cwd: str):
    """Semantic placement matcher, or ``None`` when off/unavailable.

    Gated by the same ``skillSelection.directoryPresel`` switch as routing
    preselection: placement and discovery should agree on the directory
    semantics. Construction is fail-closed — without embedding settings the
    proposal falls back to lexical scoring alone.
    """
    from minicode.config import load_runtime_config
    from minicode.skill_directory_presel import DirectoryPreselMatcher

    try:
        skill_selection = load_runtime_config(cwd).get("skillSelection") or {}
        if not skill_selection.get("directoryPresel"):
            return None
    except Exception:
        return None
    return DirectoryPreselMatcher.from_environment(cwd)


def create_propose_skill_tool(
    cwd: str,
    *,
    capability_registry_provider: Callable[[], CapabilityRegistry],
) -> ToolDefinition:
    if not callable(capability_registry_provider):
        raise TypeError("capability_registry_provider must be callable")

    def _run(input_data: dict, _context) -> ToolResult:
        registry = capability_registry_provider()
        proposal = propose_skill(
            cwd,
            input_data,
            registry,
            presel_matcher=_presel_matcher(cwd),
        )
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
