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
