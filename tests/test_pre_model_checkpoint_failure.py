"""A required host checkpoint must stop provider calls when storage fails."""
from copy import deepcopy
import subprocess

import pytest
from minicode.agent_loop import run_agent_turn
from minicode.coverage_audit_state import record_document
from test_completion_coverage_audit import _case
from test_independent_coverage_inspection import assess, scripted, read_source


def test_pre_model_checkpoint_failure_is_not_optional(tmp_path, monkeypatch):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    case = _case(tmp_path, monkeypatch)
    rejected, accepted = [], []
    def sink(state):
        if state and record_document(state['inspectionRecord'])['reads']:
            rejected.append(deepcopy(state))
            raise OSError('required host checkpoint storage unavailable')
        accepted.append(deepcopy(state))
    def should_not_call(_body):
        raise KeyboardInterrupt('provider called after required checkpoint failure')
    calls = scripted(monkeypatch, case, [read_source(), should_not_call])
    caught = None
    try:
        assess(case, sink=sink)
    except BaseException as error:
        caught = error
    assert rejected and accepted
    assert len(calls) == 1, 'required checkpoint failed, but next provider request was still sent'
    assert isinstance(caught, OSError), repr(caught)


@pytest.mark.parametrize('soft_landing', [False, True])
def test_required_checkpoint_precedes_working_and_closing_provider_calls(tmp_path, monkeypatch, soft_landing):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    case = _case(tmp_path, monkeypatch)
    observed = []
    def sink(messages):
        observed.append(deepcopy(messages))
        if any(message.get('role') == 'tool_result' for message in messages):
            raise OSError('cannot durably retain delivered result')
    def should_not_call(_body):
        pytest.fail('provider called after checkpoint failure')
    calls = scripted(monkeypatch, case, [read_source(), should_not_call])
    with pytest.raises(OSError, match='cannot durably retain delivered result'):
        run_agent_turn(model=case['adapter'], tools=case['tools'], cwd=str(case['root']),
            permissions=case['permissions'], messages=[{'role': 'user', 'content': 'Read stock.py.'}],
            implementation_task=False, enable_implementation_phases=False, enable_work_chain=False,
            allow_user_interaction=False, defer_curator_maintenance=True,
            max_steps=1 if soft_landing else None, max_steps_soft_landing=soft_landing,
            context_checkpoint_sink=sink)
    assert len(calls) == 1
    assert any(message.get('role') == 'tool_result' and 'return n + amount' in message.get('content', '')
               for message in observed[-1])
