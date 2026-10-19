"""A broken profile must not disable healthy agents or their result transport."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import minicode.tools.task as task_module
from minicode.subagent_mailbox import SubagentMailbox
from minicode.subagent_profiles import load_agent_profiles
from minicode.subagent_result import extract_subagent_result
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext


def _profile(root: Path) -> Path:
    directory = root / ".mini-code" / "agents"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "test-locator.md"
    target.write_text(
        "---\nname: test-locator\ndescription: Locate tests.\nmax_turns: 8\n"
        "allowed_tools: read_file\n---\nFind the relevant tests.\n",
        encoding="utf-8",
    )
    return target


def test_broken_profile_symlink_does_not_disable_healthy_agent_menu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    target = _profile(tmp_path)
    try:
        target.with_name("broken.md").symlink_to(tmp_path / "missing-profile.md")
    except OSError:
        pytest.skip("host cannot create symlinks")
    profiles, errors = load_agent_profiles(tmp_path)
    assert "test-locator" in profiles
    assert any("broken.md" in error for error in errors)
    menu = task_module.build_task_tool_for_project(str(tmp_path))
    assert "test-locator" in menu.input_schema["properties"]["agent_type"]["enum"]
    assert "Rejected profile files" in menu.description


def test_rejected_only_profiles_are_visible_while_builtins_remain_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    target = _profile(tmp_path)
    target.write_text("malformed profile", encoding="utf-8")
    menu = task_module.build_task_tool_for_project(str(tmp_path))
    assert "Rejected profile files" in menu.description
    assert "test-locator" not in menu.input_schema["properties"]["agent_type"]["enum"]
    assert {"explore", "plan", "workflow"} <= set(
        menu.input_schema["properties"]["agent_type"]["enum"]
    )


def test_profile_reload_detects_same_size_preserved_mtime_edit(tmp_path: Path) -> None:
    target = _profile(tmp_path)
    before = target.stat()
    first, _ = load_agent_profiles(tmp_path, user_root=tmp_path / "home")
    assert first["test-locator"]["max_turns"] == 8
    target.write_text(target.read_text(encoding="utf-8").replace("max_turns: 8", "max_turns: 7"), encoding="utf-8")
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    second, _ = load_agent_profiles(tmp_path, user_root=tmp_path / "home")
    assert second["test-locator"]["max_turns"] == 7


def test_custom_profile_can_publish_a_report_without_declaring_transport_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _profile(tmp_path)
    calls = []
    monkeypatch.setattr(task_module, "_build_sub_agent_system_prompt", lambda **kw: (kw["agent_def"]["system_prompt"], None))
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", lambda *a, **kw: SimpleNamespace(runtime={"model": "fake"}))
    monkeypatch.setattr("minicode.intent_semantic_classifier.build_semantic_intent_adapter", lambda *a, **kw: None)

    def finished(**kwargs):
        calls.append(kwargs)
        names = {tool.name for tool in kwargs["tools"].list()}
        assert "subagent_note_write" in names
        assert not {"edit_file", "write_file", "run_command", "task"} & names
        key = re.search(r"mailbox key `([^`]+)`", kwargs["messages"][0]["content"]).group(1)
        published = kwargs["tools"].execute(
            "subagent_note_write",
            {"key": key, "content": json.dumps({
                "resultVersion": 1, "summary": "tests/test_login.py is relevant.",
                "files": [{"path": "tests/test_login.py", "action": "read"}],
                "risks": [], "verification": {"status": "not_run", "checks": []},
            })},
            ToolContext(cwd=str(tmp_path), _subagent_mailbox=kwargs["subagent_mailbox"]),
        )
        assert published.ok
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [*kwargs["messages"], {"role": "assistant", "content": "<final> Located tests/test_login.py."}]

    monkeypatch.setattr(task_module, "run_agent_turn", finished)
    result = task_module.task_tool.run(
        {"description": "locate tests", "prompt": "Locate login tests.", "agent_type": "test-locator"},
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}, _subagent_mailbox=SubagentMailbox()),
    )
    assert result.ok, result.output
    assert extract_subagent_result(result.output)["contractStatus"] == "reported"
    assert len(calls) == 1
