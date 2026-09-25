"""Project-defined read-only sub-agent profiles (.mini-code/agents/*.md)."""

from pathlib import Path

import pytest

from minicode.subagent_profiles import (
    AGENT_NAME_RE,
    RESERVED_AGENT_NAMES,
    load_agent_profiles,
    resolve_agent_types,
)

_VALID_FRONTMATTER = (
    "---\n"
    "name: test-locator\n"
    "description: Locate failing tests and their fixtures.\n"
    "max_turns: 8\n"
    "allowed_tools: read_file, list_files, grep_files, find_symbols\n"
    "---\n"
    "You are a test-locator agent. Find the test and fixture paths."
)


def _write(root: Path, name: str, content: str) -> Path:
    target = root / ".mini-code" / "agents" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def test_valid_profile_parses(tmp_path):
    _write(tmp_path, "test-locator.md", _VALID_FRONTMATTER)
    loaded, errors = load_agent_profiles(tmp_path)
    assert errors == []
    assert set(loaded) == {"test-locator"}
    entry = loaded["test-locator"]
    assert entry["max_turns"] == 8
    assert entry["allowed_tools"] == frozenset(
        {"read_file", "list_files", "grep_files", "find_symbols"}
    )
    assert entry["system_prompt"].startswith("You are a test-locator agent.")
    assert entry["source"] == "project-profile"
    assert entry["description"] == "Locate failing tests and their fixtures."


def test_body_without_frontmatter_is_rejected(tmp_path):
    _write(tmp_path, "no-frontmatter.md", "just a body")
    loaded, errors = load_agent_profiles(tmp_path)
    assert loaded == {} and len(errors) == 1
    assert "no-frontmatter.md" in errors[0]


def test_unclosed_frontmatter_is_rejected(tmp_path):
    _write(
        tmp_path,
        "unclosed.md",
        _VALID_FRONTMATTER.split("---\n", 2)[0] + "---\nname: x\n",
    )
    loaded, errors = load_agent_profiles(tmp_path)
    assert loaded == {} and errors


def test_malformed_frontmatter_line_is_rejected(tmp_path):
    _write(
        tmp_path,
        "malformed.md",
        _VALID_FRONTMATTER.replace("max_turns: 8", "max_turns 8"),
    )
    loaded, errors = load_agent_profiles(tmp_path)
    assert loaded == {} and errors


def test_reserved_names_are_rejected(tmp_path):
    for name in sorted(RESERVED_AGENT_NAMES):
        _write(
            tmp_path,
            f"{name}.md",
            _VALID_FRONTMATTER.replace("test-locator", name),
        )
    loaded, errors = load_agent_profiles(tmp_path)
    assert loaded == {} and len(errors) == len(RESERVED_AGENT_NAMES)


def test_bad_slug_is_rejected(tmp_path):
    _write(tmp_path, "badslug.md", _VALID_FRONTMATTER.replace("test-locator", "Bad Slug"))
    loaded, errors = load_agent_profiles(tmp_path)
    assert loaded == {} and errors


def test_write_tool_is_rejected(tmp_path):
    _write(
        tmp_path,
        "writer.md",
        _VALID_FRONTMATTER.replace(
            "allowed_tools: read_file, list_files, grep_files, find_symbols",
            "allowed_tools: read_file, edit_file",
        ),
    )
    loaded, errors = load_agent_profiles(tmp_path)
    assert loaded == {} and any("edit_file" in error for error in errors)


def test_unknown_tool_is_rejected(tmp_path):
    _write(
        tmp_path,
        "ghost-tool.md",
        _VALID_FRONTMATTER.replace("find_symbols", "not_a_real_tool"),
    )
    loaded, errors = load_agent_profiles(tmp_path)
    assert loaded == {} and any("not_a_real_tool" in error for error in errors)


def test_out_of_range_turns_fail_closed(tmp_path):
    for value in ("0", "500", "eight", "3.5"):
        _write(
            tmp_path,
            f"turns-{value.replace('.', '_')}.md",
            _VALID_FRONTMATTER.replace("max_turns: 8", f"max_turns: {value}"),
        )
    loaded, errors = load_agent_profiles(tmp_path)
    assert loaded == {} and len(errors) == 4


def test_unknown_frontmatter_key_rejected(tmp_path):
    _write(
        tmp_path,
        "extra.md",
        _VALID_FRONTMATTER.replace(
            "max_turns: 8", "max_turns: 8\nunknown_key: 1"
        ),
    )
    loaded, errors = load_agent_profiles(tmp_path)
    assert loaded == {} and len(errors) == 1


def test_empty_body_rejected(tmp_path):
    _write(
        tmp_path,
        "nobody.md",
        _VALID_FRONTMATTER.split("---\nYou are")[0] + "---\n   \n",
    )
    loaded, errors = load_agent_profiles(tmp_path)
    assert loaded == {} and errors


def test_missing_directory_is_empty(tmp_path):
    loaded, errors = load_agent_profiles(tmp_path)
    assert loaded == {} and errors == []


def test_non_markdown_files_ignored(tmp_path):
    directory = tmp_path / ".mini-code" / "agents"
    directory.mkdir(parents=True)
    (directory / "notes.txt").write_text("not a profile", encoding="utf-8")
    loaded, errors = load_agent_profiles(tmp_path)
    assert loaded == {} and errors == []


def test_mtime_cache_invalidates(tmp_path):
    target = _write(tmp_path, "test-locator.md", _VALID_FRONTMATTER)
    first, _ = load_agent_profiles(tmp_path)
    assert set(first) == {"test-locator"}
    target.write_text(
        _VALID_FRONTMATTER.replace("name: test-locator", "name: other-locator"),
        encoding="utf-8",
    )
    second, _ = load_agent_profiles(tmp_path)
    assert set(second) == {"other-locator"}


def test_builtin_names_win_in_resolution(tmp_path):
    _write(tmp_path, "ignored.md", "---\nname: explore\n---\nbody")
    profiles, errors = load_agent_profiles(tmp_path)
    assert profiles == {} and len(errors) == 1
    merged = resolve_agent_types(tmp_path)
    assert merged["explore"] is not None
    assert "source" not in merged["explore"]
    assert set(merged) >= {"explore", "plan", "general", "workflow"}


def test_resolution_merges_custom_types(tmp_path):
    _write(tmp_path, "test-locator.md", _VALID_FRONTMATTER)
    merged = resolve_agent_types(tmp_path)
    assert merged["test-locator"]["max_turns"] == 8
    assert merged["explore"]["max_turns"] == 12


def test_agent_name_re_shape():
    assert AGENT_NAME_RE.fullmatch("abc") is not None
    assert AGENT_NAME_RE.fullmatch("test-locator") is not None
    assert AGENT_NAME_RE.fullmatch("Test") is None
    assert AGENT_NAME_RE.fullmatch("a") is None  # 总长至少 2
    assert AGENT_NAME_RE.fullmatch("bad name") is None


# ---------------------------------------------------------------------------
# User-level profile directory (project wins on collision)
# ---------------------------------------------------------------------------


def _write_user(root: Path, name: str, content: str) -> Path:
    # user_root is the HOME directory: profiles live under
    # <home>/.mini-code/agents, mirroring the project layout.
    target = root / ".mini-code" / "agents" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def test_user_level_profile_loads(tmp_path):
    user_root = tmp_path / "home"
    _write_user(user_root, "audit-explorer.md", _VALID_FRONTMATTER.replace(
        "name: test-locator", "name: audit-explorer"
    ))
    loaded, errors = load_agent_profiles(tmp_path, user_root=user_root)
    assert errors == []
    assert set(loaded) == {"audit-explorer"}
    merged = resolve_agent_types(tmp_path, user_root=user_root)
    assert merged["audit-explorer"]["max_turns"] == 8


def test_project_wins_over_user_on_same_name(tmp_path):
    user_root = tmp_path / "home"
    _write_user(user_root, "shared-type.md", _VALID_FRONTMATTER.replace(
        "name: test-locator", "name: shared-type"
    ).replace("max_turns: 8", "max_turns: 5"))
    _write(tmp_path, "shared-type.md", _VALID_FRONTMATTER.replace(
        "name: test-locator", "name: shared-type"
    ))
    loaded, errors = load_agent_profiles(tmp_path, user_root=user_root)
    assert errors == []
    assert loaded["shared-type"]["max_turns"] == 8  # project value


def test_errors_carry_origin_prefix(tmp_path):
    user_root = tmp_path / "home"
    _write_user(user_root, "broken.md", "no frontmatter")
    loaded, errors = load_agent_profiles(tmp_path, user_root=user_root)
    assert loaded == {}
    assert len(errors) == 1
    assert "user:broken.md" in errors[0]


def test_both_roots_merge(tmp_path):
    user_root = tmp_path / "home"
    _write_user(user_root, "user-type.md", _VALID_FRONTMATTER.replace(
        "name: test-locator", "name: user-type"
    ))
    _write(tmp_path, "project-type.md", _VALID_FRONTMATTER.replace(
        "name: test-locator", "name: project-type"
    ))
    loaded, errors = load_agent_profiles(tmp_path, user_root=user_root)
    assert errors == []
    assert set(loaded) == {"user-type", "project-type"}


def test_default_user_root_is_home(tmp_path, monkeypatch):
    monkeypatch.setattr("minicode.subagent_profiles.Path.home", lambda: tmp_path)
    _write_user(tmp_path, "home-type.md", _VALID_FRONTMATTER.replace(
        "name: test-locator", "name: home-type"
    ))
    loaded, errors = load_agent_profiles(tmp_path)
    assert errors == []
    assert set(loaded) == {"home-type"}


# ---------------------------------------------------------------------------
# Task 2: closed typed contracts accept project-defined agent types
# ---------------------------------------------------------------------------


def test_result_contract_accepts_custom_agent_type():
    from minicode.subagent_result import project_subagent_result

    result = project_subagent_result(
        None,
        subagent_id="sub_" + "1" * 32,
        agent_type="test-locator",
        outcome="completed",
        fallback_summary="found it",
        partial=False,
    )
    assert result["contractStatus"] == "fallback"


def test_result_contract_still_rejects_bad_type_names():
    from minicode.subagent_result import project_subagent_result

    with pytest.raises(ValueError):
        project_subagent_result(
            None,
            subagent_id="sub_" + "1" * 32,
            agent_type="bad name; drop",
            outcome="completed",
            fallback_summary="x",
            partial=False,
        )


def test_model_routing_inherits_for_custom_type():
    from minicode.subagent_model_routing import resolve_subagent_model_route

    route = resolve_subagent_model_route(
        {"model": "parent-model"}, "test-locator"
    )
    assert route.provider == "inherit"
    assert route.model == "parent-model"


def test_observation_payload_accepts_custom_agent_type():
    from minicode.subagent_observation import project_subagent_event

    payload = project_subagent_event(
        subagent_id="sub_" + "1" * 32,
        agent_type="test-locator",
        outcome="completed",
        model_turns=3,
        tool_calls=5,
        duration_ms=1200,
        max_turns=8,
        result_contract_status="reported",
        result_truncated=False,
    )
    assert payload is not None
    assert payload["agentType"] == "test-locator"


# ---------------------------------------------------------------------------
# Task 3: task dispatch accepts project-defined types
# ---------------------------------------------------------------------------

_IMPORTS_DONE = False


def _dispatch_imports():
    global _IMPORTS_DONE
    if not _IMPORTS_DONE:
        import minicode.tools.task as task_mod
        from minicode.task_outcome import canonicalize_task_outcome
        from minicode.tooling import ToolContext
        from minicode.tools.task import task_tool

        globals()["task_module"] = task_mod
        globals()["canonicalize_task_outcome"] = canonicalize_task_outcome
        globals()["ToolContext"] = ToolContext
        globals()["task_tool"] = task_tool
        _IMPORTS_DONE = True


def test_run_dispatches_custom_profile_type(tmp_path, monkeypatch):
    _dispatch_imports()
    _write(tmp_path, "test-locator.md", _VALID_FRONTMATTER)
    captured: dict = {}

    def fake_run_agent_turn(**kwargs):
        captured["messages"] = kwargs["messages"]
        captured["max_steps"] = kwargs["max_steps"]
        kwargs["outcome_capture"].record(
            canonicalize_task_outcome("success", 0)
        )
        return [{"role": "assistant", "content": "located tests"}]

    monkeypatch.setattr(task_module, "run_agent_turn", fake_run_agent_turn)
    result = task_tool.run(
        {
            "description": "locate tests",
            "prompt": "find the tests covering billing",
            "agent_type": "test-locator",
        },
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
    )
    assert result.ok, result.output
    assert captured["max_steps"] == 8
    system = captured["messages"][0]["content"].lower()
    assert "test-locator agent" in system


def test_unknown_type_lists_available_and_load_errors(tmp_path):
    _dispatch_imports()
    _write(tmp_path, "broken.md", "no frontmatter")
    result = task_module._run(
        {"description": "x", "prompt": "y", "agent_type": "ghost"},
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
    )
    assert not result.ok
    assert "ghost" in result.output
    assert "broken.md" in result.output


def test_validate_rejects_malformed_type_name():
    _dispatch_imports()
    with pytest.raises(ValueError):
        task_module._validate(
            {"description": "x", "prompt": "y", "agent_type": "Bad Name"}
        )


def test_custom_type_is_concurrency_safe_and_read_only():
    _dispatch_imports()
    assert task_module._call_is_concurrency_safe(
        {"agent_type": "test-locator"}
    )
    assert not task_module._call_is_concurrency_safe(
        {"agent_type": "general"}
    )
    profile = task_module._capability_for(
        {"description": "x", "prompt": "y", "agent_type": "test-locator"}
    )
    assert profile.effects.read_only is True


# ---------------------------------------------------------------------------
# Task 4: per-turn dynamic task-tool description and schema enum
# ---------------------------------------------------------------------------


def test_task_tool_description_lists_custom_type(tmp_path):
    _dispatch_imports()
    _write(tmp_path, "test-locator.md", _VALID_FRONTMATTER)
    dynamic = task_module.build_task_tool_for_project(str(tmp_path))
    assert "test-locator" in dynamic.description
    assert "Locate failing tests and their fixtures." in dynamic.description
    enum = dynamic.input_schema["properties"]["agent_type"]["enum"]
    assert "test-locator" in enum and "workflow" in enum
    baseline = task_module.build_task_tool_for_project("/tmp")
    assert "test-locator" not in baseline.description
    baseline_enum = baseline.input_schema["properties"]["agent_type"]["enum"]
    assert baseline_enum == ["explore", "plan", "general", "workflow"]


def test_registry_uses_dynamic_task_tool(tmp_path):
    _dispatch_imports()
    from minicode.tools import create_default_tool_registry

    _write(tmp_path, "test-locator.md", _VALID_FRONTMATTER)
    registry = create_default_tool_registry(
        str(tmp_path),
        include_mcp=False,
        task_tool_definition=task_module.build_task_tool_for_project(str(tmp_path)),
    )
    assert "test-locator" in registry.find("task").description


# ---------------------------------------------------------------------------
# Task 5: end-to-end — real filtered registry and loop boundaries
# ---------------------------------------------------------------------------


def test_custom_type_filtered_registry_has_only_whitelisted_tools(tmp_path):
    _dispatch_imports()
    from minicode.tools import create_default_tool_registry

    _write(tmp_path, "test-locator.md", _VALID_FRONTMATTER)
    merged = resolve_agent_types(tmp_path)
    allowed = merged["test-locator"]["allowed_tools"]
    full_tools = create_default_tool_registry(str(tmp_path), include_mcp=False)
    child = full_tools.filtered(lambda tool: tool.name in allowed)
    names = {tool.name for tool in child.list()}
    assert {"grep_files", "read_file"} <= names
    assert "edit_file" not in names
    assert "write_file" not in names
    assert "task" not in names


def test_spawn_rejected_for_custom_type(tmp_path):
    _dispatch_imports()
    _write(tmp_path, "test-locator.md", _VALID_FRONTMATTER)
    with pytest.raises(ValueError, match="read-only explore or plan"):
        task_module._validate(
            {
                "action": "spawn",
                "description": "locate tests",
                "prompt": "find tests",
                "agent_type": "test-locator",
            }
        )


def test_continuation_rejected_for_custom_type(tmp_path):
    _dispatch_imports()
    _write(tmp_path, "test-locator.md", _VALID_FRONTMATTER)
    with pytest.raises(ValueError, match="read-only"):
        task_module._validate(
            {
                "description": "follow-up",
                "prompt": "which test?",
                "agent_type": "test-locator",
                "continue_subagent_id": "sub_" + "1" * 32,
            }
        )


def test_custom_type_real_loop_round_trip(tmp_path, monkeypatch):
    _dispatch_imports()
    from minicode.tools.task import _run as task_run

    _write(tmp_path, "test-locator.md", _VALID_FRONTMATTER)
    (tmp_path / "needle.txt").write_text("found-it", encoding="utf-8")

    class _ScriptedModel:
        def __init__(self, steps):
            self._steps = steps
            self.calls = 0

        def next(self, messages, on_stream_chunk=None):
            step = self._steps[self.calls]
            self.calls += 1
            return step

    from minicode.types import AgentStep

    model = _ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "1",
                        "toolName": "grep_files",
                        "input": {"pattern": "found-it", "path": "."},
                    }
                ],
            ),
            AgentStep(
                type="assistant",
                content="<final> needle.txt contains found-it",
            ),
        ]
    )

    import minicode.subagent_model_routing as routing_module

    monkeypatch.setattr(
        routing_module,
        "create_subagent_model_adapter",
        lambda runtime, agent_type, *, tools: model,
    )
    result = task_run(
        {
            "description": "locate tests",
            "prompt": "grep for found-it",
            "agent_type": "test-locator",
        },
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
    )
    assert result.ok, result.output
    assert "Type: test-locator" in result.output
    assert "needle.txt contains found-it" in result.output
    assert "=== SUBAGENT RESULT v1 ===" in result.output
