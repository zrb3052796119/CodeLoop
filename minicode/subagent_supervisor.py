"""Parent-side supervisor for subprocess-isolated ``general`` sub-agents.

The supervisor owns the hard boundary the in-process backend cannot provide:
it spawns ``minicode.subagent_worker`` as a detached process group, watches
the parent's deadline and cancellation token, and on either one kills the
whole group instead of waiting for a stuck provider call to return.

Terminal-state rule: exactly one decision wins. Once a kill is issued the
outcome is ``timed_out``/``cancelled`` and any result file the child managed
to write is deliberately ignored — a killed process must not overwrite the
terminal state with a late success.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_COMMAND: tuple[str, ...] = (
    sys.executable,
    "-m",
    "minicode.subagent_worker",
)

_WORKER_STATUSES = frozenset(
    {"completed", "failed", "deadline_exceeded", "budget_exceeded", "error"}
)

# The worker bounds its own payload well below this; the cap only guards the
# parent against reading an unbounded file from a compromised or buggy child.
_MAX_RESULT_BYTES = 1_000_000
_MAX_LOG_TAIL_CHARS = 2_000


@dataclass(frozen=True, slots=True)
class SubagentProcessOutcome:
    """Single terminal state of one supervised sub-agent process."""

    status: str
    result: Mapping[str, object] | None
    error: str | None
    duration_ms: int
    pid: int | None


def _windows_creationflags() -> int:
    # CREATE_NEW_PROCESS_GROUP isolates the child's console Ctrl handlers
    # from the parent. It exists only on Windows; keep the lookup portable.
    return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def _kill_process_tree(process: subprocess.Popen, *, grace_seconds: float) -> None:
    """Terminate the child group, escalate, and make a bounded reap attempt."""
    grace_seconds = max(0.0, float(grace_seconds))
    if os.name == "posix":
        # start_new_session=True made the child a process-group leader. Signal
        # the group even if its leader already exited: descendants may still be
        # holding pipes/PTYs open after the direct process has terminated.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            pass
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except OSError:
                break
            time.sleep(min(0.02, max(0.001, deadline - time.monotonic())))
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    else:
        # Windows has no portable killpg equivalent. CREATE_NEW_PROCESS_GROUP
        # keeps control handling isolated; terminate/kill safely reap the direct
        # child. Embedders that need recursive Windows cleanup should use a Job.
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=grace_seconds)
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        pass
    try:
        process.wait(timeout=grace_seconds + 5.0)
    except (OSError, subprocess.TimeoutExpired):  # pragma: no cover - defensive reap
        pass


def _read_result_file(result_path) -> dict[str, object] | None:
    try:
        if not result_path.is_file() or result_path.is_symlink():
            return None
        if result_path.stat().st_size > _MAX_RESULT_BYTES:
            return None
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _log_tail(log_path) -> str:
    try:
        if not log_path.is_file():
            return ""
        data = log_path.read_bytes()[-_MAX_LOG_TAIL_CHARS * 4 :]
        return data.decode("utf-8", errors="replace")[-_MAX_LOG_TAIL_CHARS:]
    except OSError:
        return ""


def _cancellation_requested(cancellation_token: object | None) -> bool:
    if cancellation_token is None:
        return False
    checker = getattr(cancellation_token, "raise_if_requested", None)
    if callable(checker):
        try:
            checker()
        except Exception:
            return True
        return False
    requested = getattr(cancellation_token, "is_requested", None)
    if callable(requested):
        try:
            return bool(requested())
        except Exception:  # noqa: BLE001 - observation never kills the child
            return False
    return False


def run_subagent_process(
    request: Mapping[str, object],
    *,
    deadline_monotonic: float | None = None,
    cancellation_token: object | None = None,
    command: Sequence[str] | None = None,
    poll_interval_seconds: float = 0.05,
    kill_grace_seconds: float = 1.0,
    on_tool_evidence=None,
) -> SubagentProcessOutcome:
    """Run one sub-agent worker process and return its single terminal state.

    ``command`` replaces the default ``python -m minicode.subagent_worker``
    argv (``--request``/``--result`` paths are appended); it exists for tests
    and embedders that need a different interpreter or wrapper.
    """
    if deadline_monotonic is not None and (
        isinstance(deadline_monotonic, bool)
        or not isinstance(deadline_monotonic, (int, float))
        or not math.isfinite(deadline_monotonic)
    ):
        raise ValueError("deadline_monotonic must be finite or None")
    started_at = time.monotonic()
    workdir_path = Path(tempfile.mkdtemp(prefix="minicode-subagent-"))
    request_path = workdir_path / "request.json"
    result_path = workdir_path / "result.json"
    stdout_path = workdir_path / "stdout.log"
    stderr_path = workdir_path / "stderr.log"
    from minicode.subagent_tool_evidence import open_transport_directory, replay_tool_evidence
    evidence_directory = open_transport_directory(workdir_path)

    def finish(
        status: str,
        *,
        result: Mapping[str, object] | None = None,
        error: str | None = None,
        pid: int | None = None,
    ) -> SubagentProcessOutcome:
        # A killed child's terminal result remains untrusted. Already flushed
        # diagnostics have no outcome authority and may still be retained.
        replay_tool_evidence(evidence_directory, on_tool_evidence)
        return SubagentProcessOutcome(
            status=status,
            result=result,
            error=error,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            pid=pid,
        )

    try:
        request_path.write_text(
            json.dumps(dict(request), ensure_ascii=False),
            encoding="utf-8",
        )
        argv = list(command or DEFAULT_COMMAND) + [
            "--request",
            str(request_path),
            "--result",
            str(result_path),
        ]
        with (
            open(stdout_path, "wb") as stdout_file,
            open(stderr_path, "wb") as stderr_file,
        ):
            try:
                process: subprocess.Popen[bytes] = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=os.name == "posix",
                    creationflags=_windows_creationflags(),
                )
            except OSError as exc:
                return finish(
                    "spawn_failed",
                    error=f"{type(exc).__name__}: {exc}",
                )

            killed_for: str | None = None
            while True:
                if _cancellation_requested(cancellation_token):
                    killed_for = "cancelled"
                elif (
                    isinstance(deadline_monotonic, (int, float))
                    and time.monotonic() >= float(deadline_monotonic)
                ):
                    killed_for = "timed_out"
                if killed_for is not None:
                    _kill_process_tree(process, grace_seconds=kill_grace_seconds)
                    return finish(
                        killed_for,
                        error=(
                            "sub-agent process killed: "
                            + (
                                "parent turn cancellation requested"
                                if killed_for == "cancelled"
                                else "task deadline exceeded"
                            )
                        ),
                        pid=process.pid,
                    )
                if process.poll() is not None:
                    break
                time.sleep(poll_interval_seconds)

            # The child exited on its own; its result file is the outcome.
            result = _read_result_file(result_path)
            if result is None:
                tail = _log_tail(stderr_path).strip()
                detail = f"worker exited {process.returncode} without a result"
                if tail:
                    detail += f"; stderr tail: {tail}"
                return finish("result_missing", error=detail, pid=process.pid)
            status = result.get("status")
            if status not in _WORKER_STATUSES:
                return finish(
                    "result_invalid",
                    error=f"worker result status is invalid: {status!r}",
                    pid=process.pid,
                )
            error = result.get("error")
            return finish(
                str(status),
                result=result,
                error=error if isinstance(error, str) else None,
                pid=process.pid,
            )
    finally:
        if evidence_directory is not None:
            os.close(evidence_directory)
        shutil.rmtree(workdir_path, ignore_errors=True)


__all__ = [
    "DEFAULT_COMMAND",
    "SubagentProcessOutcome",
    "run_subagent_process",
]
