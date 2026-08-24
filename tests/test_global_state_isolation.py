from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.global_state_isolation import (
    FormalStateChanged,
    GlobalStateGuard,
    IsolatedTestHome,
    assert_minicode_not_preloaded,
    compare_snapshots,
    snapshot_paths,
)


def test_guard_detects_file_modification_without_rollback(tmp_path: Path) -> None:
    protected = tmp_path / "formal.json"
    protected.write_text('{"state": "before"}\n', encoding="utf-8")
    guard = GlobalStateGuard([protected])
    guard.capture_start()

    protected.write_text('{"state": "after"}\n', encoding="utf-8")

    with pytest.raises(FormalStateChanged, match="content_hash"):
        guard.assert_unchanged()
    assert protected.read_text(encoding="utf-8") == '{"state": "after"}\n'


def test_guard_detects_new_file_without_deleting_it(tmp_path: Path) -> None:
    protected = tmp_path / "created-during-test.json"
    guard = GlobalStateGuard([protected])
    guard.capture_start()

    protected.write_text("{}\n", encoding="utf-8")

    with pytest.raises(FormalStateChanged, match="created"):
        guard.assert_unchanged()
    assert protected.exists()


def test_guard_detects_new_file_anywhere_under_protected_tree(tmp_path: Path) -> None:
    protected_root = tmp_path / ".mini-code"
    protected_root.mkdir()
    guard = GlobalStateGuard([], protected_roots=[protected_root])
    guard.capture_start()
    unexpected = protected_root / "tasks" / "unexpected.json"
    unexpected.parent.mkdir()
    unexpected.write_text("{}\n", encoding="utf-8")

    with pytest.raises(FormalStateChanged, match=r"tasks[\\/]unexpected\.json.*created"):
        guard.assert_unchanged()
    assert unexpected.exists()


def test_snapshot_comparison_reports_only_path_and_change_types(tmp_path: Path) -> None:
    protected = tmp_path / "private.json"
    private_value = "private-value-must-not-appear"
    protected.write_text(private_value, encoding="utf-8")
    before = snapshot_paths([protected])
    protected.write_text("changed-private-value", encoding="utf-8")

    changes = compare_snapshots(before, snapshot_paths([protected]))
    serialized = json.dumps(changes, sort_keys=True)

    assert len(changes) == 1
    assert changes[0]["path"] == str(protected)
    change_types = set(changes[0]["changes"])
    assert {"content_hash", "size"} <= change_types
    assert change_types <= {"content_hash", "size", "mtime_ns"}
    assert private_value not in serialized
    assert "changed-private-value" not in serialized


def test_isolated_home_uses_process_worker_and_minimal_secret_free_config(tmp_path: Path) -> None:
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    env = {
        "HOME": str(real_home),
        "USERPROFILE": str(real_home),
        "OPENAI_API_KEY": "must-be-scrubbed",
        "ANTHROPIC_API_KEY": "must-be-scrubbed",
    }

    isolation = IsolatedTestHome.create(
        real_home=real_home,
        environ=env,
        temp_root=tmp_path,
        process_id=4242,
        worker_id="gw3",
    )
    settings = json.loads(isolation.settings_path.read_text(encoding="utf-8"))

    assert isolation.home.name.startswith("minicode-pytest-4242-gw3-")
    assert env["HOME"] == str(isolation.home)
    assert env["USERPROFILE"] == str(isolation.home)
    assert env["XDG_CONFIG_HOME"] == str(isolation.home / ".config")
    assert env["MINI_CODE_MODEL_MODE"] == "mock"
    assert env["MINI_CODE_SHOW_GUIDE"] == "0"
    assert env["MINI_CODE_TOOL_PROFILE"] == "core"
    assert env["MINI_CODE_MODEL"] == "claude-sonnet-4-20250514"
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert env["ANTHROPIC_AUTH_TOKEN"] == "pytest-mock-auth-not-a-secret"
    assert settings == {"model": "claude-sonnet-4-20250514", "toolProfile": "core"}
    if os.name == "posix":
        assert stat.S_IMODE(isolation.home.stat().st_mode) == 0o700
        assert stat.S_IMODE(isolation.settings_path.stat().st_mode) == 0o600


def test_isolated_home_reset_preserves_only_minimal_config(tmp_path: Path) -> None:
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    env: dict[str, str] = {}
    isolation = IsolatedTestHome.create(real_home=real_home, environ=env, temp_root=tmp_path)
    mini_code_dir = isolation.home / ".mini-code"
    for relative in (
        "memory/memory.json",
        "sessions/a.json",
        "sessions_index.json",
        "history.json",
        "context_state.json",
        "tasks/task.json",
        "task_graphs/graph.json",
        "approval_audit.json",
        "minicode.log",
    ):
        target = mini_code_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("test-state", encoding="utf-8")

    isolation.reset()

    assert sorted(path.relative_to(mini_code_dir).as_posix() for path in mini_code_dir.rglob("*")) == [
        "settings.json"
    ]


def test_preimport_guard_identifies_minicode_module_and_path() -> None:
    with pytest.raises(RuntimeError, match=r"minicode\.session.*real-home"):
        assert_minicode_not_preloaded(
            {"minicode.session": object()},
            real_home=Path("/private/real-home"),
        )


def test_config_and_global_paths_point_inside_process_isolated_home(
    minicode_test_home: Path,
) -> None:
    from minicode import config, context_manager, cybernetic_supervisor, history
    from minicode import logging_config, memory, session, task_graph, task_tracker
    from minicode.user_profile import UserProfileManager

    expected = minicode_test_home / ".mini-code"
    manager = memory.MemoryManager(project_root=minicode_test_home / "workspace")
    profile = UserProfileManager(cwd=minicode_test_home / "workspace")

    paths = [
        config.MINI_CODE_DIR,
        config.MINI_CODE_SETTINGS_PATH,
        memory.MINI_CODE_DIR,
        manager.paths.user_memory,
        session.MINI_CODE_DIR,
        session.SESSIONS_DIR,
        session._session_index_file(),
        context_manager.MINI_CODE_DIR / "context_state.json",
        history.MINI_CODE_HISTORY_PATH,
        logging_config.LOG_FILE,
        task_tracker.MINI_CODE_DIR / "tasks",
        task_graph._TASK_GRAPH_DIR,
        cybernetic_supervisor.SUPERVISOR_STATE_PATH,
        profile.global_path,
    ]

    assert all(path == expected or path.is_relative_to(expected) for path in paths)


def test_user_memory_write_stays_outside_real_home(
    minicode_test_home: Path,
    minicode_real_home: Path,
    tmp_path: Path,
) -> None:
    from minicode.memory import MemoryManager, MemoryScope

    manager = MemoryManager(project_root=tmp_path / "workspace")
    manager.add_entry(MemoryScope.USER, "test", "isolated user memory")

    assert (minicode_test_home / ".mini-code/memory/memory.json").exists()
    assert not (minicode_real_home / ".mini-code/memory/isolated-test-sentinel").exists()


def test_session_save_stays_outside_real_home(
    minicode_test_home: Path,
    minicode_real_home: Path,
) -> None:
    from minicode.session import create_new_session, save_session

    session = create_new_session(workspace="/tmp/isolated-session-workspace")
    save_session(session)

    assert (minicode_test_home / ".mini-code/sessions_index.json").exists()
    assert not (minicode_real_home / ".mini-code/sessions/isolated-test-sentinel.json").exists()


def test_two_workspaces_share_user_memory_inside_one_test(tmp_path: Path) -> None:
    from minicode.memory import MemoryManager, MemoryScope

    first = MemoryManager(project_root=tmp_path / "first")
    first.add_entry(MemoryScope.USER, "preference", "shared only inside this test")
    second = MemoryManager(project_root=tmp_path / "second")

    assert any(entry.content == "shared only inside this test" for entry in second.memories[MemoryScope.USER].entries)


def test_user_memory_starts_clean_in_independent_test(minicode_test_home: Path) -> None:
    assert not (minicode_test_home / ".mini-code/memory/memory.json").exists()


def test_session_index_starts_clean_in_independent_test(minicode_test_home: Path) -> None:
    assert not (minicode_test_home / ".mini-code/sessions_index.json").exists()


def test_subprocess_inherits_isolated_home(minicode_test_home: Path) -> None:
    command = [
        sys.executable,
        "-c",
        (
            "import json, os; from pathlib import Path; from minicode.config import MINI_CODE_DIR; "
            "print(json.dumps({'home': os.environ['HOME'], 'mini': str(MINI_CODE_DIR)}))"
        ),
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True, env=os.environ.copy())
    observed = json.loads(completed.stdout)

    assert observed["home"] == str(minicode_test_home)
    assert observed["mini"] == str(minicode_test_home / ".mini-code")


def test_plain_pytest_subprocess_automatically_installs_its_own_home(
    minicode_test_home: Path,
) -> None:
    env = os.environ.copy()
    env["MINICODE_ISOLATION_PROBE_PARENT_HOME"] = str(minicode_test_home)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_global_state_isolation.py::test_plain_command_probe",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_plain_command_probe(minicode_test_home: Path) -> None:
    parent_home = os.environ.get("MINICODE_ISOLATION_PROBE_PARENT_HOME")
    if parent_home:
        assert minicode_test_home != Path(parent_home)
