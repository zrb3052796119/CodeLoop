"""Archived fixed public inputs reach all real verifier paths, never runtime state."""
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_execution import ImplementationSpec
from minicode.permissions import PermissionManager
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.types import AgentStep, ModelAdapter
from scripts.public_verification_fixtures import (PublicFixtureError, PublicVerificationFixture,
    bind_public_fixtures, bind_verifier_public_inputs, public_fixture_manifest)

ROOT=Path(__file__).resolve().parents[1]
FIXTURE='.mini-code/agents/visual-judge.md'
SHA='112882e006b335964130255ed339fd28c54c847376e31d82faccf6a8205e4828'
POSIX_FIXTURE_IO = pytest.mark.skipif(
    os.name != 'posix' or not hasattr(os, 'O_DIRECTORY') or not hasattr(os, 'O_NOFOLLOW')
    or not hasattr(os, 'O_NONBLOCK') or os.open not in getattr(os, 'supports_dir_fd', ()), reason='fixture projection requires POSIX no-follow dir_fd IO')


def _document():
    content=(ROOT/'tests/fixtures/public_visual_judge.md').read_text()
    return {'cases':[{'id':'real-vision-budget','files':{FIXTURE:content}}]}


def _bound():
    return bind_public_fixtures(_document(),'real-vision-budget')


@pytest.mark.parametrize('mutation',['content','missing','credential','runtime','traversal'])
def test_only_registered_archived_fixture_can_be_bound(mutation):
    item=_bound()[0]
    if mutation=='content':
        document=_document();document['cases'][0]['files'][FIXTURE]+='changed'
        action=lambda:bind_public_fixtures(document,'real-vision-budget')
    elif mutation=='missing':
        document=_document();document['cases'][0]['files'].clear()
        action=lambda:bind_public_fixtures(document,'real-vision-budget')
    else:
        path={'credential':'.env','runtime':'.mini-code/settings.json','traversal':'../visual-judge.md'}[mutation]
        action=lambda:PublicVerificationFixture(item.case,path,item.sha256,item.content)
    with pytest.raises(PublicFixtureError):action()


@POSIX_FIXTURE_IO
@pytest.mark.parametrize('attack',['parent','existing_runtime','file_symlink','directory_symlink','changed_bytes','changed_mode'])
def test_projection_fails_closed_before_publishing_success(tmp_path,attack):
    root=tmp_path/'clone';root.mkdir()
    parent=tmp_path/'parent';parent.mkdir()
    target=root/FIXTURE
    secret=tmp_path/'private-marker';secret.write_text('synthetic private data')
    if attack=='existing_runtime':
        (root/'.mini-code').mkdir();(root/'.mini-code/settings.json').write_text('runtime')
    original=secret.read_bytes();evidence=[];called=[]
    def command(_argv,**_kwargs):
        called.append(True)
        if attack=='file_symlink':
            target.unlink();target.symlink_to(secret)
        elif attack=='directory_symlink':
            target.unlink();target.parent.rmdir();target.parent.symlink_to(tmp_path,target_is_directory=True)
        elif attack=='changed_bytes':
            target.chmod(0o600);target.write_text('changed');target.chmod(0o400)
        elif attack=='changed_mode':target.chmod(0o600)
        return subprocess.CompletedProcess(_argv,0,'passed','')
    with bind_verifier_public_inputs(_bound(), receipt_sink=lambda _doc: None),pytest.raises((PublicFixtureError,OSError)):
        DeterministicVerifier(command_runner=command).run(checks=(VerificationCheck('check','tests',('pytest','-q'),5),),
            cwd=str(root),permission_cwd=str(root if attack=='parent' else parent),
            workflow_id='wf_input',task_id='task_input',execution_assignment_id='asg_input',patch_digest='sha256:'+'1'*64,
            evidence_sink=lambda _key,doc:evidence.append(doc))
    assert not evidence,'fixture mutation must prevent publication of passing evidence'
    assert secret.read_bytes()==original
    assert bool(called)==(attack not in {'parent','existing_runtime'})


@POSIX_FIXTURE_IO
def test_original_verifier_exception_is_not_hidden(tmp_path):
    original=KeyboardInterrupt('original cancel')
    def command(*_a,**_k):raise original
    with bind_verifier_public_inputs(_bound(), receipt_sink=lambda _doc: None),pytest.raises(KeyboardInterrupt) as caught:
        DeterministicVerifier(command_runner=command).run(checks=(VerificationCheck('check','tests',('pytest','-q'),5),),
            cwd=str(tmp_path),permission_cwd=str(tmp_path.parent),workflow_id='wf_input',task_id='task_input',
            execution_assignment_id='asg_input',patch_digest='sha256:'+'1'*64)
    assert caught.value is original


@POSIX_FIXTURE_IO
@pytest.mark.parametrize('native',[False,True])
def test_public_input_reaches_single_executor_and_independent_verifier(tmp_path,monkeypatch,native):
    from minicode import subagent_model_routing
    import minicode.workflow_runner as runner
    from test_workflow_v2_structured_routing import _request,_run
    (tmp_path/'allowed.txt').write_text('before')
    (tmp_path/'test_public.py').write_text(
        "from pathlib import Path\nimport hashlib\n"
        "def test_archived_public_input():\n"
        "    assert Path('allowed.txt').read_text() == 'after'\n"
        f"    assert hashlib.sha256(Path({FIXTURE!r}).read_bytes()).hexdigest()=={SHA!r}\n"
        "    assert not Path('.env').exists()\n"
        "    assert not Path('.mini-code/settings.json').exists()\n")
    (tmp_path/'.env').write_text('synthetic-secret')
    (tmp_path/'.mini-code').mkdir();(tmp_path/'.mini-code/settings.json').write_text('synthetic-runtime')
    paths=[];seen=[];reviewer_seen=[];fixture_receipts=[]
    def command(_argv,**kwargs):
        paths.append(Path(kwargs['cwd']))
        return subprocess.run([sys.executable,'-m','pytest','-q','test_public.py'],**kwargs)
    monkeypatch.setattr(runner,'_deterministic_verifier_for_context',lambda *_a,**_k:DeterministicVerifier(command_runner=command))
    monkeypatch.setattr(PermissionManager,'ensure_command',lambda *_a,**_k:None)
    class Model(ModelAdapter):
        def next(self,messages,**kwargs):
            seen.append(list(messages))
            if len(seen)==1:
                return AgentStep(type='tool_calls',calls=[{'id':'edit','toolName':'edit_file','input':{'path':'allowed.txt','old':'before','new':'after'}}])
            assert len(seen)==2
            receipt=next(m for m in messages if m.get('toolUseId')=='edit' and m.get('role')=='tool_result')
            assert '1 passed' in receipt['content']
            assert fixture_receipts and fixture_receipts[0]['beforeAfterDigestMatched']
            if native:
                return AgentStep(type='tool_calls',calls=[{'id':'submit','toolName':'executor_submit','input':
                    {'status':'completed','steps':[{'step':1,'status':'completed'}],'deviations':[],'blockers':[]}}])
            return AgentStep(type='assistant',kind='final',content='Updated and public checks passed.')
    class Reviewer(ModelAdapter):
        def next(self,messages,**kwargs):
            # This fixture measures public-input propagation through the real
            # implementation check and formal Verifier. Policy 4 also requires
            # a separate Reviewer; it must not reuse the Executor's two steps.
            reviewer_seen.append(list(messages))
            assert len(reviewer_seen)==1
            assert (tmp_path/'allowed.txt').read_text()=='before'
            assert len(paths)==2 and len(fixture_receipts)==2
            assert all(r['beforeAfterDigestMatched'] for r in fixture_receipts)
            prompt=next(m['content'] for m in messages if m.get('role')=='user'
                        and '\n\nFROZEN DIFF:\n' in str(m.get('content','')))
            frozen=json.loads(prompt.split('\n\nFROZEN DIFF:\n',1)[1].split('\n\n',1)[0])
            verification=json.loads(prompt.split('\n\nVERIFICATION RESULT:\n',1)[1].split('\n\n',1)[0])
            assert frozen['complete'] and '-before' in frozen['diff'] and '+after' in frozen['diff']
            assert verification['status']=='passed'
            assert verification['patchDigest']==frozen['patchDigest']
            return AgentStep(type='tool_calls',calls=[{'id':'review','toolName':'review_submit','input':{
                'verdict':'approved','criteria':[{'criterionId':'ac1','status':'satisfied'}],
                'blockingFindings':[],'warnings':[],'requestedChecks':[],'confidence':'high'}}])
    def model_factory(_runtime,_agent_type,*,tools):
        return Reviewer() if any(t.name=='review_submit' for t in tools.list()) else Model()
    with bind_verifier_public_inputs(_bound(), receipt_sink=fixture_receipts.append):
        if native:
            monkeypatch.setattr('minicode.tools.create_default_tool_registry',lambda *_a,**_k:ToolRegistry([edit_file_tool]))
            monkeypatch.setattr(subagent_model_routing,'create_subagent_model_adapter',model_factory)
            result=_run(tmp_path,_request(risk="low", complex=False),monkeypatch,commit_audit_waived=True)
            assert result.ok,result.output
        else:
            result=run_agent_turn(model=Model(),tools=ToolRegistry([edit_file_tool]),messages=[{'role':'user','content':'Fix allowed.txt.'}],
                cwd=str(tmp_path),implementation_task=True,implementation_spec=ImplementationSpec(('allowed.txt',),
                    (VerificationCheck('public','tests',('pytest','-q','test_public.py'),10),),'Fix allowed.txt'),
                enable_work_chain=False,max_steps=8)
            assert result[-1]['content']=='Updated and public checks passed.'
    assert len(seen)==2
    assert len(reviewer_seen)==(1 if native else 0)
    assert len(paths)==(2 if native else 1)
    assert len(fixture_receipts)==len(paths)
    assert all(r["publicVerificationFixtures"][0]["sha256"] == SHA for r in fixture_receipts)
    assert all(path!=tmp_path and not path.exists() for path in paths)
    assert (tmp_path/'allowed.txt').read_text()=='after'
    assert not (tmp_path/FIXTURE).exists(), 'verification input must not be copied into delivered bytes'
    assert (tmp_path/'.mini-code/settings.json').read_text()=='synthetic-runtime'
    assert (tmp_path/'.env').read_text()=='synthetic-secret'
    assert public_fixture_manifest(_document())==[{'case':'real-vision-budget','path':FIXTURE,'sha256':SHA,'bytes':1430}]


def _audit_fixture(tmp_path):
    from types import SimpleNamespace
    from tests.test_implementation_progress_live import _preflight_fixture
    from scripts import public_verification_fixtures as inputs
    document=_document()
    document['cases'][0]['verification_argv']=['python3','-m','pytest','-q','tests/test_public.py']
    (tmp_path/'tasks.json').write_text(json.dumps(document))
    _preflight_fixture(document,tmp_path/'public-preflight')
    raw=(tmp_path/'public-preflight/receipt.json').read_bytes()
    import hashlib
    manifest={'publicVerificationFixturePolicy':inputs.PUBLIC_FIXTURE_POLICY,
              'publicVerificationFixtures':inputs.public_fixture_manifest(document),
              'publicPreflightSha256':hashlib.sha256(raw).hexdigest()}
    bundle=SimpleNamespace(read=lambda _name:Path(inputs.__file__).read_bytes())
    return document,manifest,bundle


def test_independent_auditor_requires_new_policy_but_accepts_old_archives_without_it(tmp_path):
    from scripts.audit_implementation_progress import Audit
    document,manifest,bundle=_audit_fixture(tmp_path)
    new=Audit(tmp_path)
    new.public_fixture_preflight(manifest,bundle,'shared-public-checks-source-context-and-fixtures-v2')
    assert not new.issues
    missing=dict(manifest);missing.pop('publicVerificationFixturePolicy')
    invalid=Audit(tmp_path)
    invalid.public_fixture_preflight(missing,bundle,'shared-public-checks-source-context-and-fixtures-v2')
    assert any(i['code']=='public_fixture_policy' for i in invalid.issues)
    old=Audit(tmp_path/'nonexistent')
    old.public_fixture_preflight({},None,'shared-public-checks-and-source-context-v1')
    assert not old.issues, 'old frozen v1 archives do not retroactively require new inputs'


@pytest.mark.parametrize('mutation',['missing_case','failed','changed_argv','changed_fixture'])
def test_preflight_rejects_incomplete_or_changed_public_baseline(tmp_path,mutation):
    import hashlib
    from scripts.public_verification_fixtures import validate_public_preflight
    document,manifest,_bundle=_audit_fixture(tmp_path)
    path=tmp_path/'public-preflight/receipt.json'
    validate_public_preflight(document,path,manifest['publicPreflightSha256'])
    report=json.loads(path.read_text())
    if mutation=='missing_case':report['cases'].clear()
    elif mutation=='failed':report['cases'][0]['evidence'][0]['exitCode']=1
    elif mutation=='changed_argv':report['cases'][0]['argv']=['python3','-m','pytest','-q','different.py']
    else:report['fixtures'][0]['sha256']='0'*64
    path.write_text(json.dumps(report))
    with pytest.raises(PublicFixtureError):validate_public_preflight(document,path,hashlib.sha256(path.read_bytes()).hexdigest())


def test_freeze_refuses_failed_public_baseline_before_archive_or_paid_execution(tmp_path,monkeypatch):
    from scripts import run_implementation_progress_live as live
    from tests.test_implementation_progress_live import fixture_source,offline_runtime
    source,output=tmp_path/'source',tmp_path/'output'
    fixture_source(source)
    live.prepare(output,'engineering',source)
    offline_runtime(monkeypatch)
    observed=[]
    def failed(document,report_root,*,runtime):
        assert runtime == {"model": "glm-5.3-flash"}
        observed.extend(c['id'] for c in document['cases'])
        return {'passed':False}
    monkeypatch.setattr(live,'run_public_preflight',failed)
    with pytest.raises(ValueError,match='public baseline preflight failed'):
        live.freeze(output)
    assert observed==list(live.SMALL_CASES)
    assert not (output/'manifest.json').exists()
    assert not (output/'frozen-code.zip').exists()


@pytest.mark.parametrize('missing', ['O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK', 'dir_fd', 'supports_dir_fd'])
def test_unsupported_fixture_io_fails_closed_before_any_copy(tmp_path, monkeypatch, missing):
    if missing == 'dir_fd':
        monkeypatch.setattr(os, 'supports_dir_fd', set())
    else:
        monkeypatch.delattr(os, missing, raising=False)
    with pytest.raises(PublicFixtureError, match='requires POSIX'):
        with bind_verifier_public_inputs(_bound(), receipt_sink=lambda _doc: None):
            pytest.fail('unsupported projection must not be activated')
    assert not (tmp_path / '.mini-code').exists()


@POSIX_FIXTURE_IO
@pytest.mark.parametrize('replacement', ['fifo', 'socket'])
def test_postcheck_rejects_special_files_without_hanging(tmp_path, replacement):
    # A subprocess bounds the regression itself: a blocking FIFO open in the
    # post-check verifier must fail this test instead of hanging the test suite.
    program = r"""
from pathlib import Path
import socket
import subprocess
import sys
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from scripts.public_verification_fixtures import (PublicFixtureError,
    bind_public_fixtures, bind_verifier_public_inputs)
root = Path(sys.argv[1])
root.mkdir()
name = '.mini-code/agents/visual-judge.md'
content = Path('tests/fixtures/public_visual_judge.md').read_text()
bindings = bind_public_fixtures({'cases': [{'id': 'real-vision-budget',
    'files': {name: content}}]}, 'real-vision-budget')
opened = []
evidence = []
def command(argv, **kwargs):
    target = root / name
    target.unlink()
    if sys.argv[2] == 'fifo':
        import os
        os.mkfifo(target)
    else:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.bind(str(target))
        opened.append(connection)
    print('entered post-check verification', flush=True)
    return subprocess.CompletedProcess(argv, 0, 'passed', '')
try:
    with bind_verifier_public_inputs(bindings, receipt_sink=lambda doc: None):
        DeterministicVerifier(command_runner=command).run(
            checks=(VerificationCheck('public', 'tests', ('pytest', '-q'), 1),),
            cwd=str(root), permission_cwd=str(root.parent), workflow_id='wf_input',
            task_id='task_input', execution_assignment_id='asg_input',
            patch_digest='sha256:' + '1' * 64,
            evidence_sink=lambda key, doc: evidence.append(doc))
except (PublicFixtureError, OSError):
    assert not evidence, 'replacement must not publish passing evidence'
    print('rejected special file without publishing evidence', flush=True)
else:
    raise AssertionError('special replacement accepted')
finally:
    for connection in opened:
        connection.close()
"""
    result = subprocess.run([sys.executable, '-c', program, str(tmp_path / 'clone'), replacement],
                            cwd=ROOT, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'rejected special file without publishing evidence' in result.stdout


def test_public_preflight_resolves_the_actual_trial_runtime(tmp_path, monkeypatch):
    from minicode import verifier_sandbox
    from scripts.public_verification_fixtures import run_public_preflight
    runtime = object()
    observed = []
    class CapabilityProbeReached(Exception):
        pass
    def resolve(value):
        observed.append(value)
        raise CapabilityProbeReached
    monkeypatch.setattr(verifier_sandbox, 'resolve_verifier_host_capabilities', resolve)
    document = {'cases': [{'id': 'real-concurrent-context', 'files': {},
                          'verification_argv': ['python3', '-m', 'pytest', '-q'],
                          'authorized_paths': ['source.py']}]}
    with pytest.raises(CapabilityProbeReached):
        run_public_preflight(document, tmp_path, runtime=runtime)
    assert observed == [runtime]
