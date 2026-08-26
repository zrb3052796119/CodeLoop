"""Deterministic, argv-only verification independent of Worker prose."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from minicode.deterministic_verifier import (
    DeterministicVerifier,
    VerificationCheck,
    VerificationDeadlineExceeded,
)
from minicode.scheduler_audit import _validate_verification_stream


PATCH = "sha256:" + "e" * 64


@dataclass
class _Completed:
    returncode: int


def test_verifier_runs_policy_recognized_argv_and_binds_patch_digest() -> None:
    calls = []

    def runner(argv, *, cwd, timeout, **_kwargs):
        calls.append((argv, cwd, timeout))
        return _Completed(0)

    verifier = DeterministicVerifier(command_runner=runner)
    result = verifier.run(
        checks=(
            VerificationCheck(
                check_id="check_tests",
                kind="tests",
                argv=("python3", "-m", "pytest", "tests/test_auth.py", "-q"),
                timeout_seconds=30,
            ),
        ),
        cwd="/workspace",
        workflow_id="wf_verifier1",
        task_id="task_verifier1",
        execution_assignment_id="asg_verifier1",
        patch_digest=PATCH,
    )

    assert calls == [
        (
            ("python3", "-m", "pytest", "tests/test_auth.py", "-q"),
            "/workspace",
            30,
        )
    ]
    assert result["status"] == "passed"
    assert result["patchDigest"] == PATCH
    assert result["checks"][0]["exitCode"] == 0


def test_verifier_publishes_bounded_diagnostic_evidence() -> None:
    published: dict[str, dict[str, object]] = {}

    def runner(argv, **_kwargs):
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout=b"x" * 5_000,
            stderr=b"assertion failed",
        )

    result = DeterministicVerifier(command_runner=runner).run(
        checks=(
            VerificationCheck(
                check_id="check_tests",
                kind="tests",
                argv=("pytest", "-q", "tests/test_auth.py"),
                timeout_seconds=30,
            ),
        ),
        cwd="/workspace",
        workflow_id="wf_verifier1",
        task_id="task_verifier1",
        execution_assignment_id="asg_verifier1",
        patch_digest=PATCH,
        evidence_sink=lambda artifact_id, evidence: published.__setitem__(
            artifact_id,
            evidence,
        ),
    )

    artifact_ref = result["checks"][0]["artifactRef"]
    assert result["status"] == "failed"
    assert artifact_ref in published
    evidence = published[artifact_ref]
    assert evidence["argv"] == ["pytest", "-q", "tests/test_auth.py"]
    assert evidence["commandDigest"].startswith("sha256:")
    assert evidence["stdout"]["byteCount"] == 5_000
    assert evidence["stdout"]["truncated"] is True
    assert evidence["stderr"]["preview"] == "assertion failed"


@pytest.mark.parametrize(
    "argv",
    [
        ("sh", "-c", "pytest"),
        ("python3 -m pytest",),
        ("pytest", "tests", "&&", "curl", "example.invalid"),
        ("rm", "-rf", "build"),
        ("npx", "jest", "tests"),
        ("./pytest", "-q"),
        ("tools/pytest", "-q"),
    ],
)
def test_verification_check_rejects_shell_or_unrecognized_commands(argv) -> None:
    with pytest.raises(ValueError):
        VerificationCheck(
            check_id="check_unsafe",
            kind="tests",
            argv=argv,
            timeout_seconds=30,
        )


def test_failed_exit_is_authoritative_failure() -> None:
    verifier = DeterministicVerifier(
        command_runner=lambda *_args, **_kwargs: _Completed(3)
    )

    result = verifier.run(
        checks=(
            VerificationCheck(
                check_id="check_lint",
                kind="lint",
                argv=("ruff", "check", "."),
                timeout_seconds=10,
            ),
        ),
        cwd="/workspace",
        workflow_id="wf_verifier1",
        task_id="task_verifier1",
        execution_assignment_id="asg_verifier1",
        patch_digest=PATCH,
    )

    assert result["status"] == "failed"
    assert result["checks"][0]["exitCode"] == 3


def test_timeout_is_inconclusive_and_never_reported_as_passed() -> None:
    def timeout(*_args, **_kwargs):
        raise TimeoutError

    result = DeterministicVerifier(command_runner=timeout).run(
        checks=(
            VerificationCheck(
                check_id="check_build",
                kind="build",
                argv=("python3", "-m", "compileall", "minicode"),
                timeout_seconds=1,
            ),
        ),
        cwd="/workspace",
        workflow_id="wf_verifier1",
        task_id="task_verifier1",
        execution_assignment_id="asg_verifier1",
        patch_digest=PATCH,
    )

    assert result["status"] == "inconclusive"
    assert result["checks"][0]["exitCode"] is None


def test_global_deadline_bounds_each_command_and_stops_remaining_checks() -> None:
    timeouts = []

    def runner(_argv, **kwargs):
        timeouts.append(kwargs["timeout"])
        raise TimeoutError

    verifier = DeterministicVerifier(
        command_runner=runner,
        clock=lambda: 10.0,
    )

    with pytest.raises(VerificationDeadlineExceeded, match="global deadline"):
        verifier.run(
            checks=(
                VerificationCheck(
                    check_id="check_first",
                    kind="tests",
                    argv=("pytest", "-q"),
                    timeout_seconds=30,
                ),
                VerificationCheck(
                    check_id="check_second",
                    kind="lint",
                    argv=("ruff", "check", "."),
                    timeout_seconds=30,
                ),
            ),
            cwd="/workspace",
            workflow_id="wf_verifier1",
            task_id="task_verifier1",
            execution_assignment_id="asg_verifier1",
            patch_digest=PATCH,
            deadline_monotonic=12.5,
        )

    assert timeouts == [2.5]


def test_cancellation_checkpoint_runs_before_starting_a_check() -> None:
    class Cancelled(RuntimeError):
        pass

    def cancelled():
        raise Cancelled("stop")

    with pytest.raises(Cancelled, match="stop"):
        DeterministicVerifier(
            command_runner=lambda *_args, **_kwargs: pytest.fail("not called")
        ).run(
            checks=(
                VerificationCheck(
                    check_id="check_tests",
                    kind="tests",
                    argv=("pytest", "-q"),
                    timeout_seconds=30,
                ),
            ),
            cwd="/workspace",
            workflow_id="wf_verifier1",
            task_id="task_verifier1",
            execution_assignment_id="asg_verifier1",
            patch_digest=PATCH,
            cancellation_checkpoint=cancelled,
        )


@pytest.mark.parametrize(
    "argv",
    [
        ("pytest", "/tmp/outside_test.py"),
        ("pytest", "../outside_test.py"),
        ("pytest", "--rootdir=/tmp"),
        ("pytest", "--pyargs", "installed_package"),
        ("make", "-f", "/tmp/Makefile"),
    ],
)
def test_verification_argv_cannot_select_external_code(argv) -> None:
    with pytest.raises(ValueError, match="safe argv"):
        VerificationCheck(
            check_id="check_external",
            kind="tests" if argv[0] == "pytest" else "build",
            argv=argv,
            timeout_seconds=30,
        )


def test_verifier_redacts_parent_environment_and_uses_permission_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    authorizations: list[tuple[str, list[str], str]] = []

    class Permissions:
        def ensure_command(self, command, args, cwd):
            authorizations.append((command, args, cwd))

        def ensure_operation_active(self):
            captured["checkpoint"] = True

    def runner(_argv, **kwargs):
        captured.update(kwargs)
        return _Completed(0)

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-verifier")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--pyargs outside")
    original_home = os.environ.get("HOME")
    result = DeterministicVerifier(command_runner=runner).run(
        checks=(
            VerificationCheck(
                check_id="check_tests",
                kind="tests",
                argv=("pytest", "-q", "tests"),
                timeout_seconds=30,
            ),
        ),
        cwd="/workspace",
        workflow_id="wf_verifier1",
        task_id="task_verifier1",
        execution_assignment_id="asg_verifier1",
        patch_digest=PATCH,
        permissions=Permissions(),
        permission_cwd="/source",
    )

    environment = captured["env"]
    assert result["status"] == "passed"
    assert authorizations == [("pytest", ["-q", "tests"], "/source")]
    assert captured["checkpoint"] is True
    assert "OPENAI_API_KEY" not in environment
    assert "PYTEST_ADDOPTS" not in environment
    assert environment["HOME"] != original_home
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert environment["NPM_CONFIG_OFFLINE"] == "true"
    assert environment["CARGO_NET_OFFLINE"] == "true"
    assert environment["GOPROXY"] == "off"


def test_default_host_runner_fails_closed_without_process_sandbox(
    tmp_path: Path,
) -> None:
    published: dict[str, dict[str, object]] = {}

    result = DeterministicVerifier(
        trusted_executables={"python3": str(Path(sys.executable).resolve())}
    ).run(
        checks=(
            VerificationCheck(
                check_id="check_compile",
                kind="build",
                argv=("python3", "-m", "compileall", ".", "-q"),
                timeout_seconds=10,
            ),
        ),
        cwd=str(tmp_path),
        workflow_id="wf_sandbox_gate",
        task_id="task_sandbox_gate",
        execution_assignment_id="asg_sandbox_gate",
        patch_digest=PATCH,
        evidence_sink=lambda artifact_id, evidence: published.__setitem__(
            artifact_id,
            evidence,
        ),
    )

    assert result["status"] == "inconclusive"
    evidence = published[result["checks"][0]["artifactRef"]]
    assert evidence["failureReason"] == "host_sandbox_unavailable"
    assert evidence["resolvedExecutable"] == str(Path(sys.executable).resolve())
    assert evidence["stdout"]["byteCount"] == 0


def test_default_host_runner_rejects_unregistered_path_toolchain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile_bin = tmp_path / "source-venv" / "bin"
    hostile_bin.mkdir(parents=True)
    hostile = hostile_bin / "pytest"
    hostile.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hostile.chmod(0o755)
    monkeypatch.setenv("PATH", f"{hostile_bin}{os.pathsep}{os.defpath}")
    published: dict[str, dict[str, object]] = {}

    result = DeterministicVerifier().run(
        checks=(
            VerificationCheck(
                check_id="check_tests",
                kind="tests",
                argv=("pytest", "-q"),
                timeout_seconds=10,
            ),
        ),
        cwd=str(tmp_path),
        workflow_id="wf_untrusted_toolchain",
        task_id="task_untrusted_toolchain",
        execution_assignment_id="asg_untrusted_toolchain",
        patch_digest=PATCH,
        evidence_sink=lambda artifact_id, evidence: published.__setitem__(
            artifact_id,
            evidence,
        ),
    )

    assert result["status"] == "inconclusive"
    evidence = published[result["checks"][0]["artifactRef"]]
    assert evidence["failureReason"] == "trusted_executable_unavailable"
    assert evidence["resolvedExecutable"] is None


def test_internal_oracle_requires_a_host_frozen_git_capability(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    published: dict[str, dict[str, object]] = {}
    check = VerificationCheck(
        check_id="check_patch",
        kind="oracle",
        argv=("git", "diff", "--cached", "--check"),
        timeout_seconds=10,
    )

    result = DeterministicVerifier().run(
        checks=(check,),
        cwd=str(tmp_path),
        workflow_id="wf_oracle_untrusted",
        task_id="task_oracle_untrusted",
        execution_assignment_id="asg_oracle_untrusted",
        patch_digest=PATCH,
        evidence_sink=lambda artifact_id, evidence: published.__setitem__(
            artifact_id,
            evidence,
        ),
    )

    assert result["status"] == "inconclusive"
    evidence = published[result["checks"][0]["artifactRef"]]
    assert evidence["failureReason"] == "trusted_executable_unavailable"


def test_internal_oracle_uses_the_host_frozen_git_capability(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    trusted_path = shutil.which("git", path=os.defpath)
    assert trusted_path is not None

    result = DeterministicVerifier(
        trusted_executables={"git": str(Path(trusted_path).resolve())}
    ).run(
        checks=(
            VerificationCheck(
                check_id="check_patch",
                kind="oracle",
                argv=("git", "diff", "--cached", "--check"),
                timeout_seconds=10,
            ),
        ),
        cwd=str(tmp_path),
        workflow_id="wf_oracle_trusted",
        task_id="task_oracle_trusted",
        execution_assignment_id="asg_oracle_trusted",
        patch_digest=PATCH,
    )

    assert result["status"] == "passed"


def test_host_owned_sandbox_backend_enables_managed_check(
    tmp_path: Path,
) -> None:
    class TestSandbox:
        def wrap_command(self, *, argv, cwd, verifier_home):
            assert cwd == str(tmp_path)
            assert verifier_home != str(tmp_path)
            return argv

    result = DeterministicVerifier(
        sandbox_backend=TestSandbox(),
        trusted_executables={"python3": str(Path(sys.executable).resolve())},
    ).run(
        checks=(
            VerificationCheck(
                check_id="check_compile",
                kind="build",
                argv=("python3", "-m", "compileall", ".", "-q"),
                timeout_seconds=10,
            ),
        ),
        cwd=str(tmp_path),
        workflow_id="wf_sandbox_enabled",
        task_id="task_sandbox_enabled",
        execution_assignment_id="asg_sandbox_enabled",
        patch_digest=PATCH,
    )

    assert result["status"] == "passed"


def test_sandbox_launcher_failure_is_inconclusive_not_a_failed_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Sandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv

        def infrastructure_failure(self, *, returncode, stderr):
            return returncode == 71 and stderr.startswith("sandbox-exec:")

    monkeypatch.setattr(
        DeterministicVerifier,
        "_run_managed_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=71,
            stdout=b"",
            stderr=b"sandbox-exec: profile rejected",
        ),
    )
    published: dict[str, dict[str, object]] = {}

    result = DeterministicVerifier(
        sandbox_backend=Sandbox(),
        trusted_executables={"python3": str(Path(sys.executable).resolve())},
    ).run(
        checks=(
            VerificationCheck(
                check_id="check_compile",
                kind="build",
                argv=("python3", "-m", "compileall", ".", "-q"),
                timeout_seconds=10,
            ),
        ),
        cwd=str(tmp_path),
        workflow_id="wf_sandbox_failed",
        task_id="task_sandbox_failed",
        execution_assignment_id="asg_sandbox_failed",
        patch_digest=PATCH,
        evidence_sink=lambda artifact_id, evidence: published.__setitem__(
            artifact_id,
            evidence,
        ),
    )

    assert result["status"] == "inconclusive"
    evidence = published[result["checks"][0]["artifactRef"]]
    assert evidence["failureReason"] == "host_sandbox_failed"
    assert evidence["exitCode"] is None


def test_verifier_environment_uses_private_temp_and_host_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attacker_path = str(tmp_path / "source-venv" / "bin")
    monkeypatch.setenv("PATH", f"{attacker_path}{os.pathsep}{os.defpath}")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "parent-temp"))
    verifier_home = tmp_path / "verifier-home"
    verifier_home.mkdir()

    environment = DeterministicVerifier._environment(str(verifier_home))

    assert attacker_path not in environment["PATH"].split(os.pathsep)
    assert environment["PATH"] == os.defpath
    for name in ("TMP", "TEMP", "TMPDIR"):
        assert Path(environment[name]).is_relative_to(verifier_home)
        assert Path(environment[name]).is_dir()
    assert environment["XDG_CACHE_HOME"] == str(verifier_home / "xdg-cache")
    assert environment["XDG_CONFIG_HOME"] == str(verifier_home / "xdg-config")
    assert environment["XDG_DATA_HOME"] == str(verifier_home / "xdg-data")


def test_managed_runner_streams_bounded_output(tmp_path: Path) -> None:
    completed = DeterministicVerifier()._run_managed_command(
        (
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('x' * 100000)",
        ),
        cwd=str(tmp_path),
        timeout_seconds=5,
        environment=DeterministicVerifier._environment(str(tmp_path)),
        cancellation_checkpoint=None,
    )

    evidence = completed.stdout.evidence()
    assert completed.returncode == 0
    assert evidence["byteCount"] == 100_000
    assert evidence["truncated"] is True
    assert len(evidence["preview"]) < 5_000


def test_stream_evidence_binds_non_utf8_output_as_original_bytes() -> None:
    evidence = DeterministicVerifier._stream_evidence(b"\xff")

    assert evidence["preview"] == "\ufffd"
    assert evidence["previewBase64"] == "/w=="
    assert _validate_verification_stream(evidence) is True

    forged = dict(evidence)
    forged["previewBase64"] = "77+9"
    assert _validate_verification_stream(forged) is False


def test_managed_timeout_kills_the_entire_child_process_group(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "orphan-marker.txt"
    child_code = (
        "import pathlib,time; time.sleep(0.3); "
        f"pathlib.Path({str(marker)!r}).write_text('orphan'); time.sleep(1)"
    )
    parent_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(2)"
    )

    with pytest.raises(subprocess.TimeoutExpired):
        DeterministicVerifier()._run_managed_command(
            (sys.executable, "-c", parent_code),
            cwd=str(tmp_path),
            timeout_seconds=0.1,
            environment=DeterministicVerifier._environment(str(tmp_path)),
            cancellation_checkpoint=None,
        )

    time.sleep(0.4)
    assert marker.exists() is False
