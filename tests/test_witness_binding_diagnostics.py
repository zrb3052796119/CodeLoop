"""Exact binding errors reach native provider wire; no semantic answer supplied."""
from copy import deepcopy
import json

from minicode.completion_coverage_audit import audit_completion_coverage
from minicode.permissions import PermissionManager
from minicode.review_probe_execution import run_isolated_review_probe
from minicode.reviewer_verification import ReviewerProbe, ReviewerProbeLedger
from minicode.workflow_workspace import WorkflowWorkspaceTransaction
from test_completion_coverage_audit import _case, OBJECTIVE, _coverage_packet
from test_coverage_audit_resume import native, outputs


def add_executed_probe(case, criterion_id):
    probe = ReviewerProbe.create(criterion_ids=[criterion_id], scenario='One registered inventory criterion',
                                code=case['probe'].code)
    with WorkflowWorkspaceTransaction(str(case['root'])) as transaction:
        frozen = transaction.freeze(allowed_paths=case['spec'].write_paths, paths_relative_to_cwd=True)
        receipt = run_isolated_review_probe(probe=probe, frozen=frozen, check=case['check'],
            verifier=case['verifier'], test_path='test_public.py', read_roots=('.',), forbidden_paths=('.git',),
            run_kwargs={'workflow_id': 'wf_' + 'b' * 32, 'task_id': 'task_binding_probe',
                'execution_assignment_id': 'asg_binding', 'patch_digest': frozen.patch_digest,
                'permissions': PermissionManager(str(case['root']), prompt=None), 'permission_cwd': str(case['root'])})
        receipt = ReviewerProbeLedger().record(probe, frozen.patch_digest, receipt)
    assert receipt['status'] == 'passed'
    wrapped = {'probeId': probe.probe_id, 'candidateDigest': case['candidate_digest'], 'receipt': receipt,
               'receiptRef': case['persist']('pytest-receipt', receipt)}
    return {'source': probe.to_dict(), 'receipt': wrapped, 'purpose': 'acceptance', 'active': True}


def test_native_wire_batches_all_binding_errors_then_rechecks_each_draft_patch(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)
    units = case['units']
    evidence = {case['probe'].probe_id: case['evidence']}
    singles = [add_executed_probe(case, row['id']) for row in units]
    for item in singles:
        evidence[item['source']['probeId']] = item
    for index, row in enumerate(case['proposal']['requirements']):
        row['probeIds'].append(singles[index]['source']['probeId'])
    before = deepcopy(evidence)
    invalid = deepcopy(case['valid'])
    for index, row in enumerate(invalid['requirements']):
        row['witnesses'][0]['probeId'] = singles[1 - index]['source']['probeId']
    observed = []

    def action(body, _count):
        packet = _coverage_packet(body)
        pages = {value['documentId']: value for value in outputs(body) if 'documentId' in value}
        for item in packet['evidenceIndex']:
            doc = item['sourceDocumentId']
            page = pages.get(doc)
            if page is None or page['nextCursor'] is not None:
                return 'review_evidence_read', {'documentId': doc} if page is None else {'cursor': page['nextCursor']}
            assert page['completeDelivered']
        errors = [value for value in outputs(body) if value.get('errorCode') == 'coverage_audit_invalid']
        if not errors:
            return 'completion_coverage_audit_submit', invalid
        error = errors[-1]
        expected_indexes = [0, 1] if len(errors) == 1 else [1]
        assert error['missingDocumentIds'] == []
        assert [row['path'] for row in error['fieldErrors']] == [f'/requirements/{i}/witnesses/0' for i in expected_indexes]
        for row, index in zip(error['fieldErrors'], expected_indexes):
            wrong = singles[1 - index]['source']
            assert row['criterionId'] == units[index]['id'] and row['probeId'] == wrong['probeId']
            assert row['bindingFailures'] == [{'code': 'criterion_not_bound', 'field': 'source.criterionIds',
                'expected': units[index]['id'], 'actual': wrong['criterionIds']}]
            assert row['sourceDocumentId'] in pages
        observed.append(deepcopy(error))
        index = len(errors) - 1
        return 'completion_coverage_audit_submit', {'draftId': error['draftId'], 'updates': [
            {'path': f'/requirements/{index}/witnesses', 'value': case['valid']['requirements'][index]['witnesses']}]}

    calls = native(monkeypatch, action)
    result = audit_completion_coverage(objective=OBJECTIVE, requirements=units, proposal=case['proposal'],
        candidate_digest=case['candidate_digest'], model=case['adapter'], tools=case['tools'],
        cwd=str(case['root']), permissions=case['permissions'], host=case['host'], event_sink=None,
        persist=case['persist'], checkpoint=lambda: None, probe_evidence=lambda pid: evidence.get(pid),
        source_anchors=lambda anchors: case['owner']._anchors(anchors, case['root'], case['permissions']))
    assert result['status'] == 'approved' and len(observed) == 2 and len(calls) == 6
    assert len(calls.inspection_requests) == 2 and len(calls.all_requests) == 8
    assert before == evidence
    drafts = [json.loads(path.read_text()) for kind, path in case['saved'] if kind == 'coverage-audit-submission-draft']
    assert drafts[0]['submission'] == invalid
    assert drafts[1]['submission']['requirements'][1] == invalid['requirements'][1]
    assert result['requirements'][0]['witnesses'][0]['probeId'] == case['probe'].probe_id


def test_shared_predicate_reports_all_identity_failures_without_weakening_failed_boundary(tmp_path, monkeypatch):
    from minicode.completion_coverage_audit import _witness_binding_failures
    case = _case(tmp_path, monkeypatch)
    probe = case['probe']
    source = probe.to_dict()
    evidence = deepcopy(case['evidence'])
    wrapped = evidence['receipt']
    candidate = case['candidate_digest']
    witness = case['valid']['requirements'][0]['witnesses'][0]
    args = (case['units'][0]['id'], witness, source, probe, wrapped, evidence, candidate)
    assert _witness_binding_failures(*args) == []
    wrapped['receipt']['status'] = 'failed'
    assert [x['code'] for x in _witness_binding_failures(*args)] == ['receipt_status']
    assert _witness_binding_failures(*args, allow_failed=True) == []
    evidence.update(active=False, purpose='diagnostic')
    wrapped.update(candidateDigest='sha256:' + '0' * 64, probeId='wrong', receiptRef='')
    bad = _witness_binding_failures('req_unregistered', witness, source, probe, wrapped, evidence, candidate)
    assert {x['code'] for x in bad} == {'criterion_not_bound', 'inactive_probe', 'non_acceptance_probe',
        'receipt_probe', 'candidate_mismatch', 'receipt_status', 'receipt_reference'}
    assert {x['code'] for x in _witness_binding_failures('req_unregistered', witness, source, probe,
        wrapped, evidence, candidate, allow_failed=True)} == {x['code'] for x in bad} - {'receipt_status'}
