"""Actual parent/reviewer/auditor/runtime and pytest; local scripted model only."""
import json
import pytest

from minicode.agent_loop import run_agent_turn
from minicode.types import AgentStep
from minicode.implementation_completion_review import requirement_units
from test_behavior_evidence_parent_continuation import (
    api_store as api_store_fixture, PARTIAL, COMPLETE, POSITIVE, FULL, PUBLIC, OBJECTIVE, packet,
)
from test_completion_feedback_chronology import _call, _result
from test_implementation_completion_review import _native_coverage_step

api_store = api_store_fixture


@pytest.mark.parametrize('negative_first,outer_status', [(True, 'changes_required'), (False, 'changes_required'), (False, 'inconclusive')])
def test_independent_missing_returns_same_owner_before_unrelated_evidence_and_finishes(api_store, negative_first, outer_status):
    workspace, options, commands, events, checkpoints = api_store
    (workspace / 'stock.py').write_text(PARTIAL)
    owners = []
    class Model:
        model_id = 'local-independent-inspection'
        parent_calls = reviewer_calls = inspections = 0
        fixed = False
        def next(self, messages, **kwargs):
            names = set(kwargs['tools_override'].list_all())
            if 'completion_coverage_inspection_submit' in names:
                assert not {'review_probe_read', 'review_evidence_read', 'completion_evidence_read', 'completion_coverage_audit_submit'} & names
                assert not any(m.get('role') == 'user' and '"proposal"' in m.get('content', '') for m in messages)
                meta = next(json.loads(m['content'])['coverageInspection'] for m in messages
                    if m.get('role') == 'user' and m.get('content', '').startswith('{"coverageInspection":'))
                reads = [m for m in messages if m.get('role') == 'tool_result' and m.get('toolName') == 'read_file']
                if not reads:
                    return _call('independent-source', 'read_file', path='stock.py', start_line=1,
                        end_line=len((COMPLETE if self.fixed else PARTIAL).splitlines()))
                self.inspections += 1
                step = _call('inspection-result', 'completion_coverage_inspection_submit',
                    status='ready_for_evidence' if self.fixed else outer_status, observations=[{
                        'requirementId': meta['requirements'][0]['id'], 'status': 'checked' if self.fixed else 'missing',
                        'sourceEvidence': [{'path': 'stock.py', 'startLine': 10,
                            'endLine': len((COMPLETE if self.fixed else PARTIAL).splitlines())}],
                        'expectedBehavior': 'Negative amounts must raise and preserve the saved stock.',
                        'observedBehavior': 'Negative guard and persistent path are present.' if self.fixed else 'release unconditionally saves count plus amount; no negative guard exists.',
                        'probeIds': []}], findings=[] if self.fixed else ['Negative release changes persisted stock instead of rejecting.'])
                if not self.fixed:
                    positive = {'requirementId': meta['requirements'][0]['id'], 'status': 'checked',
                        'sourceEvidence': [{'path': 'stock.py', 'startLine': 1, 'endLine': 6}],
                        'expectedBehavior': 'The store has a persistence method.', 'observedBehavior': 'Store.save assigns the current count.', 'probeIds': []}
                    rows = step.calls[0]['input']['observations']
                    rows.insert(len(rows) if negative_first else 0, positive)
                return step
            if 'completion_coverage_audit_submit' in names:
                assert self.fixed, 'Known missing must return owner before evidence reconciliation.'
                step = _native_coverage_step(messages, stable_evidence=True)
                if step.calls[0]['toolName'] == 'completion_coverage_audit_submit':
                    meta = packet(messages, '"evidenceIndex"')
                    pid = meta['proposal']['requirements'][0]['probeIds'][0]
                    children = meta['behaviorContract']['obligations']
                    def w(n):
                        return {'probeId': pid, 'startLine': n, 'endLine': n}
                    step.calls[0]['input'] = {'status': 'approved', 'findings': [], 'requirements': [{
                        'id': meta['requirements'][0]['id'], 'status': 'supported',
                        'reason': 'Actual API positive persistence and rejected negative leave the expected saved state.',
                        'witnesses': [w(5), w(9)], 'obligations': [
                            {'id': child['id'], 'reason': 'Nonzero addition persists seven.' if index == 0 else 'Negative input raises and saved stock stays seven.',
                             'witnesses': [w(5 if index == 0 else 9)]} for index, child in enumerate(children)]}]}
                return step
            if 'completion_review_submit' in names:
                self.reviewer_calls += 1
                current = {'requirements': list(requirement_units(OBJECTIVE))}
                if self.reviewer_calls % 2:
                    return _call('review-probe', 'review_test', purpose='acceptance',
                        criterionIds=[r['id'] for r in current['requirements']], scenario='Inventory API behavior',
                        code=FULL if self.fixed else POSITIVE)
                receipt = json.JSONDecoder().raw_decode(_result(messages, 'review-probe')['content'])[0]
                return _call('review-proposal', 'completion_review_submit', status='approved', findings=[], requirements=[{
                    'id': current['requirements'][0]['id'], 'status': 'covered', 'probeIds': [receipt['probeId']],
                    # Deliberately distinct from actual inspection anchors.
                    'sourceEvidence': [{'path': 'stock.py', 'startLine': 1, 'endLine': 6}],
                    'rationale': 'The ordinary probe passed.'}])
            self.parent_calls += 1
            if self.parent_calls == 1:
                return _call('initial-source', 'read_file', path='stock.py')
            if self.parent_calls == 2:
                return _call('initial-edit', 'write_file', path='stock.py', content=PARTIAL + '\n')
            if self.parent_calls == 3:
                return AgentStep(type='assistant', kind='final', content='Ready for delivery.')
            if self.parent_calls == 4:
                feedback = next(m for m in messages if m.get('_implementation_completion_feedback'))
                original = feedback.get('_completion_evidence_original', feedback['content'])
                receipt = json.loads(original.split('\nCompletion evidence:\n', 1)[1])
                assert receipt['status'] == 'changes_required' and receipt['continuesSameExecutor']
                assert receipt['requirements'][0]['sourceEvidence'][0]['startLine'] == 10
                owners.append(receipt['executorId'])
                assert _result(messages, 'initial-edit')['isError'] is False
                assert _result(messages, 'initial-source')['isError'] is False
                self.fixed = True
                return _call('repair-negative', 'write_file', path='stock.py', content=COMPLETE)
            if self.parent_calls == 5:
                return _call('repair-tests', 'write_file', path='test_public.py', content=PUBLIC + '\n' + FULL)
            if self.parent_calls == 6:
                return _call('repair-check', 'executor_check', check=1)
            assert self.parent_calls == 7
            assert json.loads(_result(messages, 'repair-check')['content'])['status'] == 'passed'
            return AgentStep(type='assistant', kind='final', content='The implementation, negative guard and real tests are complete.')
    model = Model()
    messages = run_agent_turn(model=model, messages=[{'role': 'user', 'content': OBJECTIVE}], **options)
    decisions = [row['payload'] for row in events if row['type'] == 'implementation.completion_decided']
    assert [row['status'] for row in decisions] == ['changes_required', 'approved']
    assert {row['executorId'] for row in decisions} == set(owners)
    assert model.inspections == 2 and model.parent_calls == 7
    assert (workspace / 'stock.py').read_text() == COMPLETE
    assert any('complete' in m.get('content', '') for m in messages if m.get('role') == 'assistant')
