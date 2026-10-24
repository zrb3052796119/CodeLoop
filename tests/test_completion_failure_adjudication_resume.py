"""Actual parent/reviewer loops must audit failed evidence before resumed edits."""

from copy import deepcopy
import json

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.context_manager import ContextManager
from minicode.implementation_completion_review import ImplementationCompletionReview, requirement_units
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.types import AgentStep
import test_completion_feedback_chronology as chronology
from test_completion_feedback_chronology import (
    COMPLETE, OBJECTIVE, PARTIAL, PROBE, _call, _result,
)
from test_implementation_completion_review import _native_coverage_step, _inspection_step


inventory = chronology.inventory
WRONG_PROBE = PROBE.replace('assert release(7, 3) == 10', 'assert release(7, 3) == 4')


def seed_failed_checkpoint(case, monkeypatch, *, product_gap):
    """Persist a genuine rejected review plus parent interruption, not fake state."""
    (case['workspace'] / 'stock.py').write_text(PARTIAL if product_gap else COMPLETE)
    criterion_ids = [row['id'] for row in requirement_units(OBJECTIVE)]
    failure = KeyboardInterrupt('retain old failed acceptance evidence')

    class OldParent:
        parent_calls = 0
        review_calls = 0
        probe_id = None

        def next(self, messages, **kwargs):
            inspection_step = _inspection_step(messages, kwargs["tools_override"])
            if inspection_step is not None:
                return inspection_step
            names = set(kwargs['tools_override'].list_all())
            if 'completion_review_submit' in names:
                self.review_calls += 1
                if self.review_calls == 1:
                    return _call('old-acceptance', 'review_test', purpose='acceptance',
                        criterionIds=criterion_ids, scenario='Both inventory operations preserve quantity.',
                        code=PROBE if product_gap else WRONG_PROBE)
                evidence = json.JSONDecoder().raw_decode(_result(messages, 'old-acceptance')['content'])[0]
                assert evidence['receipt']['status'] == 'failed'
                self.probe_id = evidence['probeId']
                return _call('old-review', 'completion_review_submit', status='changes_required',
                    # No independently established source omission bypasses
                    # the failure attribution that these resume cases exercise.
                    requirements=[{'id': identity, 'status': 'unknown',
                        'sourceEvidence': [], 'probeIds': [],
                        'rationale': 'The failed acceptance has not been independently attributed.'}
                        for identity in criterion_ids], findings=['The retained acceptance needs independent attribution.'])
            self.parent_calls += 1
            if self.parent_calls == 1:
                return _call('old-source-read', 'read_file', path='stock.py')
            if self.parent_calls == 2:
                assert _result(messages, 'old-source-read')['isError'] is False
                return AgentStep(type='assistant', kind='final', content='Implementation is ready.')
            assert any(m.get('_implementation_completion_feedback') for m in messages)
            raise failure

    model = OldParent()
    manager = ContextManager(model='chronology-test')
    # Reproduce the previous scheduling policy only. The parent/reviewer loop,
    # native probe execution, pytest, verdict and saved host state remain real.
    with monkeypatch.context() as legacy:
        legacy.setattr(ImplementationCompletionReview, 'needs_failure_adjudication',
            lambda self, candidate_digest=None: False, raising=False)
        with pytest.raises(KeyboardInterrupt) as caught:
            run_agent_turn(model=model, messages=[{'role': 'user', 'content': OBJECTIVE}],
                context_manager=manager, **case['options'])
    assert caught.value is failure
    document, saved = case['checkpoints'][-1]
    assert saved == manager.messages
    assert document['completionFeedback'] in saved
    assert document['reviewState']['receipts'][-1]['receipt']['status'] == 'failed'
    assert document['reviewState'].get('acceptedRepairDecision') is None
    assert all(row['status'] == 'unknown' and not row['sourceEvidence']
               for row in document['reviewState']['reviews'][-1]['requirements'])
    assert model.probe_id is not None and case['commands']
    return document, deepcopy(saved), model.probe_id


@pytest.mark.parametrize('product_gap', [False, True], ids=['wrong-probe', 'real-product-gap'])
def test_resume_first_request_adjudicates_before_parent_write_surface(inventory, monkeypatch, product_gap):
    case = inventory
    document, saved, _ = seed_failed_checkpoint(case, monkeypatch, product_gap=product_gap)
    restored = HostImplementationContinuation.from_document(document, spec=case['spec'], messages=saved)
    stop = KeyboardInterrupt('inspected first restored request')
    calls = []

    class FirstRequest:
        def next(self, messages, **kwargs):
            inspection_step = _inspection_step(messages, kwargs["tools_override"])
            if inspection_step is not None:
                return inspection_step
            names = set(kwargs['tools_override'].list_all())
            calls.append(names)
            raise stop

    with pytest.raises(KeyboardInterrupt) as caught:
        run_agent_turn(model=FirstRequest(), messages=saved, implementation_continuation=restored, **case['options'])
    assert caught.value is stop and len(calls) == 1
    assert 'completion_probe_adjudication_submit' in calls[0], (
        'Restored failed acceptance evidence reached the parent implementation request before independent adjudication', calls[0])
    assert not {'write_file', 'edit_file', 'executor_check', 'run_command', 'task'} & calls[0]


def packet_with(messages, key):
    for message in reversed(messages):
        content = message.get('content')
        if message.get('role') == 'user' and isinstance(content, str) and content.startswith('{'):
            try:
                packet = json.loads(content)
            except ValueError:
                continue
            if isinstance(packet, dict) and key in packet:
                return packet
    raise AssertionError(f'No actual native packet contains {key}')


@pytest.mark.parametrize('product_gap', [False, True], ids=['fix-probe-preserve-product', 'fix-real-product-gap'])
def test_same_owner_resumes_independent_adjudication_real_tests_and_delivery(inventory, monkeypatch, product_gap):
    # This exercises complete strict continuation, including all replacement
    # evidence pages. Bounded phase exhaustion has separate delivery tests.
    monkeypatch.setenv('MINI_CODE_COMPLETION_REVIEW_MODE', 'full')
    case = inventory
    document, saved, old_probe_id = seed_failed_checkpoint(case, monkeypatch, product_gap=product_gap)
    preserved_tool_result = deepcopy(_result(saved, 'old-source-read'))
    original_files = {name: (case['workspace'] / name).read_bytes() for name in ('stock.py', 'test_public.py')}
    initial_command_count = len(case['commands'])
    saved_state = deepcopy(document['reviewState'])
    restored = HostImplementationContinuation.from_document(document, spec=case['spec'], messages=saved)
    final_text = 'The requested inventory operations passed the current checks and independent review.'
    all_requests, adjudications, checks, audit_reads, audit_document_reads = [], [], [], [], []

    class ResumeDriver:
        adjudication_calls = 0
        parent_calls = 0
        coverage_calls = 0

        def next(self, messages, **kwargs):
            names = set(kwargs['tools_override'].list_all())
            all_requests.append(names)
            inspection_step = _inspection_step(messages, kwargs["tools_override"])
            if inspection_step is not None:
                return inspection_step
            if 'completion_coverage_audit_submit' in names:
                self.coverage_calls += 1
                assert not {'write_file', 'executor_check', 'completion_review_submit'} & names
                packet = packet_with(messages, 'evidenceIndex')
                # A correction retains the old failed source in audit history,
                # but only its active passed successor is proposed for coverage.
                assert len(packet['evidenceIndex']) == 1
                assert packet['evidenceIndex'][0]['active'] is True
                assert packet['evidenceIndex'][0]['status'] == 'passed'
                step = _native_coverage_step(messages)
                assert step is not None
                if step.calls[0]['toolName'] == 'review_probe_read':
                    audit_reads.append(step.calls[0]['input'])
                elif step.calls[0]['toolName'] == 'review_evidence_read':
                    audit_document_reads.append(step.calls[0]['input'])
                return step
            if 'completion_probe_adjudication_submit' in names:
                self.adjudication_calls += 1
                assert names == {'read_file', 'completion_probe_adjudication_submit', 'completion_evidence_read'}
                packet = packet_with(messages, 'probe')
                assert packet['probe']['probeId'] == old_probe_id
                assert packet['probe']['code'] == (PROBE if product_gap else WRONG_PROBE)
                if self.adjudication_calls == 1:
                    return _call('adjudicator-source', 'read_file', path='stock.py')
                assert self.adjudication_calls == 2
                assert _result(messages, 'adjudicator-source')['isError'] is False
                verdict = 'product_gap' if product_gap else 'probe_error'
                adjudications.append(verdict)
                values = {'verdict': verdict,
                    'reason': ('release currently returns the unchanged count, contrary to adding the amount.'
                        if product_gap else 'The objective requires addition; the probe expected subtraction instead.'),
                    'objectiveEvidence': [{'startLine': 2, 'endLine': 2}],
                    'sourceEvidence': [{'path': 'stock.py', 'startLine': 4, 'endLine': 5}]}
                if not product_gap:
                    values['replacement'] = {'criterionIds': packet['probe']['criterionIds'],
                        'scenario': packet['probe']['scenario'], 'code': PROBE}
                return _call('adjudication', 'completion_probe_adjudication_submit', **values)
            if 'completion_review_submit' in names:
                packet = packet_with(messages, 'retainedProbes')
                retained = packet['retainedProbes']
                assert retained
                passed = [row['probeId'] for row in retained if row['status'] == 'passed']
                status = 'approved' if len(passed) == len(retained) else 'changes_required'
                assert status == 'approved' or product_gap
                return _call('current-review-' + status, 'completion_review_submit', status=status,
                    requirements=[{'id': row['id'], 'status': 'covered' if status == 'approved' else 'missing',
                        'sourceEvidence': [{'path': 'stock.py', 'startLine': 1, 'endLine': 5}],
                        'probeIds': passed if status == 'approved' else [],
                        'rationale': 'The current source and real isolated acceptance receipts establish this behavior.'}
                        for row in requirement_units(OBJECTIVE)],
                    findings=[] if status == 'approved' else ['release must add the amount.'])
            assert 'write_file' in names and 'executor_check' in names
            self.parent_calls += 1
            assert adjudications, 'No implementation request may precede independent failure adjudication.'
            assert _result(messages, 'old-source-read') == preserved_tool_result
            if product_gap:
                if self.parent_calls == 1:
                    return _call('same-owner-repair', 'write_file', path='stock.py', content=COMPLETE)
                if self.parent_calls == 2:
                    assert _result(messages, 'same-owner-repair')['isError'] is False
                    return _call('same-owner-tests', 'executor_check', check=1)
                assert self.parent_calls == 3
                check = json.loads(_result(messages, 'same-owner-tests')['content'])
                checks.append(check)
                assert check['status'] == 'passed' and check['evidence']['exitCode'] == 0
            else:
                assert self.parent_calls == 1
                assert not any(message.get('_implementation_completion_feedback') for message in messages)
                assert any(message.get('_implementation_completion_status') for message in messages)
                assert {name: (case['workspace'] / name).read_bytes() for name in original_files} == original_files
            return AgentStep(type='assistant', kind='final', content=final_text)

    model = ResumeDriver()
    result = run_agent_turn(model=model, messages=saved, implementation_continuation=restored, **case['options'])
    assert all_requests and 'completion_probe_adjudication_submit' in all_requests[0]
    assert sum('completion_coverage_inspection_submit' in names for names in all_requests) == 2
    assert adjudications == ['product_gap' if product_gap else 'probe_error']
    # A semantic replacement additionally reads the retained invalid source and
    # its independent authority; a genuine product repair has one active probe.
    assert model.coverage_calls == len(audit_reads) + len(audit_document_reads) + 1
    assert len({json.dumps(row, sort_keys=True) for row in audit_document_reads}) == len(audit_document_reads)
    assert len({read['probeId'] for read in audit_reads}) == (2 if not product_gap else 1)
    assert len({(read['probeId'], read.get('offset', 0)) for read in audit_reads}) == len(audit_reads)
    assert _result(result, 'old-source-read') == preserved_tool_result
    assert result[-1]['content'] == final_text and not result[-1].get('_partial')
    decisions = [event['payload'] for event in case['events'] if event['type'] == 'implementation.completion_decided']
    assert decisions[0]['status'] == 'changes_required' and decisions[-1]['status'] == 'approved'
    assert {row['executorId'] for row in decisions} == {document['executorId']}
    assert decisions[-1]['attempt'] > document['attempt']
    assert len(case['commands']) > initial_command_count
    assert all(row['cwd'] != case['workspace'] for row in case['commands'])
    assert (case['workspace'] / 'stock.py').read_text() == COMPLETE
    assert (case['workspace'] / 'test_public.py').read_bytes() == original_files['test_public.py']
    final_document, _ = case['checkpoints'][-1]
    assert final_document['executorId'] == document['executorId']
    state = final_document['reviewState']
    assert any(row['probeId'] == old_probe_id and row['receipt']['status'] == 'failed'
               for row in state['receipts']), 'Original failed evidence must survive correction or repair.'
    assert all(row in state['receipts'] for row in saved_state['receipts'])
    if product_gap:
        assert checks and final_document['candidateDigest'] != document['candidateDigest']
        assert any(row['probeId'] == old_probe_id and row['receipt']['status'] == 'passed' for row in state['receipts'])
    else:
        assert final_document['candidateDigest'] == document['candidateDigest']
        assert {name: (case['workspace'] / name).read_bytes() for name in original_files} == original_files
        assert any(row['probeId'] != old_probe_id and row['receipt']['status'] == 'passed' for row in state['receipts'])
