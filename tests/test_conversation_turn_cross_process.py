from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from minicode.conversation import ConversationTurnService
from minicode.conversation_turn_store import (
    ConversationTurnStore,
    TurnStoreError,
    request_fingerprint,
)

TURN_ID = "turn_" + "d" * 32
MESSAGE = "one cross-process request"
OWNER_A = "1" * 32
OWNER_B = "2" * 32
PROJECT_ROOT = Path(__file__).resolve().parents[1]


_LIVE_OWNER_SCRIPT = r"""
import json
import os
import sys
from pathlib import Path

from minicode.conversation_turn_store import ConversationTurnStore, request_fingerprint

workspace = Path(sys.argv[1]).resolve()
data_dir = Path(sys.argv[2]).resolve()
turn_id = sys.argv[3]
message = sys.argv[4]
owner_id = sys.argv[5]
store = ConversationTurnStore(workspace, data_dir=data_dir, owner_id=owner_id)
fingerprint = request_fingerprint(
    workspace_id=store.workspace_id,
    session_id=None,
    message=message,
)
claim = store.claim(turn_id=turn_id, fingerprint=fingerprint)
if claim.disposition != "claimed":
    raise RuntimeError(f"unexpected claim: {claim.disposition}")
started = store.mark_running(turn_id)
if not started.execution_started:
    raise RuntimeError("owner did not start")
print(json.dumps({"status": started.record.status, "pid": os.getpid()}), flush=True)
for raw in sys.stdin:
    command = raw.strip()
    if command == "snapshot":
        record = store.get(turn_id)
        print(
            json.dumps(
                {
                    "status": None if record is None else record.status,
                    "ownerId": None if record is None else record.owner_id,
                }
            ),
            flush=True,
        )
    elif command == "stop":
        store.release_claim(turn_id)
        print(json.dumps({"stopped": True}), flush=True)
        break
    elif command == "crash":
        os._exit(23)
    else:
        raise RuntimeError(f"unexpected command: {command}")
"""


_SIMULTANEOUS_CLAIM_SCRIPT = r"""
import json
import sys
from pathlib import Path

from minicode.conversation_turn_store import ConversationTurnStore, request_fingerprint

workspace = Path(sys.argv[1]).resolve()
data_dir = Path(sys.argv[2]).resolve()
turn_id = sys.argv[3]
message = sys.argv[4]
owner_id = sys.argv[5]
store = ConversationTurnStore(workspace, data_dir=data_dir, owner_id=owner_id)
fingerprint = request_fingerprint(
    workspace_id=store.workspace_id,
    session_id=None,
    message=message,
)
print(json.dumps({"ready": True}), flush=True)
if sys.stdin.readline().strip() != "claim":
    raise RuntimeError("claim barrier was not released")
claim = store.claim(turn_id=turn_id, fingerprint=fingerprint)
print(json.dumps({"disposition": claim.disposition}), flush=True)
if claim.disposition == "claimed":
    if sys.stdin.readline().strip() != "release":
        raise RuntimeError("lease barrier was not released")
    store.release_claim(turn_id)
"""


_PAUSED_CANCEL_SCRIPT = r"""
import json
import sys
from pathlib import Path

from minicode.conversation_turn_store import ConversationTurnStore

workspace = Path(sys.argv[1]).resolve()
data_dir = Path(sys.argv[2]).resolve()
turn_id = sys.argv[3]
owner_id = sys.argv[4]
store = ConversationTurnStore(workspace, data_dir=data_dir, owner_id=owner_id)
real_read = store._read
paused = False

def read_then_pause(candidate_turn_id):
    global paused
    record = real_read(candidate_turn_id)
    if not paused:
        paused = True
        print(json.dumps({"pausedAfterRead": record.status}), flush=True)
        if sys.stdin.readline().strip() != "continue":
            raise RuntimeError("cancel barrier was not released")
    return record

store._read = read_then_pause
decision = store.request_cancel(turn_id)
print(
    json.dumps(
        {
            "accepted": decision.cancellation_accepted,
            "status": decision.record.status,
        }
    ),
    flush=True,
)
"""


_FORKED_OWNER_SCRIPT = r"""
import json
import os
import sys
import time
from pathlib import Path

from minicode.conversation_turn_store import ConversationTurnStore, request_fingerprint

workspace = Path(sys.argv[1]).resolve()
data_dir = Path(sys.argv[2]).resolve()
turn_id = sys.argv[3]
message = sys.argv[4]
owner_id = sys.argv[5]
child_ready = Path(sys.argv[6])
child_stop = Path(sys.argv[7])
store = ConversationTurnStore(workspace, data_dir=data_dir, owner_id=owner_id)
fingerprint = request_fingerprint(
    workspace_id=store.workspace_id,
    session_id=None,
    message=message,
)
if store.claim(turn_id=turn_id, fingerprint=fingerprint).disposition != "claimed":
    raise RuntimeError("owner did not claim")
store.mark_running(turn_id)
child_pid = os.fork()
if child_pid == 0:
    child_ready.write_text(str(os.getpid()), encoding="utf-8")
    deadline = time.monotonic() + 10.0
    while not child_stop.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    os._exit(0)
while not child_ready.exists():
    time.sleep(0.01)
print(json.dumps({"childPid": child_pid}), flush=True)
os._exit(0)
"""


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "home" / ".mini-code"
    workspace.mkdir()
    return workspace, data_dir


def _fingerprint(store: ConversationTurnStore) -> str:
    return request_fingerprint(
        workspace_id=store.workspace_id,
        session_id=None,
        message=MESSAGE,
    )


def _spawn(
    script: str,
    *arguments: object,
    stdin: bool = True,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", script, *(str(argument) for argument in arguments)],
        cwd=PROJECT_ROOT,
        text=True,
        stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _read_json(process: subprocess.Popen[str]) -> dict[str, object]:
    assert process.stdout is not None
    lines: queue.Queue[str] = queue.Queue(maxsize=1)
    reader = threading.Thread(
        target=lambda: lines.put(process.stdout.readline()),
        daemon=True,
    )
    reader.start()
    try:
        line = lines.get(timeout=5)
    except queue.Empty as error:
        raise AssertionError("child response timed out") from error
    if not line:
        stderr = ""
        if process.stderr is not None:
            stderr = process.stderr.read()
        raise AssertionError(f"child exited without a response: {stderr}")
    value = json.loads(line)
    assert isinstance(value, dict)
    return value


def _send(process: subprocess.Popen[str], command: str) -> dict[str, object]:
    assert process.stdin is not None
    process.stdin.write(command + "\n")
    process.stdin.flush()
    return _read_json(process)


def _stop_live_owner(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        try:
            _send(process, "stop")
        except (AssertionError, BrokenPipeError, OSError):
            process.kill()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)


def _live_owner(workspace: Path, data_dir: Path) -> subprocess.Popen[str]:
    process = _spawn(
        _LIVE_OWNER_SCRIPT,
        workspace,
        data_dir,
        TURN_ID,
        MESSAGE,
        OWNER_A,
    )
    ready = _read_json(process)
    assert ready["status"] == "running"
    return process


def test_foreign_process_cannot_claim_a_live_turn(tmp_path: Path) -> None:
    workspace, data_dir = _workspace(tmp_path)
    owner = _live_owner(workspace, data_dir)
    try:
        observer = ConversationTurnStore(
            workspace,
            data_dir=data_dir,
            owner_id=OWNER_B,
        )

        duplicate = observer.claim(
            turn_id=TURN_ID,
            fingerprint=_fingerprint(observer),
        )

        assert duplicate.disposition == "in_progress"
        assert duplicate.record.status == "running"
        assert duplicate.record.owner_id == OWNER_A
    finally:
        _stop_live_owner(owner)


def test_simultaneous_cross_process_claims_have_exactly_one_owner(
    tmp_path: Path,
) -> None:
    workspace, data_dir = _workspace(tmp_path)
    contenders = [
        _spawn(
            _SIMULTANEOUS_CLAIM_SCRIPT,
            workspace,
            data_dir,
            TURN_ID,
            MESSAGE,
            owner_id,
        )
        for owner_id in (OWNER_A, OWNER_B)
    ]
    try:
        assert [_read_json(process) for process in contenders] == [
            {"ready": True},
            {"ready": True},
        ]
        for process in contenders:
            assert process.stdin is not None
            process.stdin.write("claim\n")
            process.stdin.flush()
        results = [_read_json(process)["disposition"] for process in contenders]

        assert sorted(results) == ["claimed", "in_progress"]
        winner = contenders[results.index("claimed")]
        assert winner.stdin is not None
        winner.stdin.write("release\n")
        winner.stdin.flush()
    finally:
        for process in contenders:
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)


def test_status_does_not_interrupt_a_live_foreign_owner(tmp_path: Path) -> None:
    workspace, data_dir = _workspace(tmp_path)
    owner = _live_owner(workspace, data_dir)
    try:
        observer = ConversationTurnStore(
            workspace,
            data_dir=data_dir,
            owner_id=OWNER_B,
        )
        service = ConversationTurnService(
            workspace,
            observation_enabled=False,
            turn_store=observer,
        )

        status = service.status(TURN_ID)
        owner_snapshot = _send(owner, "snapshot")

        assert status.status == "running"
        assert status.error_code is None
        assert owner_snapshot == {"status": "running", "ownerId": OWNER_A}
    finally:
        _stop_live_owner(owner)


def test_cancel_read_modify_write_serializes_against_owner_commit(
    tmp_path: Path,
) -> None:
    workspace, data_dir = _workspace(tmp_path)
    owner = ConversationTurnStore(
        workspace,
        data_dir=data_dir,
        owner_id=OWNER_A,
    )
    assert owner.claim(
        turn_id=TURN_ID,
        fingerprint=_fingerprint(owner),
    ).disposition == "claimed"
    owner.mark_running(TURN_ID)
    owner.attach_session(
        TURN_ID,
        session_id="session_cross_process",
        created_session=True,
    )
    canceller = _spawn(
        _PAUSED_CANCEL_SCRIPT,
        workspace,
        data_dir,
        TURN_ID,
        OWNER_B,
    )
    commit_started = threading.Event()
    commit_finished = threading.Event()
    outcome: list[object] = []

    def begin_commit() -> None:
        commit_started.set()
        try:
            outcome.append(owner.begin_commit(TURN_ID))
        except TurnStoreError as error:  # pragma: no cover - assertion reports it
            outcome.append(error)
        finally:
            commit_finished.set()

    try:
        paused = _read_json(canceller)
        assert paused == {"pausedAfterRead": "running"}
        commit_thread = threading.Thread(target=begin_commit)
        commit_thread.start()
        assert commit_started.wait(timeout=2)
        # The canceller is holding the cross-process Turn-state transaction
        # after its read. Completion cannot publish from that stale snapshot.
        assert not commit_finished.wait(timeout=0.2)

        cancel_result = _send(canceller, "continue")
        assert cancel_result == {"accepted": True, "status": "cancel_requested"}
        commit_thread.join(timeout=5)
        assert not commit_thread.is_alive()
        assert len(outcome) == 1
        assert not isinstance(outcome[0], BaseException)
        assert outcome[0].commit_allowed is False
        assert outcome[0].record.status == "cancel_requested"

        terminal = owner.mark_cancelled(TURN_ID)
        assert terminal.status == "cancelled"
        assert terminal.error_code == "turn_cancelled"
    finally:
        if canceller.poll() is None:
            canceller.kill()
        canceller.communicate(timeout=5)
        owner.release_claim(TURN_ID)


def test_process_death_releases_lease_and_allows_stale_recovery(
    tmp_path: Path,
) -> None:
    workspace, data_dir = _workspace(tmp_path)
    owner = _live_owner(workspace, data_dir)
    assert owner.stdin is not None
    owner.stdin.write("crash\n")
    owner.stdin.flush()
    assert owner.wait(timeout=5) == 23

    observer = ConversationTurnStore(
        workspace,
        data_dir=data_dir,
        owner_id=OWNER_B,
    )
    stale = observer.claim(
        turn_id=TURN_ID,
        fingerprint=_fingerprint(observer),
    )
    assert stale.disposition == "recover"
    assert stale.record.status == "running"

    service = ConversationTurnService(
        workspace,
        observation_enabled=False,
        turn_store=observer,
    )
    recovered = service.status(TURN_ID)
    assert recovered.status == "interrupted"
    assert recovered.error_code == "turn_interrupted"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_forked_child_does_not_keep_dead_parent_lease_alive(
    tmp_path: Path,
) -> None:
    workspace, data_dir = _workspace(tmp_path)
    child_ready = tmp_path / "fork-child-ready"
    child_stop = tmp_path / "fork-child-stop"
    owner = _spawn(
        _FORKED_OWNER_SCRIPT,
        workspace,
        data_dir,
        TURN_ID,
        MESSAGE,
        OWNER_A,
        child_ready,
        child_stop,
        stdin=False,
    )
    forked_pid: int | None = None
    try:
        response = _read_json(owner)
        forked_pid = int(response["childPid"])
        assert owner.wait(timeout=5) == 0
        assert child_ready.exists()

        observer = ConversationTurnStore(
            workspace,
            data_dir=data_dir,
            owner_id=OWNER_B,
        )
        stale = observer.claim(
            turn_id=TURN_ID,
            fingerprint=_fingerprint(observer),
        )

        assert stale.disposition == "recover"
        assert stale.record.status == "running"
    finally:
        child_stop.touch()
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        if forked_pid is not None:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                try:
                    os.kill(forked_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            else:
                os.kill(forked_pid, signal.SIGKILL)
