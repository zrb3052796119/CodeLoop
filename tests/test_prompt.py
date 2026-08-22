from pathlib import Path

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
