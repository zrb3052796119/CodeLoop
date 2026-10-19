"""Primary and delegated implementations receive the same real test feedback."""

from pathlib import Path
import json
import subprocess
import sys

from minicode.agent_loop import run_agent_turn
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.permissions import PermissionManager
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep, ModelAdapter


def test_primary_repairs_actual_test_failure_without_spending_a_test_model_turn(tmp_path, monkeypatch):
    from minicode.implementation_execution import ImplementationSpec
    from minicode.implementation_completion_review import ImplementationCompletionReview
    import minicode.workflow_runner as runner

    def approve(_self, *, checks_receipt, **_kwargs):
        # This test isolates shared automatic-check feedback, not independent
        # semantic review. Preserve the real current-candidate public checks;
        # only the separately tested review-model boundary is substituted.
        assert checks_receipt["status"] == "passed"
        return {"status": "approved", "patchDigest": checks_receipt["patchDigest"],
                "requirements": [], "findings": []}

    monkeypatch.setattr(ImplementationCompletionReview, "assess", approve)
    (tmp_path / "app.py").write_text("value = 1\n")
    (tmp_path / "test_app.py").write_text("from app import value\ndef test_value():\n    assert value == 3\n")
    commands = []
    def command(argv, **kwargs):
        commands.append(kwargs["cwd"])
        return subprocess.run([sys.executable, "-m", "pytest", "-q", "test_app.py"], **kwargs)
    monkeypatch.setattr(runner, "_deterministic_verifier_for_context", lambda *a, **k: DeterministicVerifier(command_runner=command))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *a, **k: None)
    seen = []
    class Model(ModelAdapter):
        def next(self, messages, **kwargs):
            seen.append(str(messages))
            if len(seen) <= 2:
                return AgentStep(type="tool_calls", calls=[{"id": str(len(seen)), "toolName": "edit_file", "input": {
                    "path": "app.py", "old": f"value = {len(seen)}", "new": f"value = {len(seen)+1}"}}])
            return AgentStep(type="assistant", content="<final>Fixed and tested.")
    spec = ImplementationSpec(write_paths=("app.py",), checks=(VerificationCheck("test", "tests", ("python3", "-m", "pytest", "-q", "test_app.py"), 20),), objective="Fix value in app.py")
    run_agent_turn(model=Model(), tools=ToolRegistry([edit_file_tool, write_file_tool]),
        messages=[{"role": "user", "content": "Fix app.py and test it."}], cwd=str(tmp_path),
        implementation_task=True, implementation_spec=spec, enable_work_chain=False, max_steps=20)
    assert "assert value == 3" in seen[0], "published test context must be available before the first request"
    assert "AssertionError" in seen[1], "the first edit must expose its actual failing pytest result"
    assert '"status": "passed"' in seen[2]
    assert len(commands) == 2 and len(seen) == 3
    assert all(Path(cwd) != tmp_path for cwd in commands), "tests must run in isolated copies"
    assert (tmp_path / "app.py").read_text() == "value = 3\n"


def test_primary_verifier_cannot_read_internal_or_parent_denied_files(tmp_path, monkeypatch):
    from minicode.implementation_execution import ImplementationSpec, direct_verification_capability
    from minicode.permissions import PermissionDeniedError
    from minicode.tooling import ToolContext
    import minicode.workflow_runner as runner

    (tmp_path / "app.py").write_text("value = 1\n")
    (tmp_path / ".mini-code-memory").mkdir()
    internal = tmp_path / ".mini-code-memory" / "private.txt"
    internal.write_text("PRIVATE_MEMORY_SENTINEL")
    denied = tmp_path / "restricted.txt"
    denied.write_text("DENIED_SCOPE_SENTINEL")
    (tmp_path / "alias.txt").symlink_to(denied.name)
    (tmp_path / "test_app.py").write_text(
        "from pathlib import Path\n"
        "def test_read_scope():\n"
        "    for name in ('.mini-code-memory/private.txt', 'restricted.txt', 'alias.txt'):\n"
        "        path = Path(name)\n"
        "        assert not path.exists(), path.read_text()\n"
        "    assert Path('app.py').read_text() == 'value = 1\\n'\n"
        "    Path('app.py').write_text('test side effect\\n')\n"
    )
    checks = []
    class Permissions:
        def ensure_path_access(self, path, intent):
            checks.append((Path(path), intent))
            if Path(path) == denied.resolve():
                raise PermissionDeniedError("private scope", code="path_assignment_scope_denied")
        def ensure_command(self, *args, **kwargs):
            pass

    actual_workspaces = []
    def command(argv, **kwargs):
        actual_workspaces.append(Path(kwargs["cwd"]))
        return subprocess.run([sys.executable, "-m", "pytest", "-q", "test_app.py"], **kwargs)
    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
                        lambda *a, **k: DeterministicVerifier(command_runner=command))
    spec = ImplementationSpec(("app.py",), (VerificationCheck(
        "test", "tests", ("python3", "-m", "pytest", "-q", "test_app.py"), 20),), "Fix app")
    capability = direct_verification_capability(spec)
    try:
        result = capability.tool.run({"check": 1}, ToolContext(cwd=str(tmp_path), permissions=Permissions()))
    finally:
        capability.close()
    assert result.ok, result.output
    receipt = json.loads(result.output)
    assert receipt["status"] == "passed" and "1 passed" in receipt["evidence"]["stdout"]["preview"]
    assert "PRIVATE_MEMORY_SENTINEL" not in result.output
    assert "DENIED_SCOPE_SENTINEL" not in result.output
    assert (denied.resolve(), "read") in checks
    assert (tmp_path / "app.py").read_text() == "value = 1\n", "test writes stay inside verification copy"
    assert internal.read_text() == "PRIVATE_MEMORY_SENTINEL" and denied.read_text() == "DENIED_SCOPE_SENTINEL"
    assert all(path != tmp_path and not path.exists() for path in actual_workspaces)


def test_empty_public_checks_remain_a_valid_unverified_implementation_spec(tmp_path):
    from minicode.implementation_execution import ImplementationSpec, direct_verification_capability
    from minicode.tooling import ToolContext

    spec = ImplementationSpec(("app.py", "tests/test_new.py"), (), "Implement without public checks")
    assert spec.checks == ()
    assert spec.test_paths == ("tests/test_new.py",)
    capability = direct_verification_capability(spec)
    registry = capability.bind_registry(ToolRegistry([edit_file_tool, write_file_tool]))
    assert "executor_check" not in registry.list_all()
    try:
        result = registry.execute("executor_test", {
            "path": "tests/test_new.py", "code": "def test_new():\n    assert True\n",
        }, ToolContext(cwd=str(tmp_path)))
    finally:
        capability.close()
    assert result.ok and result._file_changed
    assert "IMPLEMENTATION CHECK FEEDBACK" not in result.output
    assert (tmp_path / "tests/test_new.py").is_file()
