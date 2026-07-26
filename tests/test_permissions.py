from pathlib import Path

import pytest

import minicode.permissions as permissions_module
from minicode.permissions import PermissionManager, _classify_dangerous_command, _is_within_directory


@pytest.fixture(autouse=True)
def isolated_permission_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store_path = tmp_path / "home" / "permissions.json"
    monkeypatch.setattr(permissions_module, "MINI_CODE_PERMISSIONS_PATH", store_path)
    permissions_module._normalize_path_cached.cache_clear()
    yield store_path
    permissions_module._normalize_path_cached.cache_clear()


def test_permission_manager_uses_prompt_for_external_path(tmp_path: Path) -> None:
    external = tmp_path.parent / "outside.txt"
    manager = PermissionManager(str(tmp_path), prompt=lambda request: {"decision": "allow_once"})
    manager.ensure_path_access(str(external), "read")


def test_permission_manager_denies_external_path_without_prompt(tmp_path: Path) -> None:
    external = tmp_path.parent / "outside.txt"
    manager = PermissionManager(str(tmp_path))
    with pytest.raises(RuntimeError):
        manager.ensure_path_access(str(external), "read")


def test_allow_always_persists_external_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    external_dir = tmp_path / "shared"
    workspace.mkdir()
    external_dir.mkdir()
    first = external_dir / "first.txt"
    second = external_dir / "second.txt"

    manager = PermissionManager(
        str(workspace),
        prompt=lambda request: {"decision": "allow_always"},
    )
    manager.ensure_path_access(str(first), "read")

    reloaded = PermissionManager(str(workspace), prompt=None)
    reloaded.ensure_path_access(str(second), "read")


def test_deny_always_persists_external_directory_and_wins(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    external_dir = tmp_path / "shared"
    workspace.mkdir()
    external_dir.mkdir()
    target = external_dir / "blocked.txt"

    manager = PermissionManager(
        str(workspace),
        prompt=lambda request: {"decision": "deny_always"},
    )
    with pytest.raises(RuntimeError, match="Access denied"):
        manager.ensure_path_access(str(target), "read")

    reloaded = PermissionManager(
        str(workspace),
        prompt=lambda request: pytest.fail("persisted deny should not reprompt"),
    )
    with pytest.raises(RuntimeError, match="Access denied"):
        reloaded.ensure_path_access(str(target), "read")


def test_dangerous_command_allow_always_persists(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    manager = PermissionManager(
        str(workspace),
        prompt=lambda request: {"decision": "allow_always"},
    )
    manager.ensure_command("git", ["reset", "--hard"], str(workspace))

    reloaded = PermissionManager(
        str(workspace),
        prompt=lambda request: pytest.fail("persisted command allow should not reprompt"),
    )
    reloaded.ensure_command("git", ["reset", "--hard"], str(workspace))


def test_dangerous_command_deny_always_persists(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    manager = PermissionManager(
        str(workspace),
        prompt=lambda request: {"decision": "deny_always"},
    )
    with pytest.raises(RuntimeError, match="Command denied"):
        manager.ensure_command("git", ["clean", "-fd"], str(workspace))

    reloaded = PermissionManager(
        str(workspace),
        prompt=lambda request: pytest.fail("persisted command deny should not reprompt"),
    )
    with pytest.raises(RuntimeError, match="Command denied"):
        reloaded.ensure_command("git", ["clean", "-fd"], str(workspace))


def test_edit_deny_with_feedback_surfaces_guidance(tmp_path: Path) -> None:
    target = tmp_path / "demo.py"
    target.write_text("old\n", encoding="utf-8")
    manager = PermissionManager(
        str(tmp_path),
        prompt=lambda request: {
            "decision": "deny_with_feedback",
            "feedback": "Please keep this file stable.",
        },
    )

    with pytest.raises(RuntimeError, match="Please keep this file stable"):
        manager.ensure_edit(str(target), "- old\n+ new\n")


def test_turn_scoped_edit_permissions_reset_between_turns(tmp_path: Path) -> None:
    target = tmp_path / "demo.py"
    target.write_text("old\n", encoding="utf-8")
    prompts: list[dict] = []

    def prompt(request: dict):
        prompts.append(request)
        return {"decision": "allow_turn"}

    manager = PermissionManager(str(tmp_path), prompt=prompt)
    manager.begin_turn()
    manager.ensure_edit(str(target), "- old\n+ new\n")
    manager.ensure_edit(str(target), "- old\n+ newer\n")
    assert len(prompts) == 1

    manager.end_turn()
    manager.ensure_edit(str(target), "- old\n+ newest\n")
    assert len(prompts) == 2


def test_allow_all_turn_applies_to_multiple_files_for_one_turn(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("old\n", encoding="utf-8")
    second.write_text("old\n", encoding="utf-8")
    prompts: list[dict] = []

    def prompt(request: dict):
        prompts.append(request)
        return {"decision": "allow_all_turn"}

    manager = PermissionManager(str(tmp_path), prompt=prompt)
    manager.begin_turn()
    manager.ensure_edit(str(first), "- old\n+ new\n")
    manager.ensure_edit(str(second), "- old\n+ new\n")
    assert len(prompts) == 1

    manager.end_turn()
    manager.ensure_edit(str(first), "- old\n+ newest\n")
    assert len(prompts) == 2


@pytest.mark.parametrize(
    ("command", "args", "expected"),
    [
        ("git", ["reset", "--hard"], "discard local changes"),
        ("git", ["push", "--force"], "rewrites remote history"),
        ("rm", ["-rf", "build"], "catastrophic data loss"),
        ("python", ["script.py"], "arbitrary local code"),
        ("chmod", ["777", "file"], "opens permissions"),
        # Absolute interpreter paths must classify like their bare names —
        # the shell-snippet execution path invokes $SHELL by absolute path.
        ("/bin/zsh", ["-lc", "python3 -c 'x'; true"], "arbitrary local code"),
        ("/bin/sh", ["-c", "echo hi > out"], "arbitrary local code"),
        ("/usr/bin/python3", ["script.py"], "arbitrary local code"),
    ],
)
def test_destructive_command_classification(command: str, args: list[str], expected: str) -> None:
    reason = _classify_dangerous_command(command, args)

    assert reason is not None
    assert expected in reason


def test_default_mode_prompt_action_does_not_silently_allow(tmp_path: Path) -> None:
    """DEFAULT mode returns action="prompt" for unclassified commands; that
    must reach the approval flow instead of silently allowing the command."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    prompts: list[dict] = []
    manager = PermissionManager(
        str(workspace),
        prompt=lambda request: prompts.append(request) or {"decision": "allow_once"},
    )
    manager.ensure_command("npm", ["install"], str(workspace))
    assert prompts, "non-dangerous command in DEFAULT mode should prompt, not pass silently"

    # Once approved for the session, the same signature does not reprompt.
    manager.ensure_command("npm", ["install"], str(workspace))
    assert len(prompts) == 1


def test_default_mode_without_prompt_rejects_unclassified_command(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    manager = PermissionManager(str(workspace), prompt=None)
    with pytest.raises(RuntimeError, match="requires approval"):
        manager.ensure_command("npm", ["install"], str(workspace))


def test_windows_style_directory_match_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(permissions_module, "_is_win", True)

    assert _is_within_directory("/Users/Alice/Repo", "/users/alice/repo/src/main.py")
