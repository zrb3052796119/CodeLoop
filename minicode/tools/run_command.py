from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from minicode.background_tasks import (
    register_background_shell_task,
    release_background_task_slot,
    reserve_background_task_slot,
)
from minicode.subagent_supervisor import _kill_process_tree
from minicode.task_deadline import TaskDeadlineExceeded, raise_task_deadline
from minicode.tooling import (
    ToolDefinition,
    ToolResult,
    ToolTerminationReason,
)
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.verification_observation import project_command_verification
from minicode.workspace import resolve_tool_path
from minicode.workspace import INTERNAL_WORKSPACE_STORE_NAMES

# 命令执行超时（秒）- 5 分钟
COMMAND_TIMEOUT = 300

# 传给子进程前从环境中剔除的敏感变量标记（防止模型可控的命令外泄凭证，
# 例如 `curl -d "$ANTHROPIC_API_KEY" ...`）。设置 MINICODE_PASS_SENSITIVE_ENV=1
# 可显式恢复完整环境。
_SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "APIKEY",
    "AUTH_TOKEN",
    "ACCESS_TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
    # Bare TOKEN names carry credentials too: GITHUB_TOKEN,
    # AWS_SESSION_TOKEN, GITLAB_TOKEN, HF_TOKEN... Over-stripping a
    # benign TOKENIZE-style variable is harmless compared to leaking one
    # of these.
    "TOKEN",
)


def _subprocess_environment() -> dict[str, str]:
    """Environment for tool subprocesses with credentials stripped."""
    if os.environ.get("MINICODE_PASS_SENSITIVE_ENV") == "1":
        return os.environ.copy()
    return {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in _SENSITIVE_ENV_MARKERS)
    }

# 最大输出大小（字符）- 防止超大输出撑爆上下文
MAX_OUTPUT_CHARS = 200_000


def _truncate_large_output(output: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    """Truncate very large command output to prevent context bloat.

    Keeps roughly the first 60% and last 40% of the character budget so both
    the beginning of the output and its final lines survive.
    """
    if len(output) <= max_chars:
        return output

    head_budget = int(max_chars * 0.6)
    tail_budget = max_chars - head_budget

    lines = output.split("\n")
    head_lines: list[str] = []
    consumed = 0
    for line in lines:
        cost = len(line) + 1
        if consumed + cost > head_budget:
            break
        head_lines.append(line)
        consumed += cost

    tail_lines: list[str] = []
    consumed = 0
    for line in reversed(lines[len(head_lines):]):
        cost = len(line) + 1
        if consumed + cost > tail_budget:
            break
        tail_lines.append(line)
        consumed += cost
    tail_lines.reverse()

    if not head_lines and not tail_lines:
        # Degenerate case: individual lines larger than the budget — fall
        # back to raw character slicing.
        return (
            f"{output[:head_budget]}\n\n... [output truncated, was {len(output):,} chars] ...\n\n"
            f"{output[-tail_budget:]}"
        )

    omitted = len(lines) - len(head_lines) - len(tail_lines)
    head = "\n".join(head_lines)
    tail = "\n".join(tail_lines)
    return f"{head}\n\n... [{omitted} lines omitted, output was {len(output):,} chars] ...\n\n{tail}"

# Read-only commands that never need permission prompts.
# Includes both Unix and Windows equivalents.
READONLY_COMMANDS = {
    # Unix
    "pwd",
    "ls",
    "find",
    "rg",
    "grep",
    "cat",
    "head",
    "tail",
    "wc",
    "sed",
    "echo",
    "df",
    "du",
    "whoami",
    # Windows equivalents
    "dir",
    "type",
    "where",
    "findstr",
    "more",
    "hostname",
}

# Development commands (write access but commonly allowed).
DEVELOPMENT_COMMANDS = {
    "git",
    "npm",
    "node",
    "python",
    "python3",
    "pytest",
    "bash",
    "sh",
    # Windows-common development tools
    "pip",
    "pip3",
    "cargo",
    "go",
    "make",
    "cmake",
    "dotnet",
    "powershell",
    "pwsh",
    "cmd",
}


def split_command_line(command_line: str) -> list[str]:
    """Split a command string into tokens.

    On Windows, ``shlex.split(posix=True)`` can choke on backslash paths
    (e.g. ``C:\\Users\\foo``).  We fall back to ``posix=False`` which
    preserves backslashes, then try the native ``shlex.split`` as a
    last resort.
    """
    if os.name == "nt":
        try:
            return shlex.split(command_line, posix=False)
        except ValueError:
            # If even non-posix fails, fall back to simple whitespace split
            return command_line.split()
    return shlex.split(command_line, posix=True)


def _is_allowed_command(command: str) -> bool:
    cmd = command.lower() if os.name == "nt" else command
    return cmd in READONLY_COMMANDS or cmd in DEVELOPMENT_COMMANDS


def _is_read_only_command(command: str) -> bool:
    cmd = command.lower() if os.name == "nt" else command
    return cmd in READONLY_COMMANDS


_INTERPRETER_COMMANDS = frozenset(
    {
        "bash",
        "cmd",
        "node",
        "powershell",
        "pwsh",
        "python",
        "python3",
        "sh",
    }
)
_PATH_CAPABLE_READONLY_COMMANDS = frozenset(
    {
        "cat",
        "dir",
        "du",
        "find",
        "findstr",
        "grep",
        "head",
        "ls",
        "more",
        "rg",
        "sed",
        "tail",
        "type",
        "wc",
        "where",
    }
)


def _argument_path_candidate(argument: str) -> str | None:
    """Project an obvious argv path without pretending to parse every CLI."""
    value = argument.split("=", 1)[1] if argument.startswith("--") and "=" in argument else argument
    if not value or value == "-":
        return None
    if Path(value).is_absolute() or value.startswith(("../", "..\\")):
        return value
    return None


def _outside_workspace_path(argument: str, cwd: str) -> bool:
    candidate = _argument_path_candidate(argument)
    if candidate is None:
        return False
    try:
        workspace = Path(cwd).resolve(strict=False)
        target = Path(candidate)
        if not target.is_absolute():
            target = workspace / target
        target.resolve(strict=False).relative_to(workspace)
        return False
    except (OSError, ValueError):
        return True


def _command_approval_reason(command: str, args: Sequence[str], cwd: str) -> str | None:
    """Return why argv is not provably read-only and workspace-contained."""
    command_name = Path(command).name.lower()
    lowered_args = [arg.lower() for arg in args]
    if command_name in _INTERPRETER_COMMANDS:
        return f"interpreter command can execute arbitrary code: {command_name}"
    if command_name == "find" and any(
        arg in {"-delete", "-exec", "-execdir", "-ok", "-okdir"}
        for arg in lowered_args
    ):
        return "find arguments can delete files or execute another command"
    if command_name == "sed" and any(
        arg == "--in-place" or arg.startswith("--in-place=")
        or (arg.startswith("-i") and arg != "-")
        for arg in lowered_args
    ):
        return "sed in-place editing can modify files"
    if command_name == "rg" and any(
        arg == "--pre" or arg.startswith("--pre=") for arg in lowered_args
    ):
        return "rg preprocessor arguments can execute another command"
    if command_name in _PATH_CAPABLE_READONLY_COMMANDS:
        if any(_outside_workspace_path(arg, cwd) for arg in args):
            return "command arguments reference a path outside the workspace"
        if args:
            return "command arguments require review for filesystem scope"
    if command_name in DEVELOPMENT_COMMANDS:
        return "development command can execute code or modify the workspace"
    return None


def _internal_store_command_risk(command: str, args: list[str]) -> str | None:
    """Reject command shapes that can bypass Memory retrieval authority."""
    normalized = [str(value).replace("\\", "/").lower() for value in (command, *args)]
    if any(
        store in value
        for value in normalized
        for store in INTERNAL_WORKSPACE_STORE_NAMES
    ):
        return "command targets MiniCode internal Memory state"
    command_name = Path(command).name.lower() if command else ""
    if command_name == "find" and any(
        value in {"-exec", "-execdir"} for value in normalized[1:]
    ):
        return "find -exec can traverse MiniCode internal Memory state"
    if command_name in {"grep", "egrep", "fgrep"} and any(
        value in {"-r", "--recursive"} for value in normalized[1:]
    ):
        return "recursive grep can traverse MiniCode internal Memory state"
    if command_name == "rg" and "--hidden" in normalized[1:]:
        return "hidden-file search can traverse MiniCode internal Memory state"
    return None


def _looks_like_shell_snippet(command: str, args: list[str]) -> bool:
    return not args and any(char in command for char in "|&;<>()$`")


def _is_background_shell_snippet(command: str, args: list[str]) -> bool:
    trimmed = command.strip()
    return not args and trimmed.endswith("&") and not trimmed.endswith("&&")


def _strip_trailing_background_operator(command: str) -> str:
    return command.strip().removesuffix("&").strip()


def _classify_shell_snippet_risk(command: str) -> str | None:
    lowered = command.lower()
    collapsed = re.sub(r"\s+", " ", lowered).strip()
    if re.search(r"\brm\s+-[a-z]*r[a-z]*f\b|\brm\s+-[a-z]*f[a-z]*r\b", collapsed):
        return f"shell snippet contains rm -rf payload: {command}"
    if re.search(r"\b(del|erase)\b.*\s/(s|q)\b", collapsed):
        return f"shell snippet contains recursive Windows delete payload: {command}"
    if re.search(r"\b(rmdir|rd)\b.*\s/s\b", collapsed):
        return f"shell snippet contains recursive Windows directory removal: {command}"
    if re.search(r"\b(curl|wget)\b.*\|\s*(sh|bash|zsh|fish)\b", collapsed):
        return f"shell snippet downloads and executes a shell script: {command}"
    if re.search(r"\b(iwr|irm|invoke-webrequest|invoke-restmethod|curl|wget)\b.*\|\s*(iex|invoke-expression)\b", collapsed):
        return f"shell snippet downloads and executes PowerShell code: {command}"
    if re.search(r"\b(powershell|pwsh)\b.*\b(iex|invoke-expression)\b", collapsed):
        return f"shell snippet invokes PowerShell expression execution: {command}"
    if re.search(r"\b(sh|bash|zsh|fish|cmd|powershell|pwsh)\b\s+(-c|/c|/command)\b", collapsed):
        return f"shell snippet invokes an explicit command interpreter: {command}"
    return None


def _normalize_command_input(input_data: dict) -> tuple[str, list[str]]:
    command = str(input_data.get("command", "")).strip()
    raw_args = input_data.get("args") or []
    if raw_args:
        return command, [str(arg) for arg in raw_args]
    parsed = split_command_line(command) if command else []
    return (parsed[0], parsed[1:]) if parsed else ("", [])


def _is_windows_shell_builtin(command: str) -> bool:
    return os.name == "nt" and command.lower() in {
        "cd",
        "chdir",
        "cls",
        "copy",
        "date",
        "del",
        "dir",
        "echo",
        "erase",
        "md",
        "mkdir",
        "mklink",
        "move",
        "rd",
        "ren",
        "rename",
        "rmdir",
        "time",
        "type",
        "ver",
        "vol",
    }


def _build_execution_command(
    raw_command: str,
    normalized_command: str,
    normalized_args: Sequence[str],
    *,
    use_shell: bool,
    background_shell: bool,
) -> tuple[str, list[str]]:
    if use_shell:
        shell_command = _strip_trailing_background_operator(raw_command) if background_shell else raw_command
        if os.name == "nt":
            return "cmd", ["/d", "/s", "/c", shell_command]
        # Use the user's preferred shell (macOS defaults to zsh since
        # Catalina).  Fall back to /bin/sh for maximum POSIX compatibility.
        shell = os.environ.get("SHELL", "/bin/sh")
        return shell, ["-lc", shell_command]
    if _is_windows_shell_builtin(normalized_command):
        quoted_args = subprocess.list2cmdline(list(normalized_args))
        shell_command = normalized_command if not quoted_args else f"{normalized_command} {quoted_args}"
        return "cmd", ["/d", "/s", "/c", shell_command]
    return normalized_command, list(normalized_args)


def _validate(input_data: dict) -> dict:
    command = input_data.get("command")
    if not isinstance(command, str):
        raise ValueError("command is required")
    args = input_data.get("args") or []
    if not isinstance(args, list):
        raise ValueError("args must be a list")
    cwd = input_data.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise ValueError("cwd must be a string")
    # Optional timeout (seconds), clamped to [1, 600]
    timeout = input_data.get("timeout")
    if timeout is not None:
        try:
            timeout = max(1, min(600, int(timeout)))
        except (ValueError, TypeError):
            timeout = None
    return {"command": command, "args": [str(arg) for arg in args], "cwd": cwd, "timeout": timeout}


def _with_verification(input_data: dict, result: ToolResult) -> ToolResult:
    result.verification = project_command_verification(
        input_data,
        passed=result.ok,
    )
    return result


@dataclass(slots=True)
class _StreamCapture:
    """Drain an arbitrary stream while retaining bounded head/tail output."""

    limit: int = MAX_OUTPUT_CHARS
    total: int = 0
    _head: bytearray = field(default_factory=bytearray)
    _tail: bytearray = field(default_factory=bytearray)

    def add(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.total += len(chunk)
        head_limit = int(self.limit * 0.6)
        tail_limit = self.limit - head_limit
        if len(self._head) < head_limit:
            used = min(len(chunk), head_limit - len(self._head))
            self._head.extend(chunk[:used])
            chunk = chunk[used:]
        if chunk:
            self._tail.extend(chunk)
            if len(self._tail) > tail_limit:
                del self._tail[: len(self._tail) - tail_limit]

    def text(self) -> str:
        if self.total <= self.limit:
            raw = bytes(self._head + self._tail)
        else:
            marker = (
                f"\n\n... [output truncated, was {self.total:,} bytes] ...\n\n"
            ).encode()
            raw = bytes(self._head) + marker + bytes(self._tail)
        return raw.decode("utf-8", errors="replace")


class _CommandTimeout(TimeoutError):
    pass


def _execution_deadline(input_data: dict, context) -> tuple[float, float]:
    timeout_seconds = float(input_data.get("timeout") or COMMAND_TIMEOUT)
    deadline = time.monotonic() + timeout_seconds
    task_deadline = getattr(context, "_task_deadline_monotonic", None)
    if isinstance(task_deadline, (int, float)):
        deadline = min(deadline, float(task_deadline))
    return deadline, timeout_seconds


def _execution_checkpoint(context, *, deadline: float) -> None:
    # The shared helper checks the owning Turn token first and then its task
    # deadline. The command timeout can only shorten that monotonic boundary.
    raise_task_deadline(context)
    if time.monotonic() >= deadline:
        raise _CommandTimeout


def _popen_options() -> dict[str, object]:
    if os.name == "nt":
        return {
            "creationflags": int(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        }
    return {"start_new_session": True}


def _finish_reader_threads(
    process: subprocess.Popen,
    threads: Sequence[threading.Thread],
) -> None:
    for stream in (process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except OSError:
                pass
    for thread in threads:
        thread.join(timeout=1.0)


def _run_with_pipes(
    argv: list[str],
    *,
    cwd: str,
    environment: dict[str, str],
    context,
    deadline: float,
) -> tuple[int, str]:
    process: subprocess.Popen[bytes] | None = None
    threads: list[threading.Thread] = []
    stdout_capture = _StreamCapture()
    stderr_capture = _StreamCapture()
    try:
        _execution_checkpoint(context, deadline=deadline)
        process = subprocess.Popen(  # noqa: S603
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            **_popen_options(),
        )

        def drain(stream: object, capture: _StreamCapture) -> None:
            reader = getattr(stream, "read", None)
            if not callable(reader):
                return
            try:
                while chunk := reader(64 * 1024):
                    capture.add(chunk)
            except (OSError, ValueError):
                pass

        threads = [
            threading.Thread(
                target=drain,
                args=(process.stdout, stdout_capture),
                name="minicode-command-stdout",
                daemon=True,
            ),
            threading.Thread(
                target=drain,
                args=(process.stderr, stderr_capture),
                name="minicode-command-stderr",
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        while process.poll() is None:
            _execution_checkpoint(context, deadline=deadline)
            remaining = deadline - time.monotonic()
            time.sleep(min(0.05, max(0.001, remaining)))
        return_code = process.returncode
        if not isinstance(return_code, int):
            return_code = process.wait(timeout=0.1)
    finally:
        if process is not None:
            # A successful direct child can still leave descendants holding the
            # pipes. End its complete group before joining the drain threads.
            _kill_process_tree(process, grace_seconds=0.2)
            _finish_reader_threads(process, threads)
    output = "\n".join(
        part.strip()
        for part in (stdout_capture.text(), stderr_capture.text())
        if part.strip()
    )
    return return_code, _truncate_large_output(output)


def _run_with_pty(
    argv: list[str],
    *,
    cwd: str,
    environment: dict[str, str],
    context,
    deadline: float,
    master_fd: int,
    slave_fd: int,
) -> tuple[int, str]:
    import select

    process: subprocess.Popen[bytes] | None = None
    capture = _StreamCapture()
    process_group_cleaned = False
    try:
        _execution_checkpoint(context, deadline=deadline)
        process = subprocess.Popen(  # noqa: S603
            argv,
            cwd=cwd,
            env=environment,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            **_popen_options(),
        )
        # Keep our slave open through the final drain. On macOS, closing
        # the last slave can discard output before the master has read it.

        while True:
            _execution_checkpoint(context, deadline=deadline)
            process_exited = process.poll() is not None
            if process_exited and not process_group_cleaned:
                # Descendants may still own the PTY or keep writing after the
                # direct child exits. Stop the group before draining queued data.
                _kill_process_tree(process, grace_seconds=0.2)
                process_group_cleaned = True
                _execution_checkpoint(context, deadline=deadline)
            remaining = deadline - time.monotonic()
            readable, _, _ = select.select(
                [master_fd],
                [],
                [],
                0 if process_exited else min(0.05, max(0.001, remaining)),
            )
            if readable:
                try:
                    chunk = os.read(master_fd, 64 * 1024)
                except OSError:
                    chunk = b""
                if chunk:
                    capture.add(chunk)
                    continue
            if process_exited:
                _execution_checkpoint(context, deadline=deadline)
                break

        return_code = process.returncode
        if not isinstance(return_code, int):
            return_code = process.wait(timeout=0.1)
    finally:
        try:
            if process is not None and not process_group_cleaned:
                _kill_process_tree(process, grace_seconds=0.2)
        finally:
            try:
                os.close(slave_fd)
            except OSError:
                pass
            try:
                os.close(master_fd)
            except OSError:
                pass
    output = capture.text().replace("\r\n", "\n").strip()
    return return_code, _truncate_large_output(output)


def _control_failure_result(input_data: dict, error: BaseException) -> ToolResult:
    if isinstance(error, TurnCancellationRequested):
        return _with_verification(
            input_data,
            ToolResult(
                ok=False,
                output="error[turn_cancelled]: command process group terminated",
                _termination_reason=ToolTerminationReason.TURN_CANCELLED,
            ),
        )
    if isinstance(error, TaskDeadlineExceeded):
        return _with_verification(
            input_data,
            ToolResult(
                ok=False,
                output=f"error[task_deadline_exceeded]: {error}",
                _termination_reason=ToolTerminationReason.DEADLINE_EXCEEDED,
            ),
        )
    raise TypeError("unsupported command control failure")


def _run(input_data: dict, context) -> ToolResult:
    effective_cwd = (
        str(resolve_tool_path(context, input_data["cwd"], "list"))
        if input_data.get("cwd")
        else context.cwd
    )
    normalized_command, normalized_args = _normalize_command_input(input_data)
    if not normalized_command:
        return ToolResult(ok=False, output="Command not allowed: empty command")

    raw_args = input_data.get("args") or []
    use_shell = _looks_like_shell_snippet(input_data["command"], raw_args)
    background_shell = _is_background_shell_snippet(input_data["command"], raw_args)
    known_command = _is_allowed_command(normalized_command)
    internal_store_risk = _internal_store_command_risk(
        normalized_command,
        normalized_args,
    )
    if internal_store_risk:
        return ToolResult(
            ok=False,
            output=f"Command not allowed: {internal_store_risk}.",
        )

    command, args = _build_execution_command(
        input_data["command"],
        normalized_command,
        normalized_args,
        use_shell=use_shell,
        background_shell=background_shell,
    )
    if use_shell:
        force_prompt_reason = (
            _classify_shell_snippet_risk(input_data["command"])
            or "shell snippet executes through the user's shell and requires "
            f"approval: {input_data['command']}"
        )
    elif not known_command:
        force_prompt_reason = (
            f"Unknown command '{normalized_command}' is not in the built-in "
            "read-only/development set"
        )
    else:
        force_prompt_reason = _command_approval_reason(
            normalized_command,
            normalized_args,
            effective_cwd,
        )

    try:
        _execution_checkpoint(context, deadline=float("inf"))
    except (TurnCancellationRequested, TaskDeadlineExceeded) as error:
        return _control_failure_result(input_data, error)
    if context.permissions is not None:
        if force_prompt_reason:
            context.permissions.ensure_command(
                command,
                args,
                effective_cwd,
                force_prompt_reason=force_prompt_reason,
            )
        elif use_shell or not _is_read_only_command(normalized_command):
            context.permissions.ensure_command(command, args, effective_cwd)
        checkpoint = getattr(
            context.permissions,
            "ensure_operation_active",
            None,
        )
        if callable(checkpoint):
            checkpoint()
    try:
        _execution_checkpoint(context, deadline=float("inf"))
    except (TurnCancellationRequested, TaskDeadlineExceeded) as error:
        return _control_failure_result(input_data, error)

    environment = _subprocess_environment()
    if use_shell and background_shell:
        reservation_id = reserve_background_task_slot()
        if reservation_id is None:
            return ToolResult(
                ok=False,
                output="No background task slots are available.",
            )
        child: subprocess.Popen | None = None
        try:
            _execution_checkpoint(context, deadline=float("inf"))
            child = subprocess.Popen(  # noqa: S603
                [command, *args],
                cwd=effective_cwd,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                close_fds=True,
                **_popen_options(),
            )
            if child.pid is None:
                raise RuntimeError("spawned background command has no PID")
            background_task = register_background_shell_task(
                command=_strip_trailing_background_operator(
                    input_data["command"]
                ),
                pid=child.pid,
                cwd=effective_cwd,
                process=child,
                reservation_id=reservation_id,
                owns_process_group=True,
            )
            reservation_id = None
        except BaseException:
            if child is not None:
                _kill_process_tree(child, grace_seconds=0.2)
            raise
        finally:
            release_background_task_slot(reservation_id)
        return ToolResult(
            ok=True,
            output=(
                "Background command started.\n"
                f"TASK: {background_task.taskId}\nPID: {background_task.pid}"
            ),
            backgroundTask=background_task,
        )

    deadline, effective_timeout = _execution_deadline(input_data, context)
    try:
        if sys.platform != "win32":
            try:
                import pty
            except ImportError:
                pass
            else:
                # openpty can allocate one descriptor before failing on the
                # other. Python closes those internally; after success this
                # call owns both descriptors until _run_with_pty returns.
                master_fd, slave_fd = pty.openpty()
                return_code, output = _run_with_pty(
                    [command, *args],
                    cwd=effective_cwd,
                    environment=environment,
                    context=context,
                    deadline=deadline,
                    master_fd=master_fd,
                    slave_fd=slave_fd,
                )
                return _with_verification(
                    input_data,
                    ToolResult(ok=return_code == 0, output=output),
                )

        return_code, output = _run_with_pipes(
            [command, *args],
            cwd=effective_cwd,
            environment=environment,
            context=context,
            deadline=deadline,
        )
        return _with_verification(
            input_data,
            ToolResult(ok=return_code == 0, output=output),
        )
    except TurnCancellationRequested:
        return _with_verification(
            input_data,
            ToolResult(
                ok=False,
                output="error[turn_cancelled]: command process group terminated",
                _termination_reason=ToolTerminationReason.TURN_CANCELLED,
            ),
        )
    except TaskDeadlineExceeded as error:
        return _with_verification(
            input_data,
            ToolResult(
                ok=False,
                output=f"error[task_deadline_exceeded]: {error}",
                _termination_reason=ToolTerminationReason.DEADLINE_EXCEEDED,
            ),
        )
    except _CommandTimeout:
        return _with_verification(
            input_data,
            ToolResult(
                ok=False,
                output=(
                    f"Command timed out after {effective_timeout:g} seconds "
                    "(process group terminated)."
                ),
                _termination_reason=ToolTerminationReason.DEADLINE_EXCEEDED,
            ),
        )


run_command_tool = ToolDefinition(
    name="run_command",
    description="Run a common development command from an allowlist. Supports optional timeout parameter (1-600 seconds).",
    input_schema={"type": "object", "properties": {"command": {"type": "string", "description": "Command to run"}, "args": {"type": "array", "items": {"type": "string"}, "description": "Arguments"}, "cwd": {"type": "string", "description": "Working directory"}, "timeout": {"type": "integer", "description": "Timeout in seconds (1-600, default 300)"}}, "required": ["command"]},
    validator=_validate,
    run=_run,
)
