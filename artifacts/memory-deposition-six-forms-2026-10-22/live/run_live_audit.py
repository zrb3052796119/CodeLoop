"""Five original yield scenarios against the configured live provider.

All writable runtime state is isolated beside this file. No global settings,
original experiment files, credentials, prompts or response schemas are edited.
Each child process has a 180-second outer timeout. Complete reflection inputs
and content-free funnel lines are captured for reproducible follow-up replay.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import importlib.util
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
sys.path.insert(0, str(REPO))
EXTRA = Path('/private/tmp/minicode-deposition-test-deps')
if EXTRA.exists():
    sys.path.insert(0, str(EXTRA))

from scripts.memory_deposition_yield_audit import POLICY, PROMPTS

WORKSPACE = ROOT / 'workspaces' / 'yieldtest'
RESULTS = ROOT / 'results'
MEMORY_ROOT = ROOT / 'runtime-data' / 'memory-home-inject'
JOURNAL_ROOT = ROOT / 'runtime-data' / 'journal-home'


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _probe():
    source = REPO / '.experiment-work/deposition-experiment-v1/e2e_probe.py'
    spec = importlib.util.spec_from_file_location('yield_original_probe', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.EXPERIMENT_ROOT = ROOT
    module.MEMORY_DATA_ROOT = MEMORY_ROOT
    module.JOURNAL_DATA_ROOT = JOURNAL_ROOT
    module._isolate_runtime_storage()
    # Preserve the configured model credentials, while keeping personal
    # instructions, profiles, skills and unrelated MCP services out of this
    # synthetic fixture audit. These overrides exist only in this process.
    import minicode.config as config
    import minicode.cybernetic_supervisor as supervisor
    import minicode.headless as headless
    import minicode.prompt as prompt_module
    import minicode.skills as skills

    fixture_home = ROOT / 'runtime-data' / 'fixture-home'
    fixture_home.mkdir(parents=True, exist_ok=True)
    config.MINI_CODE_DIR = fixture_home / '.mini-code'
    config.MINI_CODE_USER_PROFILE_PATH = config.MINI_CODE_DIR / 'USER.md'
    supervisor.SUPERVISOR_STATE_PATH = config.MINI_CODE_DIR / 'cybernetic_supervisor.json'
    skills._home_dir = lambda: fixture_home
    original_read = prompt_module._maybe_read
    prompt_module._maybe_read = lambda path: (
        original_read(path) if Path(path).resolve().is_relative_to(WORKSPACE.resolve()) else None
    )
    original_runtime = headless.create_agent_turn_runtime

    def fixture_runtime(*args, **kwargs):
        kwargs['include_mcp'] = False
        return original_runtime(*args, **kwargs)

    headless.create_agent_turn_runtime = fixture_runtime
    return module


def _prepare():
    # Fresh fixture only: refusing a reset protects both this run and originals.
    WORKSPACE.mkdir(parents=True, exist_ok=False)
    (WORKSPACE / 'src').mkdir()
    (WORKSPACE / 'tools').mkdir()
    (WORKSPACE / 'policy.md').write_text(POLICY, encoding='utf-8')
    (WORKSPACE / 'requirements.txt').write_text('httpx>=0.27\n', encoding='utf-8')
    (WORKSPACE / 'src/app.py').write_text('def main() -> str:\n    return "app ok"\n', encoding='utf-8')
    check = WORKSPACE / 'tools/check.sh'
    check.write_text('#!/usr/bin/env bash\nset -euo pipefail\ncd "$(dirname "$0")/.."\npython3 -m py_compile src/app.py\necho "ALL CHECKS PASSED"\n', encoding='utf-8')
    check.chmod(0o755)
    _write(ROOT / 'runtime-data/permissions.json', {
        'allowedCommandPatterns': ['./tools/check.sh', 'bash ./tools/check.sh',
            'bash tools/check.sh', 'pytest', 'python3 -m pytest',
            'python3 src/app.py', 'python src/app.py', 'python3 -m py_compile src/app.py'],
        'deniedCommandPatterns': [], 'allowedDirectoryPrefixes': [],
        'deniedDirectoryPrefixes': [], 'allowedEditPatterns': [], 'deniedEditPatterns': [],
    })


def _session(number: int):
    os.environ['MINI_CODE_MEMORY_DEPOSITION_MODE'] = 'inject'
    os.environ['MINI_CODE_MEMORY_REDIS_URL'] = ''
    os.environ['MINI_CODE_REFLECTION_SYNTHESIZER_MODE'] = 'rule'
    os.environ['MINI_CODE_LOG_LEVEL'] = 'WARNING'
    for feature in ('USER_PREFERENCES', 'DECLARED_DEPENDENCIES', 'PENDING_DRAFTS'):
        os.environ[f'MINI_CODE_MEMORY_DEPOSITION_{feature}'] = '1'
    probe = _probe()
    from minicode.memory import MemoryManager, MemoryScope
    from minicode.memory_pipeline import MemoryPipeline
    original_write = MemoryPipeline.write_result
    captures = []
    funnels = []

    def capture_write(self, task_description, execution_trace, *args, **kwargs):
        capture = {'task_description': task_description, 'execution_trace': deepcopy(execution_trace)}
        captures.append(capture)
        _write(RESULTS / f'session-{number}-traces.json', captures)
        result = original_write(self, task_description, execution_trace, *args, **kwargs)
        capture['write_reasons'] = list(result.reason_codes)
        capture['authority_kind'] = result.authority_kind
        capture['project_facts_status'] = result.project_facts_effect.status
        _write(RESULTS / f'session-{number}-traces.json', captures)
        return result

    class FunnelHandler(logging.Handler):
        def emit(self, record):
            message = record.getMessage()
            if 'MemoryPipeline: deposition candidates=' in message:
                funnels.append(message)
                _write(RESULTS / f'session-{number}-funnels.json', funnels)

    MemoryPipeline.write_result = capture_write
    logger = logging.getLogger('minicode.memory_pipeline')
    logger.setLevel(logging.INFO)
    logger.addHandler(FunnelHandler())
    os.chdir(WORKSPACE)
    from minicode.headless import run_headless
    started = time.monotonic()
    response = run_headless(
        PROMPTS[number - 1], run_source='headless',
        run_journal_factory=probe._journal_factory, run_observation_enabled=True,
    )
    evidence = probe._collect_evidence(WORKSPACE, response)
    manager = MemoryManager(project_root=WORKSPACE, data_root=MEMORY_ROOT)
    evidence['userMemories'] = [entry.to_dict() for entry in manager.memories[MemoryScope.USER].entries]
    evidence['reflectionTraceCount'] = len(captures)
    evidence['funnelLogs'] = funnels
    evidence['elapsedSeconds'] = round(time.monotonic() - started, 3)
    evidence['originalPrompt'] = PROMPTS[number - 1]
    evidence['liveModelAudit'] = True
    evidence['fixtureIsolation'] = {'personal_instructions': False, 'personal_skills': False, 'mcp': False}
    _write(RESULTS / f'session-{number}.json', evidence)
    summary = {'session': number, 'run_status': evidence.get('run', {}).get('status'),
        'model_calls': evidence.get('modelCalls'), 'trace_count': len(captures),
        'project_entries': len(evidence.get('projectMemories', [])),
        'user_entries': len(evidence['userMemories']), 'elapsed_seconds': evidence['elapsedSeconds'],
        'response_is_error': response.startswith('Error:')}
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary['run_status'] == 'completed' and not summary['response_is_error'] else 1


def _all():
    _prepare()
    summaries = []
    for number in range(1, 6):
        if number == 3:
            (WORKSPACE / 'src/app.py').write_text('def main() -> str\n    return "app ok"\n', encoding='utf-8')
        stdout_path = RESULTS / f'session-{number}.stdout.log'
        stderr_path = RESULTS / f'session-{number}.stderr.log'
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        with stdout_path.open('w') as stdout, stderr_path.open('w') as stderr:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--session', str(number)],
                stdout=stdout, stderr=stderr, cwd=REPO, start_new_session=True)
            timed_out = False
            try:
                code = child.wait(timeout=180)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
                code = 124
        item = {'session': number, 'exit_code': code, 'timed_out': timed_out,
            'elapsed_seconds': round(time.monotonic() - started, 3)}
        if (RESULTS / f'session-{number}.json').exists():
            evidence = json.loads((RESULTS / f'session-{number}.json').read_text())
            item.update(model_calls=evidence.get('modelCalls'), trace_count=evidence.get('reflectionTraceCount'),
                run_status=evidence.get('run', {}).get('status'))
        summaries.append(item)
        _write(RESULTS / 'live-summary.json', {'kind': 'actual_live_model_five_sessions', 'sessions': summaries})
        print(json.dumps(item), flush=True)
        if code != 0:
            # Provider/config/network failures are recorded, not silently replaced
            # with mock results or repeated charged calls.
            break
    return 0 if len(summaries) == 5 and all(item['exit_code'] == 0 for item in summaries) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=int, choices=range(1, 6))
    args = parser.parse_args()
    return _session(args.session) if args.session else _all()


if __name__ == '__main__':
    raise SystemExit(main())
