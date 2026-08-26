"""Scheduler-owned execution of policy-recognized verification commands."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
from typing import Callable, Mapping, Protocol

from minicode.multi_agent_contracts import parse_verification_result
from minicode.verification_observation import project_command_verification
from minicode.verification_policy import verification_argv_is_safe


class VerificationDeadlineExceeded(TimeoutError):
    """The scheduler's absolute workflow deadline stopped verification."""


class VerifierSandboxBackend(Protocol):
    """Administrator-owned process sandbox capability.

    The backend receives an already resolved executable and must return a
    shell-free argv that constrains filesystem access to ``cwd``, denies
    network access, keeps writable runtime state under ``verifier_home``, and
    prevents descendants from escaping host cleanup on timeout.
    Workflow requests and model output never select this capability.
    """

    def wrap_command(
        self,
        *,
        argv: tuple[str, ...],
        cwd: str,
        verifier_home: str,
    ) -> tuple[str, ...]: ...


@dataclass(slots=True)
class _BoundedStreamCapture:
    """Hash an arbitrary stream while retaining at most 6 KiB."""

    byte_count: int = 0
    _head: bytearray | None = None
    _tail: bytearray | None = None
    _digest: object | None = None

    def __post_init__(self) -> None:
        self._head = bytearray()
        self._tail = bytearray()
        self._digest = hashlib.sha256()

    def add(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.byte_count += len(chunk)
        digest = self._digest
        if digest is not None:
            digest.update(chunk)
        head = self._head
        tail = self._tail
        if head is not None and len(head) < 4_000:
            head.extend(chunk[: 4_000 - len(head)])
        if tail is not None:
            tail.extend(chunk)
            if len(tail) > 2_000:
                del tail[: len(tail) - 2_000]

    def evidence(self) -> dict[str, object]:
        head = bytes(self._head or b"")
        tail = bytes(self._tail or b"")
        truncated = self.byte_count > 4_000
        preview = (
            head
            if not truncated
            else head[:2_000] + b"\n...<truncated>...\n" + tail
        )
        digest = self._digest
        return {
            "byteCount": self.byte_count,
            "digest": "sha256:"
            + (digest.hexdigest() if digest is not None else hashlib.sha256().hexdigest()),
            "preview": preview.decode("utf-8", errors="replace"),
            "previewBase64": base64.b64encode(preview).decode("ascii"),
            "truncated": truncated,
        }


@dataclass(frozen=True, slots=True)
class _ManagedCompletedProcess:
    returncode: int
    stdout: _BoundedStreamCapture
    stderr: _BoundedStreamCapture


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    check_id: str
    kind: str
    argv: tuple[str, ...]
    timeout_seconds: int

    def __post_init__(self) -> None:
        internal_oracle = self.kind == "oracle" and self.argv == (
            "git",
            "diff",
            "--cached",
            "--check",
        )
        if (
            not isinstance(self.check_id, str)
            or not self.check_id.strip()
            or len(self.check_id) > 128
        ):
            raise ValueError("verification check_id is invalid")
        if self.kind not in {"tests", "build", "lint", "typecheck", "oracle"}:
            raise ValueError("verification check kind is invalid")
        if not isinstance(self.argv, tuple) or (
            not internal_oracle and not verification_argv_is_safe(self.argv)
        ):
            raise ValueError("verification command must be safe argv")
        projected = (
            None
            if internal_oracle
            else project_command_verification(
                {"command": self.argv[0], "args": list(self.argv[1:])},
                passed=True,
            )
        )
        if not internal_oracle and (
            projected is None or projected.get("kind") != self.kind
        ):
            raise ValueError("verification command is not recognized for its kind")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or not 1 <= self.timeout_seconds <= 3600
        ):
            raise ValueError("verification timeout must be between 1 and 3600")


class DeterministicVerifier:
    """Run immutable checks without a model or shell interpretation layer."""

    def __init__(
        self,
        *,
        command_runner: Callable[..., object] = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
        sandbox_backend: VerifierSandboxBackend | None = None,
        trusted_executables: Mapping[str, str] | None = None,
        untrusted_executable_roots: tuple[str, ...] = (),
    ) -> None:
        if not callable(command_runner) or not callable(clock):
            raise TypeError("verifier ports must be callable")
        if sandbox_backend is not None and not callable(
            getattr(sandbox_backend, "wrap_command", None)
        ):
            raise TypeError("sandbox_backend must expose wrap_command")
        projected_trusted: dict[str, str] = {}
        for name, executable in (trusted_executables or {}).items():
            if (
                not isinstance(name, str)
                or not name
                or "/" in name
                or "\\" in name
                or not isinstance(executable, str)
                or not Path(executable).is_absolute()
            ):
                raise TypeError("trusted_executables contains an invalid capability")
            projected_trusted[name.casefold()] = executable
        projected_untrusted_roots: list[Path] = []
        for root in untrusted_executable_roots:
            if not isinstance(root, str) or not Path(root).is_absolute():
                raise TypeError("untrusted_executable_roots must be absolute paths")
            projected_untrusted_roots.append(Path(root).resolve(strict=False))
        home = Path.home().resolve(strict=False)
        if home not in projected_untrusted_roots:
            projected_untrusted_roots.append(home)
        self._command_runner = command_runner
        self._clock = clock
        self._sandbox_backend = sandbox_backend
        self._trusted_executables = projected_trusted
        self._untrusted_executable_roots = tuple(projected_untrusted_roots)
        self._managed_subprocess = command_runner is subprocess.run

    def run(
        self,
        *,
        checks: tuple[VerificationCheck, ...],
        cwd: str,
        workflow_id: str,
        task_id: str,
        execution_assignment_id: str,
        patch_digest: str,
        permissions: object | None = None,
        permission_cwd: str | None = None,
        deadline_monotonic: float | None = None,
        cancellation_checkpoint: Callable[[], None] | None = None,
        evidence_sink: Callable[[str, dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        if (
            not isinstance(checks, tuple)
            or not checks
            or any(not isinstance(check, VerificationCheck) for check in checks)
        ):
            raise ValueError("verifier requires a non-empty tuple of checks")
        if len({check.check_id for check in checks}) != len(checks):
            raise ValueError("verification check ids must be unique")
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError("verification cwd must be non-empty")
        if permissions is not None and (
            not callable(getattr(permissions, "ensure_command", None))
            or not isinstance(permission_cwd, str)
            or not permission_cwd.strip()
        ):
            raise TypeError(
                "verification permissions require an approval cwd"
            )
        if (
            deadline_monotonic is not None
            and (
                isinstance(deadline_monotonic, bool)
                or not isinstance(deadline_monotonic, (int, float))
            )
        ):
            raise TypeError("verification deadline must be numeric or null")
        if cancellation_checkpoint is not None and not callable(
            cancellation_checkpoint
        ):
            raise TypeError("cancellation_checkpoint must be callable or null")
        if evidence_sink is not None and not callable(evidence_sink):
            raise TypeError("evidence_sink must be callable or null")

        result_checks: list[dict[str, object]] = []
        statuses: list[str] = []
        with tempfile.TemporaryDirectory(
            prefix="minicode-verifier-home-"
        ) as verifier_home:
            environment = self._environment(verifier_home)
            for check in checks:
                started = self._clock()
                stdout: bytes | _BoundedStreamCapture = b""
                stderr: bytes | _BoundedStreamCapture = b""
                failure_reason: str | None = None
                resolved_executable: str | None = None
                if cancellation_checkpoint is not None:
                    cancellation_checkpoint()
                timeout_seconds: float = float(check.timeout_seconds)
                deadline_limited = False
                if deadline_monotonic is not None:
                    remaining = float(deadline_monotonic) - float(started)
                    if remaining <= 0:
                        raise VerificationDeadlineExceeded(
                            "verification global deadline exceeded"
                        )
                    if remaining < timeout_seconds:
                        timeout_seconds = max(0.001, remaining)
                        deadline_limited = True
                try:
                    prepared_argv = check.argv
                    if self._managed_subprocess:
                        prepared_argv, resolved_executable, failure_reason = (
                            self._prepare_managed_command(
                                check=check,
                                cwd=cwd,
                                verifier_home=verifier_home,
                                environment=environment,
                            )
                        )
                    if failure_reason is not None:
                        status = "inconclusive"
                        exit_code = None
                    else:
                        if permissions is not None:
                            permissions.ensure_command(
                                check.argv[0],
                                list(check.argv[1:]),
                                permission_cwd,
                            )
                            checkpoint = getattr(
                                permissions,
                                "ensure_operation_active",
                                None,
                            )
                            if callable(checkpoint):
                                checkpoint()
                        if self._managed_subprocess:
                            completed = self._run_managed_command(
                                prepared_argv,
                                cwd=cwd,
                                timeout_seconds=timeout_seconds,
                                environment=environment,
                                cancellation_checkpoint=cancellation_checkpoint,
                            )
                            stdout = completed.stdout
                            stderr = completed.stderr
                        else:
                            completed = self._command_runner(
                                prepared_argv,
                                cwd=cwd,
                                timeout=timeout_seconds,
                                capture_output=True,
                                check=False,
                                env=environment,
                            )
                            stdout = self._output_bytes(
                                getattr(completed, "stdout", b"")
                            )
                            stderr = self._output_bytes(
                                getattr(completed, "stderr", b"")
                            )
                        if cancellation_checkpoint is not None:
                            cancellation_checkpoint()
                        if (
                            deadline_monotonic is not None
                            and float(self._clock()) >= float(deadline_monotonic)
                        ):
                            raise VerificationDeadlineExceeded(
                                "verification global deadline exceeded"
                            )
                        raw_returncode = getattr(completed, "returncode", None)
                        classifier = getattr(
                            self._sandbox_backend,
                            "infrastructure_failure",
                            None,
                        )
                        sandbox_failed = (
                            callable(classifier)
                            and isinstance(raw_returncode, int)
                            and classifier(
                                returncode=raw_returncode,
                                stderr=str(
                                    self._stream_evidence(stderr)["preview"]
                                ),
                            )
                            is True
                        )
                        if sandbox_failed:
                            status = "inconclusive"
                            exit_code = None
                            failure_reason = "host_sandbox_failed"
                        elif isinstance(raw_returncode, bool) or not isinstance(
                            raw_returncode, int
                        ):
                            status = "inconclusive"
                            exit_code = None
                            failure_reason = "invalid_process_result"
                        else:
                            exit_code = raw_returncode if raw_returncode >= 0 else 1
                            status = "passed" if exit_code == 0 else "failed"
                except VerificationDeadlineExceeded:
                    raise
                except (TimeoutError, subprocess.TimeoutExpired):
                    if deadline_limited or (
                        deadline_monotonic is not None
                        and float(self._clock()) >= float(deadline_monotonic)
                    ):
                        raise VerificationDeadlineExceeded(
                            "verification global deadline exceeded"
                        )
                    status = "inconclusive"
                    exit_code = None
                    failure_reason = "check_timeout"
                except OSError:
                    status = "inconclusive"
                    exit_code = None
                    failure_reason = "command_unavailable"
                duration_ms = max(0, int((self._clock() - started) * 1000))
                artifact_ref = self._evidence_artifact_id(
                    workflow_id=workflow_id,
                    execution_assignment_id=execution_assignment_id,
                    patch_digest=patch_digest,
                    check_id=check.check_id,
                )
                evidence = {
                    "evidenceVersion": 1,
                    "checkId": check.check_id,
                    "kind": check.kind,
                    "argv": list(check.argv),
                    "resolvedExecutable": resolved_executable,
                    "commandDigest": "sha256:"
                    + hashlib.sha256("\0".join(check.argv).encode("utf-8")).hexdigest(),
                    "status": status,
                    "exitCode": exit_code,
                    "durationMs": duration_ms,
                    "failureReason": failure_reason,
                    "stdout": self._stream_evidence(stdout),
                    "stderr": self._stream_evidence(stderr),
                }
                if evidence_sink is not None:
                    evidence_sink(artifact_ref, evidence)
                statuses.append(status)
                result_checks.append(
                    {
                        "checkId": check.check_id,
                        "kind": check.kind,
                        "status": status,
                        "exitCode": exit_code,
                        "durationMs": duration_ms,
                        "artifactRef": artifact_ref,
                    }
                )

        aggregate = (
            "failed"
            if "failed" in statuses
            else "inconclusive"
            if "inconclusive" in statuses
            else "passed"
        )
        identity_material = (
            f"{workflow_id}\0{task_id}\0{execution_assignment_id}\0{patch_digest}"
        ).encode("utf-8")
        verification_id = "verification_" + hashlib.sha256(
            identity_material
        ).hexdigest()[:24]
        raw = {
            "verificationVersion": 1,
            "verificationId": verification_id,
            "workflowId": workflow_id,
            "taskId": task_id,
            "executionAssignmentId": execution_assignment_id,
            "patchDigest": patch_digest,
            "status": aggregate,
            "checks": result_checks,
        }
        return parse_verification_result(
            raw,
            workflow_id=workflow_id,
            task_id=task_id,
            execution_assignment_id=execution_assignment_id,
            patch_digest=patch_digest,
            required_check_ids=tuple(check.check_id for check in checks),
        )

    def _prepare_managed_command(
        self,
        *,
        check: VerificationCheck,
        cwd: str,
        verifier_home: str,
        environment: dict[str, str],
    ) -> tuple[tuple[str, ...], str | None, str | None]:
        internal_oracle = check.kind == "oracle" and check.argv == (
            "git",
            "diff",
            "--cached",
            "--check",
        )
        if internal_oracle:
            trusted = self._trusted_executables.get("git")
        else:
            trusted = self._trusted_executables.get(check.argv[0].casefold())
        if trusted is None:
            return check.argv, None, "trusted_executable_unavailable"
        resolved, resolution_error = self._resolve_trusted_executable(
            trusted,
            cwd=cwd,
        )
        if resolved is None:
            return check.argv, None, resolution_error or "command_unavailable"
        prepared = (resolved, *check.argv[1:])
        if internal_oracle:
            return prepared, resolved, None
        backend = self._sandbox_backend
        if backend is None:
            return prepared, resolved, "host_sandbox_unavailable"
        try:
            wrapped = backend.wrap_command(
                argv=prepared,
                cwd=cwd,
                verifier_home=verifier_home,
            )
        except Exception:  # noqa: BLE001 - an invalid capability fails closed
            return prepared, resolved, "host_sandbox_unavailable"
        if (
            not isinstance(wrapped, tuple)
            or not wrapped
            or any(
                not isinstance(token, str) or not token or "\x00" in token
                for token in wrapped
            )
        ):
            return prepared, resolved, "host_sandbox_unavailable"
        if not Path(wrapped[0]).is_absolute():
            return prepared, resolved, "host_sandbox_unavailable"
        launcher, launcher_error = self._resolve_trusted_executable(
            wrapped[0],
            cwd=cwd,
        )
        if launcher is None:
            return prepared, resolved, launcher_error or "host_sandbox_unavailable"
        return (launcher, *wrapped[1:]), resolved, None

    def _resolve_trusted_executable(
        self,
        executable: str,
        *,
        cwd: str,
    ) -> tuple[str | None, str | None]:
        try:
            resolved = Path(executable).resolve(strict=True)
            workspace = Path(cwd).resolve(strict=True)
        except OSError:
            return None, "trusted_executable_unavailable"
        if (
            not Path(executable).is_absolute()
            or resolved.is_relative_to(workspace)
            or any(
                resolved.is_relative_to(root)
                for root in self._untrusted_executable_roots
            )
            or not resolved.is_file()
            or not os.access(resolved, os.X_OK)
        ):
            return None, "trusted_executable_unavailable"
        return str(resolved), None

    def _run_managed_command(
        self,
        argv: tuple[str, ...],
        *,
        cwd: str,
        timeout_seconds: float,
        environment: dict[str, str],
        cancellation_checkpoint: Callable[[], None] | None,
    ) -> _ManagedCompletedProcess:
        popen_options: dict[str, object] = {
            "cwd": cwd,
            "env": environment,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
            "close_fds": True,
        }
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_options["start_new_session"] = True
        process = subprocess.Popen(argv, **popen_options)
        stdout_capture = _BoundedStreamCapture()
        stderr_capture = _BoundedStreamCapture()

        def drain(stream: object, capture: _BoundedStreamCapture) -> None:
            reader = getattr(stream, "read", None)
            if not callable(reader):
                return
            try:
                while chunk := reader(64 * 1024):
                    capture.add(chunk)
            except (OSError, ValueError):
                pass
            finally:
                closer = getattr(stream, "close", None)
                if callable(closer):
                    closer()

        stdout_thread = threading.Thread(
            target=drain,
            args=(process.stdout, stdout_capture),
            name="minicode-verifier-stdout",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=drain,
            args=(process.stderr, stderr_capture),
            name="minicode-verifier-stderr",
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        expires_at = self._clock() + timeout_seconds
        try:
            while process.poll() is None:
                if cancellation_checkpoint is not None:
                    cancellation_checkpoint()
                remaining = expires_at - self._clock()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout_seconds)
                time.sleep(min(0.05, max(0.001, remaining)))
            return_code = process.returncode
            if not isinstance(return_code, int):
                return_code = process.wait(timeout=1)
        except BaseException:
            self._terminate_process_group(process)
            self._finish_capture_threads(
                process,
                stdout_thread,
                stderr_thread,
            )
            raise
        # A verifier check cannot leave background children alive after its
        # main process exits, even when the main process reported success.
        self._terminate_process_group(process)
        self._finish_capture_threads(process, stdout_thread, stderr_thread)
        return _ManagedCompletedProcess(
            returncode=return_code,
            stdout=stdout_capture,
            stderr=stderr_capture,
        )

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen) -> None:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    @staticmethod
    def _finish_capture_threads(
        process: subprocess.Popen,
        *threads: threading.Thread,
    ) -> None:
        try:
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass
        for thread in threads:
            thread.join(timeout=1)
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass
        for thread in threads:
            thread.join(timeout=0.1)

    @staticmethod
    def _output_bytes(value: object) -> bytes:
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8", errors="replace")
        return b""

    @staticmethod
    def _stream_evidence(
        value: bytes | _BoundedStreamCapture,
    ) -> dict[str, object]:
        if isinstance(value, _BoundedStreamCapture):
            return value.evidence()
        limit = 4_000
        if len(value) <= limit:
            preview = value
            truncated = False
        else:
            preview = value[: limit // 2] + b"\n...<truncated>...\n" + value[-limit // 2 :]
            truncated = True
        return {
            "byteCount": len(value),
            "digest": "sha256:" + hashlib.sha256(value).hexdigest(),
            "preview": preview.decode("utf-8", errors="replace"),
            "previewBase64": base64.b64encode(preview).decode("ascii"),
            "truncated": truncated,
        }

    @staticmethod
    def _evidence_artifact_id(
        *,
        workflow_id: str,
        execution_assignment_id: str,
        patch_digest: str,
        check_id: str,
    ) -> str:
        material = (
            f"{workflow_id}\0{execution_assignment_id}\0{patch_digest}\0{check_id}"
        ).encode("utf-8")
        return "artifact_verification_" + hashlib.sha256(material).hexdigest()[:24]

    @staticmethod
    def _environment(verifier_home: str) -> dict[str, str]:
        """Expose toolchain discovery but not the parent Agent's secrets."""
        allowed = {
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
        }
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in allowed and isinstance(value, str)
        }
        home = Path(verifier_home)
        private_temp = home / "tmp"
        xdg_cache = home / "xdg-cache"
        xdg_config = home / "xdg-config"
        xdg_data = home / "xdg-data"
        for directory in (private_temp, xdg_cache, xdg_config, xdg_data):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        environment.update(
            {
                "CARGO_NET_OFFLINE": "true",
                "CI": "1",
                "GONOSUMDB": "*",
                "GOPROXY": "off",
                "GIT_CONFIG_NOSYSTEM": "1",
                "HOME": verifier_home,
                "NPM_CONFIG_AUDIT": "false",
                "NPM_CONFIG_FUND": "false",
                "NPM_CONFIG_OFFLINE": "true",
                "PIP_NO_INDEX": "1",
                "PATH": os.defpath,
                "PYTHONNOUSERSITE": "1",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "TEMP": str(private_temp),
                "TMP": str(private_temp),
                "TMPDIR": str(private_temp),
                "XDG_CACHE_HOME": str(xdg_cache),
                "XDG_CONFIG_HOME": str(xdg_config),
                "XDG_DATA_HOME": str(xdg_data),
            }
        )
        return environment


__all__ = [
    "DeterministicVerifier",
    "VerificationCheck",
    "VerificationDeadlineExceeded",
    "VerifierSandboxBackend",
]
