"""Late supported implementation and healthy final streams keep their handoff."""
from pathlib import Path
import subprocess
import sys
import time

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.executor_verification import ExecutorCheckCapability
from minicode.implementation_execution import ImplementationSpec
from minicode.implementation_phases import ImplementationPhase, ImplementationPhaseController
from minicode.model_call_control import checkpoint_model_call, bounded_request_timeout
from minicode.permissions import PermissionManager
from minicode.task_outcome import AgentOutcomeCapture
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep, ModelAdapter


def _call(name, identity, **data):
    return {"id": identity, "toolName": name, "input": data}


def _assert_local_production():
    assert Path(run_agent_turn.__code__.co_filename).resolve().parents[1] == Path(__file__).resolve().parents[1]


def _real_checks(monkeypatch):
    import minicode.workflow_runner as runner
    commands = []
    def command(_argv, **kwargs):
        commands.append(Path(kwargs['cwd']))
        return subprocess.run([sys.executable, '-m', 'pytest', '-q'], **kwargs)
    monkeypatch.setattr(runner, '_deterministic_verifier_for_context',
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command))
    monkeypatch.setattr(PermissionManager, 'ensure_command', lambda *_a, **_k: None)
    return commands


@pytest.mark.parametrize('native', [False, True])
def test_first_source_edit_in_finalization_gets_real_checks_then_final_handoff(tmp_path, monkeypatch, native):
    _assert_local_production()
    from minicode import subagent_model_routing
    from test_workflow_v2_structured_routing import _request, _run
    (tmp_path/'allowed.txt').write_text('before')
    (tmp_path/'test_acceptance.py').write_text(
        "from pathlib import Path\ndef test_value(): assert Path('allowed.txt').read_text() == 'after'\n")
    commands = _real_checks(monkeypatch)
    seen = []
    actual_request = ImplementationPhaseController.request
    def finalize_after_read(self, snapshot, **kwargs):
        if self.calls == 1:
            assert not self.implementation_started
            # The native completion gate now marks missing approved receipts
            # as pending verification before any check has run.
            assert self._verification_failed is native
            self._advance(ImplementationPhase.FINALIZE)
        return actual_request(self, snapshot, **kwargs)
    monkeypatch.setattr(ImplementationPhaseController, 'request', finalize_after_read)
    registry = lambda: ToolRegistry([read_file_tool, edit_file_tool, write_file_tool])
    class Model(ModelAdapter):
        def next(self, messages, tools_override=None, **kwargs):
            names = {tool.name for tool in tools_override.list()}
            seen.append((names, list(messages)))
            turn = len(seen)
            if turn == 1:
                assert not commands and (tmp_path/'allowed.txt').read_text() == 'before'
                return AgentStep(type='tool_calls', calls=[_call('read_file','read',path='allowed.txt')])
            if turn == 2:
                assert not commands, 'there is no prior failed check to unlock editing'
                assert any(m.get('toolUseId') == 'read' and not m.get('isError') for m in messages)
                assert any(m.get('_implementation_phase') and 'finalize' in m['content'] for m in messages)
                assert 'edit_file' in names, 'first checked source edit was hidden at finalization'
                assert 'read_file' not in names and 'grep_files' not in names
                return AgentStep(type='tool_calls', calls=[_call('edit_file','late-edit',path='allowed.txt',old='before',new='after')])
            assert turn == 3, 'no added round is needed for checked first edit and final handoff'
            assert names == ({'executor_submit'} if native else set())
            receipt = next(m for m in messages if m.get('role') == 'tool_result' and m.get('toolUseId') == 'late-edit')
            assert not receipt.get('isError') and '"status": "passed"' in receipt['content'] and '1 passed' in receipt['content']
            if native:
                assert (tmp_path/'allowed.txt').read_text() == 'before', 'feedback has not promoted child bytes'
                return AgentStep(type='tool_calls', calls=[_call('executor_submit','submit',status='completed',
                    steps=[{'step':1,'status':'completed'}],deviations=[],blockers=[])])
            return AgentStep(type='assistant',kind='final',content='Updated allowed.txt; the public test passed.')
    if native:
        from minicode.tools import task as task_module
        from test_workflow_v2_structured_routing import _write_reviewer_result
        original_worker = task_module._run
        reviews = []
        def worker(input_data, context):
            # This case isolates late Executor edits and real host checks.
            # Reviewer model/repair behavior has its own completion-coverage
            # and native-budget regressions; do not send it this Executor fake.
            if context._logical_agent_role == 'reviewer':
                assert (tmp_path/'allowed.txt').read_text() == 'before'
                assert (Path(context.cwd)/'allowed.txt').read_text() == 'after'
                assert len(commands) == 2
                _write_reviewer_result(input_data, context)
                reviews.append(context._logical_agent_role)
                return ToolResult(ok=True, output='Reviewed the checked candidate.')
            return original_worker(input_data, context)
        monkeypatch.setattr(task_module, '_run', worker)
        monkeypatch.setattr('minicode.tools.create_default_tool_registry',lambda *_a,**_k: registry())
        monkeypatch.setattr(subagent_model_routing,'create_subagent_model_adapter',lambda *_a,**_k: Model())
        result = _run(tmp_path,_request(risk='low',complex=False),monkeypatch,commit_audit_waived=True)
        assert result.ok,result.output
        assert reviews == ['reviewer']
    else:
        outcome = AgentOutcomeCapture()
        messages = run_agent_turn(model=Model(),tools=registry(),messages=[{'role':'user','content':'Fix allowed.txt and run the public test.'}],
            cwd=str(tmp_path),implementation_task=True,implementation_spec=ImplementationSpec(('allowed.txt',),
                (VerificationCheck('public','tests',('python3','-m','pytest','-q'),20),),'Fix allowed.txt'),
            enable_work_chain=False,max_steps=10,outcome_capture=outcome)
        assert messages[-1]['content'] == 'Updated allowed.txt; the public test passed.'
        assert outcome.outcome.status == 'success'
    assert len(seen) == 3
    assert (tmp_path/'allowed.txt').read_text() == 'after'
    assert len(commands) == (2 if native else 1), 'native promotion still requires an independent Verifier run'
    assert all(path != tmp_path and not path.exists() for path in commands)


@pytest.mark.parametrize('capability', ['absent','empty','closed','unbound'])
def test_first_final_edit_requires_live_bound_nonempty_host_checks(tmp_path, monkeypatch, capability):
    (tmp_path/'app.py').write_text('value=1\n')
    registry = ToolRegistry([read_file_tool,edit_file_tool,write_file_tool])
    checks = () if capability == 'empty' else (VerificationCheck('public','tests',('pytest','-q'),20),)
    cap = ExecutorCheckCapability(checks,lambda *_a: {'status':'passed'})
    if capability in {'empty','closed'}:
        registry = cap.bind_registry(registry)
    if capability == 'closed':
        cap.close()
    seen=[]
    class Model(ModelAdapter):
        def next(self,messages,tools_override=None,**kwargs):
            seen.append({tool.name for tool in tools_override.list()})
            if len(seen)==1:
                return AgentStep(type='tool_calls',calls=[_call('read_file','read',path='app.py')])
            assert 'edit_file' not in seen[-1] and 'write_file' not in seen[-1]
            return AgentStep(type='assistant',kind='final',content='No checked edit was made.')
    run_agent_turn(model=Model(),tools=registry,messages=[{'role':'user','content':'Fix app.py.'}],cwd=str(tmp_path),
        implementation_task=True,enable_work_chain=False,max_steps=3)
    assert len(seen)==2 and (tmp_path/'app.py').read_text()=='value=1\n'


def test_healthy_final_stream_can_exceed_45_seconds_and_preserves_handoff(tmp_path,monkeypatch):
    _assert_local_production()
    import minicode.agent_loop as loop
    import minicode.model_call_control as model_control
    (tmp_path/'app.py').write_text('value=1\n')
    start=time.monotonic()
    class Clock:
        current=start
        def monotonic(self): return self.current
        def __getattr__(self,name): return getattr(time,name)
    clock=Clock()
    monkeypatch.setattr(loop,'time',clock)
    monkeypatch.setattr(model_control,'time',clock)
    observations=[]
    checks=[]
    chunks=[]
    def check(_data,_context):
        checks.append(clock.current)
        return ToolResult(ok=True,output='real host check result available')
    registry=ToolRegistry([read_file_tool,edit_file_tool,ToolDefinition(name='executor_check',description='observe result',
        input_schema={'type':'object'},validator=lambda v:v,run=check)])
    class Model(ModelAdapter):
        def next(self,messages,tools_override=None,deadline_monotonic=None,on_stream_chunk=None,**kwargs):
            observations.append((clock.current,deadline_monotonic,{t.name for t in tools_override.list()}))
            turn=len(observations)
            if turn==1:
                # The scripted provider's earlier read-only activity is summarized
                # by elapsed logical time; no sleep or actual HTTP request is used.
                clock.current=start+141
                return AgentStep(type='tool_calls',calls=[_call('read_file','read',path='app.py')])
            if turn==2:
                assert bounded_request_timeout(180,deadline_monotonic=deadline_monotonic)<=180
                for _ in range(6):
                    clock.current+=10
                    if on_stream_chunk: on_stream_chunk('healthy stream event')
                    checkpoint_model_call(deadline_monotonic=deadline_monotonic)
                return AgentStep(type='tool_calls',calls=[_call('executor_check','checked')])
            assert turn==3 and not tools_override.list()
            return AgentStep(type='assistant',kind='final',content='Delivered the observed result.')
    # Force the first investigation read to have the normal implementation
    # deadline; finalization then begins naturally at the existing call ceiling.
    actual_request=ImplementationPhaseController.request
    def allocated_read(self,snapshot,**kwargs):
        if self.calls==0:self._advance(ImplementationPhase.IMPLEMENT)
        return actual_request(self,snapshot,**kwargs)
    monkeypatch.setattr(ImplementationPhaseController,'request',allocated_read)
    messages=run_agent_turn(model=Model(),tools=registry,messages=[{'role':'user','content':'Fix app.py.'}],cwd=str(tmp_path),
        implementation_task=True,enable_work_chain=False,max_steps=3,deadline_monotonic=start+600,on_assistant_stream_chunk=chunks.append)
    assert len(observations)==3 and len(chunks)==6
    assert len(checks)==1, 'a healthy 60-second final stream was cut off at the unrelated 45-second cap'
    assert any(m.get('toolUseId')=='checked' and not m.get('isError') for m in messages)
    optional_started,optional_deadline,_=observations[1]
    handoff_started,handoff_deadline,_=observations[2]
    assert optional_deadline-optional_started == pytest.approx(180)
    assert handoff_started < optional_deadline < handoff_deadline <= start+600
    assert messages[-1]['content']=='Delivered the observed result.'


@pytest.mark.parametrize('remaining',[2,3])
def test_final_time_allocation_preserves_request_ceiling_and_fixed_handoff(remaining):
    budget=AgentTurnBudget(max_model_calls=24,max_total_tokens=500000)
    controller=ImplementationPhaseController(snapshot=budget.snapshot(),max_steps=24,now=0,deadline=600,
        allow_test_completion=remaining==3)
    controller._advance(ImplementationPhase.FINALIZE)
    now=141
    fixed_handoff=controller.handoff_start
    while remaining:
        request=controller.request(budget.snapshot(),estimated_input=1000,now=now)
        boundary=600 if remaining==1 else fixed_handoff
        assert request.deadline == pytest.approx(min(now+180,boundary))
        assert request.handoff_only == (remaining==1)
        assert request.deadline<=600
        assert controller.handoff_start==fixed_handoff
        controller.admitted()
        now=request.deadline
        remaining-=1
    assert controller.exhausted and now<=600
