"""Offline parser probes; isolated pytest confirms the scope-changing flag.

No network, model provider, or persistent-memory write is used. The inline-cd
case uses synthetic trace results; the ignore-glob case uses real pytest output.
"""
from __future__ import annotations
import json
import runpy
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
base = runpy.run_path(str(HERE.parent / 'evidence_probe.py'))
from minicode.reflection_evidence import call_objective, objectives_same_target


def command(args, ok, output, cwd=None):
    payload={'command':args[0],'args':args[1:]}
    if cwd: payload['cwd']=cwd
    return ('run_command',payload,ok,output)


def ignored_failure():
    with tempfile.TemporaryDirectory(prefix='recovery-selection-', dir='/private/tmp') as directory:
        tests=Path(directory)/'tests'
        tests.mkdir()
        (tests/'test_auth.py').write_text('def test_expired_token():\n    assert False, "expired auth token accepted"\n')
        (tests/'test_billing.py').write_text('def test_billing():\n    assert True\n')
        steps=[]
        outputs=[]
        for extra in ([], ['--ignore-glob=*/test_auth.py']):
            args=['tests/', '-q', '--tb=short', '-p', 'no:cacheprovider', *extra]
            result=subprocess.run([sys.executable,'-m','pytest',*args],cwd=directory,
                                  capture_output=True,text=True,check=False)
            output=result.stdout+result.stderr
            steps.append(command(['pytest',*args],result.returncode==0,output))
            outputs.append({'args':args,'exit_code':result.returncode,'output':output})
        return steps,outputs


def main():
    ignored_steps,actual_pytest_runs=ignored_failure()
    scenarios=[
        ('unrecognized_pytest_selector',
         'pytest tests/ -q --tb=short -p no:cacheprovider', 'pytest tests/ -q --tb=short -p no:cacheprovider --ignore-glob=*/test_auth.py', '',
         ignored_steps,
         'Unknown scope-changing flags must not silently compare equal.'),
        ('inline_cd_overrides_structured_cwd',
         "bash -lc 'cd backend && pytest tests/test_auth.py'", "bash -lc 'cd frontend && pytest tests/test_auth.py'", '/repo',
         [command(['bash','-lc','cd backend && pytest tests/test_auth.py'],False,'AssertionError: backend expired auth token accepted','/repo'),command(['bash','-lc','cd frontend && pytest tests/test_auth.py'],True,'1 passed','/repo')],
         'Only /repo/backend and /repo/frontend are actual cwd; /repo is not an alternative execution anchor.'),
    ]
    results=[]
    for name,first,second,cwd,steps,expected in scenarios:
        left=call_objective(first,cwd=cwd)
        right=call_objective(second,cwd=cwd)
        result=base['evaluate'](name,steps,expected)
        result.update({'failed_objective':asdict(left),'passed_objective':asdict(right),'contract_match':objectives_same_target(left,right)})
        if name=='unrecognized_pytest_selector':
            result['actual_pytest_runs']=actual_pytest_runs
        results.append(result)
    target=HERE/'parser_probe.json'
    target.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps([{'case':r['name'],'failed_objective':r['failed_objective'],'passed_objective':r['passed_objective'],'contract_match':r['contract_match'],'value_accepted':r['value_accepted'],'claims':[{'status':c['epistemic_status'],'statement':c['statement']} for c in r['persistable_claims']]} for r in results],ensure_ascii=False,indent=2))

if __name__ == '__main__':main()
