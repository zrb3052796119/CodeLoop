"""Terminal control flow cannot erase already observed private tool results."""
from pathlib import Path
import functools
import json
import sys

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.subagent_journal import SubagentRunJournal, new_subagent_id
from minicode.tooling import ToolContext, ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.task import task_tool
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from minicode.types import AgentStep, ModelAdapter


def journal_context(tmp_path, **kwargs):
    workspace=tmp_path/'workspace';workspace.mkdir()
    directory=tmp_path/('run_'+'9'*32);directory.mkdir()
    journal=SubagentRunJournal(directory)
    class Sink:
        def open_subagent_journal(self):return journal
        def emit(self,*args,**kwargs):pass
    return workspace,directory,journal,ToolContext(cwd=str(workspace),_runtime={'model':'fake'},_event_sink=Sink(),**kwargs)


def records(directory):
    return [json.loads(line) for path in directory.rglob('*.tools.jsonl') for line in path.read_text().splitlines()]


def test_cancel_after_actual_tool_return_retains_private_result_without_delivery(tmp_path,monkeypatch):
    from minicode import subagent_model_routing
    token=TurnCancellationToken('turn_'+'8'*32)
    workspace,directory,journal,context=journal_context(tmp_path,_cancellation_token=token)
    executed=[];seen=[]
    def read(value,context):
        executed.append(value)
        token.request()
        return ToolResult(ok=False,output='actual returned diagnostic: FAILED approved fixture')
    tool=ToolDefinition(name='read_file',description='local probe',input_schema={'type':'object'},validator=lambda x:x,run=read)
    class Model(ModelAdapter):
        def next(self,messages,**kwargs):
            seen.append(list(messages))
            assert len(seen)==1,'cancelled result must never reach a follow-up model request'
            return AgentStep(type='tool_calls',calls=[{'id':'once','toolName':'read_file','input':{'path':'fixture.py'}}])
    monkeypatch.setattr('minicode.tools.create_default_tool_registry',lambda *a,**k:ToolRegistry([tool]))
    monkeypatch.setattr(subagent_model_routing,'create_subagent_model_adapter',lambda *a,**k:Model())
    with pytest.raises(TurnCancellationRequested) as caught:
        task_tool.run({'description':'inspect','prompt':'Read fixture.py','agent_type':'explore','_subagent_id':new_subagent_id()},context)
    assert caught.value.turn_id==token.turn_id
    assert len(executed)==len(seen)==1
    evidence=records(directory)
    assert [r['type'] for r in evidence]==['tool.started','tool.finished'], 'cancellation discarded a tool result already returned by the actual tool'
    assert evidence[0]['invocationId']==evidence[1]['invocationId']
    assert evidence[1]['output']=='actual returned diagnostic: FAILED approved fixture'
    assert evidence[1]['isError'] is True
    assert journal.list_runs()[0].outcome=='cancelled'


WORKER = r'''
import json, sys
from pathlib import Path
import minicode.tools
import minicode.tools.task as task
import minicode.subagent_model_routing as routing
from minicode.tooling import ToolRegistry
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep,ModelAdapter
from minicode.subagent_worker import main
class Model(ModelAdapter):
    def next(self,messages,**kwargs):
        return AgentStep(type='tool_calls',calls=[{'id':'missing','toolName':'read_file','input':{'path':'approved-missing.py'}}])
minicode.tools.create_default_tool_registry=lambda *a,**k:ToolRegistry([read_file_tool])
routing.create_subagent_model_adapter=lambda *a,**k:Model()
task._build_sub_agent_system_prompt=lambda **k:('Read the approved fixture.',None)
request=Path(sys.argv[sys.argv.index('--request')+1])
Path(json.loads(request.read_text())['cwd'],'transport-root.txt').write_text(str(request.parent))
raise SystemExit(main())
'''


def test_real_general_worker_budget_exit_keeps_tool_evidence_after_transport_cleanup(tmp_path,monkeypatch):
    import minicode.tools.task as task
    from minicode.subagent_supervisor import run_subagent_process
    workspace,directory,journal,context=journal_context(tmp_path,_agent_budget=AgentTurnBudget(max_model_calls=2,max_total_tokens=500000))
    monkeypatch.setattr(task,'run_subagent_process',functools.partial(run_subagent_process,command=[sys.executable,'-c',WORKER]))
    result=task_tool.run({'description':'inspect','prompt':'Read approved-missing.py','agent_type':'general','_subagent_id':new_subagent_id()},context)
    assert not result.ok
    assert journal.list_runs()[0].outcome=='budget_exceeded',result.output
    transport=Path((workspace/'transport-root.txt').read_text())
    assert not transport.exists(),'supervisor must retain original transport cleanup'
    evidence=records(directory)
    assert [r['type'] for r in evidence]==['tool.started','tool.finished'], 'general subprocess lost completed tool evidence on its budget terminal'
    assert evidence[0]['invocationId']==evidence[1]['invocationId']
    assert evidence[0]['input']=={'path':'approved-missing.py'}
    assert evidence[1]['isError'] is True
    assert 'error[not_found]' in evidence[1]['output']
    assert 'approved-missing.py' not in repr(journal.list_events(journal.list_runs()[0].subagent_id))


@pytest.mark.parametrize('terminal',['timeout','cancel'])
def test_real_general_hard_terminal_keeps_completed_tools_before_kill(tmp_path,monkeypatch,terminal):
    import threading
    import time
    import minicode.tools.task as task
    from minicode.subagent_supervisor import run_subagent_process
    token=TurnCancellationToken('turn_'+'7'*32)
    workspace,directory,journal,context=journal_context(tmp_path,_cancellation_token=token,
        _agent_budget=AgentTurnBudget(max_model_calls=5,max_total_tokens=500000),
        _task_deadline_monotonic=time.monotonic()+4)
    worker=WORKER.replace('class Model(ModelAdapter):\n', 'class Model(ModelAdapter):\n    calls=0\n').replace(
        '    def next(self,messages,**kwargs):\n',
        "    def next(self,messages,**kwargs):\n        self.calls+=1\n        if self.calls==2:\n            Path('ready-to-kill').write_text('ready')\n            import time; time.sleep(60)\n")
    monkeypatch.setattr(task,'run_subagent_process',functools.partial(run_subagent_process,
        command=[sys.executable,'-c',worker],kill_grace_seconds=0.05))
    def cancel_after_first_tool():
        end=time.monotonic()+6
        while time.monotonic()<end:
            if (workspace/'ready-to-kill').exists():
                token.request();return
            time.sleep(0.01)
    thread=threading.Thread(target=cancel_after_first_tool,daemon=True)
    if terminal=='cancel':thread.start()
    if terminal=='cancel':
        with pytest.raises(TurnCancellationRequested):
            task_tool.run({'description':'inspect','prompt':'Read approved-missing.py','agent_type':'general','_subagent_id':new_subagent_id()},context)
        thread.join(timeout=1)
    else:
        result=task_tool.run({'description':'inspect','prompt':'Read approved-missing.py','agent_type':'general','_subagent_id':new_subagent_id()},context)
        assert not result.ok and 'timed_out' in result.output
    assert (workspace/'ready-to-kill').exists(),'actual worker completed its first tool before the hard terminal'
    assert journal.list_runs()[0].outcome==('cancelled' if terminal=='cancel' else 'timed_out')
    assert not Path((workspace/'transport-root.txt').read_text()).exists()
    evidence=records(directory)
    assert [r['type'] for r in evidence]==['tool.started','tool.finished']
    assert evidence[0]['invocationId']==evidence[1]['invocationId']
    assert 'error[not_found]' in evidence[1]['output']


def test_parallel_same_name_calls_keep_their_own_private_pairs(tmp_path):
    from minicode.agent_loop import run_agent_turn
    from minicode.tools.read_file import read_file_tool
    from minicode.tools.task import _SubagentJournalSink
    workspace,directory,journal,context=journal_context(tmp_path)
    for name in ['left','right']:(workspace/(name+'.txt')).write_text(name+'-unique-body')
    class Model(ModelAdapter):
        count=0
        def next(self,messages,**kwargs):
            self.count+=1
            if self.count==1:
                return AgentStep(type='tool_calls',calls=[{'id':name,'toolName':'read_file','input':{'path':name+'.txt'}} for name in ['left','right']])
            return AgentStep(type='assistant',kind='final',content='Done')
    run_agent_turn(model=Model(),tools=ToolRegistry([read_file_tool]),messages=[{'role':'user','content':'Read both.'}],
                   cwd=str(workspace),event_sink=_SubagentJournalSink(journal,new_subagent_id()),enable_work_chain=False,max_steps=4)
    evidence=records(directory)
    assert len(evidence)==4
    for start in [r for r in evidence if r['type']=='tool.started']:
        finish=next(r for r in evidence if r['type']=='tool.finished' and r['invocationId']==start['invocationId'])
        assert start['input']['path'].removesuffix('.txt')+'-unique-body' in finish['output']


@pytest.mark.skipif(sys.platform=='win32',reason='POSIX evidence transport')
@pytest.mark.parametrize('unsafe',['symlink','fifo','directory','oversize','foreign_path'])
def test_parent_replay_rejects_unsafe_child_spool(tmp_path,unsafe):
    import os
    from minicode.subagent_tool_evidence import open_transport_directory,replay_tool_evidence
    target=tmp_path/'tool-evidence.jsonl';observed=[]
    if unsafe=='symlink':
        other=tmp_path/'outside';other.write_text('not an evidence file');target.symlink_to(other)
    elif unsafe=='fifo':os.mkfifo(target)
    elif unsafe=='directory':target.mkdir()
    elif unsafe=='oversize':
        with target.open('wb') as stream:stream.truncate(4*1024*1024+1)
    else:
        target.write_text(json.dumps({'tool_name':'read_file','phase':'finished','tool_input':None,'output':'x',
                         'is_error':False,'invocation_id':'tool-1','path':'../parent-journal'})+'\n')
    fd=open_transport_directory(tmp_path)
    try:replay_tool_evidence(fd,lambda **record:observed.append(record))
    finally:os.close(fd)
    assert observed==[]


def test_parent_replay_redacts_again_and_bad_sink_does_not_change_terminal(tmp_path):
    import os
    from minicode.subagent_tool_evidence import open_transport_directory,replay_tool_evidence
    from minicode.tools.task import _SubagentJournalSink
    transport=tmp_path/'transport';transport.mkdir()
    _,directory,journal,_=journal_context(tmp_path)
    (transport/'tool-evidence.jsonl').write_text(json.dumps({'tool_name':'read_file','phase':'finished',
        'tool_input':None,'output':'Authorization: Bearer synthetic-secret','is_error':True,'invocation_id':'tool-1'})+'\n')
    fd=open_transport_directory(transport)
    try:
        replay_tool_evidence(fd,_SubagentJournalSink(journal,new_subagent_id()).record_tool_evidence)
        def broken(**record):raise RuntimeError('broken optional diagnostic writer')
        replay_tool_evidence(fd,broken)
    finally:os.close(fd)
    assert 'synthetic-secret' not in json.dumps(records(directory))
    assert records(directory)[0]['output']=='Authorization: Bearer [REDACTED]'
