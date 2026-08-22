"""Focused verification for workflow workspace commit semantics."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

import minicode.tools.task as task_module
from minicode.subagent_mailbox import SubagentMailbox
from minicode.tooling import ToolContext, ToolResult


def _context(workspace: Path, mailbox: SubagentMailbox) -> ToolContext:
    return ToolContext(
        cwd=str(workspace),
        _runtime={"model": "fake"},
        _subagent_mailbox=mailbox,
    )


def _write_review(mailbox: SubagentMailbox, prompt: str) -> None:
    match = re.search(r"review verdict key `([^`]+)`", prompt)
    assert match is not None
    mailbox.write(
        match.group(1),
        json.dumps(
            {
                "reviewVersion": 1,
                "verdict": "approved",
                "blockingFindings": [],
                "warnings": [],
            }
        ),
        author="reviewer",
    )


def test_approved_workflow_applies_isolated_delta(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mailbox = SubagentMailbox()

    def fake_run(input_data: dict, context: ToolContext) -> ToolResult:
        if input_data["description"].startswith("execute:"):
            Path(context.cwd, "approved.txt").write_text("approved", encoding="utf-8")
        if input_data["description"].startswith("review:"):
            _write_review(mailbox, input_data["prompt"])
        return ToolResult(ok=True, output="phase complete")

    monkeypatch.setattr(task_module, "_run", fake_run)
    result = task_module.task_tool.run(
        {
            "description": "approved delta",
            "prompt": "Create approved.txt.",
            "agent_type": "workflow",
        },
        _context(tmp_path, mailbox),
    )

    assert result.ok is True
    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "approved"


def test_parent_conflict_prevents_workflow_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "shared.txt"
    target.write_text("baseline", encoding="utf-8")
    mailbox = SubagentMailbox()

    def fake_run(input_data: dict, context: ToolContext) -> ToolResult:
        if input_data["description"].startswith("execute:"):
            Path(context.cwd, "shared.txt").write_text("workflow", encoding="utf-8")
        if input_data["description"].startswith("review:"):
            target.write_text("external edit", encoding="utf-8")
            _write_review(mailbox, input_data["prompt"])
        return ToolResult(ok=True, output="phase complete")

    monkeypatch.setattr(task_module, "_run", fake_run)
    result = task_module.task_tool.run(
        {
            "description": "conflicting delta",
            "prompt": "Update shared.txt.",
            "agent_type": "workflow",
        },
        _context(tmp_path, mailbox),
    )

    assert result.ok is False
    assert "workflow_commit_failed" in result.output
    assert target.read_text(encoding="utf-8") == "external edit"


def test_git_snapshot_uses_dirty_worktree_without_touching_real_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("committed", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-q",
            "-m",
            "baseline",
        ],
        cwd=tmp_path,
        check=True,
    )
    tracked.write_text("user dirty baseline", encoding="utf-8")
    untracked = tmp_path / "untracked.txt"
    untracked.write_text("user untracked baseline", encoding="utf-8")
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    mailbox = SubagentMailbox()

    def fake_run(input_data: dict, context: ToolContext) -> ToolResult:
        isolated = Path(context.cwd)
        if input_data["description"].startswith("execute:"):
            assert (isolated / "tracked.txt").read_text(encoding="utf-8") == (
                "user dirty baseline"
            )
            assert (isolated / "untracked.txt").read_text(encoding="utf-8") == (
                "user untracked baseline"
            )
            (isolated / "tracked.txt").write_text(
                "approved workflow delta",
                encoding="utf-8",
            )
            (isolated / "untracked.txt").write_text(
                "approved untracked delta",
                encoding="utf-8",
            )
        if input_data["description"].startswith("review:"):
            _write_review(mailbox, input_data["prompt"])
        return ToolResult(ok=True, output="phase complete")

    monkeypatch.setattr(task_module, "_run", fake_run)
    result = task_module.task_tool.run(
        {
            "description": "dirty baseline",
            "prompt": "Update both files.",
            "agent_type": "workflow",
        },
        _context(tmp_path, mailbox),
    )

    assert result.ok is True
    assert tracked.read_text(encoding="utf-8") == "approved workflow delta"
    assert untracked.read_text(encoding="utf-8") == "approved untracked delta"
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    cached_diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=tmp_path,
        check=False,
    )
    assert head_after == head_before
    assert cached_diff.returncode == 0


def test_non_git_escaping_symlink_fails_before_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside baseline", encoding="utf-8")
    (tmp_path / "escape").symlink_to(outside)
    calls = 0

    def fake_run(_input_data: dict, _context: ToolContext) -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult(ok=True, output="must not run")

    monkeypatch.setattr(task_module, "_run", fake_run)
    result = task_module.task_tool.run(
        {
            "description": "symlink escape",
            "prompt": "Write through escape.",
            "agent_type": "workflow",
        },
        _context(tmp_path, SubagentMailbox()),
    )

    assert result.ok is False
    assert "workflow_isolation_unavailable" in result.output
    assert calls == 0
    assert outside.read_text(encoding="utf-8") == "outside baseline"
