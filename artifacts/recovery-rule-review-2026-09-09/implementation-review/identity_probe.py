"""Synthetic trace review probes; no tools or durable memory execute."""
from __future__ import annotations
import json
import runpy
from pathlib import Path

REVIEW = Path(__file__).resolve().parent
original = runpy.run_path(str(REVIEW.parent / 'evidence_probe.py'))
evaluate = original['evaluate']


def cmd(executable, args, ok, output, cwd=None):
    payload = {'command': executable, 'args': args}
    if cwd is not None:
        payload['cwd'] = cwd
    return ('run_command', payload, ok, output)


def main():
    results = [
        evaluate('shared_cwd_unrelated_tests', [
            cmd('pytest', ['tests/test_auth.py'], False, 'AssertionError: expired auth token accepted', '/repo/workspace'),
            cmd('python', ['-m', 'pytest', 'tests/test_billing.py'], True, '3 passed', '/repo/workspace'),
        ], 'No confirmed recovery: the only common resource is cwd; the failed auth target was never rerun.'),
        evaluate('different_cwd_same_relative_test', [
            cmd('pytest', ['tests/test_config.py'], False, 'AssertionError: backend config broken', '/repo/backend'),
            cmd('python', ['-m', 'pytest', 'tests/test_config.py'], True, '3 passed', '/repo/frontend'),
        ], 'No confirmed recovery: identical relative paths resolve to distinct files under different cwd.'),
        evaluate('relative_suffix_is_not_file_identity', [
            cmd('pytest', ['tests/test_auth.py'], False, 'AssertionError: root auth token accepted'),
            cmd('python', ['-m', 'pytest', 'backend/tests/test_auth.py'], True, '3 passed'),
        ], 'No confirmed recovery: two relative files in the same workspace can coexist, with no rename evidence.'),
        evaluate('changed_filter_skips_failed_test', [
            cmd('pytest', ['tests/test_auth.py', '-k=expired'], False, 'AssertionError: expired token accepted'),
            ('edit_file', {'path': 'docs/README.md', 'old': 'Foo', 'new': 'Bar'}, True, 'updated'),
            cmd('pytest', ['tests/test_auth.py', '-k=valid'], True, '1 passed, 1 deselected'),
        ], 'No confirmed recovery: successful invocation selects a different test, and documentation edit is unrelated.'),
    ]
    output = REVIEW / 'identity_probe.json'
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps([{
        'case': r['name'],
        'value_accepted': r['value_accepted'],
        'claims': [{'statement': c['statement'], 'status': c['epistemic_status'], 'verification_ids': c['verification_ids']} for c in r['persistable_claims']],
    } for r in results], ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
