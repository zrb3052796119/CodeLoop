from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from minicode.implementation_candidate import (
    CandidateProtectionError, ImplementationCandidateGuard,
)
from minicode.tooling import ToolResult
from minicode.verification_observation import project_verification


class _Permissions:
    def __init__(self, root: Path, paths: tuple[str, ...]) -> None:
        self.root = root
        self.paths = paths
        self.observed: list[tuple[str, str]] = []

    def ensure_path_access(self, path: str, intent: str) -> None:
        relative = Path(path).relative_to(self.root).as_posix()
        self.observed.append((relative, intent))
        if relative not in self.paths:
            raise PermissionError("not in host-authorized file scope")


def _write(path: str = "app.py", content: str = "fixed") -> dict:
    return {"toolName": "write_file", "input": {"path": path, "content": content}}


def _check(*, passed: bool = True, tool: str = "run_command") -> tuple[dict, ToolResult]:
    call = {"toolName": tool, "input": {"command": "python", "args": ["-m", "pytest", "tests/test_app.py"]}}
    result = ToolResult(ok=passed, output="private test output", verification=project_verification(
        kind="tests", passed=passed, source="test_runner" if tool == "test_runner" else "run_command_exit",
    ))
    return call, result


def _guard(root: Path, *, dynamic: bool = False, **kwargs) -> ImplementationCandidateGuard:
    permissions = _Permissions(root, ("app.py", "tests/test_app.py", "other.py"))
    return ImplementationCandidateGuard(
        str(root), allowed_paths=None if dynamic else ("app.py", "tests/test_app.py"),
        permissions=permissions, **kwargs,
    )


def _fix(root: Path, guard: ImplementationCandidateGuard, *, value: str = "fixed") -> None:
    assert guard.preflight_write("app.py", value).allowed
    (root / "app.py").write_text(value)


def _verify(guard: ImplementationCandidateGuard, *, epoch: int = 1, passed: bool = True):
    pair = _check(passed=passed)
    before = guard.capture_before([pair[0]], mutation_epoch=epoch)
    return guard.observe_results([pair], before, mutation_epoch=epoch)


def test_real_test_pass_candidate_survives_rejected_baseline_counterexample(tmp_path: Path):
    baseline = "def value():\n    return 0\n"
    fixed = "def value():\n    return 1\n"
    (tmp_path / "app.py").write_text(baseline)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_app.py").write_text("from app import value\n\ndef test_value():\n    assert value() == 1\n")
    guard = _guard(tmp_path)
    _fix(tmp_path, guard, value=fixed)
    call = {"toolName": "run_command", "input": {"command": "python", "args": ["-m", "pytest", "tests/test_app.py"]}}
    before = guard.capture_before([call], mutation_epoch=1)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_app.py"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": "", "PYTHONPATH": str(tmp_path)},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = ToolResult(ok=True, output=completed.stdout, verification=project_verification(
        kind="tests", passed=True, source="run_command_exit",
    ))
    decision = guard.observe_results([(call, result)], before, mutation_epoch=1)
    assert decision.candidate_retained and decision.verified_current

    # The pre-fix behavior simply wrote the baseline and lost the passing
    # implementation if the turn ended here. Admission now protects actual bytes.
    rejection = guard.preflight_write("app.py", baseline)
    assert not rejection.allowed
    assert rejection.code == "verified_candidate_baseline_revert"
    assert (tmp_path / "app.py").read_text() == fixed
    assert guard.delivery_check().verified_current


def test_uninstrumented_command_rollback_fails_delivery_without_auto_restore(tmp_path: Path):
    (tmp_path / "app.py").write_text("original")
    guard = _guard(tmp_path)
    _fix(tmp_path, guard)
    assert _verify(guard).verified_current
    call = {"toolName": "run_command", "input": {"command": "python", "args": ["-c", "rollback"]}}
    before = guard.capture_before([call], mutation_epoch=1)
    (tmp_path / "app.py").write_text("original")
    result = guard.observe_results([(call, ToolResult(ok=False, output="cancelled"))], before, mutation_epoch=1)
    assert not result.allowed and result.code == "verified_candidate_lost"
    assert not guard.delivery_check().allowed
    assert (tmp_path / "app.py").read_text() == "original"


def test_concurrent_user_edit_is_preserved_and_needs_new_verification(tmp_path: Path):
    (tmp_path / "app.py").write_text("original")
    guard = _guard(tmp_path)
    _fix(tmp_path, guard)
    assert _verify(guard).verified_current
    (tmp_path / "app.py").write_text("user edit")
    decision = guard.delivery_check()
    assert decision.allowed and not decision.verified_current
    assert decision.code == "candidate_requires_reverification"
    assert (tmp_path / "app.py").read_text() == "user edit"


def test_dynamic_scope_captures_before_first_write_and_never_reads_denied_path(tmp_path: Path):
    (tmp_path / "app.py").write_text("original")
    guard = _guard(tmp_path, dynamic=True)
    _fix(tmp_path, guard)
    assert _verify(guard).verified_current
    assert not guard.preflight_write("app.py", "original").allowed
    (tmp_path / "private.py").write_text("private content")
    rejection = guard.preflight_write("private.py", "new")
    assert not rejection.allowed
    assert "private content" not in rejection.message
    assert (tmp_path / "private.py").read_text() == "private content"


def test_dynamic_scope_requires_existing_permission_manager(tmp_path: Path):
    with pytest.raises(ValueError, match="existing path permissions"):
        ImplementationCandidateGuard(str(tmp_path))


@pytest.mark.parametrize("tool", ["run_command", "test_runner"])
def test_only_real_typed_passed_check_can_promote(tmp_path: Path, tool: str):
    (tmp_path / "app.py").write_text("original")
    guard = _guard(tmp_path)
    _fix(tmp_path, guard)
    pair = _check(tool=tool)
    before = guard.capture_before([pair[0]], mutation_epoch=1)
    assert guard.observe_results([pair], before, mutation_epoch=1).verified_current


@pytest.mark.parametrize("failure", ["failed", "ok_false", "no_marker", "wrong_tool", "wrong_source"])
def test_untrusted_or_failed_result_does_not_promote(tmp_path: Path, failure: str):
    (tmp_path / "app.py").write_text("original")
    guard = _guard(tmp_path)
    _fix(tmp_path, guard)
    call, result = _check()
    if failure == "failed":
        call, result = _check(passed=False)
    elif failure == "ok_false":
        result.ok = False
    elif failure == "no_marker":
        result.verification = None
        result.output = "100% passed"
    elif failure == "wrong_tool":
        call["toolName"] = "read_file"
    else:
        result.verification = project_verification(kind="tests", passed=True, source="test_runner")
    before = guard.capture_before([call], mutation_epoch=1)
    decision = guard.observe_results([(call, result)], before, mutation_epoch=1)
    assert not decision.candidate_retained
    assert not guard.delivery_check().verified_current


@pytest.mark.parametrize("change", ["source", "test", "epoch", "byte_aba", "new_scope"])
def test_check_cannot_bind_to_another_source_or_test_epoch(tmp_path: Path, change: str):
    (tmp_path / "app.py").write_text("original")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_app.py").write_text("test v1")
    guard = _guard(tmp_path, dynamic=change == "new_scope")
    _fix(tmp_path, guard)
    if change == "new_scope":
        assert guard.preflight_write("tests/test_app.py", "test v1").allowed
    call, result = _check()
    before = guard.capture_before([call], mutation_epoch=1)
    epoch = 1
    if change == "source":
        (tmp_path / "app.py").write_text("different")
    elif change == "test":
        (tmp_path / "tests/test_app.py").write_text("test v2")
    elif change == "epoch":
        epoch = 2
    elif change == "byte_aba":
        replacement = tmp_path / "replacement.py"
        replacement.write_text("fixed")
        replacement.replace(tmp_path / "app.py")
    else:
        assert guard.preflight_write("other.py", "new source").allowed
        (tmp_path / "other.py").write_text("new source")
    decision = guard.observe_results([(call, result)], before, mutation_epoch=epoch)
    assert decision.code == "candidate_check_unbound"
    assert not decision.candidate_retained


def test_mixed_write_and_verification_batch_cannot_claim_post_write_success(tmp_path: Path):
    (tmp_path / "app.py").write_text("original")
    guard = _guard(tmp_path)
    write = _write()
    pair = _check()
    before = guard.capture_before([write, pair[0]], mutation_epoch=0)
    (tmp_path / "app.py").write_text("fixed")
    decision = guard.observe_results([(write, ToolResult(ok=True, output="written")), pair], before, mutation_epoch=1)
    assert not decision.candidate_retained


def test_observation_leases_are_one_use_and_bound_to_guard_and_call(tmp_path: Path):
    (tmp_path / "app.py").write_text("original")
    guard = _guard(tmp_path)
    other_guard = _guard(tmp_path)
    _fix(tmp_path, guard)
    pair = _check()
    before = guard.capture_before([pair[0]], mutation_epoch=1)
    assert not other_guard.observe_results([pair], before, mutation_epoch=1).candidate_retained
    changed_call = {**pair[0], "input": {"command": "python", "args": ["-m", "pytest", "other.py"]}}
    assert not guard.observe_results([(changed_call, pair[1])], before, mutation_epoch=1).candidate_retained
    assert not guard.observe_results([pair], before, mutation_epoch=1).candidate_retained


def test_test_only_changes_do_not_create_implementation_candidate(tmp_path: Path):
    (tmp_path / "app.py").write_text("original")
    guard = _guard(tmp_path)
    (tmp_path / "tests").mkdir()
    assert guard.preflight_write("tests/test_app.py", "test new").allowed
    (tmp_path / "tests/test_app.py").write_text("test new")
    decision = _verify(guard)
    assert decision.code == "candidate_source_unchanged"
    assert not decision.candidate_retained


def test_later_failure_and_same_bytes_new_identity_retire_current_evidence(tmp_path: Path):
    (tmp_path / "app.py").write_text("original")
    guard = _guard(tmp_path)
    _fix(tmp_path, guard)
    assert _verify(guard).verified_current
    assert _verify(guard, passed=False).code == "candidate_check_failed"
    assert not guard.delivery_check().verified_current
    assert _verify(guard).verified_current
    replacement = tmp_path / "replacement.py"
    replacement.write_text("fixed")
    replacement.replace(tmp_path / "app.py")
    assert not guard.delivery_check().verified_current


@pytest.mark.parametrize("kind", ["outside", "file_link", "directory_link", "directory", "large"])
def test_unsafe_targets_fail_closed_without_touching_private_bytes(tmp_path: Path, kind: str):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "private.py"
    outside.write_text("private bytes")
    path = "app.py"
    if kind == "outside":
        path = "../private.py"
    elif kind == "file_link":
        (root / path).symlink_to(outside)
    elif kind == "directory_link":
        (root / "linked").symlink_to(tmp_path, target_is_directory=True)
        path = "linked/private.py"
    elif kind == "directory":
        (root / path).mkdir()
    else:
        (root / path).write_bytes(b"x" * 17)
    with pytest.raises(CandidateProtectionError):
        ImplementationCandidateGuard(str(root), allowed_paths=(path,), max_file_bytes=16)
    assert outside.read_text() == "private bytes"


def test_file_and_snapshot_limits_are_hard_without_dropping_protected_files(tmp_path: Path):
    (tmp_path / "app.py").write_text("old")
    guard = _guard(tmp_path, dynamic=True, max_files=1, max_snapshot_bytes=7)
    _fix(tmp_path, guard, value="fixed")
    assert _verify(guard).verified_current
    rejection = guard.preflight_write("other.py", "x")
    assert not rejection.allowed
    assert not (tmp_path / "other.py").exists()
    assert not guard.delivery_check().allowed

    with pytest.raises(CandidateProtectionError, match="baseline byte limit"):
        ImplementationCandidateGuard(str(tmp_path), allowed_paths=("app.py",), max_snapshot_bytes=4)


def test_symlink_swap_after_verification_does_not_restore_through_link(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "app.py").write_text("original")
    guard = _guard(root)
    _fix(root, guard)
    assert _verify(guard).verified_current
    outside = tmp_path / "private.py"
    outside.write_text("private bytes")
    (root / "app.py").unlink()
    (root / "app.py").symlink_to(outside)
    assert not guard.preflight_write("app.py", "fixed").allowed
    assert not guard.delivery_check().allowed
    assert outside.read_text() == "private bytes"


def test_exception_cancel_and_deadline_during_a_check_never_install_baseline(tmp_path: Path):
    (tmp_path / "app.py").write_text("original")
    guard = _guard(tmp_path)
    _fix(tmp_path, guard)
    assert _verify(guard).verified_current
    pair = _check()
    for error in (RuntimeError("tool failed"), KeyboardInterrupt(), TimeoutError("deadline")):
        before = guard.capture_before([pair[0]], mutation_epoch=1)
        try:
            raise error
        except BaseException:
            # The exit hook checks current bytes even if no result was emitted.
            assert guard.delivery_check().verified_current
        assert (tmp_path / "app.py").read_text() == "fixed"
        assert before.usable


def test_unavailable_descriptor_safety_fails_closed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(os, "supports_dir_fd", set())
    with pytest.raises(CandidateProtectionError, match="descriptor-relative"):
        ImplementationCandidateGuard(str(tmp_path), allowed_paths=("app.py",))
