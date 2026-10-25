from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

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
    host_os_name = os.name
    host_platform = sys.platform
    host_subprocess = run_command_module.subprocess
    host_popen = host_subprocess.Popen

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

    # Keep the simulated platform local: changing shared os.name also changes
    # pathlib's concrete path selection and breaks pytest reporting on 3.11.
    monkeypatch.setattr(run_command_module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        run_command_module, "os", SimpleNamespace(name="nt", environ=os.environ)
    )
    monkeypatch.setattr(
        run_command_module,
        "subprocess",
        SimpleNamespace(
            **{
                **vars(host_subprocess),
                "CREATE_NEW_PROCESS_GROUP": 512,
                "Popen": fake_popen,
            }
        ),
    )

    result = _run(
        {"command": "echo", "args": []},
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok
    assert observed["creationflags"] == 512
    assert "start_new_session" not in observed
    assert os.name == host_os_name
    assert sys.platform == host_platform
    assert host_subprocess.Popen is host_popen


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


@pytest.fixture
def completed_pty_child(monkeypatch: pytest.MonkeyPatch):
    """Complete a real gated child at either side of the first read attempt."""
    if os.name != "posix":
        pytest.skip("POSIX PTY ordering")
    real_subprocess = run_command_module.subprocess
    gate_read, gate_write = os.pipe()
    children = []
    observed = {}

    def configure(*, finish_on_poll: bool = False):
        def popen(argv, **kwargs):
            child = real_subprocess.Popen(argv, **kwargs, pass_fds=(gate_read,))
            children.append(child)
            observed["slave_fd"] = kwargs["stdout"]

            def complete():
                if "exit_code" not in observed:
                    os.write(gate_write, b"x")
                    observed["exit_code"] = child.wait(timeout=5)

            if not finish_on_poll:
                complete()
                return child

            class Process:
                def __getattr__(self, name):
                    return getattr(child, name)

                def poll(self):
                    # The original loop reaches poll only after a real empty
                    # select. No synthetic readiness or process status is used.
                    complete()
                    return child.poll()

            return Process()

        monkeypatch.setattr(
            run_command_module,
            "subprocess",
            SimpleNamespace(**{**vars(real_subprocess), "Popen": popen}),
        )
        script = (
            f"import os; os.read({gate_read}, 1); "
            "os.write(1, b'completed_pty_output\\n')"
        )
        return {"command": sys.executable, "args": ["-c", script]}, observed

    try:
        yield configure
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        os.close(gate_read)
        os.close(gate_write)


@pytest.mark.parametrize("finish_on_poll", [False, True])
def test_pty_preserves_output_written_before_child_exit(
    tmp_path: Path,
    completed_pty_child,
    finish_on_poll: bool,
) -> None:
    command, observed = completed_pty_child(finish_on_poll=finish_on_poll)

    result = _run(command, ToolContext(cwd=str(tmp_path), permissions=None))

    assert observed["exit_code"] == 0
    assert result.ok
    assert result.output == "completed_pty_output"
    with pytest.raises(OSError):
        os.fstat(observed["slave_fd"])


@pytest.mark.parametrize("control", ["cancel", "deadline"])
def test_pty_final_drain_honors_execution_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed_pty_child,
    control: str,
) -> None:
    command, observed = completed_pty_child()
    token = TurnCancellationToken("turn_" + "b" * 32)
    context = ToolContext(
        cwd=str(tmp_path), permissions=None, _cancellation_token=token
    )
    real_cleanup = run_command_module._kill_process_tree
    cleanups = []

    def cleanup(process, *, grace_seconds):
        real_cleanup(process, grace_seconds=grace_seconds)
        cleanups.append(process.pid)
        if control == "cancel":
            token.request()
        else:
            context._task_deadline_monotonic = time.monotonic() - 1

    monkeypatch.setattr(run_command_module, "_kill_process_tree", cleanup)

    result = _run(command, context)

    assert observed["exit_code"] == 0
    assert len(cleanups) == 1
    assert not result.ok
    assert result._termination_reason is (
        ToolTerminationReason.TURN_CANCELLED
        if control == "cancel"
        else ToolTerminationReason.DEADLINE_EXCEEDED
    )
    with pytest.raises(OSError):
        os.fstat(observed["slave_fd"])


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group assertion")
def test_pty_normal_exit_cleans_descendant_holding_slave(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_subprocess = run_command_module.subprocess
    gate_read, gate_write = os.pipe()
    alive_read, alive_write = os.pipe()
    children = []

    def popen(argv, **kwargs):
        child = real_subprocess.Popen(
            argv, **kwargs, pass_fds=(gate_read, alive_write)
        )
        children.append(child)
        os.close(alive_write)
        return child

    monkeypatch.setattr(
        run_command_module,
        "subprocess",
        SimpleNamespace(**{**vars(real_subprocess), "Popen": popen}),
    )
    descendant = f"import os; os.read({gate_read}, 1)"
    script = (
        "import os,subprocess,sys; "
        f"subprocess.Popen([sys.executable,'-c',{descendant!r}], "
        f"pass_fds=({gate_read},{alive_write})); "
        "os.write(1,b'parent_done\\n')"
    )
    try:
        result = _run(
            {"command": sys.executable, "args": ["-c", script], "timeout": 5},
            ToolContext(cwd=str(tmp_path), permissions=None),
        )
        import select

        assert result.ok
        assert result.output == "parent_done"
        # Only the blocked descendant owns this write end after its parent
        # exits. EOF proves cleanup; timeout is a watchdog, not a speed claim.
        assert select.select([alive_read], [], [], 1)[0]
        assert os.read(alive_read, 1) == b""
    finally:
        for child in children:
            run_command_module._kill_process_tree(child, grace_seconds=0.2)
        for fd in (gate_read, gate_write, alive_read):
            os.close(fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX PTY output bound")
def test_pty_capture_keeps_bounded_head_and_tail(tmp_path: Path) -> None:
    script = "import os; os.write(1, b'BEGIN' + b'x' * 250000 + b'END')"

    result = _run(
        {"command": sys.executable, "args": ["-c", script]},
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok
    assert result.output.startswith("BEGIN")
    assert result.output.endswith("END")
    assert "output truncated" in result.output
    assert len(result.output) < run_command_module.MAX_OUTPUT_CHARS + 1000
