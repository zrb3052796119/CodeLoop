"""Git status must report the real index/worktree state without losing paths."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from minicode.tooling import ToolContext
from minicode.tools.git import git_tool


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, timeout=10,
    ).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Ensure fixture operations cannot inherit a caller's repository or index.
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    _git(tmp_path, "init", "-q", "--template=", "--initial-branch=main")
    for name, value in {
        "user.name": "Status regression fixture",
        "user.email": "fixture@example.invalid",
        "core.hooksPath": os.devnull,
        "commit.gpgsign": "false",
    }.items():
        _git(tmp_path, "config", "--local", name, value)
    for name in ("alpha.py", "bravo.py", "charlie.py"):
        (tmp_path / name).write_text("original\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "Create local test fixture")
    return tmp_path


def _status(repo: Path) -> str:
    result = git_tool.run(
        {"action": "status", "max_lines": 50},
        ToolContext(cwd=str(repo), permissions=None),
    )
    assert result.ok, result.output
    return result.output


def test_first_unstaged_file_keeps_path_and_is_not_staged(repo: Path) -> None:
    (repo / "alpha.py").write_text("changed\n", encoding="utf-8")

    output = _status(repo)

    assert "Staged changes: 0" in output
    assert "Unstaged changes: 1" in output
    assert "  [M] alpha.py" in output


def test_untracked_file_does_not_count_as_staged(repo: Path) -> None:
    (repo / "new.py").write_text("new\n", encoding="utf-8")

    output = _status(repo)

    assert "Staged changes: 0" in output
    assert "Unstaged changes: 0" in output
    assert "Untracked files: 1" in output
    assert "  [??] new.py" in output


def test_mixed_file_counts_index_and_worktree_changes(repo: Path) -> None:
    (repo / "alpha.py").write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "alpha.py")
    (repo / "alpha.py").write_text("staged plus unstaged\n", encoding="utf-8")

    output = _status(repo)

    assert "Staged changes: 1" in output
    assert "Unstaged changes: 1" in output
    assert "  [MM] alpha.py" in output


def test_combined_status_counts_each_column_and_untracked_separately(repo: Path) -> None:
    (repo / "alpha.py").write_text("unstaged\n", encoding="utf-8")
    (repo / "bravo.py").write_text("staged\n", encoding="utf-8")
    (repo / "charlie.py").write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "bravo.py", "charlie.py")
    (repo / "charlie.py").write_text("staged plus unstaged\n", encoding="utf-8")
    (repo / "delta.py").write_text("untracked\n", encoding="utf-8")

    output = _status(repo)

    assert "Staged changes: 2" in output
    assert "Unstaged changes: 2" in output
    assert "Untracked files: 1" in output
    assert "  [M] alpha.py" in output
    assert "  [M] bravo.py" in output
    assert "  [MM] charlie.py" in output
    assert "  [??] delta.py" in output


def test_staged_rename_with_unstaged_edit_counts_both_columns(repo: Path) -> None:
    _git(repo, "mv", "alpha.py", "renamed.py")
    (repo / "renamed.py").write_text("original\nnew line\n", encoding="utf-8")

    output = _status(repo)

    assert "Staged changes: 1" in output
    assert "Unstaged changes: 1" in output
    assert "  [RM] alpha.py -> renamed.py" in output


def test_staged_only_file_has_no_unstaged_changes(repo: Path) -> None:
    (repo / "alpha.py").write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "alpha.py")

    output = _status(repo)

    assert "Staged changes: 1" in output
    assert "Unstaged changes: 0" in output
    assert "  [M] alpha.py" in output


def test_clean_repository_remains_clean(repo: Path) -> None:
    assert _status(repo) == "Working tree clean. Nothing to commit."
