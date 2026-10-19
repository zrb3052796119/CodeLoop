"""Host coverage rejection must identify the actual bad references, not infer approval."""

import json

import pytest

from minicode.reviewer_verification import ReviewerProbe, ReviewerProbeLedger
from minicode.reviewer_result_submission import create_reviewer_submit_tool
from minicode.tooling import ToolContext, ToolRegistry
from test_reviewer_verification import PATCH, OTHER, _receipt
from test_reviewer_result_submission import _approval, _bound


def _probe(criteria, label):
    return ReviewerProbe.create(criterion_ids=criteria, scenario=label,
        code=f'def test_{label}():\n    assert 1 == 1\n')


def _record(ledger, criteria, label, *, patch=PATCH, status='passed'):
    probe = _probe(criteria, label)
    ledger.record(probe, patch, _receipt(probe, patch=patch, status=status,
        exit_code=0 if status == 'passed' else 1))
    return probe


def _error(ledger, criterion, ids, patch=PATCH):
    with pytest.raises(ValueError) as captured:
        ledger.approval_coverage(patch_digest=patch, criterion_id=criterion, probe_ids=ids)
    # Access via exception instance, so pre-fix reaches real rejection rather
    # than failing to import a newly introduced exception class.
    details = getattr(captured.value, 'diagnostics', None)
    assert isinstance(details, dict), str(captured.value)
    assert json.loads(str(captured.value)) == details
    return captured.value, details


def test_wrong_probe_mapping_reports_all_bad_ids_and_available_host_mapping():
    ledger = ReviewerProbeLedger()
    good = _record(ledger, ['ac_cache'], 'cache')
    wrong = _record(ledger, ['ac_errors', 'ac_idempotence'], 'errors')
    before = ledger.feedback()
    error, details = _error(ledger, 'ac_cache', [good.probe_id, wrong.probe_id, 'review_probe_unknown'])
    assert details['errorCode'] == 'review_probe_coverage_mismatch'
    assert details['criterionId'] == 'ac_cache' and details['patchDigest'] == PATCH
    assert details['invalidProbeReferences'] == [
        {'probeId': wrong.probe_id, 'reasons': ['criterion_mismatch'],
         'registeredCriterionIds': ['ac_errors', 'ac_idempotence'], 'currentStatus': 'passed'},
        {'probeId': 'review_probe_unknown', 'reasons': ['unknown'],
         'registeredCriterionIds': [], 'currentStatus': None},
    ]
    assert {row['probeId']: row['criterionIds'] for row in details['availablePassedProbes']} == {
        good.probe_id: ['ac_cache'], wrong.probe_id: ['ac_errors', 'ac_idempotence']}
    details['invalidProbeReferences'].clear()
    assert error.diagnostics['invalidProbeReferences'], 'Consumer mutation must not change saved diagnostics.'
    assert ledger.feedback() == before
    assert ledger.approval_coverage(patch_digest=PATCH, criterion_id='ac_cache', probe_ids=[good.probe_id]) == (good.probe_id,)


@pytest.mark.parametrize(('state', 'reason', 'status'), [
    ('stale', 'stale', None), ('failed', 'not_passed', 'failed'),
    ('inconclusive', 'inactive', 'inconclusive'), ('retired', 'retired', 'passed'),
])
def test_reason_is_derived_from_current_host_state(state, reason, status):
    ledger = ReviewerProbeLedger()
    probe = _probe(['ac_cache'], 'cache')
    receipt = _receipt(probe, patch=OTHER if state == 'stale' else PATCH,
        status='failed' if state == 'failed' else 'passed', exit_code=1 if state == 'failed' else 0)
    if state == 'inconclusive':
        receipt['status'] = 'inconclusive'
    ledger.record(probe, receipt['patchDigest'], receipt)
    if state == 'retired':
        # Simulate the already-covered host correction transition; no source
        # or model message is used to authorize retirement in production.
        ledger._active.pop(probe.probe_id)
        ledger._retired.add(probe.probe_id)
    before = ledger.feedback()
    _caught, details = _error(ledger, 'ac_cache', [probe.probe_id])
    row = details['invalidProbeReferences'][0]
    assert reason in row['reasons'] and row['currentStatus'] == status
    assert row['registeredCriterionIds'] == ['ac_cache']
    assert details['availablePassedProbes'] == []
    assert ledger.feedback() == before


def test_native_rejection_delivers_mapping_without_writing_approval_channel():
    assignment, store, channel, _tool, kwargs = _bound(build=False)
    ledger = ReviewerProbeLedger()
    good = _record(ledger, ['ac_behavior'], 'behavior')
    other = _record(ledger, ['ac_tests'], 'tests')
    def guard(raw, _declaration, _context):
        ledger.require_all_current_passed(PATCH)
        for row in raw['criteria']:
            ledger.approval_coverage(patch_digest=PATCH,
                criterion_id=row['criterionId'], probe_ids=row['probeIds'])
    tool = create_reviewer_submit_tool(assignment, channel, **kwargs,
        coverage_ref=f'artifact_review_coverage_{assignment.assignment_id}', coverage_guard=guard)
    value = _approval()
    value['criteria'][0].update(rationale='Cache behavior inspected.', probeIds=[good.probe_id, other.probe_id])
    value['criteria'][1].update(rationale='Tests inspected.', probeIds=[other.probe_id])
    result = ToolRegistry([tool]).execute(tool.name, value, ToolContext(_subagent_mailbox=channel))
    assert not result.ok and result._review_evidence_incomplete
    assert store.read(channel.write_key) is None
    prefix = 'error[review_evidence_incomplete]: '
    assert result.output.startswith(prefix)
    details = json.loads(result.output[len(prefix):])
    assert details['criterionId'] == 'ac_behavior'
    assert details['invalidProbeReferences'][0]['probeId'] == other.probe_id
    assert details['invalidProbeReferences'][0]['registeredCriterionIds'] == ['ac_tests']


def test_shape_validation_and_current_passing_coverage_remain_unchanged():
    ledger = ReviewerProbeLedger()
    probe = _record(ledger, ['ac_cache'], 'cache')
    for ids in ([], [probe.probe_id, probe.probe_id], [False], 'not-a-list'):
        with pytest.raises(ValueError, match='distinct'):
            ledger.approval_coverage(patch_digest=PATCH, criterion_id='ac_cache', probe_ids=ids)
    assert ledger.approval_coverage(patch_digest=PATCH, criterion_id='ac_cache', probe_ids=[probe.probe_id]) == (probe.probe_id,)
