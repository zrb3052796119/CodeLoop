"""Scheduler-owned execution of policy-recognized verification commands."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Callable, Mapping, Protocol
import xml.etree.ElementTree as ET

from minicode.multi_agent_contracts import parse_verification_result
from minicode.review_probe_correction import MAX_PYTEST_REPORT_BYTES
from minicode.verification_observation import project_command_verification
from minicode.verification_policy import verification_argv_is_safe
from minicode.verification_output import VerificationOutputStore


class VerificationDeadlineExceeded(TimeoutError):
    """The scheduler's absolute workflow deadline stopped verification."""


_MAX_PYTEST_REPORT_BYTES = MAX_PYTEST_REPORT_BYTES

# Installed by the host outside the candidate and loaded explicitly. The
# classification uses actual exception objects, never model-controlled output.
_PYTEST_CASE_PLUGIN = '''import builtins, hashlib, json
from pathlib import Path
import pytest
from _pytest.outcomes import Failed
_cases = []
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _execution.start(item.nodeid)
    try:
        yield
    finally:
        _execution.stop()
def _kind(error):
    if isinstance(error, (AssertionError, Failed)):
        return "assertion"
    if isinstance(error, getattr(builtins, "BaseExceptionGroup", ())):
        kinds = [_kind(child) for child in error.exceptions]
        return "assertion" if "assertion" in kinds else "unknown"
    if isinstance(getattr(error, "exceptions", None), (tuple, list)):
        return "unknown"
    return "exception" if isinstance(error, Exception) else "unknown"
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if call.when == "call" and report.outcome in ("passed", "failed"):
        kind = None if report.passed else (_kind(call.excinfo.value) if call.excinfo else "unknown")
        _cases.append({"nodeId": item.nodeid, "outcome": report.outcome, "failureKind": kind})
def pytest_unconfigure(config):
    try:
        junit = Path(_junit_path).read_bytes()
        junit_digest = "sha256:" + hashlib.sha256(junit).hexdigest()
        document = {"junitDigest": junit_digest, "cases": _cases}
        try:
            # Keep the classification/JUnit envelope bounded independently of
            # complete execution originals. The host imports the exact private
            # shards before this verifier home is removed.
            write_execution_report(_execution.report(junit_digest), Path(_case_path))
        except (OSError, ValueError, TypeError):
            pass  # Storage failure grants no execution evidence, not a false case outcome.
        encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        with open(_case_path, "xb") as stream:
            stream.write(encoded)
    except (OSError, ValueError, TypeError):
        pass
'''


def _read_report_bytes(path: Path) -> bytes | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= _MAX_PYTEST_REPORT_BYTES):
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(_MAX_PYTEST_REPORT_BYTES + 1)
        after = os.fstat(descriptor)
        if (len(data) != before.st_size or len(data) > _MAX_PYTEST_REPORT_BYTES
                or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
            return None
        return data
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _attach_pytest_case_report(report: dict | None, path: Path) -> dict | None:
    if report is None:
        return None
    data = _read_report_bytes(path)
    if data is None:
        return report
    try:
        document = json.loads(data)
        if (not isinstance(document, dict) or set(document) not in (
                {"junitDigest", "cases"}, {"junitDigest", "cases", "executionReport"})
                or document["junitDigest"] != report["reportDigest"]
                or json.dumps(document, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode("utf-8") != data):
            return report
        classification = json.dumps({"junitDigest": document["junitDigest"], "cases": document["cases"]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        extended = {**report, "caseReport": {
            "digest": "sha256:" + hashlib.sha256(classification).hexdigest(), "cases": document["cases"]}}
        from minicode.pytest_execution_evidence import read_execution_report, validate_execution_report
        execution = document.get("executionReport")
        if "executionReport" not in document:
            manifest_bytes = _read_report_bytes(path.with_name(path.name + ".execution.json"))
            if manifest_bytes is not None:
                try:
                    manifest = json.loads(manifest_bytes)
                    if json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False).encode("utf-8") == manifest_bytes:
                        execution = read_execution_report(manifest, path)
                except (ValueError, TypeError, UnicodeError):
                    pass  # Invalid supplemental storage cannot erase valid classification.
        if execution is not None:
            traced = {**extended, "executionReport": execution}
            if validate_execution_report(traced):
                extended = traced
        from minicode.review_probe_correction import validate_pytest_case_report
        return extended if validate_pytest_case_report(extended) else report
    except (ValueError, TypeError, UnicodeError):
        return report


def _pytest_command(argv: tuple[str, ...]) -> bool:
    return argv[:1] == ("pytest",) or argv[:3] in (
        ("python", "-m", "pytest"), ("python3", "-m", "pytest"),
    )


def _read_pytest_report(path: Path) -> dict[str, object] | None:
    """Read one managed-process report; stdout can never substitute for it."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= _MAX_PYTEST_REPORT_BYTES):
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(_MAX_PYTEST_REPORT_BYTES + 1)
        after = os.fstat(descriptor)
        if (len(data) != before.st_size or len(data) > _MAX_PYTEST_REPORT_BYTES
                or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper()):
            return None
        document = ET.fromstring(data)
        if document.tag == "testsuite":
            suites = [document]
        elif document.tag == "testsuites":
            suites = list(document)
        else:
            return None
        totals = {"collected": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        for suite in suites:
            if suite.tag != "testsuite":
                return None
            cases = suite.findall("testcase")
            counts = {"collected": len(cases), "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
            for case in cases:
                outcomes = [child.tag for child in case if child.tag in {"failure", "error", "skipped"}]
                if len(outcomes) > 1:
                    return None
                label = {"failure": "failed", "error": "errors", "skipped": "skipped"}.get(
                    outcomes[0] if outcomes else "", "passed")
                counts[label] += 1
            for field, attribute in (("collected", "tests"), ("failed", "failures"),
                                     ("errors", "errors"), ("skipped", "skipped")):
                raw = suite.get(attribute)
                if raw is None or not raw.isascii() or not raw.isdigit() or int(raw) != counts[field]:
                    return None
            for field in totals:
                totals[field] += counts[field]
        return {**totals, "reportDigest": "sha256:" + hashlib.sha256(data).hexdigest()}
    except (OSError, ValueError, ET.ParseError):
        return None
    finally:
        os.close(descriptor)


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
        extra_read_roots: tuple[str, ...] = (),
    ) -> tuple[str, ...]: ...


@dataclass(slots=True)
class _BoundedStreamCapture:
    """Hash an arbitrary stream while retaining at most 6 KiB."""

    byte_count: int = 0
    _head: bytearray | None = None
    _tail: bytearray | None = None
    _digest: object | None = None
    sink: Callable[[bytes], None] | None = None
    stream_complete: bool = True

    def __post_init__(self) -> None:
        self._head = bytearray()
        self._tail = bytearray()
        self._digest = hashlib.sha256()

    def add(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self.sink is not None:
            self.sink(chunk)
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
        explicit_trusted_executables: Mapping[str, str] | None = None,
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
        explicit_trusted_paths: set[str] = set()
        for name, executable in (explicit_trusted_executables or {}).items():
            if (
                not isinstance(name, str)
                or not name
                or "/" in name
                or "\\" in name
                or not isinstance(executable, str)
                or not Path(executable).is_absolute()
            ):
                raise TypeError(
                    "explicit_trusted_executables contains an invalid capability"
                )
            projected_trusted[name.casefold()] = executable
            try:
                explicit_trusted_paths.add(
                    str(Path(executable).resolve(strict=True))
                )
            except OSError:
                # Keep the mapping resolvable later; only the untrusted-root
                # exemption is lost for an entry that cannot resolve now.
                continue
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
        self._explicit_trusted_paths = frozenset(explicit_trusted_paths)
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
        pytest_report_sink: Callable[[dict[str, object] | None], None] | None = None,
        output_store: VerificationOutputStore | None = None,
        output_sink: Callable[[dict[str, object]], None] | None = None,
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
        # Seatbelt profiles match canonical paths only: a symlinked cwd
        # (macOS /var -> /private/var) would be denied mid-check.
        cwd = os.path.realpath(cwd)
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
        if pytest_report_sink is not None and not callable(pytest_report_sink):
            raise TypeError("pytest_report_sink must be callable or null")
        if output_store is not None and not isinstance(output_store, VerificationOutputStore):
            raise TypeError("output_store must be a VerificationOutputStore or null")
        if output_sink is not None and not callable(output_sink):
            raise TypeError("output_sink must be callable or null")
        if output_sink is not None and output_store is None:
            raise ValueError("output_sink requires an output_store")

        result_checks: list[dict[str, object]] = []
        statuses: list[str] = []
        with tempfile.TemporaryDirectory(
            prefix="minicode-verifier-home-"
        ) as verifier_home:
            # Keep HOME on the same canonical path the sandbox profile allows.
            verifier_home = str(Path(verifier_home).resolve())
            environment = self._environment(verifier_home)
            for check in checks:
                started = self._clock()
                stdout: bytes | _BoundedStreamCapture = b""
                stderr: bytes | _BoundedStreamCapture = b""
                failure_reason: str | None = None
                resolved_executable: str | None = None
                report_path = (
                    Path(verifier_home) / ("pytest-" + uuid.uuid4().hex + ".xml")
                    if pytest_report_sink is not None and self._managed_subprocess
                    and check.kind == "tests" and _pytest_command(check.argv) else None
                )
                case_path = None
                diagnostic_arguments = ()
                check_environment = environment
                if report_path is not None:
                    from minicode.pytest_execution_evidence import execution_plugin_source
                    case_path = report_path.with_suffix(".json")
                    module_name = "_minicode_pytest_" + uuid.uuid4().hex
                    (Path(verifier_home) / (module_name + ".py")).write_text(
                        execution_plugin_source(cwd, check.argv)
                        + "_junit_path = " + repr(str(report_path)) + "\n_case_path = "
                        + repr(str(case_path)) + "\n" + _PYTEST_CASE_PLUGIN, encoding="utf-8")
                    diagnostic_arguments = ("-p", module_name, "--junitxml=" + str(report_path))
                    check_environment = dict(environment)
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
                output_capture = (output_store.begin(workflow_id=workflow_id,
                    assignment_id=execution_assignment_id, check_id=check.check_id,
                    patch_digest=patch_digest) if output_store is not None else None)
                capture_options = {}
                normal_completion = False
                if output_capture is not None and self._managed_subprocess:
                    stdout = _BoundedStreamCapture(sink=lambda data: output_capture.add("stdout", data))
                    stderr = _BoundedStreamCapture(sink=lambda data: output_capture.add("stderr", data))
                    capture_options = {"stdout_capture": stdout, "stderr_capture": stderr}
                try:
                    prepared_argv = check.argv
                    if self._managed_subprocess:
                        prepared_argv, resolved_executable, failure_reason = (
                            self._prepare_managed_command(
                                check=check,
                                cwd=cwd,
                                verifier_home=verifier_home,
                                environment=check_environment,
                                diagnostic_arguments=diagnostic_arguments,
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
                            if report_path is not None:
                                # Command preparation may sanitize interpreter
                                # discovery variables. Bind this host-created
                                # diagnostic capability afterwards; never merge
                                # caller paths back into the cleaned environment.
                                # The sandbox already grants this private home.
                                check_environment["PYTHONPATH"] = verifier_home
                            completed = self._run_managed_command(
                                prepared_argv,
                                cwd=cwd,
                                timeout_seconds=timeout_seconds,
                                environment=check_environment,
                                cancellation_checkpoint=cancellation_checkpoint,
                                **capture_options,
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
                            if output_capture is not None:
                                output_capture.add("stdout", stdout)
                                output_capture.add("stderr", stderr)
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
                        normal_completion = True
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
                finally:
                    if output_capture is not None:
                        interrupted = sys.exc_info()[0] is not None
                        try:
                            manifest = output_capture.finish(
                                stdout=self._stream_evidence(stdout), stderr=self._stream_evidence(stderr),
                                complete=normal_completion and all(
                                    not isinstance(value, _BoundedStreamCapture) or value.stream_complete
                                    for value in (stdout, stderr)))
                            if output_sink is not None:
                                output_sink(manifest)
                        except Exception:
                            # Preserve an existing cancellation/deadline/permission
                            # exception even if optional diagnostic publication fails.
                            if not interrupted:
                                raise
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
                if pytest_report_sink is not None:
                    report = _read_pytest_report(report_path) if report_path is not None else None
                    pytest_report_sink(_attach_pytest_case_report(report, case_path)
                                       if case_path is not None else report)
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
        diagnostic_arguments: tuple[str, ...] = (),
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
        prepared = (resolved, *check.argv[1:], *diagnostic_arguments)
        if internal_oracle:
            return prepared, resolved, None
        backend = self._sandbox_backend
        if backend is None:
            return prepared, resolved, "host_sandbox_unavailable"
        try:
            try:
                wrapped = backend.wrap_command(
                    argv=prepared,
                    cwd=cwd,
                    verifier_home=verifier_home,
                    extra_read_roots=self._read_roots_for(resolved),
                )
            except TypeError:
                # Host-injected backends predating the read-root extension
                # keep working with their original signature.
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

    def _read_roots_for(self, resolved_executable: str) -> tuple[str, ...]:
        """Sandbox read roots one explicitly trusted toolchain needs.

        A host-user-registered interpreter (conda/venv) cannot even start
        unless its prefix — the parent of the ``bin`` directory holding the
        executable — stays readable: the shebang interpreter, libpython and
        site-packages all live there.  System-root toolchains resolve to no
        extra root because ``/usr`` is already readable.
        """

        roots: list[str] = []
        candidate = Path(resolved_executable).resolve(strict=False)
        for _ in range(3):
            parent = candidate.parent
            if parent.name == "bin":
                prefix = parent.parent.resolve(strict=False)
                if str(prefix) not in {"/", "/usr", "/System", "/Library"}:
                    roots.append(str(prefix))
                break
            if parent.parent == parent:
                break
            candidate = parent
        return tuple(dict.fromkeys(roots))

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
            or not resolved.is_file()
            or not os.access(resolved, os.X_OK)
        ):
            return None, "trusted_executable_unavailable"
        # A host-user-registered executable (MINI_CODE_VERIFIER_TRUSTED_
        # EXECUTABLES) is exempt from the untrusted-root rejection — that
        # default exists to keep auto-discovery off user-writable paths, not
        # to override an explicit user decision.  Workspace binaries stay
        # rejected either way: verification must never execute repo bytes.
        if str(resolved) not in self._explicit_trusted_paths and any(
            resolved.is_relative_to(root)
            for root in self._untrusted_executable_roots
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
        stdout_capture: _BoundedStreamCapture | None = None,
        stderr_capture: _BoundedStreamCapture | None = None,
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
        stdout_capture = stdout_capture or _BoundedStreamCapture()
        stderr_capture = stderr_capture or _BoundedStreamCapture()
        stdout_capture.stream_complete = stderr_capture.stream_complete = False

        def drain(stream: object, capture: _BoundedStreamCapture) -> None:
            reader = getattr(stream, "read", None)
            if not callable(reader):
                return
            try:
                while chunk := reader(64 * 1024):
                    capture.add(chunk)
                capture.stream_complete = True
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
