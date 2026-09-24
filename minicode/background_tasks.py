from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Callable

from minicode.subagent_supervisor import _kill_process_tree
from minicode.tooling import BackgroundTaskResult

# Every registry transition, reservation, and callback claim is protected by
# one re-entrant lock.  Process termination happens outside it so slow children
# cannot block task listing or race another terminal-state transition.
_state_lock = threading.RLock()
_background_tasks: dict[str, dict[str, Any]] = {}
_slot_callbacks: dict[str, Callable[[str, dict[str, Any]], object]] = {}
_slot_reservations: set[str] = set()
_max_slots = 5
_accepting_tasks = True

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_INTERNAL_RECORD_KEYS = frozenset({"_process", "_callback_fired", "_owns_process_group"})


def _now_ms() -> int:
    return int(time.time() * 1000)


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in _INTERNAL_RECORD_KEYS
    }


def _is_process_alive(pid: int) -> bool | None:
    """Return whether ``pid`` is alive, or ``None`` when it is unknowable."""
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            process_query_limited_information = 0x1000
            still_active = 259
            handle = kernel32.OpenProcess(
                process_query_limited_information,
                False,
                pid,
            )
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == still_active
                return None
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001 - liveness observation is best effort
            return None

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def _finish_record_locked(
    record: dict[str, Any],
    *,
    status: str,
    exit_code: int | None,
    termination_reason: str | None = None,
) -> None:
    if record.get("status") in _TERMINAL_STATUSES:
        return
    record["status"] = status
    record["completedAt"] = _now_ms()
    record["exitCode"] = exit_code
    if termination_reason is not None:
        record["terminationReason"] = termination_reason


def _process_group_alive(pid: int) -> bool:
    if os.name != "posix":
        return False
    try:
        os.killpg(pid, 0)
        return True
    except OSError:
        return False


def _refresh_record_locked(record: dict[str, Any]) -> None:
    if record.get("status") not in {"running", "cancelling"}:
        return

    process = record.get("_process")
    if process is not None:
        try:
            return_code = process.poll()
        except OSError:
            return_code = None
        if isinstance(return_code, int):
            pid = record.get("pid")
            owns_group = record.get("_owns_process_group") is True
            if (
                isinstance(pid, int)
                and owns_group
                and _process_group_alive(pid)
            ):
                # The group leader exited after launching descendants. Keep the
                # slot live until explicit cancellation/shutdown reaps the group.
                return
            status = "completed" if return_code == 0 else "failed"
            _finish_record_locked(
                record,
                status=status,
                exit_code=return_code,
            )
        return

    pid = record.get("pid")
    if not isinstance(pid, int):
        _finish_record_locked(record, status="failed", exit_code=None)
        return
    alive = _is_process_alive(pid)
    if alive is False:
        _finish_record_locked(record, status="completed", exit_code=None)
    elif alive is None:
        _finish_record_locked(record, status="failed", exit_code=None)


def _claim_callback_locked(
    task_id: str,
    record: dict[str, Any],
) -> tuple[Callable[[str, dict[str, Any]], object], str, dict[str, Any]] | None:
    if (
        record.get("status") not in _TERMINAL_STATUSES
        or record.get("_callback_fired") is True
    ):
        return None
    callback = _slot_callbacks.pop(task_id, None)
    if callback is None:
        return None
    record["_callback_fired"] = True
    return callback, task_id, _public_record(record)


def _run_callbacks(
    callbacks: list[
        tuple[Callable[[str, dict[str, Any]], object], str, dict[str, Any]]
    ],
) -> None:
    for callback, task_id, record in callbacks:
        try:
            callback(task_id, record)
        except Exception:
            pass


def reserve_background_task_slot() -> str | None:
    """Atomically reserve one spawn slot, or return ``None`` if unavailable."""
    with _state_lock:
        if not _accepting_tasks:
            return None
        for record in _background_tasks.values():
            _refresh_record_locked(record)
        running = sum(
            1
            for record in _background_tasks.values()
            if record.get("status") in {"running", "cancelling"}
        )
        if running + len(_slot_reservations) >= _max_slots:
            return None
        reservation_id = f"reservation_{uuid.uuid4().hex}"
        _slot_reservations.add(reservation_id)
        return reservation_id


def release_background_task_slot(reservation_id: str | None) -> None:
    """Roll back a spawn reservation; repeated calls are harmless."""
    if reservation_id is None:
        return
    with _state_lock:
        _slot_reservations.discard(reservation_id)


def register_background_shell_task(
    command: str,
    pid: int,
    cwd: str,
    *,
    process: subprocess.Popen | None = None,
    reservation_id: str | None = None,
    owns_process_group: bool = True,
) -> BackgroundTaskResult:
    """Consume a reserved slot and register a spawned background process."""
    if not isinstance(pid, int) or pid <= 0:
        raise ValueError("background task pid must be a positive integer")

    with _state_lock:
        if not _accepting_tasks:
            if reservation_id is not None:
                _slot_reservations.discard(reservation_id)
            raise RuntimeError("background task registry is shutting down")
        if reservation_id is not None:
            if reservation_id not in _slot_reservations:
                raise RuntimeError("background task slot reservation is invalid")
            _slot_reservations.remove(reservation_id)
        else:
            for record in _background_tasks.values():
                _refresh_record_locked(record)
            running = sum(
                1
                for record in _background_tasks.values()
                if record.get("status") in {"running", "cancelling"}
            )
            if running + len(_slot_reservations) >= _max_slots:
                raise RuntimeError("no background task slots are available")

        result = BackgroundTaskResult(
            taskId=f"task_{uuid.uuid4().hex[:8]}",
            type="local_bash",
            command=command,
            pid=pid,
            status="running",
            startedAt=_now_ms(),
        )
        _background_tasks[result.taskId] = {
            "taskId": result.taskId,
            "type": result.type,
            "command": result.command,
            "pid": result.pid,
            "cwd": cwd,
            "status": result.status,
            "startedAt": result.startedAt,
            "label": command[:60],
            "processGroupId": pid if os.name == "posix" and owns_process_group else None,
            "ownsProcessGroup": bool(owns_process_group),
            "terminationRequestedAt": None,
            "terminationReason": None,
            "completedAt": None,
            "exitCode": None,
            "_process": process,
            "_callback_fired": False,
            "_owns_process_group": bool(owns_process_group),
        }
        return result


def list_background_tasks() -> list[dict[str, Any]]:
    """Return detached snapshots of all tracked tasks with current statuses."""
    callbacks = []
    with _state_lock:
        for task_id, record in _background_tasks.items():
            _refresh_record_locked(record)
            callback = _claim_callback_locked(task_id, record)
            if callback is not None:
                callbacks.append(callback)
        records = [_public_record(record) for record in _background_tasks.values()]
    _run_callbacks(callbacks)
    return records


def get_background_task(task_id: str) -> dict[str, Any] | None:
    """Return a detached snapshot of one task, if it exists."""
    callbacks = []
    with _state_lock:
        record = _background_tasks.get(task_id)
        if record is None:
            return None
        _refresh_record_locked(record)
        callback = _claim_callback_locked(task_id, record)
        if callback is not None:
            callbacks.append(callback)
        public = _public_record(record)
    _run_callbacks(callbacks)
    return public


def get_slot_stats() -> dict[str, Any]:
    """Return slot counts including in-flight spawn reservations."""
    with _state_lock:
        for record in _background_tasks.values():
            _refresh_record_locked(record)
        running = sum(
            1
            for record in _background_tasks.values()
            if record.get("status") in {"running", "cancelling"}
        )
        used = running + len(_slot_reservations)
        return {
            "used_slots": used,
            "max_slots": _max_slots,
            "available_slots": max(0, _max_slots - used),
            "total_tracked": len(_background_tasks),
            "reserved_slots": len(_slot_reservations),
            "accepting_tasks": _accepting_tasks,
        }


def can_start_new_task() -> bool:
    """Observe capacity; callers that spawn must use the reservation API."""
    return get_slot_stats()["available_slots"] > 0 and _accepting_tasks


def set_max_slots(max_slots: int) -> None:
    """Set the process-wide background task limit (minimum one)."""
    global _max_slots
    with _state_lock:
        _max_slots = max(1, int(max_slots))


def register_completion_callback(
    task_id: str,
    callback: Callable[[str, dict[str, Any]], object],
) -> None:
    """Register a callback, invoking it once if the task is already terminal."""
    if not callable(callback):
        raise TypeError("background task callback must be callable")
    callbacks = []
    with _state_lock:
        record = _background_tasks.get(task_id)
        if record is None:
            raise KeyError(task_id)
        _refresh_record_locked(record)
        if record.get("_callback_fired") is True:
            return
        _slot_callbacks[task_id] = callback
        claimed = _claim_callback_locked(task_id, record)
        if claimed is not None:
            callbacks.append(claimed)
    _run_callbacks(callbacks)


def check_completed_tasks() -> list[str]:
    """Refresh all tasks and fire each completion callback at most once."""
    completed: list[str] = []
    callbacks = []
    with _state_lock:
        for task_id, record in _background_tasks.items():
            was_terminal = record.get("status") in _TERMINAL_STATUSES
            _refresh_record_locked(record)
            if not was_terminal and record.get("status") in _TERMINAL_STATUSES:
                completed.append(task_id)
            callback = _claim_callback_locked(task_id, record)
            if callback is not None:
                callbacks.append(callback)
    _run_callbacks(callbacks)
    return completed


def _terminate_pid_only(pid: int, *, grace_seconds: float) -> None:
    """Best-effort compatibility cleanup for callers that did not retain Popen."""
    if os.name == "posix":
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            return
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while time.monotonic() < deadline:
            try:
                os.killpg(pid, 0)
            except OSError:
                return
            time.sleep(min(0.02, max(0.001, deadline - time.monotonic())))
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass
        return

    # A retained Popen handle is required for the safe Windows path. Avoid
    # applying POSIX group semantics to a bare Windows PID.


def cancel_background_task(
    task_id: str,
    *,
    reason: str = "cancelled",
    grace_seconds: float = 1.0,
) -> bool:
    """Cancel one task and its POSIX process group; repeated calls are harmless."""
    with _state_lock:
        record = _background_tasks.get(task_id)
        if record is None:
            return False
        _refresh_record_locked(record)
        if record.get("status") in _TERMINAL_STATUSES:
            return True
        record["status"] = "cancelling"
        if record.get("terminationRequestedAt") is None:
            record["terminationRequestedAt"] = _now_ms()
        if record.get("terminationReason") is None:
            record["terminationReason"] = reason
        process = record.get("_process")
        pid = record.get("pid")
        owns_group = record.get("_owns_process_group") is True

    if process is not None:
        _kill_process_tree(process, grace_seconds=grace_seconds)
    elif isinstance(pid, int) and owns_group:
        _terminate_pid_only(pid, grace_seconds=grace_seconds)

    callbacks = []
    with _state_lock:
        record = _background_tasks.get(task_id)
        if record is None:
            return True
        exit_code = None
        if process is not None:
            try:
                polled = process.poll()
                exit_code = polled if isinstance(polled, int) else None
            except OSError:
                pass
        _finish_record_locked(
            record,
            status="cancelled",
            exit_code=exit_code,
            termination_reason=reason,
        )
        callback = _claim_callback_locked(task_id, record)
        if callback is not None:
            callbacks.append(callback)
    _run_callbacks(callbacks)
    return True


def shutdown_background_tasks(
    *,
    reason: str = "application_shutdown",
    grace_seconds: float = 1.0,
) -> list[str]:
    """Stop accepting work and idempotently terminate every live task."""
    global _accepting_tasks
    with _state_lock:
        _accepting_tasks = False
        _slot_reservations.clear()
        task_ids = [
            task_id
            for task_id, record in _background_tasks.items()
            if record.get("status") not in _TERMINAL_STATUSES
        ]
    for task_id in task_ids:
        cancel_background_task(
            task_id,
            reason=reason,
            grace_seconds=grace_seconds,
        )
    return task_ids


def format_slot_status() -> str:
    """Format slot state for display."""
    stats = get_slot_stats()
    running_tasks = [
        record
        for record in list_background_tasks()
        if record.get("status") in {"running", "cancelling"}
    ]
    lines = [
        "Background Task Slots",
        "=" * 50,
        f"Slots: {stats['used_slots']}/{stats['max_slots']} used",
        f"Available: {stats['available_slots']}",
        f"Total tracked: {stats['total_tracked']}",
        "",
    ]
    if running_tasks:
        lines.append("Running Tasks:")
        for task in running_tasks:
            lines.append(
                f"  • [{task.get('taskId', '?')}] "
                f"{task.get('label', task.get('command', 'unknown'))}"
            )
        lines.append("")
    return "\n".join(lines)


def _reset_background_tasks_for_tests() -> None:
    """Restore the process-local registry after isolated unit tests."""
    global _accepting_tasks, _max_slots
    shutdown_background_tasks(reason="test_reset", grace_seconds=0.01)
    with _state_lock:
        _background_tasks.clear()
        _slot_callbacks.clear()
        _slot_reservations.clear()
        _max_slots = 5
        _accepting_tasks = True


__all__ = [
    "can_start_new_task",
    "cancel_background_task",
    "check_completed_tasks",
    "format_slot_status",
    "get_background_task",
    "get_slot_stats",
    "list_background_tasks",
    "register_background_shell_task",
    "register_completion_callback",
    "release_background_task_slot",
    "reserve_background_task_slot",
    "set_max_slots",
    "shutdown_background_tasks",
]
