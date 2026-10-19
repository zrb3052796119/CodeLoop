"""Independent completion-gate review with real filesystem/public pytest inputs."""
from pathlib import Path
import subprocess
import sys

import pytest

from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_execution import (
    ImplementationSpec, direct_verification_capability, implementation_candidate_digest,
)
from minicode.permissions import PermissionManager
from minicode.tooling import ToolContext


def test_readonly_dependency_change_invalidates_prior_passing_public_check(tmp_path, monkeypatch):
    import minicode.workflow_runner as runner

    (tmp_path / "candidate.py").write_text("from settings import VALUE\ndef answer(): return VALUE\n")
    (tmp_path / "settings.py").write_text("VALUE = 1\n")
    (tmp_path / "test_public.py").write_text("from candidate import answer\ndef test_answer(): assert answer() == 1\n")
    checks = []

    def command(argv, **kwargs):
        result = subprocess.run([sys.executable, *argv[1:]], **kwargs)
        checks.append({"cwd": Path(kwargs["cwd"]), "returncode": result.returncode})
        return result

    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    spec = ImplementationSpec(("candidate.py",), (
        VerificationCheck("public", "tests", ("python3", "-m", "pytest", "-q", "test_public.py"), 20),
    ), "Preserve the requested answer behavior.")
    context = ToolContext(cwd=str(tmp_path))
    cap = direct_verification_capability(spec)
    try:
        first = cap.ensure_completion_checks(context)
        assert first["status"] == "passed"
        assert cap.ensure_completion_checks(context)["status"] == "passed"
        assert len(checks) == 1, "unchanged current evidence should not trigger redundant checks"
        # A concurrent human/library update is not in the model's write scope.
        (tmp_path / "settings.py").write_text("VALUE = 2\n")
        second = cap.ensure_completion_checks(context)
        assert second["status"] == "failed", "the old pass cannot cover changed imported input"
        assert second["patchDigest"] != first["patchDigest"]
        assert len(checks) == 2 and checks[-1]["returncode"] != 0
        assert (tmp_path / "candidate.py").read_text().endswith("return VALUE\n")
        assert all(c["cwd"] != tmp_path and not c["cwd"].exists() for c in checks)
    finally:
        cap.close()


def test_directory_scope_and_new_inputs_are_part_of_same_candidate(tmp_path):
    directory = tmp_path / "package"
    directory.mkdir()
    (directory / "feature.py").write_text("VALUE = 1\n")
    spec = ImplementationSpec(("package",), (), "Implement the package behavior.")
    context = ToolContext(cwd=str(tmp_path))
    first = implementation_candidate_digest(spec, context)
    (directory / "new_feature.py").write_text("VALUE = 2\n")
    assert implementation_candidate_digest(spec, context) != first


def test_denied_and_external_symlink_inputs_are_never_read_for_digest(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "candidate.py").write_text("VALUE = 1\n")
    denied = workspace / "denied.txt"
    denied.write_text("PRIVATE_CONTENT")
    external = tmp_path / "outside.txt"
    external.write_text("EXTERNAL_PRIVATE_CONTENT")
    (workspace / "link.txt").symlink_to(external)

    class ReadScope:
        def ensure_path_access(self, path, mode):
            assert mode == "read"
            if Path(path) == denied:
                raise PermissionError("denied")

    original = Path.read_bytes
    accessed = []

    def read_bytes(path):
        accessed.append(path.resolve())
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    spec = ImplementationSpec(("candidate.py",), (), "Change public candidate only.")
    context = ToolContext(cwd=str(workspace), permissions=ReadScope())
    first = implementation_candidate_digest(spec, context)
    denied.write_text("OTHER_PRIVATE_CONTENT")
    external.write_text("OTHER_EXTERNAL_PRIVATE_CONTENT")
    assert implementation_candidate_digest(spec, context) == first
    assert denied not in accessed and external not in accessed


@pytest.mark.parametrize("name", ["test_public.py", "pytest.ini", "settings.py"])
def test_public_test_configuration_and_imported_input_are_candidate_dependencies(tmp_path, name):
    (tmp_path / "candidate.py").write_text("VALUE = 1\n")
    dependency = tmp_path / name
    dependency.write_text("BEFORE\n")
    spec = ImplementationSpec(("candidate.py",), (), "Implement the public requirement.")
    context = ToolContext(cwd=str(tmp_path))
    first = implementation_candidate_digest(spec, context)
    dependency.write_text("AFTER\n")
    assert implementation_candidate_digest(spec, context) != first
