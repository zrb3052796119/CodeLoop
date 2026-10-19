"""Exact guardian/caller functions with real local children; no model or tasks."""
import ast
import json
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1]


def function(name, filename, namespace):
    path = SOURCE / 'scripts' / filename
    tree = ast.parse(path.read_text(), filename=str(path))
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    Path(path).write_text(json.dumps(value))


@pytest.fixture
def guardian(tmp_path):
    children, stops = [], []
    # Accelerate only the guardian's wall clock; process lifetime/timeout and
    # pytest use real time. This proves the >30s boundary in under one second.
    start = time.monotonic()
    clock = SimpleNamespace(time=lambda: 1700000000 + (time.monotonic() - start) * 1000)
    def stop_trial(pid, path):
        process = next(child for child in children if child.pid == pid)
        assert path == tmp_path / 'processes.jsonl'
        stops.append(pid)
        process.kill()
        process.wait(timeout=5)
        return {'cleanupComplete': True, 'ownedProcessId': pid}
    wait = function('wait_trial_process', 'run_three_arm_real.py', {
        'time': clock, 'subprocess': subprocess, 'signal': signal,
        'read_json': read_json, 'write_json': write_json, 'stop_trial': stop_trial})
    def launch(*, execution_marker=False, exit_code=0, delay=0.6):
        code = ("import json,time,sys; from pathlib import Path; print('ready',flush=True); "
                f"time.sleep({delay}); "
                + ("Path(sys.argv[1]).write_text(json.dumps({'deadlineEpoch':None})); time.sleep(0.1); "
                   if execution_marker else '')
                + f"sys.exit({exit_code})")
        process = subprocess.Popen([sys.executable, '-c', code, str(tmp_path / 'execution-start.json')],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        children.append(process)
        assert select.select([process.stdout], [], [], 10)[0], 'generic child did not initialize'
        assert process.stdout.readline().strip() == 'ready'
        return process
    yield SimpleNamespace(wait=wait, launch=launch, clock=clock, stops=stops)
    for process in children:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


@pytest.mark.parametrize('execution_marker', [False, True])
def test_explicit_unbounded_setup_survives_slow_validation_and_no_execution_limit(guardian, tmp_path, execution_marker):
    process = guardian.launch(execution_marker=execution_marker)
    started = guardian.clock.time()
    assert guardian.wait(process, tmp_path, setup_seconds=None) == 0
    assert guardian.clock.time() - started > 30
    assert guardian.stops == []
    assert not (tmp_path / 'process-cleanup.json').exists()


def test_default_benchmark_setup_still_expires_and_cleans_up(guardian, tmp_path):
    process = guardian.launch(delay=2)
    assert guardian.wait(process, tmp_path) == -signal.SIGKILL
    cleanup = read_json(tmp_path / 'process-cleanup.json')
    assert cleanup['cleanupComplete'] is True
    assert cleanup['ownedProcessId'] == process.pid and guardian.stops == [process.pid]
    assert cleanup['deadlineReachedEpoch'] >= 1700000000
    assert cleanup['cleanupSeconds'] >= 0


def test_explicit_execution_deadline_still_applies_with_unbounded_setup(guardian, tmp_path):
    process = guardian.launch(delay=2)
    write_json(tmp_path / 'execution-start.json', {'deadlineEpoch': guardian.clock.time() - 1})
    assert guardian.wait(process, tmp_path, setup_seconds=None) == -signal.SIGKILL
    assert guardian.stops == [process.pid]
    assert read_json(tmp_path / 'process-cleanup.json')['cleanupComplete'] is True


def test_already_exited_child_returns_its_real_exit_code(guardian, tmp_path):
    process = guardian.launch(exit_code=17, delay=0)
    assert process.wait(timeout=5) == 17
    assert guardian.wait(process, tmp_path) == 17
    assert guardian.stops == []


@pytest.mark.parametrize('wall_seconds,expected', [(None, {'setup_seconds': None}), (1800, {})])
def test_continuation_callsite_only_disables_setup_for_explicit_unbounded_policy(tmp_path, wall_seconds, expected):
    class ReachedGuardian(Exception):
        pass
    observed = []
    child = object()
    def wait(process, root, **kwargs):
        assert process is child and root == tmp_path / 'private' / 'generic'
        observed.append(kwargs)
        raise ReachedGuardian
    service = SimpleNamespace(wait_trial_process=wait)
    run = function('run_suite', 'run_requirement_completion_checkpoint.py', {
        'Path': Path, 'validate': lambda output: {}, 'load_checkpoint': lambda output: ({}, {}, []),
        'TRIAL_ID': 'generic', 'services': lambda output: service, 'time': time,
        'subprocess': SimpleNamespace(Popen=lambda *args, **kwargs: child),
        'sys': sys, 'ENTRY': tmp_path / 'unused-entry.py', 'ROOT': tmp_path,
        'LIMITS': {'wallSeconds': wall_seconds}})
    with pytest.raises(ReachedGuardian):
        run(tmp_path)
    assert observed == [expected]
