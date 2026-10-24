"""Real review paths expose progress without changing acceptance authority."""

from copy import deepcopy
import json

from minicode.completion_feedback_view import CompletionFeedbackViews
from minicode.coverage_audit_state import record_document
from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.types import AgentStep
from test_native_review_economics import economics, _decisions


def test_review_exhaustion_reports_unentered_legs_and_retained_evidence(economics):
    case = economics['run']('repeat_entry')
    expected = [
        {'leg': 'review', 'state': 'budget_exhausted'},
        {'leg': 'inspection', 'state': 'not_entered'},
        {'leg': 'reconciliation', 'state': 'not_entered'},
    ]
    decision = _decisions(case)[-1]
    exhausted = next(row['payload'] for row in case['events']
                     if row['type'] == 'implementation.completion_review_budget_exhausted')
    assert decision['reviewLegs'] == exhausted['reviewLegs'] == expected
    assert decision['completionVersion'] == 1 and decision['requirementsAccepted'] is False
    feedback = next(row for row in case['messages'] if row.get('_implementation_completion_feedback'))
    original = feedback['_completion_evidence_original']
    receipt = json.loads(original.split('\nCompletion evidence:\n', 1)[1])
    assert receipt['reviewLegs'] == expected
    assert 'coverage inspection, evidence reconciliation have not started' in feedback['content']
    assert 'review exhausted its budget' in feedback['content']
    assert decision['evidenceDirectory'] in feedback['content']
    restored = CompletionFeedbackViews().project_message(feedback)
    assert restored['content'] == feedback['content']
    assert restored['_completion_evidence_original'] == original
    assert restored['content'].count('Completion review is incomplete:') == 1
    owner = ImplementationCompletionReview.from_state(case['owner'].spec, case['checkpoints'][-1][0]['reviewState'])
    stopped_before_selection = {**case['created'][0], 'active': None}
    prior = deepcopy(owner.state)
    assert owner.review_legs(decision['patchDigest'], limits=stopped_before_selection,
                             budget_exhausted=True) == [
        {'leg': 'review', 'state': 'interrupted'},
        {'leg': 'inspection', 'state': 'not_entered'},
        {'leg': 'reconciliation', 'state': 'not_entered'},
    ]
    assert all(row['state'] == 'not_entered' for row in owner.review_legs(
        'sha256:' + '0' * 64, limits=stopped_before_selection, budget_exhausted=True))
    assert owner.state == prior


def test_pending_inspection_interruption_is_not_reported_as_unentered(economics, monkeypatch):
    import test_native_review_economics as fixture_module

    original_step = fixture_module._inspection_step
    calls = 0

    def read_then_pause(messages, registry):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_step(messages, registry)
        return AgentStep(type='assistant', kind='final', content='Inspection remains unfinished.')

    monkeypatch.setattr(fixture_module, '_inspection_step', read_then_pause)
    case = economics['run']()
    decision = _decisions(case)[-1]
    assert decision['reason'] == 'coverage_audit_interrupted'
    assert case['model'].counts['inspection'] == 2
    state = case['checkpoints'][-1][0]['reviewState']
    inspection = record_document(state['pendingCoverageAudit']['inspectionRecord'])
    assert inspection['reads'] and inspection['result'] is None
    owner = ImplementationCompletionReview.from_state(case['owner'].spec, state)
    before = deepcopy(owner.state)
    assert owner.review_legs(decision['patchDigest'], decision=decision) == [
        {'leg': 'review', 'state': 'completed'},
        {'leg': 'inspection', 'state': 'interrupted'},
        {'leg': 'reconciliation', 'state': 'not_entered'},
    ]
    assert owner.state == before
    # The new payload is budget-only; the native paused decision stays intact.
    assert 'reviewLegs' not in decision and 'completionReview' not in decision


def test_approved_completion_keeps_receipt_free_of_leg_display_fields(economics):
    case = economics['run']()
    decision = _decisions(case)[-1]
    assert decision['completionReview'] == 'approved' and decision['requirementsAccepted'] is True
    assert decision['completionVersion'] == 1 and 'reviewLegs' not in decision
    state = case['checkpoints'][-1][0]['reviewState']
    assert state['reviews'][-1]['status'] == 'approved'
    assert all('reviewLegs' not in row for row in state['reviews'])
    owner = ImplementationCompletionReview.from_state(case['owner'].spec, state)
    assert owner.review_legs(decision['patchDigest'], decision=decision) == [
        {'leg': 'review', 'state': 'completed'},
        {'leg': 'inspection', 'state': 'completed'},
        {'leg': 'reconciliation', 'state': 'approved'},
    ]
    assert not any(row.get('_implementation_completion_feedback') for row in case['messages'])
