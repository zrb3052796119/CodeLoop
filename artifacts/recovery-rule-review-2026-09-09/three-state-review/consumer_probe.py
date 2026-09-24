"""Three-state-contract review: real local pytest, no provider or memory writes.

Run from repository root:
  .venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/three-state-review/consumer_probe.py

All counterexamples execute pytest in temporary directories, feed its actual
stdout/return status through run_command's validator and the runtime trace
emitter, and run the current ReflectionEngine. Test files are removed with the
temporary directories. Output is retained only in consumer_probe.json.
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

from minicode.reflection_evidence import TraceEvidenceExtractor, call_objective_from_rendered, objective_relation


def pytest_run(work: Path, args: list[str]) -> tuple[dict, tuple]:
    env = {**os.environ, 'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')}
    full_args = ['-m', 'pytest', *args, '-q', '--tb=short']
    proc = subprocess.run(
        ['python', *full_args], cwd=work, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    run = {'command': 'python', 'args': full_args, 'exit_code': proc.returncode, 'output': proc.stdout}
    step = ('run_command', {'command': 'python', 'args': full_args}, proc.returncode == 0, proc.stdout)
    return run, step


def reflect_case(name: str, steps: list[tuple], expected: str) -> dict:
    result = evaluate(name, steps, expected)
    evidence = TraceEvidenceExtractor().extract('Review and verify the project', result['trace'])
    objectives = [call_objective_from_rendered(item.invocation) for item in evidence.tool_calls]
    result['raw_objectives'] = [asdict(item) for item in objectives]
    result['raw_objective_relation'] = objective_relation(objectives[0], objectives[-1])
    return result


def partial_relocation_case() -> dict:
    with tempfile.TemporaryDirectory(prefix='recovery-path-proof-', dir='/private/tmp') as directory:
        work = Path(directory)
        (work / 'tests').mkdir()
        (work / 'backend/tests').mkdir(parents=True)
        (work / 'tests/test_auth.py').write_text('def test_auth():\n    assert True\n')
        (work / 'backend/tests/test_auth.py').write_text('def test_auth():\n    assert True\n')
        pairs = [
            pytest_run(work, ['tests/test_auth.py', 'tests/test_missing.py']),
            pytest_run(work, ['backend/tests/test_auth.py']),
        ]
        result = reflect_case(
            'missing_one_target_relocating_a_different_target',
            [step for _, step in pairs],
            'Do not confirm: the missing tests/test_missing.py was dropped; only auth was relocated.',
        )
        result['actual_pytest_runs'] = [run for run, _ in pairs]
        result['missing_target_still_absent_after_success'] = not (work / 'tests/test_missing.py').exists()
        return result


def bare_target_is_not_default_case() -> dict:
    with tempfile.TemporaryDirectory(prefix='recovery-default-proof-', dir='/private/tmp') as directory:
        work = Path(directory)
        (work / 'billing').mkdir()
        (work / 'billing/test_billing.py').write_text('def test_billing():\n    assert True\n')
        pairs = [
            pytest_run(work, ['tests/test_auth.py']),
            pytest_run(work, ['billing']),
        ]
        result = reflect_case(
            'bare_billing_target_misclassified_as_default_scope',
            [step for _, step in pairs],
            'Do not confirm: billing is an explicit different directory, not default-scope retry.',
        )
        result['actual_pytest_runs'] = [run for run, _ in pairs]
        return result


def nested_argument_error_case() -> dict:
    with tempfile.TemporaryDirectory(prefix='recovery-argument-proof-', dir='/private/tmp') as directory:
        work = Path(directory)
        (work / 'tests').mkdir()
        (work / 'pytest.ini').write_text('[pytest]\nmarkers = integration: integration tests\n')
        (work / 'conftest.py').write_text(
            'import pytest\n'
            'def pytest_addoption(parser):\n'
            '    parser.addoption("--run-integration", action="store_true")\n'
            'def pytest_collection_modifyitems(config, items):\n'
            '    if config.getoption("--run-integration"): return\n'
            '    for item in items:\n'
            '        if "integration" in item.keywords:\n'
            '            item.add_marker(pytest.mark.skip(reason="integration disabled"))\n'
        )
        (work / 'tests/test_cli.py').write_text(
            'import argparse, pytest\n'
            '@pytest.mark.integration\n'
            'def test_nested_helper():\n'
            '    argparse.ArgumentParser(prog="nested-helper").parse_args(["--run-integration"])\n'
            'def test_smoke():\n'
            '    assert True\n'
        )
        pairs = [
            pytest_run(work, ['tests/test_cli.py', '--run-integration']),
            pytest_run(work, ['tests/test_cli.py']),
        ]
        steps = [step for _, step in pairs]
        result = reflect_case(
            'nested_usage_error_waives_valid_parent_selection_flag', steps,
            'Do not confirm: pytest accepted --run-integration; the nested helper rejected its own input. '
            'The second run skipped the failing test.',
        )
        result['actual_pytest_runs'] = [run for run, _ in pairs]
        original_again, _ = pytest_run(work, ['tests/test_cli.py', '--run-integration'])
        result['original_scope_after_success'] = original_again
        tool, payload, ok, output = steps[0]
        # Isolate the waiver: without the nested helper's unrecognized-args
        # phrase, the same top-level call/input difference stays a conflict.
        controlled_error = output.replace(
            'unrecognized arguments: --run-integration', 'nested helper rejected its input'
        )
        control = reflect_case(
            'nested_error_phrase_removed_control',
            [(tool, payload, ok, controlled_error), steps[1]],
            'Reject unchanged scope conflict when no invalid-argument phrase is available.',
        )
        result['phrase_removed_control'] = {
            'recoveries': control['recoveries'], 'value_accepted': control['value_accepted'],
            'raw_objective_relation': control['raw_objective_relation'],
        }
        return result


def main() -> None:
    results = [partial_relocation_case(), bare_target_is_not_default_case(), nested_argument_error_case()]
    Path(__file__).with_suffix('.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps([{
        'name': result['name'], 'relation': result['raw_objective_relation'],
        'actual_exit_codes': [item['exit_code'] for item in result['actual_pytest_runs']],
        'value_accepted': result['value_accepted'],
        'persistable_statuses': [item['epistemic_status'] for item in result['persistable_claims']],
        'original_scope_after_success_exit_code': result.get('original_scope_after_success', {}).get('exit_code'),
        'phrase_removed_control_accepted': result.get('phrase_removed_control', {}).get('value_accepted'),
    } for result in results], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
