"""Real parent and native review loops preserve evidence at economic boundaries."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from minicode import completion_review_limits as limits_api
from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.task_outcome import AgentOutcomeCapture
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelUsage
from test_implementation_completion_review import (
    OBJECTIVE, PROBE, SOURCE, _fixture, _inspection_step, _native_coverage_step, _rows,
)


FINAL = 'The candidate is ready for independent delivery review.'


def _call(identity, name, value):
    return AgentStep(type='tool_calls', calls=[{'id': identity, 'toolName': name, 'input': value}])


def _receipt(messages):
    message = next(row for row in reversed(messages) if row.get('_host_implementation_receipt'))
    assert message['_partial'] is True
    return json.loads(message['content'].split('=== IMPLEMENTATION DELIVERY RECEIPT v1 ===\n', 1)[1])


@pytest.fixture
def economics(tmp_path, monkeypatch):
    # Reuse only the existing real verifier setup, not its scripted assess()
    # wrapper. Both parent and nested run_agent_turn calls remain production.
    owner, _unused_assess, commands, unused_capability = _fixture(tmp_path, monkeypatch)
    unused_capability.close()
    (tmp_path / 'stock.py').write_text(SOURCE.replace('return n + amount', 'return n'))
    (tmp_path / 'test_public.py').write_text(PROBE)
    monkeypatch.setenv('MINI_CODE_COMPLETION_REVIEW_MODE', 'bounded')
    created, events, checkpoints, presented = [], [], [], []
    original_create = limits_api.create_review_limits

    def observe_create(context, **kwargs):
        result = original_create(context, **kwargs)
        created.append(result)
        return result

    monkeypatch.setattr(limits_api, 'create_review_limits', observe_create)
    monkeypatch.setattr('minicode.openai_adapter.open_verified_url',
        lambda *_a, **_k: pytest.fail('This regression has no remote provider.'))

    class Sink:
        def emit(self, event_type, **fields):
            events.append({'type': event_type, **fields})

    class Model:
        def __init__(self, scenario):
            self.scenario = scenario
            self.counts = {'parent': 0, 'review': 0, 'inspection': 0, 'reconciliation': 0}
            self.nested_options = []
            self.after_probe = None

        def next(self, messages, **options):
            names = set(options['tools_override'].list_all())
            if 'completion_coverage_inspection_submit' in names:
                self.counts['inspection'] += 1
                self.nested_options.append(options)
                step = _inspection_step(messages, options['tools_override'])
                if self.scenario == 'inspection_inconclusive' and step.calls[0]['toolName'] == 'completion_coverage_inspection_submit':
                    value = step.calls[0]['input']
                    value['status'] = 'inconclusive'
                    value['findings'] = ['The independent consumer observation remains uncertain.']
                    for row in value['observations']:
                        row['status'] = 'uncertain'
                return step
            if 'completion_coverage_audit_submit' in names:
                self.counts['reconciliation'] += 1
                self.nested_options.append(options)
                return _native_coverage_step(messages)
            if 'completion_review_submit' in names:
                self.counts['review'] += 1
                self.nested_options.append(options)
                number = self.counts['review']
                authority = next(row['content'].split('=== ORIGINAL REQUIREMENT AUTHORITY ===\n', 1)[1]
                    for row in messages if '=== ORIGINAL REQUIREMENT AUTHORITY ===\n' in row.get('content', ''))
                packet = json.loads(authority)
                if number == 1:
                    return _call('economic-probe', 'review_test', {'purpose': 'acceptance',
                        'criterionIds': [row['id'] for row in packet['requirements']],
                        'scenario': 'Both public inventory operations satisfy their quantities.', 'code': PROBE})
                if self.after_probe is not None:
                    callback, self.after_probe = self.after_probe, None
                    callback()
                if self.scenario == 'repeat_entry' and number == 2:
                    rows = _rows(packet, status='unknown')
                    for row in rows:
                        row.update(sourceEvidence=[], probeIds=[])
                    return _call('economic-inconclusive', 'completion_review_submit', {
                        'status': 'inconclusive', 'requirements': rows,
                        'findings': ['Further independent evidence is needed.']})
                if self.scenario in {'exhaust', 'repeat_entry'} or (self.scenario == 'slow_review' and number <= 10):
                    assert number <= 12, 'An exhausted phase cannot silently acquire a fresh allowance.'
                    return _call(f'economic-read-{number}', 'read_file', {'path': 'stock.py'})
                prior = next(json.loads(row['content']) for row in reversed(messages)
                    if row.get('role') == 'tool_result' and row.get('toolName') == 'review_test')
                return _call('economic-approved-proposal', 'completion_review_submit', {
                    'status': 'approved', 'requirements': _rows(packet, ids=[prior['probeId']]), 'findings': []})
            self.counts['parent'] += 1
            assert self.counts['parent'] <= 3, 'A local review stop cannot cause another parent model exchange.'
            if self.counts['parent'] == 1:
                return _call('economic-edit', 'edit_file', {'path': 'stock.py',
                    'old': 'def release(n, amount):\n    return n',
                    'new': 'def release(n, amount):\n    return n + amount'})
            return AgentStep(type='assistant', kind='final', content=FINAL)

    def run(scenario='approved', *, mode='bounded', max_calls=40, deadline=None, after_probe=None):
        monkeypatch.setenv('MINI_CODE_COMPLETION_REVIEW_MODE', mode)
        model = Model(scenario)
        original_next = model.next

        def with_usage(messages, **kwargs):
            step = original_next(messages, **kwargs)
            step.usage = ModelUsage(input_tokens=30, output_tokens=20, source='provider')
            return step

        model.next = with_usage
        model.after_probe = after_probe
        capture = AgentOutcomeCapture()
        budget = AgentTurnBudget(max_model_calls=max_calls, max_total_tokens=None, max_cost_usd=None)
        case.update(model=model, budget=budget, capture=capture)
        messages = run_agent_turn(model=model, tools=ToolRegistry([read_file_tool, edit_file_tool]),
            cwd=str(tmp_path), messages=[{'role': 'user', 'content': OBJECTIVE}],
            implementation_task=True, implementation_spec=owner.spec,
            enable_implementation_phases=False, enable_work_chain=False,
            max_steps=None, agent_budget=budget, deadline_monotonic=deadline,
            runtime={'model': 'local-native-economics', 'maxOutputTokens': 1024},
            outcome_capture=capture, event_sink=Sink(), on_assistant_message=presented.append,
            implementation_checkpoint_sink=lambda document, history: checkpoints.append((deepcopy(document), deepcopy(history))))
        case['messages'] = messages
        return case

    case = {'run': run, 'owner': owner, 'root': tmp_path, 'commands': commands,
        'created': created, 'events': events, 'checkpoints': checkpoints, 'presented': presented}
    return case


def _decisions(case):
    return [row['payload'] for row in case['events'] if row['type'] == 'implementation.completion_decided']


def _assert_retained_partial(case, *, probes=1):
    receipt = _receipt(case['messages'])
    assert receipt['requirementsAccepted'] is False and receipt['delivery'] == 'partial'
    assert receipt['lastPublicChecks'] == 'passed' and receipt['completionReview'] == 'budget_exhausted'
    assert (case['root'] / 'stock.py').read_text() == SOURCE
    assert FINAL not in case['presented']
    assert not case['capture'].outcome.goal_achieved
    assert not case['capture'].outcome.completion_succeeded
    assert any(row.get('toolUseId') == 'economic-edit' and row.get('role') == 'tool_result'
        and not row.get('isError') for row in case['messages'])
    state = case['checkpoints'][-1][0]['reviewState']
    assert len(state['probes']) == len(state['receipts']) == probes
    if probes:
        assert state['probes'][0]['code'] == PROBE
        assert state['receipts'][0]['receipt']['status'] == 'passed'
        for field in ('receiptRef', 'sourceRef'):
            assert Path(state['receipts'][0][field]).is_file()
    assert all(not Path(row['cwd']).exists() for row in case['commands'])
    decision = _decisions(case)[-1]
    assert decision['repairProbes'] == [] and decision['continuesSameExecutor'] is False
    feedback = next(row['content'] for row in case['messages'] if row.get('_implementation_completion_feedback'))
    assert 'Repair the evidenced gaps' not in feedback
    return decision


def test_phase_exhaustion_preserves_real_candidate_probe_and_parent_receipt(economics):
    case = economics['run']('exhaust')
    decision = _assert_retained_partial(case)
    assert decision['reason'] == 'completion_review_budget_exhausted'
    assert case['model'].counts == {'parent': 2, 'review': 8, 'inspection': 0, 'reconciliation': 0}
    assert len(case['created']) == 1
    assert case['created'][0]['stages']['review']['budget'].admitted_model_calls == 8
    assert case['budget'].snapshot().used_model_calls == 10


def test_all_three_real_stages_can_approve_within_local_limits(economics):
    case = economics['run']()
    assert case['messages'][-1]['content'] == FINAL and FINAL in case['presented']
    assert not any(row.get('_host_implementation_receipt') for row in case['messages'])
    assert case['capture'].outcome.completion_succeeded
    assert case['model'].counts == {'parent': 2, 'review': 2, 'inspection': 2, 'reconciliation': 2}
    assert _decisions(case)[-1]['completionReview'] == 'approved'
    assert all(options['max_retries_override'] == 0 for options in case['model'].nested_options)


def test_full_mode_keeps_review_running_beyond_bounded_phase_limit(economics):
    case = economics['run']('slow_review', mode='full')
    assert case['created'] == [None]
    assert case['model'].counts['review'] == 11
    assert case['messages'][-1]['content'] == FINAL
    assert _decisions(case)[-1]['completionReview'] == 'approved'
    assert not any(row['type'] == 'implementation.completion_review_budget_exhausted' for row in case['events'])
    assert all(options.get('max_retries_override') is None for options in case['model'].nested_options)


def test_ordinary_native_inconclusive_is_not_budget_exhaustion(economics):
    case = economics['run']('inspection_inconclusive')
    receipt = _receipt(case['messages'])
    assert receipt['requirementsAccepted'] is False
    assert 'completionReview' not in receipt
    assert _decisions(case)[-1]['status'] == 'inconclusive'
    assert 'completionReview' not in _decisions(case)[-1]
    assert case['model'].counts['parent'] == 2
    assert not any(row['type'] == 'implementation.completion_review_budget_exhausted' for row in case['events'])


def test_repeated_final_reuses_one_chain_and_remaining_phase_calls(economics):
    case = economics['run']('repeat_entry')
    _assert_retained_partial(case)
    assert len(case['created']) == 1 and case['model'].counts['parent'] == 3
    assert case['model'].counts['review'] == 8
    decisions = _decisions(case)
    assert [row['attempt'] for row in decisions] == [1, 2]
    assert 'completionReview' not in decisions[0]
    assert decisions[1]['completionReview'] == 'budget_exhausted'


def test_parent_two_call_floor_blocks_review_without_spending_it(economics):
    case = economics['run']('exhaust', max_calls=4)
    _assert_retained_partial(case, probes=0)
    assert case['model'].counts['parent'] == 2 and case['model'].counts['review'] == 0
    assert case['budget'].snapshot().used_model_calls == 2
    exhausted = next(row['payload'] for row in case['events']
        if row['type'] == 'implementation.completion_review_budget_exhausted')
    assert exhausted['reason'] == 'closing_reserve'


def test_no_review_time_left_preserves_live_parent_and_candidate(economics):
    case = economics['run']('exhaust', deadline=time.monotonic() + 30)
    _assert_retained_partial(case, probes=0)
    assert case['model'].counts['parent'] == 2 and case['model'].counts['review'] == 0
    assert case['budget'].snapshot().used_model_calls == 2
    exhausted = next(row['payload'] for row in case['events']
        if row['type'] == 'implementation.completion_review_budget_exhausted')
    assert exhausted['reason'] == 'wall_clock'
    assert time.monotonic() < case['created'][0]['ancestorDeadline']


@pytest.mark.parametrize(('mode', 'kind'), [('full', 'budget'), ('bounded', 'budget'), ('bounded', 'nonbudget')])
def test_unowned_errors_escape_completion_gate_without_budget_relabel(economics, monkeypatch, mode, kind):
    def fail(_self, *, context, **_kwargs):
        if kind == 'nonbudget':
            raise ValueError('invalid evidence binding')
        raise AgentBudgetExceeded('cost budget exhausted', context._agent_budget.snapshot())

    monkeypatch.setattr(ImplementationCompletionReview, 'assess', fail)
    with pytest.raises(ValueError if kind == 'nonbudget' else AgentBudgetExceeded):
        economics['run'](mode=mode)
    assert economics['model'].counts['parent'] == 2
    assert (economics['root'] / 'stock.py').read_text() == SOURCE
    assert not any(row['type'] == 'implementation.completion_review_budget_exhausted' for row in economics['events'])


def test_phase_deadline_stop_keeps_completed_probe_under_live_parent_deadline(economics, monkeypatch):
    now = time.monotonic()
    clock = SimpleNamespace(now=now)
    controlled = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr(limits_api, 'time', controlled)
    monkeypatch.setattr('minicode.task_deadline.time', controlled)
    monkeypatch.setattr('minicode.model_call_control.time', SimpleNamespace(monotonic=lambda: clock.now, sleep=time.sleep))
    case = economics['run']('exhaust', deadline=now + 600,
        after_probe=lambda: setattr(clock, 'now', now + 91))
    _assert_retained_partial(case)
    exhausted = next(row['payload'] for row in case['events']
        if row['type'] == 'implementation.completion_review_budget_exhausted')
    assert exhausted['reason'] == 'wall_clock'
    assert case['model'].counts['parent'] == 2 and case['model'].counts['review'] == 2
