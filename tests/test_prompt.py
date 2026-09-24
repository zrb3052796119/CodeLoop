from pathlib import Path
from types import SimpleNamespace

import pytest

from minicode.prompt import build_system_prompt


def test_build_system_prompt_includes_skills_and_mcp(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        str(tmp_path),
        ["cwd: test"],
        {
            "skills": [{"name": "demo", "description": "demo skill"}],
            "mcpServers": [{"name": "fake", "status": "connected", "toolCount": 1, "resourceCount": 1, "promptCount": 1, "protocol": "newline-json"}],
        },
    )

    assert "Available skills:" in prompt
    assert "demo skill" in prompt
    assert "Configured MCP servers:" in prompt
    assert "fake: connected, tools=1" in prompt


def test_build_system_prompt_mentions_sequential_thinking_server(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        str(tmp_path),
        [],
        {
            "mcpServers": [
                {"name": "SequentialThinking", "status": "connected", "toolCount": 1}
            ]
        },
    )

    assert "SEQUENTIAL THINKING MCP SERVER IS CONNECTED" in prompt
    assert "sequential_thinking" in prompt


def test_build_system_prompt_includes_memory_context(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        str(tmp_path),
        [],
        {"memory_context": "# Project Memory\n\n- Always run pytest before release."},
    )

    assert "Project Memory & Context" in prompt
    assert "Always run pytest before release." in prompt


def test_build_system_prompt_requires_propose_skill_before_new_skill_write(tmp_path: Path) -> None:
    prompt = build_system_prompt(str(tmp_path), [], {})

    assert "Skill authoring policy" in prompt
    assert "call propose_skill first" in prompt
    assert "Do not call write_file for a new Skill" in prompt


def test_noninteractive_prompt_requires_final_channel_and_hides_ask_user(
    tmp_path: Path,
) -> None:
    prompt = build_system_prompt(
        str(tmp_path),
        [],
        {"user_interaction_available": False},
    )

    assert "ask_user is unavailable" in prompt
    assert "Return results through the assistant final response" in prompt
    assert "call the ask_user tool" not in prompt


def test_system_prompt_states_the_current_date(tmp_path: Path) -> None:
    """Without a date the model has no clock and silently answers from its
    training cutoff — stating a wrong year with full confidence."""
    from datetime import datetime

    prompt = build_system_prompt(str(tmp_path), [], {})
    today = datetime.now().astimezone()

    assert "## Current date" in prompt
    assert f"{today:%Y-%m-%d}" in prompt
    assert f"{today:%A}" in prompt


def test_current_date_sits_behind_the_prompt_cache_boundary(tmp_path: Path) -> None:
    """A per-turn-changing section placed in the static prefix would
    invalidate the cacheable region on every date change."""
    from minicode.prompt_pipeline import SYSTEM_PROMPT_DYNAMIC_BOUNDARY

    prompt = build_system_prompt(str(tmp_path), [], {})
    static_prefix, dynamic_suffix = prompt.split(SYSTEM_PROMPT_DYNAMIC_BOUNDARY, 1)

    assert "## Current date" in dynamic_suffix
    assert "## Current date" not in static_prefix


def test_current_date_is_never_served_from_the_section_cache(
    tmp_path: Path, monkeypatch
) -> None:
    """PromptSection defaults to a 5-minute TTL; a clock section must opt out
    or it will emit a stale date after midnight."""
    import re
    from datetime import datetime, timedelta, timezone

    import minicode.prompt as prompt_module

    tz = timezone(timedelta(hours=8))

    class FrozenDateTime:
        current = datetime(2026, 7, 30, 10, 0, tzinfo=tz)

        @classmethod
        def now(cls, tz_=None):
            return cls.current

    monkeypatch.setattr(prompt_module, "datetime", FrozenDateTime)

    def rendered_date(text: str) -> str:
        return re.search(r"Today is (\d{4}-\d{2}-\d{2})", text).group(1)

    first = rendered_date(build_system_prompt(str(tmp_path), [], {}))
    FrozenDateTime.current = datetime(2026, 8, 1, 10, 0, tzinfo=tz)
    second = rendered_date(build_system_prompt(str(tmp_path), [], {}))

    assert first == "2026-07-30"
    assert second == "2026-08-01"


def test_fallback_skill_section_is_name_only_inventory(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        str(tmp_path),
        [],
        {
            "skills": [
                {
                    "name": "demo",
                    "qualified_name": "project/demo",
                    "description": "This description should stay out of an abstained prompt",
                    "tools": ["read_file"],
                }
            ],
            "skill_routing": {
                "used_fallback": True,
                "selected": [],
                "selected_skills": [],
            },
        },
    )

    assert "no routing evidence" in prompt
    assert "- project/demo" in prompt
    assert "This description should stay out" not in prompt
    assert "likely tools" not in prompt


@pytest.mark.parametrize("as_dict", [True, False])
def test_clarify_skill_section_is_safe_for_dict_and_object_routing(
    tmp_path: Path,
    as_dict: bool,
) -> None:
    routing_fields = {
        "used_fallback": True,
        "turn_outcome": "clarify",
        "prompt_mode": "inventory_names",
        "selected_directories": [],
    }
    routing = routing_fields if as_dict else SimpleNamespace(**routing_fields)

    prompt = build_system_prompt(
        str(tmp_path),
        [],
        {
            "skills": [
                {
                    "name": "alpha",
                    "qualified_name": "project/alpha",
                    "description": "private alpha description",
                }
            ],
            "skill_routing": routing,
        },
    )

    assert "Available skills (clarification required; names only):" in prompt
    assert "- project/alpha" in prompt
    assert "Ask one concise clarifying question" in prompt
    assert "Do not silently substitute a different Skill" in prompt
    assert "No skill matched this request" not in prompt
    assert "private alpha description" not in prompt


@pytest.mark.parametrize("as_dict", [True, False])
def test_suppressed_skill_section_never_projects_catalog_entries(
    tmp_path: Path,
    as_dict: bool,
) -> None:
    routing_fields = {
        "used_fallback": True,
        "turn_outcome": "no_skill",
        "prompt_mode": "suppressed",
    }
    routing = routing_fields if as_dict else SimpleNamespace(**routing_fields)

    prompt = build_system_prompt(
        str(tmp_path),
        [],
        {
            "skills": [
                {
                    "name": "alpha",
                    "qualified_name": "project/alpha",
                    "description": "private alpha description",
                }
            ],
            "skill_routing": routing,
        },
    )

    assert "Skill routing is suppressed for this turn" in prompt
    assert "project/alpha" not in prompt
    assert "private alpha description" not in prompt
    assert "Available skills (" not in prompt


@pytest.mark.parametrize("as_dict", [True, False])
def test_model_first_skill_section_lists_full_catalog_descriptions(
    tmp_path: Path,
    as_dict: bool,
) -> None:
    routing_fields = {
        "used_fallback": False,
        "turn_outcome": "route",
        "prompt_mode": "model",
        "selected_directories": [],
    }
    routing = routing_fields if as_dict else SimpleNamespace(**routing_fields)

    prompt = build_system_prompt(
        str(tmp_path),
        [],
        {
            "skills": [
                {
                    "name": "alpha",
                    "qualified_name": "project/alpha",
                    "description": "full alpha description",
                    "tools": ["read_file"],
                },
                {
                    "name": "beta",
                    "qualified_name": "project/beta",
                    "description": "full beta description",
                },
            ],
            "skill_routing": routing,
        },
    )

    assert "Available skills:" in prompt
    assert "- project/alpha: full alpha description" in prompt
    assert "- project/beta: full beta description" in prompt
    assert "names only" not in prompt
    assert "Routed skills" not in prompt
    assert "likely tools" not in prompt
    assert "You decide which of the listed skills apply" in prompt


@pytest.mark.parametrize("as_dict", [True, False])
def test_hierarchical_skill_section_renders_cards_and_root_skills(
    tmp_path: Path,
    as_dict: bool,
) -> None:
    routing_fields = {
        "used_fallback": False,
        "turn_outcome": "route",
        "prompt_mode": "hierarchical",
        "selected_directories": [],
    }
    routing = routing_fields if as_dict else SimpleNamespace(**routing_fields)

    prompt = build_system_prompt(
        str(tmp_path),
        [],
        {
            "skills": [
                {
                    "name": "alpha",
                    "qualified_name": "docs/alpha",
                    "description": "alpha full description",
                    "directory": "docs",
                    "directory_description": "documentation work",
                    "content_digest": "a" * 64,
                },
                {
                    "name": "beta",
                    "qualified_name": "docs/beta",
                    "description": "beta full description",
                    "directory": "docs",
                    "directory_description": "documentation work",
                    "content_digest": "b" * 64,
                },
                {
                    "name": "rooty",
                    "qualified_name": "rooty",
                    "description": "root full description",
                    "directory": "",
                    "content_digest": "c" * 64,
                },
            ],
            "skill_routing": routing,
        },
    )

    assert "Available skill directories:" in prompt
    assert "- docs: documentation work (2 skills, ref " in prompt
    assert "Undirectoried skills:" in prompt
    assert "- rooty: root full description" in prompt
    assert "browse_skill_directory" in prompt
    assert "search_skills" in prompt
    assert "load_skill with the exact qualified name" in prompt
    # Directoried skills collapse into cards: their full descriptions and
    # qualified names stay out of the prompt until browsed/searched.
    assert "alpha full description" not in prompt
    assert "beta full description" not in prompt
    assert "docs/alpha" not in prompt
    if as_dict:
        assert routing["catalog_prompt_chars"] > 0
    else:
        assert routing.catalog_prompt_chars > 0


# --- Experiment arm B: advisory intent-hint block (hierarchical only) ---


def _hint_skills() -> list[dict[str, object]]:
    return [
        {
            "name": "alpha",
            "qualified_name": "reviewing/alpha",
            "description": "alpha full description",
            "directory": "reviewing",
            "directory_description": "code review and audit helpers",
            "content_digest": "a" * 64,
        },
        {
            "name": "rooty",
            "qualified_name": "rooty",
            "description": "root full description",
            "directory": "",
            "content_digest": "b" * 64,
        },
    ]


def _hint_payload() -> dict[str, object]:
    return {
        "hinted": True,
        "stages": [
            {
                "operation": "review the parser code",
                "evidence": "review the parser code",
            }
        ],
        "entities": ["parser.py"],
        "constraints": ["do not deploy"],
        "suggested_directories": ["reviewing"],
        "confidence": 0.8,
    }


@pytest.mark.parametrize("as_dict", [True, False])
def test_hierarchical_intent_hint_section_renders_with_advisory_label(
    tmp_path: Path,
    as_dict: bool,
) -> None:
    routing_fields = {
        "used_fallback": False,
        "turn_outcome": "route",
        "prompt_mode": "hierarchical",
        "selected_directories": [],
        "intent_hints": _hint_payload(),
    }
    routing = routing_fields if as_dict else SimpleNamespace(**routing_fields)

    prompt = build_system_prompt(
        str(tmp_path),
        [],
        {"skills": _hint_skills(), "skill_routing": routing},
    )

    assert (
        "Task clues (advisory - derived from automated intent parsing, "
        "may be wrong; the user's original request is authoritative):"
    ) in prompt
    assert "Recognized operations (partial, not ordered stages):" in prompt
    assert '1. review the parser code (evidence: "review the parser code")' in prompt
    assert "Entities: parser.py" in prompt
    assert "Constraints (explicitly ruled out): do not deploy" in prompt
    assert "Suggested starting directories: reviewing" in prompt
    assert "Prefetched descriptions" not in prompt


def test_hierarchical_hint_section_absent_without_payload(
    tmp_path: Path,
) -> None:
    routing = {
        "used_fallback": False,
        "turn_outcome": "route",
        "prompt_mode": "hierarchical",
        "selected_directories": [],
    }

    prompt = build_system_prompt(
        str(tmp_path),
        [],
        {"skills": _hint_skills(), "skill_routing": routing},
    )

    assert "Available skill directories:" in prompt
    assert "Task clues" not in prompt
    assert "Suggested starting directories" not in prompt


def test_hierarchical_prefetch_section_renders_with_truncation_marker(
    tmp_path: Path,
) -> None:
    payload = _hint_payload()
    payload["prefetched_directory"] = "reviewing"
    payload["prefetched_skills"] = ["- reviewing/alpha: alpha full description"]
    payload["prefetch_truncated"] = True
    payload["prefetch_next_offset"] = 1
    routing = {
        "used_fallback": False,
        "turn_outcome": "route",
        "prompt_mode": "hierarchical",
        "selected_directories": [],
        "intent_hints": payload,
    }

    prompt = build_system_prompt(
        str(tmp_path),
        [],
        {"skills": _hint_skills(), "skill_routing": routing},
    )

    assert (
        'Prefetched descriptions for directory "reviewing" '
        "(partial, first page):"
    ) in prompt
    assert "- reviewing/alpha: alpha full description" in prompt
    assert (
        "TRUNCATED: yes - call browse_skill_directory again with offset 1"
        in prompt
    )
