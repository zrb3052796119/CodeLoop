from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from minicode.background_tasks import register_background_shell_task
from minicode.tooling import ToolDefinition, ToolResult
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


def _run(input_data: dict, context) -> ToolResult:
    effective_cwd = str(resolve_tool_path(context, input_data["cwd"], "list")) if input_data.get("cwd") else context.cwd
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
        return ToolResult(ok=False, output=f"Command not allowed: {internal_store_risk}.")

    command, args = _build_execution_command(
        input_data["command"],
        normalized_command,
        normalized_args,
        use_shell=use_shell,
        background_shell=background_shell,
    )
    if use_shell:
        # Shell snippets execute through the user's shell and cannot be
        # statically proven safe, so they always require approval. Known
        # high-risk patterns get a specific reason; everything else gets a
        # generic one instead of silently passing through.
        force_prompt_reason = (
            _classify_shell_snippet_risk(input_data["command"])
            or f"shell snippet executes through the user's shell and requires approval: {input_data['command']}"
        )
    elif known_command:
        force_prompt_reason = None
    else:
        force_prompt_reason = f"Unknown command '{normalized_command}' is not in the built-in read-only/development set"

    if context.permissions is not None:
        if force_prompt_reason:
            context.permissions.ensure_command(command, args, effective_cwd, force_prompt_reason=force_prompt_reason)
        elif use_shell or not _is_read_only_command(normalized_command):
            context.permissions.ensure_command(command, args, effective_cwd)
        checkpoint = getattr(context.permissions, "ensure_operation_active", None)
        if checkpoint is not None:
            checkpoint()

    if use_shell and background_shell:
        # Platform-specific process isolation flags
        popen_kwargs: dict = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            # On Unix, start the background process in its own session so
            # it is not killed when the parent terminal closes.
            popen_kwargs["start_new_session"] = True

        child = subprocess.Popen(  # noqa: S603
            [command, *args],
            cwd=effective_cwd,
            env=_subprocess_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            **popen_kwargs,
        )
        
        if child.pid is None:
            return ToolResult(
                ok=False,
                output="Failed to get PID for background command. Process may have exited immediately.",
            )
        
        background_task = register_background_shell_task(
            command=_strip_trailing_background_operator(input_data["command"]),
            pid=child.pid,
            cwd=effective_cwd,
        )
        return ToolResult(
            ok=True,
            output=f"Background command started.\nTASK: {background_task.taskId}\nPID: {background_task.pid}",
            backgroundTask=background_task,
        )

    if sys.platform != "win32":
        try:
            import pty
            import select
            
            master_fd, slave_fd = pty.openpty()
            effective_timeout = input_data.get("timeout") or COMMAND_TIMEOUT
            
            process = subprocess.Popen(
                [command, *args],
                cwd=effective_cwd,
                env=_subprocess_environment(),
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
            )
            
            os.close(slave_fd)
            output_bytes = bytearray()
            timed_out = False
            
            try:
                while True:
                    r, _, _ = select.select([master_fd], [], [], effective_timeout)
                    if not r:
                        timed_out = True
                        process.kill()
                        process.wait()
                        break
                    
                    try:
                        data = os.read(master_fd, 4096)
                        if not data:
                            break
                        output_bytes.extend(data)
                    except OSError:
                        # EIO happens when child closes the PTY or exits
                        break
            finally:
                os.close(master_fd)
                if not timed_out:
                    process.wait()
                
            output_str = output_bytes.decode("utf-8", errors="replace").strip()
            output_str = output_str.replace("\r\n", "\n")
            output_str = _truncate_large_output(output_str)
            
            if timed_out:
                return _with_verification(
                    input_data,
                    ToolResult(
                        ok=False,
                        output=f"Command timed out after {effective_timeout} seconds (process killed).\nPartial output:\n{output_str}",
                    ),
                )
            return _with_verification(
                input_data,
                ToolResult(ok=process.returncode == 0, output=output_str),
            )
            
        except ImportError:
            pass  # Fallback to subprocess on systems without pty

    try:
        effective_timeout = input_data.get("timeout") or COMMAND_TIMEOUT
        completed = subprocess.run(  # noqa: S603
            [command, *args],
            cwd=effective_cwd,
            env=_subprocess_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",  # 显式指定 UTF-8
            errors="replace",   # 无法解码时替换字符而非报错
            check=False,
            timeout=effective_timeout,
        )
        output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part).strip()
        output = _truncate_large_output(output)
        return _with_verification(
            input_data,
            ToolResult(ok=completed.returncode == 0, output=output),
        )
    except subprocess.TimeoutExpired as e:
        # Capture partial output from timeout
        partial_stdout = (e.stdout or "").strip() if e.stdout else ""
        partial_stderr = (e.stderr or "").strip() if e.stderr else ""
        partial = "\n".join(part for part in [partial_stdout, partial_stderr] if part)
        if partial:
            partial = f"\nPartial output:\n{_truncate_large_output(partial)}"
        return _with_verification(
            input_data,
            ToolResult(
                ok=False,
                output=f"Command timed out after {effective_timeout} seconds (process killed).{partial}",
            ),
        )


run_command_tool = ToolDefinition(
    name="run_command",
    description="Run a common development command from an allowlist. Supports optional timeout parameter (1-600 seconds).",
    input_schema={"type": "object", "properties": {"command": {"type": "string", "description": "Command to run"}, "args": {"type": "array", "items": {"type": "string"}, "description": "Arguments"}, "cwd": {"type": "string", "description": "Working directory"}, "timeout": {"type": "integer", "description": "Timeout in seconds (1-600, default 300)"}}, "required": ["command"]},
    validator=_validate,
    run=_run,
)
