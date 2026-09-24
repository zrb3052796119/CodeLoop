from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

import minicode.tools.run_command as run_command_module
from minicode.auto_mode import PermissionMode
from minicode.permissions import PermissionManager
from minicode.tooling import ToolContext, ToolTerminationReason
from minicode.tools.run_command import run_command_tool
from minicode.turn_cancellation import TurnCancellationToken


def _run(input_data: dict, context: ToolContext):
    return run_command_tool.run(run_command_tool.validator(input_data), context)


def _deny_permissions(workspace: Path) -> tuple[PermissionManager, list[dict]]:
    prompts: list[dict] = []
    manager = PermissionManager(
        str(workspace),
        prompt=lambda request: prompts.append(request) or {"decision": "deny_once"},
    )
    return manager, prompts


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("find", [".", "-delete"]),
        ("sed", ["-i", "s/a/b/", "sample.txt"]),
        ("python", ["-c", "print('executed')"]),
    ],
)
def test_risky_argv_requires_permission_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    args: list[str],
) -> None:
    manager, prompts = _deny_permissions(tmp_path)
    monkeypatch.setattr(
        run_command_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("command spawned before approval"),
    )

    with pytest.raises(RuntimeError, match="Command denied"):
        _run(
            {"command": command, "args": args},
            ToolContext(cwd=str(tmp_path), permissions=manager),
        )

    assert len(prompts) == 1


def test_read_only_program_outside_path_requires_permission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, prompts = _deny_permissions(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    monkeypatch.setattr(
        run_command_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("outside read spawned before approval"),
    )

    with pytest.raises(RuntimeError, match="Command denied"):
        _run(
            {"command": "cat", "args": [str(outside)]},
            ToolContext(cwd=str(tmp_path), permissions=manager),
        )

    assert "outside the workspace" in "\n".join(prompts[0]["details"])


def test_interpreter_force_prompt_is_not_auto_approved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[dict] = []
    manager = PermissionManager(
        str(tmp_path),
        prompt=lambda request: prompts.append(request) or {"decision": "deny_once"},
        auto_mode=PermissionMode.AUTO,
    )
    monkeypatch.setattr(
        run_command_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("interpreter spawned before review"),
    )

    with pytest.raises(RuntimeError, match="Command denied"):
        _run(
            {"command": "python", "args": ["-c", "print('executed')"]},
            ToolContext(cwd=str(tmp_path), permissions=manager),
        )

    assert len(prompts) == 1


def test_task_deadline_shortens_command_timeout(
    tmp_path: Path,
) -> None:
    context = ToolContext(
        cwd=str(tmp_path),
        permissions=None,
        _task_deadline_monotonic=time.monotonic() + 0.15,
        _task_timeout_seconds=0.15,
    )
    started = time.monotonic()

    result = _run(
        {
            "command": sys.executable,
            "args": ["-c", "import time; time.sleep(30)"],
            "timeout": 30,
        },
        context,
    )

    assert not result.ok
    assert result._termination_reason is ToolTerminationReason.DEADLINE_EXCEEDED
    assert "task_deadline_exceeded" in result.output
    assert time.monotonic() - started < 2


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group assertion")
def test_pty_timeout_uses_absolute_deadline_and_kills_descendants(
    tmp_path: Path,
) -> None:
    child_pid_file = tmp_path / "child.pid"
    script = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid)); "
        "[(print(i, flush=True), time.sleep(.05)) for i in range(200)]"
    )
    started = time.monotonic()

    result = _run(
        {"command": sys.executable, "args": ["-c", script], "timeout": 1},
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert not result.ok
    assert result._termination_reason is ToolTerminationReason.DEADLINE_EXCEEDED
    assert time.monotonic() - started < 3
    child_pid = int(child_pid_file.read_text())
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("command descendant survived process-group timeout")


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group assertion")
def test_turn_cancellation_kills_command_process_group(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    script = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid)); "
        "time.sleep(60)"
    )
    token = TurnCancellationToken("turn_" + "a" * 32)
    context = ToolContext(
        cwd=str(tmp_path),
        permissions=None,
        _cancellation_token=token,
    )
    outcome: list[object] = []
    thread = threading.Thread(
        target=lambda: outcome.append(
            _run(
                {"command": sys.executable, "args": ["-c", script], "timeout": 30},
                context,
            )
        )
    )
    thread.start()
    deadline = time.monotonic() + 2
    while not child_pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert child_pid_file.exists()

    token.request()
    thread.join(timeout=3)

    assert not thread.is_alive()
    result = outcome[0]
    assert result._termination_reason is ToolTerminationReason.TURN_CANCELLED
    child_pid = int(child_pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_fallback_uses_process_group_and_windows_safe_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class _Stream:
        closed = False

        def read(self, _size: int) -> bytes:
            return b""

        def close(self) -> None:
            self.closed = True

    class _Process:
        pid = 42
        returncode = 0
        stdout = _Stream()
        stderr = _Stream()

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def terminate(self):
            observed["terminated"] = True

        def kill(self):
            observed["killed"] = True

    def fake_popen(*_args, **kwargs):
        observed.update(kwargs)
        return _Process()

    monkeypatch.setattr(run_command_module.sys, "platform", "win32")
    monkeypatch.setattr(run_command_module.os, "name", "nt")
    monkeypatch.setattr(run_command_module.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    monkeypatch.setattr(run_command_module.subprocess, "Popen", fake_popen)

    result = _run(
        {"command": "echo", "args": []},
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok
    assert observed["creationflags"] == 512
    assert "start_new_session" not in observed


def test_pty_popen_failure_closes_both_file_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform == "win32":
        pytest.skip("PTY unavailable")
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr("pty.openpty", lambda: (read_fd, write_fd))
    monkeypatch.setattr(
        run_command_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )

    with pytest.raises(OSError, match="spawn failed"):
        _run(
            {"command": "echo", "args": []},
            ToolContext(cwd=str(tmp_path), permissions=None),
        )

    for fd in (read_fd, write_fd):
        with pytest.raises(OSError):
            os.fstat(fd)
