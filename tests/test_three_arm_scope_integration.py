"""Real local pytest subprocesses exercise evaluator guards; no model calls."""

from contextlib import ExitStack
from pathlib import Path
import shlex
import sys
import time

import pytest

from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.tooling import ToolContext, ToolResult
from minicode.tools import create_default_tool_registry
from scripts import run_three_arm_real as live


FROZEN = ("python3", "-m", "pytest", "-q", "tests/test_visible.py")
VISIBLE_TEST = '''
import os
from pathlib import Path

def test_visible():
    assert os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == "1"
    assert "PYTEST_PLUGINS" not in os.environ
    assert "PYTHONPATH" not in os.environ
    marker = Path("executed.txt")
    count = int(marker.read_text()) if marker.exists() else 0
    marker.write_text(str(count + 1))
'''


def fixture_workspace(path):
    (path / "tests").mkdir(parents=True)
    (path / "tests/test_visible.py").write_text(VISIBLE_TEST)
    (path / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n')
    return path.resolve()


def invoke(registry, name, data, context):
    tool = registry.find(name)
    return tool.run(tool.validator(data), context)


def test_real_registries_execute_only_allowed_tests_without_parent_policy_leak(tmp_path, monkeypatch):
    workspace = fixture_workspace(tmp_path / "workspace")
    hidden = tmp_path / "test_hidden.py"
    hidden_marker = tmp_path / "hidden_executed.txt"
    hidden.write_text(
        f"from pathlib import Path\ndef test_hidden(): Path({str(hidden_marker)!r}).write_text('bad')\n"
    )
    monkeypatch.setenv("PYTEST_PLUGINS", "must_not_import_external_eval_plugin")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-c /tmp/must_not_read_eval_config.ini")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "external_answers"))
    scope = live.VerificationScope(FROZEN)
    owner = create_default_tool_registry(str(workspace), include_mcp=False, include_user_interaction=False)
    originals = {name: owner.find(name).run for name in owner.list_all()}
    context = ToolContext(cwd=str(workspace), _task_deadline_monotonic=time.monotonic() + 15)
    try:
        parent = live.guard_registry(live.independent_registry(owner, "explicit"), scope)
        child = live.guard_registry(live.independent_registry(owner, "explicit"), scope)
        parent.find("write_file").run = lambda *_: ToolResult(ok=False, output="parent delegation policy")
        with ExitStack() as stack:
            live.install_verification_guards(stack, scope, workspace)
            # The real write tool in a separately constructed leaf stays usable.
            write = invoke(child, "write_file", {"path": "tests/test_eval_regression.py", "content": "def test_regression(): assert 1 + 1 == 2\n"}, context)
            assert write.ok, write.output
            result = invoke(parent, "run_command", {"command": shlex.join(FROZEN), "timeout": 10}, context)
            assert result.ok, result.output
            assert (workspace / "executed.txt").read_text() == "1"
            # test_runner must map to the same actual frozen command; its
            # original --version/discovery path is intentionally bypassed.
            result = invoke(child, "test_runner", {"path": "tests", "timeout": 10}, context)
            assert result.ok, result.output
            assert (workspace / "executed.txt").read_text() == "2"
            result = invoke(child, "test_runner", {"path": "tests/test_eval_regression.py", "timeout": 10}, context)
            assert result.ok, result.output
            for command in (
                ("pytest", "-q", str(hidden)),
                (*FROZEN, "-p", "external_eval_plugin"),
                shlex.join(FROZEN) + " ; cat " + str(hidden),
            ):
                data = {"command": command if isinstance(command, str) else command[0]}
                if not isinstance(command, str):
                    data["args"] = list(command[1:])
                denied = invoke(parent, "run_command", data, context)
                assert not denied.ok and "evaluation_scope_denied" in denied.output
            denied = invoke(child, "test_runner", {"path": str(hidden)}, context)
            assert not denied.ok and "evaluation_scope_denied" in denied.output
            (workspace / "escape.py").symlink_to(hidden)
            denied = invoke(child, "grep_files", {"path": ".", "pattern": "test_hidden"}, context)
            assert not denied.ok and "evaluation_scope_denied" in denied.output
        assert not hidden_marker.exists()
        assert (workspace / "executed.txt").read_text() == "2"
        assert all(owner.find(name).run is original for name, original in originals.items())
        assert child.find("write_file").run is originals["write_file"]
        assert live.independent_registry(owner, "single").find("task") is None
    finally:
        owner.dispose()


def test_real_verifier_method_guard_runs_managed_command_and_rejects_scope_drift(tmp_path, monkeypatch):
    original_root = fixture_workspace(tmp_path / "original")
    snapshot = fixture_workspace(tmp_path / "snapshot")
    scope = live.VerificationScope(FROZEN)
    monkeypatch.setenv("PYTEST_PLUGINS", "must_not_import_external_eval_plugin")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-c /tmp/must_not_read_eval_config.ini")

    class LocalFixtureBackend:
        # Only this synthetic local pytest fixture executes. Production's
        # trusted-executable resolution and managed subprocess are kept real;
        # this test does not claim to validate the OS sandbox implementation.
        def wrap_command(self, *, argv, cwd, verifier_home, extra_read_roots=()):
            assert Path(cwd) == snapshot
            return argv

    verifier = DeterministicVerifier(
        sandbox_backend=LocalFixtureBackend(),
        explicit_trusted_executables={"python3": sys.executable},
    )
    check = VerificationCheck("check_visible", "tests", FROZEN, 10)
    kwargs = {
        "checks": (check,), "cwd": str(snapshot),
        "permission_cwd": str(original_root),
        "workflow_id": "wf_scope", "task_id": "task_scope",
        "execution_assignment_id": "asg_scope", "patch_digest": "sha256:" + "a" * 64,
    }
    original_run = DeterministicVerifier.run
    original_prepare = DeterministicVerifier._prepare_managed_command
    with ExitStack() as stack:
        live.install_verification_guards(stack, scope, original_root)
        result = verifier.run(**kwargs)
        assert result["status"] == "passed", result
        assert result["checks"][0]["exitCode"] == 0
        assert (snapshot / "executed.txt").read_text() == "1"
        assert not (original_root / "executed.txt").exists()
        unauthorized = VerificationCheck("check_extra", "tests", ("python3", "-m", "pytest", "tests/test_extra.py"), 10)
        with pytest.raises(live.ScopeDenied):
            verifier.run(**{**kwargs, "checks": (unauthorized,)})
        with pytest.raises(live.ScopeDenied):
            verifier.run(**{**kwargs, "permission_cwd": str(tmp_path)})
        assert (snapshot / "executed.txt").read_text() == "1"
    assert DeterministicVerifier.run is original_run
    assert DeterministicVerifier._prepare_managed_command is original_prepare
