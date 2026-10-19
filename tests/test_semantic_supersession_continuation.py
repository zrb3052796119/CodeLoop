"""A broken generated fixture must not strand the original implementation owner.

The adapters script semantic decisions; parent turns, tool delivery, candidate
changes, checkpoint restore and isolated pytest execution are all real.
"""
from copy import deepcopy
import json

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.implementation_completion_review import ImplementationCompletionReview, requirement_units
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.types import AgentStep
import test_completion_feedback_chronology as chronology
from test_completion_feedback_chronology import COMPLETE, OBJECTIVE, PARTIAL, _call, _result
from test_completion_failure_adjudication_resume import packet_with
from test_implementation_completion_review import _native_coverage_step, _inspection_step


inventory = chronology.inventory
BROKEN_FIXTURE = '''from stock import hold, release
def make_backend():
    class Backend:
        reserve = staticmethod(hold)
        restore = staticmethod(release)
    return Backend()
def test_inventory():
    backend = make_backend()()
    assert backend.reserve(10, 3) == 7
    assert backend.restore(7, 3) == 10
'''
VALID_FIXTURE = BROKEN_FIXTURE.replace('make_backend()()', 'make_backend()')


def seed_native_fixture_failure(case, monkeypatch, *, product_gap):
    (case['workspace'] / 'stock.py').write_text(PARTIAL if product_gap else COMPLETE)
    ids = [row['id'] for row in requirement_units(OBJECTIVE)]
    stop = KeyboardInterrupt('retain actual candidate and failing generated fixture')

    class Seed:
        parent_calls = 0
        review_calls = 0
        probe_id = None

        def next(self, messages, **kwargs):
            inspection_step = _inspection_step(messages, kwargs["tools_override"])
            if inspection_step is not None:
                return inspection_step
            if 'completion_review_submit' in kwargs['tools_override'].list_all():
                self.review_calls += 1
                if self.review_calls == 1:
                    return _call('bad-fixture', 'review_test', purpose='acceptance', criterionIds=ids,
                        scenario='The actual inventory backend preserves both operations.', code=BROKEN_FIXTURE)
                receipt = json.JSONDecoder().raw_decode(_result(messages, 'bad-fixture')['content'])[0]
                assert receipt['receipt']['status'] == 'failed'
                self.probe_id = receipt['probeId']
                return _call('seed-review', 'completion_review_submit', status='changes_required',
                    requirements=[{'id': identity, 'status': 'unknown', 'sourceEvidence': [], 'probeIds': [],
                        'rationale': 'The generated fixture failed before its assertions.'} for identity in ids],
                    findings=['The retained generated test needs independent attribution.'])
            self.parent_calls += 1
            if self.parent_calls == 1:
                return _call('original-read', 'read_file', path='stock.py')
            if self.parent_calls == 2:
                return AgentStep(type='assistant', kind='final', content='Ready for verification.')
            raise stop

    seed = Seed()
    # Seed the historical scheduling state, without faking any source, pytest
    # result, checkpoint or tool message used by the resumed production loop.
    with monkeypatch.context() as legacy:
        legacy.setattr(ImplementationCompletionReview, 'needs_failure_adjudication', lambda *_a, **_k: False)
        with pytest.raises(KeyboardInterrupt) as error:
            run_agent_turn(model=seed, messages=[{'role': 'user', 'content': OBJECTIVE}], **case['options'])
    assert error.value is stop
    document, messages = case['checkpoints'][-1]
    return deepcopy(document), deepcopy(messages), seed.probe_id


@pytest.mark.parametrize('product_gap', [False, True], ids=['fixture-only', 'fixture-then-real-product-gap'])
@pytest.mark.parametrize('stable_evidence', [False, True], ids=['legacy-evidence', 'host-document-cursors'])
def test_same_owner_corrects_test_then_repairs_only_confirmed_product_gap(
        inventory, monkeypatch, product_gap, stable_evidence):
    case = inventory
    document, messages, old_id = seed_native_fixture_failure(case, monkeypatch, product_gap=product_gap)
    original_result = deepcopy(_result(messages, 'original-read'))
    original_public = (case['workspace'] / 'test_public.py').read_bytes()
    initial_commands = len(case['commands'])
    restored = HostImplementationContinuation.from_document(document, spec=case['spec'], messages=messages)
    ids = [row['id'] for row in requirement_units(OBJECTIVE)]
    audits, attributions = [], []
    final_text = 'Both inventory operations passed current tests and independent coverage review.'

    class Driver:
        model_id = 'semantic-supersession-integration'
        total_calls = 0
        parent_calls = 0

        def next(self, actual, **kwargs):
            self.total_calls += 1
            if self.total_calls > 45:
                raise KeyboardInterrupt('scripted protocol failed to converge')
            inspection_step = _inspection_step(actual, kwargs["tools_override"])
            if inspection_step is not None:
                return inspection_step
            names = set(kwargs['tools_override'].list_all())
            if 'completion_probe_adjudication_submit' in names:
                packet = packet_with(actual, 'probe')
                assert not {'write_file', 'edit_file', 'executor_check'} & names
                rejected = [m for m in actual if m.get('role') == 'tool_result'
                    and m.get('toolUseId') == 'attribution' and m.get('isError')]
                assert not rejected, ('A valid independently proposed fixture replacement was rejected', rejected)
                source_read = [m for m in actual if m.get('toolUseId') == 'attribution-source'
                               and m.get('role') == 'tool_result']
                if not source_read:
                    return _call('attribution-source', 'read_file', path='stock.py')
                assert not source_read[-1]['isError']
                fixture_error = packet['probe']['probeId'] == old_id
                assert packet['probe']['code'] == (BROKEN_FIXTURE if fixture_error else VALID_FIXTURE)
                verdict = 'probe_error' if fixture_error else 'product_gap'
                attributions.append(verdict)
                value = {'verdict': verdict, 'reason': (
                    'The local helper already returns an instance; the extra call prevents the unchanged assertions.'
                    if fixture_error else 'The actual release operation fails to add the requested amount.'),
                    'objectiveEvidence': [{'startLine': 1, 'endLine': 2}],
                    'sourceEvidence': [{'path': 'stock.py', 'startLine': 1, 'endLine': 5}]}
                if fixture_error:
                    value['replacement'] = {'criterionIds': ids, 'scenario': packet['probe']['scenario'],
                        'code': VALID_FIXTURE}
                return _call('attribution', 'completion_probe_adjudication_submit', **value)
            if 'completion_review_submit' in names:
                packet = packet_with(actual, 'retainedProbes')
                retained = packet['retainedProbes']
                assert retained and all(row['probeId'] != old_id for row in retained)
                passed = [row['probeId'] for row in retained if row['status'] == 'passed']
                complete = len(passed) == len(retained)
                assert complete or product_gap
                return _call('review-' + str(self.total_calls), 'completion_review_submit',
                    status='approved' if complete else 'changes_required',
                    requirements=[{'id': identity, 'status': 'covered' if complete else 'missing',
                        'sourceEvidence': [{'path': 'stock.py', 'startLine': 1, 'endLine': 5}],
                        'probeIds': passed if complete else [], 'rationale': 'Current actual backend assertions.'}
                        for identity in ids], findings=[] if complete else ['release must add the amount.'])
            if 'completion_coverage_audit_submit' in names:
                packet = packet_with(actual, 'evidenceIndex')
                step = _native_coverage_step(actual, stable_evidence=stable_evidence)
                assert step is not None
                if stable_evidence:
                    assert step.calls[0]['toolName'] in {'review_evidence_read', 'completion_coverage_audit_submit'}
                    if step.calls[0]['toolName'] == 'review_evidence_read':
                        request = step.calls[0]['input']
                        assert set(request) in ({'documentId'}, {'cursor'})
                        assert 'offset' not in request and 'receiptView' not in request
                if step.calls[0]['toolName'] == 'completion_coverage_audit_submit':
                    value = step.calls[0]['input']
                    audits.append(deepcopy(packet))
                    if packet['proposal']['status'] != 'approved':
                        assert product_gap
                        value.update(status='changes_required', findings=['Actual release still returns the unchanged count.'],
                            requirements=[{'id': identity, 'status': 'missing',
                                'reason': 'The real backend fails the original addition assertion.', 'witnesses': []}
                                for identity in ids])
                    if packet.get('supersessionIndex'):
                        assert value['supersessions'][0]['status'] == 'accepted'
                return step
            assert 'write_file' in names and 'executor_check' in names
            self.parent_calls += 1
            if product_gap:
                assert not audits, 'Confirmed repair must reach its owner before unrelated replacement retirement.'
            else:
                assert audits, 'Fixture-only changes do not establish a product repair instruction.'
            assert _result(actual, 'original-read') == original_result
            if product_gap:
                if self.parent_calls == 1:
                    feedback = next(m for m in reversed(actual) if m.get('_implementation_completion_feedback'))
                    assert VALID_FIXTURE in feedback['content'].replace('\\n', '\n')
                    return _call('repair-release', 'write_file', path='stock.py', content=COMPLETE)
                if self.parent_calls == 2:
                    assert not _result(actual, 'repair-release')['isError']
                    return _call('verify-repair', 'executor_check', check=1)
                assert self.parent_calls == 3
                assert json.loads(_result(actual, 'verify-repair')['content'])['status'] == 'passed'
            else:
                assert self.parent_calls == 1
                assert (case['workspace'] / 'stock.py').read_text() == COMPLETE
            return AgentStep(type='assistant', kind='final', content=final_text)

    driver = Driver()
    result = run_agent_turn(model=driver, messages=messages, implementation_continuation=restored, **case['options'])
    assert result[-1]['content'] == final_text and not result[-1].get('_partial')
    assert attributions == (['probe_error', 'product_gap'] if product_gap else ['probe_error'])
    assert (case['workspace'] / 'stock.py').read_text() == COMPLETE
    assert (case['workspace'] / 'test_public.py').read_bytes() == original_public
    assert len(case['commands']) > initial_commands and all(c['cwd'] != case['workspace'] for c in case['commands'])
    final_document, final_messages = case['checkpoints'][-1]
    assert final_document['executorId'] == document['executorId'] and final_document['attempt'] > document['attempt']
    assert _result(final_messages, 'original-read') == original_result
    state = final_document['reviewState']
    assert audits and audits[-1]['proposal']['status'] == 'approved'
    assert old_id in state['retiredProbeIds']
    assert any(row['probeId'] == old_id and row['receipt']['status'] == 'failed' for row in state['receipts'])
    assert any(row['oldProbeId'] == old_id and row['correctionKind'] == 'audited_semantic_supersession'
               for row in state['corrections'])
    assert state['reviews'][-1]['status'] == 'approved'
    # Native restoration validates the persisted semantic authority and source
    # chain even after the implementation candidate changed during repair.
    final_restoration = HostImplementationContinuation.from_document(final_document, spec=case['spec'],
        messages=final_messages)
    assert final_restoration.executor_id == document['executorId']
    restored_review = final_restoration.restore_review(case['spec'])
    assert old_id in restored_review.state['retiredProbeIds']
    assert restored_review.state['corrections'] == state['corrections']
    assert not restored_review._cached
