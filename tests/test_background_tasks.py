from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import minicode.background_tasks as background_tasks
import minicode.tools.run_command as run_command_module
from minicode.tooling import ToolContext
from minicode.tools.run_command import run_command_tool


@pytest.fixture(autouse=True)
def _isolated_registry():
    background_tasks._reset_background_tasks_for_tests()
    yield
    background_tasks._reset_background_tasks_for_tests()


def test_slot_reservations_are_atomic_under_concurrency() -> None:
    background_tasks.set_max_slots(1)
    barrier = threading.Barrier(8)
    reservations: list[str | None] = []
    lock = threading.Lock()

    def reserve() -> None:
        barrier.wait()
        reservation = background_tasks.reserve_background_task_slot()
        with lock:
            reservations.append(reservation)

    threads = [threading.Thread(target=reserve) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(reservation is not None for reservation in reservations) == 1
    assert background_tasks.get_slot_stats()["used_slots"] == 1


def test_background_spawn_failure_releases_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    background_tasks.set_max_slots(1)
    monkeypatch.setattr(
        run_command_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )

    with pytest.raises(OSError, match="spawn failed"):
        run_command_tool.run(
            run_command_tool.validator({"command": "echo hi &"}),
            ToolContext(cwd=str(tmp_path), permissions=None),
        )

    assert background_tasks.get_slot_stats()["available_slots"] == 1
    assert background_tasks.get_slot_stats()["reserved_slots"] == 0


def test_record_keeps_cwd_exit_code_and_callback_exactly_once(tmp_path: Path) -> None:
    reservation = background_tasks.reserve_background_task_slot()

    class _Process:
        pid = 123
        returncode = 0

        def poll(self):
            return self.returncode

    task = background_tasks.register_background_shell_task(
        "echo hi",
        123,
        str(tmp_path),
        process=_Process(),
        reservation_id=reservation,
        owns_process_group=False,
    )
    callbacks: list[tuple[str, dict]] = []
    background_tasks.register_completion_callback(
        task.taskId,
        lambda task_id, record: callbacks.append((task_id, record)),
    )

    background_tasks.check_completed_tasks()
    background_tasks.check_completed_tasks()
    record = background_tasks.get_background_task(task.taskId)

    assert record is not None
    assert record["cwd"] == str(tmp_path)
    assert record["status"] == "completed"
    assert record["exitCode"] == 0
    assert isinstance(record["completedAt"], int)
    assert callbacks == [(task.taskId, record)]


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group assertion")
def test_cancel_is_idempotent_and_kills_background_descendants(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    script = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid)); "
        "time.sleep(60)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    reservation = background_tasks.reserve_background_task_slot()
    task = background_tasks.register_background_shell_task(
        "python child",
        process.pid,
        str(tmp_path),
        process=process,
        reservation_id=reservation,
    )
    deadline = time.monotonic() + 2
    while not child_pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert child_pid_file.exists()

    assert background_tasks.cancel_background_task(task.taskId, reason="user")
    assert background_tasks.cancel_background_task(task.taskId, reason="later")

    record = background_tasks.get_background_task(task.taskId)
    assert record is not None
    assert record["status"] == "cancelled"
    assert record["terminationReason"] == "user"
    assert isinstance(record["terminationRequestedAt"], int)
    assert process.poll() is not None
    with pytest.raises(ProcessLookupError):
        os.kill(int(child_pid_file.read_text()), 0)


def test_shutdown_is_idempotent_and_rejects_new_reservations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminated: list[int] = []

    class _Process:
        pid = 456
        returncode = None
        stdout = None
        stderr = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = -15
            return self.returncode

        def terminate(self):
            terminated.append(self.pid)
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    process = _Process()
    reservation = background_tasks.reserve_background_task_slot()
    task = background_tasks.register_background_shell_task(
        "long-running",
        process.pid,
        "/workspace",
        process=process,
        reservation_id=reservation,
    )
    monkeypatch.setattr(background_tasks, "_kill_process_tree", lambda proc, grace_seconds: proc.terminate())

    assert background_tasks.shutdown_background_tasks() == [task.taskId]
    assert background_tasks.shutdown_background_tasks() == []
    assert terminated == [process.pid]
    assert background_tasks.reserve_background_task_slot() is None
    assert not background_tasks.get_slot_stats()["accepting_tasks"]
