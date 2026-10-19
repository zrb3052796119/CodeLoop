"""A failed native repair commit can retry within the original review and owner."""
from copy import deepcopy

from minicode.agent_loop import run_agent_turn
from minicode.coverage_audit_state import record_document
from minicode.implementation_completion_review import ImplementationCompletionReview, requirement_units
from minicode.types import AgentStep
import test_completion_feedback_chronology as chronology
from test_completion_feedback_chronology import OBJECTIVE, _call, _result

inventory = chronology.inventory


def test_retry_after_native_negative_commit_failure(inventory, monkeypatch):
    case = inventory
    ids = [r['id'] for r in requirement_units(OBJECTIVE)]
    seen = {'review': 0, 'parent': 0, 'commitAttempts': 0}
    original_persist = ImplementationCompletionReview._persist
    initial_source = (case['workspace'] / 'stock.py').read_bytes()
    original_read = None
    retry_failure = None
    stop = None

    def fail_once(self, kind, document):
        if kind == 'repair-decision':
            seen['commitAttempts'] += 1
            if seen['commitAttempts'] == 1:
                raise OSError('temporary repair storage failure')
        return original_persist(self, kind, document)

    monkeypatch.setattr(ImplementationCompletionReview, '_persist', fail_once)

    def submit(identity):
        return _call(identity, 'completion_review_submit', status='changes_required',
            requirements=[{'id': rid, 'status': 'missing' if i == 1 else 'unknown',
                'sourceEvidence': [{'path': 'stock.py', 'startLine': 4, 'endLine': 5}] if i == 1 else [],
                'probeIds': [], 'rationale': 'release leaves stock unchanged.' if i == 1 else 'Not claimed.'}
                for i, rid in enumerate(ids)], findings=['Implement the requested release addition.'])

    class Model:
        def next(self, messages, **kwargs):
            nonlocal original_read, retry_failure
            names = set(kwargs['tools_override'].list_all())
            assert 'completion_coverage_audit_submit' not in names
            assert 'completion_probe_adjudication_submit' not in names
            if 'completion_review_submit' in names:
                seen['review'] += 1
                if seen['review'] == 1:
                    return submit('first')
                if seen['review'] == 2:
                    failed = _result(messages, 'first')
                    assert failed['isError']
                    assert 'tool_crashed' in failed['content'] and 'OSError' in failed['content']
                    return submit('retry')
                retry_failure = deepcopy(_result(messages, 'retry'))
                raise KeyboardInterrupt('retry was rejected')
            seen['parent'] += 1
            if seen['parent'] == 1:
                return _call('original-read', 'read_file', path='stock.py')
            if seen['parent'] == 2:
                original_read = deepcopy(_result(messages, 'original-read'))
                return AgentStep(type='assistant', kind='final', content='Ready for independent verification.')
            assert seen['commitAttempts'] == 2
            assert _result(messages, 'original-read') == original_read
            raise KeyboardInterrupt('same owner received committed repair')

    try:
        run_agent_turn(model=Model(), messages=[{'role': 'user', 'content': OBJECTIVE}], **case['options'])
    except KeyboardInterrupt as error:
        stop = str(error)
    assert retry_failure is None, retry_failure
    assert stop == 'same owner received committed repair'
    assert seen == {'review': 2, 'parent': 3, 'commitAttempts': 2}
    document, messages = case['checkpoints'][-1]
    repair = record_document(document['reviewState']['acceptedRepairDecision'])
    assert repair['origin'] == 'validated_review'
    assert repair['candidateDigest'] == document['candidateDigest']
    assert repair['decision']['status'] == 'changes_required'
    decisions = [event['payload'] for event in case['events']
        if event['type'] == 'implementation.completion_decided']
    assert len(decisions) == 1
    assert decisions[0]['executorId'] == document['executorId']
    assert decisions[0]['continuesSameExecutor'] is True
    assert _result(messages, 'original-read') == original_read
    assert (case['workspace'] / 'stock.py').read_bytes() == initial_source
