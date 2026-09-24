"""Offline review of CallObjective consumers; no model or memory writes.

Run from the repository root:
  .venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/objective-contract-review/consumer_probe.py

Most cases feed synthetic ToolResults through real input validators and the
runtime trace emitter into ReflectionEngine. The missing-fixture case executes
two real, isolated pytest runs in a temporary directory to obtain traceback
output. Only this file's sibling JSON is retained.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
base = runpy.run_path(str(ROOT / 'artifacts/recovery-rule-review-2026-09-09/evidence_probe.py'))
evaluate = base['evaluate']

from minicode.reflection_evidence import TraceEvidenceExtractor, call_objective, objectives_same_target
from minicode.recovery_lesson import extract_recovery_episodes


def cmd(command, args, ok, output, **kwargs):
    return ('run_command', {'command': command, 'args': args, **kwargs}, ok, output)


EDIT = ('edit_file', {'path': 'docs/README.md', 'old': 'Foo', 'new': 'Bar'}, True, 'updated')


def check(name, steps, expected):
    result = evaluate(name, steps, expected)
    evidence = TraceEvidenceExtractor().extract('Review and verify the project', result['trace'])
    calls = [item for item in evidence.tool_calls if item.tool_name == 'run_command']
    objectives = []
    # The main extractor supplies structured cwd. These cases have none;
    # the independent episode case below covers the rendered-cwd consumer.
    for item in calls:
        objectives.append(call_objective(item.invocation))
    result['objectives'] = [asdict(item) for item in objectives]
    result['shared_contract_match'] = objectives_same_target(objectives[0], objectives[-1])
    return result


def real_missing_fixture():
    with tempfile.TemporaryDirectory(prefix='recovery-consumer-', dir='/private/tmp') as folder:
        work = Path(folder)
        (work / 'tests').mkdir()
        (work / 'backend/tests').mkdir(parents=True)
        (work / 'tests/test_auth.py').write_text(
            'def test_auth():\n    open("fixtures/credentials.json").read()\n'
        )
        (work / 'backend/tests/test_auth.py').write_text('def test_auth():\n    assert True\n')
        outputs = []
        steps = []
        for target in ('tests/test_auth.py', 'backend/tests/test_auth.py'):
            proc = subprocess.run(
                ['python', '-m', 'pytest', '-q', '--tb=short', target], cwd=work,
                env={**os.environ, 'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')},
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                check=False,
            )
            outputs.append({'target': target, 'exit_code': proc.returncode, 'output': proc.stdout})
            steps.append(cmd('python', ['-m', 'pytest', '-q', '--tb=short', target], proc.returncode == 0, proc.stdout))
        result = check(
            'real_traceback_test_path_is_not_missing_object', steps,
            'Do not confirm relocation: the requested test file exists; its fixture is missing.',
        )
        result['real_pytest_runs'] = outputs
        return result


def episode_cwd():
    trace = []
    for index, (cwd, timeout, ok) in enumerate([
        ('/repo/backend', 1, False),
        ('/repo/frontend', 120, True),
        ('/repo/another', 120, True),
        ('/repo/backend', 120, True),
    ], 1):
        payload = base['run_command_tool'].validator({
            'command': 'python', 'args': ['scripts/report.py'],
            'cwd': cwd, 'timeout': timeout,
        })
        base['_append_tool_trace_events'](
            trace, {'id': f'call-{index}', 'toolName': 'run_command', 'input': payload},
            base['ToolResult'](ok=ok, output='report created' if ok else
                               'Command timed out after 1 seconds (process group terminated).'),
            index,
        )
    evidence = TraceEvidenceExtractor().extract('Generate backend report', trace)
    episode = extract_recovery_episodes(evidence)[0]
    actual_retry = evidence.tool_calls[-1]
    return {
        'name': 'episode_rendered_cwd_not_restored', 'trace': trace,
        'expected': 'Only original backend report retry should be retained.',
        'failed': episode.failed_calls[0].invocation,
        'selected_followups': [item.invocation for item in episode.followup_calls],
        'actual_retry': actual_retry.invocation,
        'actual_retry_retained': actual_retry in episode.followup_calls,
        'causal_status': episode.causal_status,
    }


def main():
    targets = [f'tests/test_{name}.py' for name in ('one', 'two', 'three', 'four', 'five')]
    cases = [
        check('main_deriver_truncates_sixth_target', [
            cmd('pytest', targets + ['tests/test_auth.py'], False, 'AssertionError: expired token accepted'),
            EDIT,
            cmd('pytest', targets, True, '5 passed'),
        ], 'Do not confirm: auth target was dropped from the passing run.'),
        check('main_deriver_truncates_selection', [
            cmd('pytest', targets + ['-m=expired'], False, 'AssertionError: expired token accepted'),
            EDIT,
            cmd('pytest', targets + ['-m=valid'], True, '5 passed, 1 deselected'),
        ], 'Do not confirm: successful run selected a different marker.'),
        check('generic_fallback_overrides_ambiguous_objective', [
            cmd('dbtool', ['migrate', 'auth', '-m=expired'], False, 'UsageError: invalid mode'),
            cmd('dbtool', ['migrate', 'auth', '-m=valid'], True, 'Migration completed'),
        ], 'Keep candidate: shared objective explicitly marks selection ambiguous.'),
        check('generic_fallback_overrides_changed_marker', [
            cmd('pytest', ['tests', '-m=expired'], False, 'AssertionError: expired token accepted'),
            cmd('pytest', ['tests', '-m=valid'], True, '1 passed, 1 deselected'),
        ], 'Do not confirm: known selection changed; bare directory is treated as pathless.'),
        check('missing_target_default_scope_accepts_echo', [
            cmd('pytest', ['tests/test_auth.py'], False,
                "FileNotFoundError: No such file or directory: 'tests/test_auth.py'"),
            cmd('echo', ['done'], True, 'done'),
        ], 'Do not confirm: echo does not retry the failed pytest objective.'),
        check('missing_target_relocation_discards_selection_guard', [
            cmd('pytest', ['tests/test_auth.py', '-m=expired'], False,
                "FileNotFoundError: No such file or directory: 'tests/test_auth.py'"),
            cmd('pytest', ['backend/tests/test_auth.py', '-m=valid'], True, '3 passed'),
        ], 'Do not confirm: relocation does not license selecting different tests.'),
        real_missing_fixture(),
    ]
    output = {'engine_cases': cases, 'episode_case': episode_cwd()}
    Path(__file__).with_suffix('.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({
        'engine_cases': [{
            'name': item['name'], 'contract_match': item['shared_contract_match'],
            'value_accepted': item['value_accepted'],
            'persistable_statuses': [claim['epistemic_status'] for claim in item['persistable_claims']],
        } for item in cases],
        'episode_case': {key: value for key, value in output['episode_case'].items() if key != 'trace'},
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
