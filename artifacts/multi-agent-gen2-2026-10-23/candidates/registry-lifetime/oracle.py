#!/usr/bin/env python3
"""Independent behavior oracle; argv[1] is the candidate workspace."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading


def evaluate(workspace: Path) -> dict[str, object]:
    sys.path.insert(0, str(workspace))
    import minicode.background_tasks as background_tasks
    import minicode.tooling as tooling

    for module in (background_tasks, tooling):
        if not Path(module.__file__).resolve().is_relative_to(workspace):
            raise RuntimeError("Oracle imported source outside the candidate workspace")

    output: queue.Queue[str] = queue.Queue()
    disposed: list[str] = []
    failures: list[str] = []
    process: subprocess.Popen[str] | None = None
    reader: threading.Thread | None = None
    owner = tooling.ToolRegistry([], disposer=lambda: disposed.append("owner"))
    owner_closed = False
    evidence: dict[str, object] = {}

    try:
        reservation = background_tasks.reserve_background_task_slot()
        if reservation is None:
            raise RuntimeError("Fresh oracle process has no background slot")
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", (
                "import sys\n"
                "print('READY', flush=True)\n"
                "for line in sys.stdin:\n"
                "    print('ACK:' + line.strip(), flush=True)\n"
            )],
            cwd=workspace,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=os.name == "posix",
        )
        task = background_tasks.register_background_shell_task(
            "gen2 parent-owned protocol worker", process.pid, str(workspace),
            process=process, reservation_id=reservation,
            owns_process_group=os.name == "posix",
        )
        assert process.stdout is not None
        assert process.stdin is not None

        def read_output() -> None:
            assert process is not None and process.stdout is not None
            for line in process.stdout:
                output.put(line.strip())

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        if output.get(timeout=5) != "READY":
            raise RuntimeError("Background worker did not complete its startup handshake")

        def exchange(label: str) -> str:
            assert process is not None and process.stdin is not None
            process.stdin.write(label + "\n")
            process.stdin.flush()
            return output.get(timeout=5)

        if exchange("before-child-close") != "ACK:before-child-close":
            raise RuntimeError("Background worker failed the control exchange")

        child = owner.filtered(lambda _tool: True)
        nested = child.filtered(lambda _tool: True)
        child_errors: list[str] = []
        finished = threading.Event()

        def close_child_context() -> None:
            try:
                nested.dispose()
                child.dispose()
            except BaseException as error:
                child_errors.append(type(error).__name__ + ": " + str(error))
            finally:
                finished.set()

        closer = threading.Thread(target=close_child_context, daemon=True)
        closer.start()
        if not finished.wait(8):
            raise RuntimeError("Borrowed context disposal did not finish")
        closer.join(timeout=1)
        evidence["childCloseErrors"] = child_errors
        evidence["parentProcessAliveAfterChildClose"] = process.poll() is None
        evidence["ownerDisposerCallsAfterChildClose"] = len(disposed)
        evidence["backgroundAcceptingAfterChildClose"] = background_tasks.get_slot_stats()["accepting_tasks"]
        evidence["parentReplyAfterChildClose"] = None
        if process.poll() is None:
            try:
                evidence["parentReplyAfterChildClose"] = exchange("after-child-close")
            except (BrokenPipeError, OSError, queue.Empty) as error:
                evidence["parentReplyError"] = type(error).__name__

        further_reservation = background_tasks.reserve_background_task_slot()
        evidence["canReserveAfterChildClose"] = further_reservation is not None
        background_tasks.release_background_task_slot(further_reservation)
        owner.dispose()
        owner_closed = True
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            failures.append("owner disposal did not stop its background process")
        record = background_tasks.get_background_task(task.taskId)
        evidence["parentProcessStoppedAfterOwnerClose"] = process.poll() is not None
        evidence["ownerDisposerCallsAfterOwnerClose"] = len(disposed)
        evidence["terminalStatus"] = record["status"] if record else None
        evidence["terminationReason"] = record.get("terminationReason") if record else None
        evidence["backgroundAcceptingAfterOwnerClose"] = background_tasks.get_slot_stats()["accepting_tasks"]

        expectations = {
            "childCloseErrors": [],
            "parentProcessAliveAfterChildClose": True,
            "ownerDisposerCallsAfterChildClose": 0,
            "backgroundAcceptingAfterChildClose": True,
            "parentReplyAfterChildClose": "ACK:after-child-close",
            "canReserveAfterChildClose": True,
            "parentProcessStoppedAfterOwnerClose": True,
            "ownerDisposerCallsAfterOwnerClose": 1,
            "terminalStatus": "cancelled",
            "terminationReason": "application_shutdown",
            "backgroundAcceptingAfterOwnerClose": False,
        }
        for key, expected in expectations.items():
            if evidence.get(key) != expected:
                failures.append(f"{key}: expected {expected!r}, observed {evidence.get(key)!r}")
        return {"passed": not failures, "failures": failures, "evidence": evidence}
    finally:
        try:
            if not owner_closed:
                owner.dispose()
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                if process.stdin is not None:
                    process.stdin.close()
                if reader is not None:
                    reader.join(timeout=2)
                if process.stdout is not None:
                    process.stdout.close()


def main() -> int:
    workspace = Path(sys.argv[1]).resolve()
    try:
        result = evaluate(workspace)
    except Exception as error:
        print(json.dumps({"passed": False, "oracleError": type(error).__name__ + ": " + str(error)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
