"""Real session-separated descendants must stop before workspace judging."""

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

import pytest

from scripts.three_arm_processes import ProcessEvidence, stop_trial


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX guardian")
REPOSITORY = Path(__file__).resolve().parents[1]
_WRITER = """
from pathlib import Path
import sys, time
path = Path(sys.argv[1])
while True:
    with path.open('a') as stream:
        stream.write('tick\\n')
    time.sleep(0.03)
"""
_CHILD = """
import json, subprocess, sys
from pathlib import Path
writer, nested, direct, ready = sys.argv[1:]
child = subprocess.Popen([sys.executable, '-c', writer, nested],
                         start_new_session=True)
Path(ready).write_text(json.dumps({'nested': child.pid}))
sys.argv = [sys.argv[0], direct]
exec(writer, {'__name__': '__main__', 'sys': sys})
"""
_TRIAL = """
import json, signal, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts.three_arm_processes import ProcessEvidence
root, child_code, writer_code, orphan = Path(sys.argv[2]), *sys.argv[3:]
with ProcessEvidence(root / 'processes.jsonl'):
    child = subprocess.Popen(
        [sys.executable, '-c', child_code, writer_code, str(root / 'nested'),
         str(root / 'direct'), str(root / 'ready')], start_new_session=True)
    for _ in range(500):
        if all((root / name).exists() for name in ('ready', 'direct', 'nested')):
            break
        time.sleep(0.01)
    else:
        raise RuntimeError('writers did not start')
    print(json.dumps({'child': child.pid}), flush=True)
    if orphan != 'yes':
        signal.pause()
"""


def _start_trial(root: Path, *, orphan: bool = False) -> tuple[subprocess.Popen, dict]:
    process = subprocess.Popen(
        [sys.executable, "-c", _TRIAL, str(REPOSITORY), str(root), _CHILD,
         _WRITER, "yes" if orphan else "no"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    assert select.select([process.stdout], [], [], 10)[0], "trial did not start"
    message = process.stdout.readline()
    if not message:
        process.wait(timeout=5)
        raise AssertionError(process.stderr.read())
    identities = json.loads(message)
    identities.update(json.loads((root / "ready").read_text()))
    return process, identities


def _cleanup(process: subprocess.Popen, identities: dict) -> None:
    for pid in (process.pid, *identities.values()):
        try:
            if os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.communicate(timeout=5)


@pytest.mark.parametrize("orphan", [False, True])
def test_cleanup_stops_new_sessions_and_nested_writers(tmp_path: Path, orphan: bool):
    process, identities = _start_trial(tmp_path, orphan=orphan)
    try:
        if orphan:
            assert process.wait(timeout=5) == 0
        result = stop_trial(process.pid, tmp_path / "processes.jsonl")
        assert result["cleanupComplete"], result
        assert set(identities.values()) <= set(result["killedPids"])
        assert result["survivorPids"] == []
        process.wait(timeout=5)
        contents = {name: (tmp_path / name).read_bytes()
                    for name in ("direct", "nested")}
        time.sleep(0.15)
        assert all((tmp_path / name).read_bytes() == value
                   for name, value in contents.items())
    finally:
        _cleanup(process, identities)


def test_stale_pgid_record_does_not_kill_unrelated_live_process(tmp_path: Path):
    others = [subprocess.Popen(
        [sys.executable, "-c", "import signal; signal.pause()"],
        start_new_session=True,
    ) for _ in range(2)]
    trial, unrelated = others
    path = tmp_path / "processes.jsonl"
    path.write_text(json.dumps({"pid": unrelated.pid, "pgid": unrelated.pid + 1})
                    + "\n")
    try:
        result = stop_trial(trial.pid, path)
        assert result["cleanupComplete"]
        assert unrelated.pid not in result["killedPids"]
        assert unrelated.poll() is None
        assert trial.wait(timeout=5) == -signal.SIGKILL
    finally:
        for process in others:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def test_truncated_receipt_fails_closed_but_still_reclaims_trial(tmp_path: Path):
    process = subprocess.Popen(
        [sys.executable, "-c", "import signal; signal.pause()"],
        start_new_session=True,
    )
    path = tmp_path / "processes.jsonl"
    path.write_text('{"pid":')
    try:
        result = stop_trial(process.pid, path)
        assert not result["cleanupComplete"]
        assert result["evidenceErrors"] == ["invalid_process_evidence_line_1"]
        assert result["survivorPids"] == []
        assert process.wait(timeout=5) == -signal.SIGKILL
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def test_evidence_durably_records_popen_without_changing_process_api(tmp_path: Path):
    path = tmp_path / "processes.jsonl"
    original = subprocess.Popen
    with ProcessEvidence(path):
        process = subprocess.Popen(
            [sys.executable, "-c", "print('ok')"],
            stdout=subprocess.PIPE, text=True, start_new_session=True,
        )
        assert isinstance(process, original)
        assert process.communicate(timeout=5)[0] == "ok\n"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert {row["pid"] for row in rows} == {os.getpid(), process.pid}
        child = next(row for row in rows if row["pid"] == process.pid)
        assert child["pgid"] == process.pid and child["creatorPid"] == os.getpid()
    assert subprocess.Popen is original
