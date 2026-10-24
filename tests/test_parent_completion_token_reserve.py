"""Actual V16 transcript/usage replay: parent retries must retain final delivery."""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn, _estimated_context_tokens
from minicode.tooling import ToolContext, ToolDefinition, ToolRegistry, ToolResult
from minicode.types import AgentStep, ModelAdapter, ModelUsage


def _usage(record):
    return ModelUsage(input_tokens=record['usage']['input_tokens'],
        output_tokens=record['usage']['output_tokens'], source='provider')


def test_v16_parent_retries_leave_real_tool_free_closing_on_shared_500k(tmp_path):
    frozen=json.loads((Path(__file__).parent/'fixtures/parent_completion_v16.json').read_text())
    history=frozen['messages']; requests=frozen['requests']
    budget=AgentTurnBudget(max_total_tokens=500000,max_model_calls=40)
    for record in requests[:19]:
        lease=budget.reserve_model_call()
        budget.record_model_call(input_tokens=record['usage']['input_tokens'],
            output_tokens=record['usage']['output_tokens'],reservation=lease)
    assert budget.snapshot().used_total_tokens==420695
    executed=[]; closing=[]; physical=[]
    parent_steps=iter([(20,7,11),(23,11,15),(24,15,19)])
    todo_receipts=iter(history[i]['content'] for i in (8,12,16))
    def original_tool_output(index):
        # The loop re-adds its own recorded recovery note at delivery.
        return history[index]['content'].rsplit('\n\n[System note:',1)[0]

    def tool(inputs,context):
        # The second workflow really consumed requests 21/22; later workflows
        # made zero physical requests. Use those completed provider receipts.
        name=inputs.get('description')
        executed.append(name)
        if name==history[9]['input']['description']:
            for record in requests[20:22]:
                lease=context._agent_budget.reserve_model_call(record['chargedTokens'])
                context._agent_budget.record_model_call(input_tokens=record['usage']['input_tokens'],
                    output_tokens=record['usage']['output_tokens'],reservation=lease)
                physical.append(record['call'])
            return ToolResult(ok=False,output=original_tool_output(10))
        if name==history[13]['input']['description']:
            return ToolResult(ok=False,output=original_tool_output(14))
        return ToolResult(ok=False,output=original_tool_output(18))

    class Parent(ModelAdapter):
        runtime={'maxOutputTokens':8192}
        def next(self,messages,**options):
            if options.get('disable_tools'):
                assert options['max_output_tokens_override']==2048
                closing.append(list(messages)); physical.append('closing')
                return AgentStep(type='assistant',kind='final',content='Checks failed; no changes were committed.',
                    usage=ModelUsage(input_tokens=_estimated_context_tokens(messages),output_tokens=128,source='provider'))
            call,start,end=next(parent_steps)
            physical.append(call)
            group=[m for m in history[start:end] if m['role']=='assistant_tool_call']
            return AgentStep(type='tool_calls',content=group[0].get('content',''),
                reasoningContent=group[0].get('reasoningContent'),
                calls=[{'id':m['toolUseId'],'toolName':m['toolName'],'input':m['input']} for m in group],
                usage=_usage(requests[call-1]))

    tools=ToolRegistry([
        ToolDefinition(name='task',description='delegate',input_schema={'type':'object'},validator=lambda x:x,run=tool),
        ToolDefinition(name='todo_write',description='track',input_schema={'type':'object'},validator=lambda x:x,
            run=lambda inputs,context:ToolResult(ok=True,output=next(todo_receipts)))])
    result=run_agent_turn(model=Parent(),tools=tools,messages=history[:7],cwd=str(tmp_path),
        runtime={'maxOutputTokens':8192},agent_budget=budget,max_steps=40,
        enable_work_chain=False,enable_implementation_phases=False,enable_bounded_completion=True)
    assert len(closing)==1,'V16 spent protected parent handoff on retries and never called the closing model'
    assert physical==[20,21,22,'closing'], 'unaffordable replacement workflows must not run'
    assert len(executed)==1
    assert 'Checks failed; no changes were committed.' in result[-1]['content']
    assert result[-1]['_partial']
    assert budget.snapshot().used_model_calls==23
    assert budget.snapshot().used_total_tokens<500000
    assert budget.snapshot().used_total_tokens==460774+_estimated_context_tokens(closing[0])+128
    assert budget.snapshot().reserved_total_tokens==0, 'floors are not duplicate charges or persistent leases'


@pytest.mark.parametrize('enabled',[True,False])
def test_sufficient_budget_keeps_ordinary_answer_and_native_path(tmp_path,enabled):
    class Parent(ModelAdapter):
        runtime={'maxOutputTokens':256}
        def next(self,messages,**options):
            assert not options.get('disable_tools')
            return AgentStep(type='assistant',kind='final',content='Complete.',
                usage=ModelUsage(input_tokens=30,output_tokens=4,source='provider'))
    budget=AgentTurnBudget(max_total_tokens=3000,max_model_calls=5)
    result=run_agent_turn(model=Parent(),tools=ToolRegistry([]),messages=[{'role':'user','content':'Answer.'}],
        cwd=str(tmp_path),runtime={'maxOutputTokens':8192},agent_budget=budget,max_steps=5,
        enable_work_chain=False,enable_bounded_completion=enabled)
    assert result[-1]['content']=='Complete.' and not result[-1].get('_partial')
    assert budget.snapshot().used_total_tokens==34
    assert budget.snapshot().used_model_calls==1
    assert budget.snapshot().reserved_total_tokens==0


def test_actual_workflow_preserves_host_budget_reason_and_does_not_suggest_retry(tmp_path,monkeypatch):
    from minicode import subagent_model_routing
    from minicode.subagent_mailbox import SubagentMailbox
    from minicode.tools import task as task_module
    from minicode.workflow_runner import run_workflow
    from test_workflow_v2_structured_routing import _request
    (tmp_path/'allowed.txt').write_text('before')
    class NeverCalled(ModelAdapter):
        def next(self,*args,**kwargs):
            pytest.fail('insufficient child budget must prevent all provider calls')
    monkeypatch.setattr(subagent_model_routing,'create_subagent_model_adapter',lambda *_a,**_k:NeverCalled())
    quotes=[]
    class ObservedBudget(AgentTurnBudget):
        def reserve_model_call(self, estimated_input_tokens=0, **options):
            quotes.append((estimated_input_tokens,options.get('preserve_total_tokens',0)))
            return super().reserve_model_call(estimated_input_tokens,**options)
    budget=ObservedBudget(max_total_tokens=500000,max_model_calls=40)
    parent_reserve=23185
    # One token cannot fund this nonempty Executor input. The old fixed
    # prefix left 2836 tokens, enough for a legitimate 2699+137 request.
    budget.record_model_call(input_tokens=500000-parent_reserve-1)
    context=ToolContext(cwd=str(tmp_path),_runtime={'model':'fake'},_agent_budget=budget,
        _logical_agent_preserve_total_tokens=parent_reserve,_subagent_mailbox=SubagentMailbox())
    def workflow(_inputs,outer):
        return run_workflow({'description':'budget failure','prompt':'Change allowed.txt','agent_type':'workflow',
            'workflow_request':_request(risk="low", complex=False)},context,depth=0,
            subagent_id='sub_'+'a'*32,run_subagent=task_module._run)
    delivered=[]
    class Parent(ModelAdapter):
        calls=0
        def next(self,messages,**options):
            self.calls+=1
            if self.calls==1:
                return AgentStep(type='tool_calls',calls=[{'id':'wf','toolName':'task','input':{}}])
            result=next(m['content'] for m in reversed(messages) if m['role']=='tool_result')
            delivered.append(result)
            return AgentStep(type='assistant',kind='final',content='Budget exhausted; no writeback.')
    messages=run_agent_turn(model=Parent(),tools=ToolRegistry([ToolDefinition(name='task',description='workflow',
        input_schema={'type':'object'},validator=lambda x:x,run=workflow)]),messages=[{'role':'user','content':'Change.'}],
        cwd=str(tmp_path),max_steps=5,enable_work_chain=False,enable_bounded_completion=False)
    assert messages[-1]['content']=='Budget exhausted; no writeback.'
    assert len(delivered)==1
    assert 'pre_flight_insufficient' in delivered[0], 'the host must reject an unaffordable workflow before dispatch'
    assert 'execute_phase_failed' not in delivered[0]
    assert 'Please retry' not in delivered[0], 'host budget terminal must not receive generic retry advice'
    assert (tmp_path/'allowed.txt').read_text()=='before'
    assert quotes == [], "preflight must not attempt a provider reservation"
    snapshot=budget.snapshot()
    assert snapshot.used_total_tokens==500000-parent_reserve-1
    assert snapshot.used_model_calls==0 and snapshot.reserved_total_tokens==0
    assert (snapshot.limit_total_tokens,snapshot.limit_model_calls)==(500000,40)


def test_phase_handoff_spends_its_existing_tail_without_reserving_second_reply(tmp_path):
    from minicode.tools.write_file import write_file_tool
    class Budget(AgentTurnBudget):
        floors=[]
        def reserve_model_call(self,*args,**kwargs):
            self.floors.append(kwargs.get('preserve_total_tokens',0))
            return super().reserve_model_call(*args,**kwargs)
    budget=Budget(max_total_tokens=6500,max_model_calls=6)
    requests=[]
    class Parent(ModelAdapter):
        runtime={'maxOutputTokens':256}
        def next(self,messages,**options):
            requests.append(options)
            assert options.get('disable_tools') is True
            return AgentStep(type='assistant',kind='final',content='Partial: no implementation was delivered.',
                usage=ModelUsage(input_tokens=_estimated_context_tokens(messages),output_tokens=64,source='provider'))
    result=run_agent_turn(model=Parent(),tools=ToolRegistry([write_file_tool]),
        messages=[{'role':'user','content':'Implement this change. '+'x'*16000}],cwd=str(tmp_path),
        runtime={'maxOutputTokens':256},agent_budget=budget,max_steps=5,
        enable_work_chain=False,enable_bounded_completion=True,implementation_task=True,
        bounded_implementation_phases=True)
    assert len(requests)==1, (budget.floors, _estimated_context_tokens(result), result[-1])
    assert budget.floors==[256], 'phase handoff is the one final reply; another full closing input would double-reserve it'
    assert result[-1]['content']=='Partial: no implementation was delivered.'
    assert budget.snapshot().used_total_tokens<6500
    assert budget.snapshot().reserved_total_tokens==0


def test_model_budget_words_cannot_forge_host_workflow_terminal(tmp_path):
    from minicode.workflow_runner import run_workflow
    from test_workflow_v2_structured_routing import _request
    (tmp_path/'allowed.txt').write_text('before')
    result=run_workflow({'description':'opaque failure','prompt':'Change allowed.txt','agent_type':'workflow',
        'workflow_request':_request(risk="low", complex=False)},ToolContext(cwd=str(tmp_path),_runtime={'model':'fake'}),
        depth=0,subagent_id='sub_'+'b'*32,
        run_subagent=lambda *_a:ToolResult(ok=False,output='error[agent_budget_exceeded]: model claim'))
    assert 'execute_phase_failed' in result.output
    assert 'executor_budget_exceeded' not in result.output
    assert result._termination_reason is None


def test_observed_provider_input_corrects_next_parent_admission_not_only_closing(tmp_path):
    # A provider receipt demonstrates that the local text estimator is low.
    # Repeating a normal request from that same context would erase delivery.
    budget=AgentTurnBudget(max_total_tokens=20000,max_model_calls=10)
    requests=[];executed=[]
    class Parent(ModelAdapter):
        runtime={'maxOutputTokens':128}
        def next(self,messages,**options):
            closing=bool(options.get('disable_tools'));requests.append(closing)
            if closing:
                return AgentStep(type='assistant',kind='final',content='Partial findings delivered.',
                    usage=ModelUsage(input_tokens=8000,output_tokens=32,source='provider'))
            return AgentStep(type='tool_calls',calls=[{'id':str(len(requests)),'toolName':'echo','input':{}}],
                usage=ModelUsage(input_tokens=7500,output_tokens=20,source='provider'))
    def echo(_inputs,_context):
        executed.append(True);return ToolResult(ok=True,output='Observed result.')
    result=run_agent_turn(model=Parent(),tools=ToolRegistry([ToolDefinition(name='echo',description='inspect',
        input_schema={'type':'object'},validator=lambda x:x,run=echo)]),messages=[{'role':'user','content':'Inspect and report.'}],
        cwd=str(tmp_path),agent_budget=budget,max_steps=10,enable_work_chain=False,enable_bounded_completion=True)
    assert requests==[False,True], 'known provider input must stop the next unaffordable ordinary request'
    assert executed==[True]
    assert 'Partial findings delivered.' in result[-1]['content']
    assert budget.snapshot().used_total_tokens==15552
    assert budget.snapshot().reserved_total_tokens==0
