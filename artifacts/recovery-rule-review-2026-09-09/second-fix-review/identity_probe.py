"""Second-fix boundary review using runtime trace emitter; no tool execution."""
from __future__ import annotations
import json
import runpy
from pathlib import Path

HERE = Path(__file__).resolve().parent
base = runpy.run_path(str(HERE.parent / 'evidence_probe.py'))
evaluate = base['evaluate']


def cmd(executable, args, ok, message):
    return ('run_command', {'command': executable, 'args': args}, ok, message)


def main():
    cases = [
        evaluate('pytest_marker_changed', [
            cmd('pytest', ['tests/test_auth.py', '-m=expired'], False, 'AssertionError: expired token accepted'),
            ('edit_file', {'path':'docs/README.md','old':'Foo','new':'Bar'}, True, 'updated'),
            cmd('pytest', ['tests/test_auth.py', '-m=valid'], True, '1 passed, 1 deselected'),
        ], 'Do not confirm a repair: the successful run selected different pytest markers.'),
        evaluate('inline_cd_is_still_a_shared_resource', [
            cmd('bash', ['-lc', 'cd /repo/workspace && pytest tests/test_auth.py'], False, 'AssertionError: expired auth token accepted'),
            cmd('bash', ['-lc', 'cd /repo/workspace && pytest tests/test_billing.py'], True, '3 passed'),
        ], 'Do not confirm a repair: shell cd is an anchor, not a common test target.'),
        evaluate('shared_junit_output_is_not_same_test', [
            cmd('pytest', ['tests/test_auth.py', '--junitxml=/tmp/results.xml'], False, 'AssertionError: expired auth token accepted'),
            cmd('python', ['-m','pytest','tests/test_billing.py','--junitxml=/tmp/results.xml'], True, '3 passed'),
        ], 'Do not confirm a repair: output artifact is shared, test targets differ.'),
        evaluate('missing_fixture_does_not_mean_missing_test_target', [
            cmd('pytest', ['tests/test_auth.py'], False, "FileNotFoundError: No such file or directory: 'fixtures/credentials.json'"),
            cmd('python', ['-m', 'pytest', 'backend/tests/test_auth.py'], True, '3 passed'),
        ], 'Do not confirm a relocation: requested test exists; its fixture was missing, and another test ran.'),
    ]
    (HERE/'identity_probe.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps([{'case':c['name'],'value_accepted':c['value_accepted'],'claims':[{'status':r['epistemic_status'],'statement':r['statement']} for r in c['persistable_claims']]} for c in cases], ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
