"""Host pytest diagnostics must survive the real scope/environment boundary.

These local projects run actual pytest inside the production macOS Seatbelt
backend with install_verification_guards. No provider or evaluation task runs.
"""

from contextlib import ExitStack
import builtins
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.verifier_sandbox import DarwinSeatbeltVerifierSandbox
from scripts import run_three_arm_real as live


pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="production macOS Seatbelt integration")
ARGV = ("python", "-m", "pytest", "-q", "test_public.py")


@pytest.mark.parametrize("body,expected,kind", [
    ("assert 2 + 2 == 4", "passed", None),
    ("assert 2 + 2 == 5", "failed", "assertion"),
    ("raise TypeError('synthetic constructor error')", "failed", "exception"),
])
def test_real_sandbox_and_scope_guard_preserve_only_host_report_plugin(tmp_path, monkeypatch, body, expected, kind):
    _exercise_report(tmp_path, monkeypatch, body, expected, kind)


def test_registered_python3_reports_real_exception_under_the_same_guard(tmp_path, monkeypatch):
    # Use the interpreter provisioned for this suite. The macOS system shim may
    # dispatch to another Python that has no pytest; that is not a plugin test.
    _exercise_report(tmp_path, monkeypatch, "raise TypeError('synthetic constructor error')", "failed", "exception",
        executable=sys.executable, argv=("python3", "-m", "pytest", "-q", "test_public.py"))


def _exercise_report(tmp_path, monkeypatch, body, expected, kind, *, executable=None, argv=ARGV):
    workspace = (tmp_path / "workspace")
    workspace.mkdir()
    workspace = workspace.resolve()
    external = tmp_path / "untrusted_parent_plugins"
    external.mkdir()
    (external / "parent_untrusted_plugin.py").write_text("raise RuntimeError('must not import')\n")
    monkeypatch.setenv("PYTHONPATH", str(external))
    monkeypatch.setenv("PYTEST_PLUGINS", "parent_untrusted_plugin")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p parent_untrusted_plugin")
    source = (
        "import importlib.util, json, os, socket, sys\nfrom pathlib import Path\n"
        "Path('interpreter.json').write_text(json.dumps({'executable': sys.executable, 'version': list(sys.version_info[:3])}))\n"
        "def test_environment_and_behavior():\n"
        "    assert importlib.util.find_spec('parent_untrusted_plugin') is None\n"
        "    assert 'PYTEST_PLUGINS' not in os.environ\n"
        "    assert os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'\n"
        "    home = Path(os.environ['HOME']).resolve()\n"
        "    assert Path(os.environ['PYTHONPATH']).resolve() == home\n"
        "    plugins = [m for n, m in sys.modules.items() if n.startswith('_minicode_pytest_')]\n"
        "    assert len(plugins) == 1 and Path(plugins[0].__file__).resolve().parent == home\n"
        "    try:\n"
        f"        Path({str(external / 'parent_untrusted_plugin.py')!r}).read_text()\n"
        "    except PermissionError:\n        pass\n"
        "    else:\n        raise AssertionError('sandbox allowed outside file contents')\n"
        "    try:\n        sock = socket.socket(); sock.bind(('127.0.0.1', 0))\n"
        "    except PermissionError:\n        pass\n"
        "    else:\n        sock.close(); raise AssertionError('sandbox allowed network')\n"
        f"    {body}\n"
    )
    target = workspace / "test_public.py"
    target.write_text(source)
    reports, evidence = [], []
    verifier = DeterministicVerifier(sandbox_backend=DarwinSeatbeltVerifierSandbox(),
        explicit_trusted_executables={argv[0]: executable or sys.executable})
    check = VerificationCheck("public_check", "tests", argv, 15)
    with ExitStack() as stack:
        live.install_verification_guards(stack, live.VerificationScope(argv), workspace)
        result = verifier.run(checks=(check,), cwd=str(workspace), permission_cwd=str(workspace),
            workflow_id="wf_plugin_import", task_id="task_plugin_import", execution_assignment_id="asg_plugin_import",
            patch_digest="sha256:" + "1" * 64,
            evidence_sink=lambda _ref, document: evidence.append(document), pytest_report_sink=reports.append)
        unauthorized = VerificationCheck("extra", "tests", ("python", "-m", "pytest", "other.py"), 15)
        with pytest.raises(live.ScopeDenied):
            verifier.run(checks=(unauthorized,), cwd=str(workspace), workflow_id="wf_denied", task_id="task_denied",
                execution_assignment_id="asg_denied", patch_digest="sha256:" + "2" * 64,
                pytest_report_sink=reports.append)
    assert len(reports) == len(evidence) == 1
    assert reports[0] is not None, evidence[0]["stderr"]["preview"]
    assert result["status"] == expected, evidence[0]
    assert evidence[0]["argv"] == list(argv)
    assert evidence[0]["exitCode"] == (0 if expected == "passed" else 1)
    assert reports[0]["collected"] == 1 and reports[0]["errors"] == reports[0]["skipped"] == 0
    assert reports[0]["passed"] == (1 if expected == "passed" else 0)
    assert reports[0]["failed"] == (0 if expected == "passed" else 1)
    assert reports[0]["caseReport"]["cases"] == [{"nodeId": "test_public.py::test_environment_and_behavior",
        "outcome": expected, "failureKind": kind}]
    assert target.read_text() == source
    runtime = json.loads((workspace / "interpreter.json").read_text())
    assert runtime["version"] == list(sys.version_info[:3])
    assert Path(runtime["executable"]).resolve() == Path(sys.executable).resolve()


def test_ordinary_guarded_pytest_does_not_acquire_plugin_search_paths(tmp_path, monkeypatch):
    workspace = tmp_path.resolve()
    monkeypatch.setenv("PYTHONPATH", str(workspace / "untrusted_parent_plugins"))
    monkeypatch.setenv("PYTEST_PLUGINS", "parent_untrusted_plugin")
    (workspace / "test_public.py").write_text(
        "import os, sys\ndef test_ordinary():\n"
        "    assert 'PYTHONPATH' not in os.environ\n"
        "    assert 'PYTEST_PLUGINS' not in os.environ\n"
        "    assert not any(n.startswith('_minicode_pytest_') for n in sys.modules)\n")
    verifier = DeterministicVerifier(sandbox_backend=DarwinSeatbeltVerifierSandbox(),
        explicit_trusted_executables={"python": sys.executable})
    evidence = []
    with ExitStack() as stack:
        live.install_verification_guards(stack, live.VerificationScope(ARGV), workspace)
        result = verifier.run(checks=(VerificationCheck("ordinary", "tests", ARGV, 15),), cwd=str(workspace),
            workflow_id="wf_ordinary", task_id="task_ordinary", execution_assignment_id="asg_ordinary",
            patch_digest="sha256:" + "3" * 64,
            evidence_sink=lambda _ref, document: evidence.append(document))
    assert result["status"] == "passed", evidence
    assert evidence[0]["argv"] == list(ARGV)


def test_report_exception_classifier_without_builtin_exception_groups():
    from minicode.deterministic_verifier import _PYTEST_CASE_PLUGIN
    # Exercise the generated plugin itself with the pre-3.11 builtin shape,
    # without deleting globals from the test runner or claiming a 3.9 process.
    namespace = {"__builtins__": {name: value for name, value in vars(builtins).items()
                                  if name != "BaseExceptionGroup"}}
    exec(_PYTEST_CASE_PLUGIN, namespace)
    namespace["builtins"] = SimpleNamespace()
    classify = namespace["_kind"]
    assert classify(TypeError("bad fixture")) == "exception"
    assert classify(AssertionError("real defect")) == "assertion"
    class BackportLikeGroup(Exception):
        exceptions = (AssertionError("must not retire"), TypeError("bad fixture"))
    assert classify(BackportLikeGroup()) == "unknown"
