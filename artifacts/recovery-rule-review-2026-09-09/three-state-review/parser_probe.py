"""Real isolated pytest runs through the production trace emitter and Engine.

Run from the repository root with .venv-acceptance/bin/python. No provider,
network, or durable-memory API is used. Temporary tests are removed afterwards.
"""
from __future__ import annotations

import json
import runpy
import shlex
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
base = runpy.run_path(str(HERE.parent / 'evidence_probe.py'))
from minicode.reflection_evidence import call_objective, objective_relation


def evaluate_case(name, first, second, cwd, expected):
    steps, executions = [], []
    for args in (first, second):
        result = subprocess.run([sys.executable, '-m', 'pytest', *args], cwd=cwd,
                                capture_output=True, text=True, check=False)
        output = result.stdout + result.stderr
        executions.append({'args': args, 'exit_code': result.returncode, 'output': output})
        steps.append(('run_command', {'command': 'pytest', 'args': args},
                      result.returncode == 0, output))
    result = base['evaluate'](name, steps, expected)
    objectives = [call_objective(shlex.join(['pytest', *args])) for args in (first, second)]
    result.update({'real_pytest_runs': executions,
                   'objectives': [asdict(x) for x in objectives],
                   'relation': objective_relation(*objectives)})
    return result


def main():
    with tempfile.TemporaryDirectory(prefix='recovery-parser-', dir='/private/tmp') as folder:
        work = Path(folder)
        (work/'tests').mkdir()
        (work/'tests/test_auth.py').write_text('def test_expired_auth():\n    assert False, "expired token accepted"\n')
        (work/'tests/test_billing.py').write_text('def test_billing_valid():\n    assert True\n')
        (work/'tests/test_smoke.py').write_text('def test_smoke():\n    assert True\n')
        cases = [
            evaluate_case('boolean_flag_swallows_first_target',
                ['-q', 'tests/test_auth.py', 'tests/test_smoke.py', '--tb=short'],
                ['-q', 'tests/test_billing.py', 'tests/test_smoke.py', '--tb=short'],
                work, 'Do not confirm: auth was replaced by billing; -q has no argument.'),
            evaluate_case('quoted_selector_tail_disappears',
                ['tests/', '-q', '--tb=short', '-k', 'test_smoke or test_expired_auth'],
                ['tests/', '-q', '--tb=short', '-k', 'test_smoke and not test_expired_auth'],
                work, 'Do not confirm: the second complete -k expression excludes failing auth.'),
            evaluate_case('version_is_not_test_verification',
                ['tests/test_auth.py', '-q', '--tb=short'],
                ['tests/test_auth.py', '-q', '--tb=short', '--version'],
                work, 'Do not confirm: printing pytest version does not execute the failed test.'),
            evaluate_case('different_targets_without_leading_boolean_control',
                ['tests/test_auth.py', 'tests/test_smoke.py', '-q', '--tb=short'],
                ['tests/test_billing.py', 'tests/test_smoke.py', '-q', '--tb=short'],
                work, 'Do not confirm; control changes only placement of -q.'),
        ]
    Path(__file__).with_suffix('.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps([{'name':x['name'], 'relation':x['relation'],
                       'objectives':x['objectives'],
                       'exit_codes':[y['exit_code'] for y in x['real_pytest_runs']],
                       'value_accepted':x['value_accepted'],
                       'claims':[{'status':y['epistemic_status'],'statement':y['statement']} for y in x['persistable_claims']]}
                      for x in cases], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
