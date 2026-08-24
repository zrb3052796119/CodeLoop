from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from minicode.web.change_feed import DashboardChangeFeed
from minicode.web.event_stream import DashboardEventStream


RESOURCE_NAMES = (
    "runs",
    "sessions",
    "turns",
    "memory",
    "skills",
    "connections",
    "permissions",
)


class _ManualSamplerWait:
    def __init__(self) -> None:
        self.waiting = threading.Event()
        self.advance_event = threading.Event()

    def __call__(self, stop: threading.Event, _seconds: float) -> bool:
        self.waiting.set()
        while not stop.is_set():
            if self.advance_event.wait(0.05):
                self.advance_event.clear()
                self.waiting.clear()
                return False
        return True

    def advance(self) -> None:
        assert self.waiting.wait(timeout=2)
        self.advance_event.set()

    def release(self) -> None:
        self.advance_event.set()


def _payload(frame: bytes) -> dict[str, object]:
    for line in frame.decode("utf-8").splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    raise AssertionError("missing SSE data")


def _changed(subscription) -> list[str]:
    frame = subscription.next_batch(timeout=2)[0]
    payload = _payload(frame)
    assert payload["type"] == "resources.changed"
    rendered = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "/Users/",
        "/private/",
        "cross-process-secret",
        "run_",
        "turn_",
        "session-",
        "diagnostics",
    ):
        assert forbidden not in rendered
    return [item["name"] for item in payload["resources"]]


def _command(process: subprocess.Popen[str], command: str) -> str:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(command + "\n")
    process.stdin.flush()
    response = process.stdout.readline().strip()
    assert response
    return response


def test_external_process_authorities_emit_only_targeted_invalidations(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    data_dir = home / ".mini-code"
    workspace.mkdir()
    home.mkdir()
    sampler_wait = _ManualSamplerWait()
    stream = DashboardEventStream(
        DashboardChangeFeed(workspace, data_dir=data_dir),
        sampler_wait=sampler_wait,
    )
    stream.start()
    assert sampler_wait.waiting.wait(timeout=2)
    subscription = stream.subscribe()
    subscription.next_batch(timeout=0)  # ready

    writer_script = r"""
import sys
from pathlib import Path
from minicode.conversation_turn_store import (
    ConversationTurnStore,
    create_turn_id,
    request_fingerprint,
)
from minicode.run_journal import RunJournal

workspace = Path(sys.argv[1]).resolve()
data_dir = Path(sys.argv[2]).resolve()
journal = RunJournal(workspace, data_dir=data_dir)
turns = ConversationTurnStore(workspace, data_dir=data_dir)
run_id = None
turn_id = None
print("ready", flush=True)
for raw in sys.stdin:
    command = raw.strip()
    if command == "run-create":
        run_id = journal.create_run(
            title="cross-process-secret run body", source="gateway"
        ).id
        print("ok", flush=True)
    elif command == "run-append":
        journal.transition(run_id, "running")
        journal.append_event(
            run_id,
            "assistant.completed",
            payload={"summary": "cross-process-secret assistant"},
        )
        print("ok", flush=True)
    elif command == "run-complete":
        journal.transition(run_id, "completed")
        print("ok", flush=True)
    elif command == "turn-accept":
        turn_id = create_turn_id()
        fingerprint = request_fingerprint(
            workspace_id=turns.workspace_id,
            session_id=None,
            message="cross-process-secret prompt",
        )
        turns.claim(turn_id=turn_id, fingerprint=fingerprint)
        print("ok", flush=True)
    elif command == "turn-running":
        turns.mark_running(turn_id)
        print("ok", flush=True)
    elif command == "quit":
        print("bye", flush=True)
        break
    else:
        print("bad", flush=True)
"""
    environment = dict(os.environ)
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    process = subprocess.Popen(
        [sys.executable, "-c", writer_script, str(workspace), str(data_dir)],
        cwd=workspace,
        env=environment,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"

        for command in ("run-create", "run-append", "run-complete"):
            assert _command(process, command) == "ok"
            sampler_wait.advance()
            assert _changed(subscription) == ["runs"]

        for command in ("turn-accept", "turn-running"):
            assert _command(process, command) == "ok"
            sampler_wait.advance()
            assert _changed(subscription) == ["turns"]

        session_script = r"""
from minicode.session import create_new_session, save_session
import sys
session = create_new_session(sys.argv[1])
session.messages.extend([
    {"role": "user", "content": "cross-process-secret prompt"},
    {"role": "assistant", "content": "cross-process-secret answer"},
])
save_session(session, force_full=True)
"""
        subprocess.run(
            [sys.executable, "-c", session_script, str(workspace)],
            cwd=workspace,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        sampler_wait.advance()
        assert _changed(subscription) == ["sessions"]

        mutation_script = r"""
from pathlib import Path
import sys
workspace = Path(sys.argv[1])
data_dir = Path(sys.argv[2])
kind = sys.argv[3]
if kind == "memory":
    path = workspace / ".mini-code-memory" / "MEMORY.md"
elif kind == "skills":
    path = workspace / ".mini-code" / "skills" / "safe" / "SKILL.md"
elif kind == "connections":
    path = workspace / ".mcp.json"
else:
    raise SystemExit(2)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("cross-process-secret", encoding="utf-8")
"""
        for kind in ("memory", "skills", "connections"):
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    mutation_script,
                    str(workspace),
                    str(data_dir),
                    kind,
                ],
                cwd=workspace,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            sampler_wait.advance()
            assert _changed(subscription) == [kind]

        assert _command(process, "quit") == "bye"
        process.wait(timeout=5)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        subscription.close()
        sampler_wait.release()
        stream.close()
