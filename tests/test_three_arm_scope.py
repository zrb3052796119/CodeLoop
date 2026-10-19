"""Evaluation scope tests; no provider or external commands are executed."""

from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

import pytest

from scripts.three_arm_scope import (
    REGRESSION_TEST, ScopeDenied, VerificationScope,
    verification_environment, workspace_path,
)


FROZEN = (
    "python3", "-m", "pytest", "-q", "tests/test_one.py",
    "tests/test_two.py", "-k", "not slow and (read or write)",
)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    (root / "tests").mkdir(parents=True)
    for name in ("test_one.py", "test_two.py", "test_other.py"):
        (root / "tests" / name).write_text("def test_ok(): pass\n")
    return root


@pytest.fixture
def scope():
    return VerificationScope(FROZEN)


def test_exact_argv_or_quoted_snippet_and_harmless_verbosity(scope, workspace):
    expected = (sys.executable, *FROZEN[1:])
    assert scope.require_command(FROZEN, workspace=workspace) == expected
    assert scope.require_command(shlex.join(FROZEN), workspace=workspace) == expected
    assert scope.require_command("python3", args=FROZEN[1:], workspace=workspace) == expected
    assert scope.require_command(("pytest", *FROZEN[3:]), workspace=workspace) == expected
    assert scope.require_command((*FROZEN, "-vv"), workspace=workspace)[-1] == "-vv"


def test_absolute_workspace_targets_and_new_regression_test_are_legal(scope, workspace):
    actual = list(FROZEN)
    actual[4] = str(workspace / actual[4])
    assert scope.require_command(actual, workspace=workspace)[4] == "tests/test_one.py"
    # The authorized new test may not exist yet; pytest owns that diagnostic.
    assert scope.require_command(("pytest", "-q", REGRESSION_TEST), workspace=workspace) == (
        sys.executable, "-m", "pytest", "-q", REGRESSION_TEST,
    )


@pytest.mark.parametrize("command", [
    "pytest tests/test_one.py; cat /tmp/answer.py",
    "pytest tests/test_one.py && cat /tmp/answer.py",
    "pytest tests/test_one.py | cat /tmp/answer.py",
    "pytest tests/test_one.py >/tmp/answer.py",
    "pytest $(cat /tmp/answer.py)",
    "pytest `cat /tmp/answer.py`",
    "pytest tests/test_one.py\ncat /tmp/answer.py",
    "env PYTEST_PLUGINS=hidden pytest tests/test_one.py",
    "sh -c 'pytest tests/test_one.py'",
    "python3 -c 'print(1)'",
    "python3 -m compileall /tmp/hidden",
    "cat /tmp/hidden.py",
])
def test_shell_and_unpublished_executables_are_denied(scope, workspace, command):
    with pytest.raises(ScopeDenied):
        scope.require_command(command, workspace=workspace)


@pytest.mark.parametrize("extra", [
    ["/tmp/hidden.py"], ["../hidden.py"], ["--pyargs", "hidden"],
    ["-p", "hidden_plugin"], ["-c", "/tmp/pytest.ini"],
    ["--rootdir=/tmp"], ["--confcutdir=/tmp"],
    ["-o", "pythonpath=/tmp"], ["--override-ini=addopts=-p hidden"],
    ["--basetemp=/tmp/hidden"], ["@/tmp/options"], ["tests/test_one.py::test_ok"],
])
def test_external_targets_configs_plugins_and_extra_flags_denied(scope, workspace, extra):
    with pytest.raises(ScopeDenied):
        scope.require_command((*FROZEN, *extra), workspace=workspace)


def test_subsets_changed_filters_cwd_and_other_python_are_denied(scope, workspace):
    for argv in (
        ("pytest", "tests/test_one.py"),
        (*FROZEN[:-1], "read"),
        ("/tmp/python3", *FROZEN[1:]),
        ("pytest", "tests/test_other.py"),
    ):
        with pytest.raises(ScopeDenied):
            scope.require_command(argv, workspace=workspace)
    for cwd in ("tests", str(workspace.parent)):
        with pytest.raises(ScopeDenied):
            scope.require_command(FROZEN, workspace=workspace, cwd=cwd)
    scope.require_command(FROZEN, workspace=workspace, cwd=str(workspace))


def test_test_runner_maps_only_frozen_targets_or_new_test(scope, workspace):
    expected = scope.require_command(FROZEN, workspace=workspace)
    for path in (".", "tests", "tests/test_one.py", "tests/test_two.py"):
        assert scope.test_runner_argv({"path": path}, workspace=workspace) == expected
    regression = scope.test_runner_argv({"path": REGRESSION_TEST}, workspace=workspace)
    assert regression[-1] == REGRESSION_TEST
    for request in (
        {"path": "/tmp/hidden.py"}, {"path": "tests/test_other.py"},
        {"coverage": True}, {"pattern": "test_ok"}, {"framework": "unittest"},
    ):
        with pytest.raises(ScopeDenied):
            scope.test_runner_argv(request, workspace=workspace)


def test_workflow_verifier_checks_actual_snapshot_not_original_root(scope, workspace, tmp_path):
    snapshot = tmp_path / "snapshot"
    (snapshot / "tests").mkdir(parents=True)
    scope.require_verification((SimpleNamespace(argv=FROZEN),), workspace=snapshot)
    # An original-workspace absolute target is external to the frozen clone.
    outside = list(FROZEN)
    outside[4] = str(workspace / "tests/test_one.py")
    with pytest.raises(ScopeDenied):
        scope.require_verification((SimpleNamespace(argv=outside),), workspace=snapshot)
    with pytest.raises(ScopeDenied):
        scope.require_verification((SimpleNamespace(argv=("git", "show", "HEAD")),), workspace=snapshot)


@pytest.mark.parametrize("tool", ["read_file", "list_files", "grep_files", "file_tree"])
def test_read_tools_reject_direct_external_and_parent_paths(scope, workspace, tool):
    for path in (str(workspace.parent), "../hidden", "~/.mini-code/settings.json"):
        with pytest.raises(ScopeDenied):
            scope.require_tool_read(tool, {"path": path}, workspace=workspace)
    scope.require_tool_read(tool, {"path": "tests"}, workspace=workspace)


def test_internal_links_allowed_external_links_rejected_even_through_alias(scope, workspace, tmp_path):
    (workspace / "alias.py").symlink_to(workspace / "tests/test_one.py")
    scope.require_tool_read("read_file", {"path": "alias.py"}, workspace=workspace)
    (workspace / "tests/alias").symlink_to(workspace / "tests", target_is_directory=True)
    scope.require_tool_read("file_tree", {"path": "tests"}, workspace=workspace)
    # Reading a separate subdirectory can reach tests through an internal link.
    (workspace / "subdir").mkdir()
    (workspace / "subdir/linked_tests").symlink_to(workspace / "tests", target_is_directory=True)
    external = tmp_path / "hidden.py"
    external.write_text("secret")
    (workspace / "tests/escape.py").symlink_to(external)
    for tool, path in (("read_file", "tests/escape.py"), ("grep_files", "."), ("file_tree", "subdir")):
        with pytest.raises(ScopeDenied):
            scope.require_tool_read(tool, {"path": path}, workspace=workspace)
    with pytest.raises(ScopeDenied):
        workspace_path(workspace, "tests/escape.py")


def test_frozen_verifier_path_symlink_cannot_escape(scope, workspace, tmp_path):
    (workspace / "tests/test_one.py").unlink()
    external = tmp_path / "hidden.py"
    external.write_text("secret")
    (workspace / "tests/test_one.py").symlink_to(external)
    with pytest.raises(ScopeDenied):
        scope.require_command(FROZEN, workspace=workspace)


def test_diff_only_checks_paths_when_disk_content_requested(scope, workspace):
    scope.require_tool_read("diff_viewer", {"files": [{"path": "/display/label", "before": "a", "after": "b"}]}, workspace=workspace)
    with pytest.raises(ScopeDenied):
        scope.require_tool_read("diff_viewer", {"files": [{"path": "/tmp/hidden.py", "before": "__file__"}]}, workspace=workspace)


def test_pytest_environment_blocks_external_plugin_config_and_keeps_home(workspace):
    (workspace / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n')
    base = {
        "HOME": "/original-home", "PATH": "/usr/bin", "PYTEST_PLUGINS": "hidden",
        "PYTEST_ADDOPTS": "-c /tmp/hidden.ini", "PYTHONPATH": "/tmp/answers",
        "PYTHONSTARTUP": "/tmp/hidden.py", "PYTHONINSPECT": "1",
    }
    clean = verification_environment(base, workspace=workspace)
    assert clean["HOME"] == base["HOME"]
    assert clean["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == clean["PYTHONNOUSERSITE"] == "1"
    assert "PYTEST_PLUGINS" not in clean and "PYTHONPATH" not in clean
    assert shlex.split(clean["PYTEST_ADDOPTS"]) == [
        "-c", str(workspace / "pyproject.toml"),
        f"--rootdir={workspace}", f"--confcutdir={workspace}",
    ]
    assert base["PYTEST_ADDOPTS"] == "-c /tmp/hidden.ini"
