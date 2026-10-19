"""Generic local real-read/error-delivery/typed-resume recovery controls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from minicode.completion_coverage_inspection import CoverageInspection
from minicode.context_manager import ContextManager
from minicode.coverage_audit_recovery import HostCoverageAuditRecovery
from minicode.coverage_audit_state import build_coverage_audit_state, host_record, record_document, validate_coverage_audit_state
from minicode.implementation_completion_review import ImplementationCompletionReview, requirement_units
from minicode.implementation_continuation import HostImplementationContinuation, continuation_digest, implementation_spec_digest
from minicode.implementation_execution import ImplementationSpec
from minicode.requirement_obligations import build_obligation_manifest
from minicode.review_evidence_documents import EvidenceDocuments, canonical, content_digest
from minicode.scheduler_permissions import SchedulerWorkerReadPermissions
from minicode.tooling import ToolContext, ToolDefinition, ToolRegistry
from minicode.tools.read_file import read_file_tool


def _case(tmp_path, *, spec=None, candidate=None, workspace=None):
    objective = spec.objective if spec is not None else '1. Increment a generic counter by one.'
    spec = spec or ImplementationSpec(('counter.py',), (), objective)
    workspace = workspace or tmp_path
    (workspace / 'counter.py').write_text('def increment(value):\n    return value + 1\n')
    candidate = candidate or 'sha256:' + 'a' * 64
    units = list(requirement_units(objective))
    proposal = {'status': 'approved', 'requirements': [
        {'id': row['id'], 'status': 'covered', 'sourceEvidence': [], 'probeIds': [], 'rationale': 'Unapproved ordinary proposal.'}
        for row in units], 'findings': [], 'patchDigest': candidate}
    bindings = {'candidateDigest': candidate, 'objectiveDigest': content_digest(objective),
        'proposalDigest': content_digest(canonical(proposal)), 'supersessionDigest': content_digest('[]')}
    records = []
    def persist(kind, value):
        path = tmp_path / f'{len(records):05d}-{kind}.json'
        path.write_text(canonical(value))
        records.append((kind, path))
        return str(path)
    def retain(kind, value):
        path = persist(kind, value)
        return host_record(path, Path(path).read_bytes())
    manifest = build_obligation_manifest(objective, units)
    authority = retain('coverage-audit-authority', {**bindings, 'originalObjective': objective,
        'requirements': units, 'proposal': proposal, 'sourceAnchors': {}, 'probeEvidence': {},
        'supersessionObligations': [], 'behaviorContract': manifest})
    audit_id = 'coverage_audit_' + hashlib.sha256(authority['ref'].encode()).hexdigest()[:24]
    documents = retain('coverage-audit-evidence-documents', {**bindings,
        'documents': EvidenceDocuments(candidate_digest=candidate).snapshot()})
    delivery = retain('coverage-audit-delivery', {'auditId': audit_id, **bindings, 'pairs': []})
    manager = ContextManager(model='generic-local-recovery')
    permissions = SchedulerWorkerReadPermissions(str(workspace), read_roots=('.',), forbidden_paths=('.git',))
    owner = ImplementationCompletionReview(spec)
    def anchors(rows):
        return owner._anchors(rows, workspace, permissions)
    inspection_kwargs = dict(bindings={'auditId': audit_id, **bindings}, objective=objective,
        requirements=units, manifest=manifest, cwd=str(workspace), permissions=permissions,
        source_anchors=anchors, probe_evidence=lambda _pid: None, persist=persist, manager=manager,
        finish_negative=None, capability_profile=None)
    inspection = CoverageInspection(**inspection_kwargs)
    manager.messages = inspection.initial_messages()
    def forbidden_execution(_value, _context):
        raise AssertionError('The inspection historical-fields guard must reject before execution.')
    review_test = ToolDefinition(name='review_test', description='Local never-executed test capability.',
        input_schema={'type': 'object'}, validator=lambda value: value, run=forbidden_execution)
    tools = inspection.registry(ToolRegistry([read_file_tool, review_test]))
    context = ToolContext(cwd=str(workspace), permissions=permissions)
    def execute(name, value, registry=tools):
        result = registry.execute(name, value, context)
        identity = 'local-' + str(len(manager.messages))
        manager.messages.extend([
            {'role': 'assistant_tool_call', 'toolUseId': identity, 'toolName': name, 'input': deepcopy(value)},
            {'role': 'tool_result', 'toolUseId': identity, 'toolName': name, 'isError': not result.ok, 'content': result.output}])
        if result._delivery_ack is not None:
            result._delivery_ack()
        return result
    assert execute('read_file', {'path': 'counter.py'}).ok
    old_pending = build_coverage_audit_state(authority_record=authority, documents_record=documents,
        messages_record=retain('coverage-audit-messages', {'auditId': audit_id, **bindings, 'messages': manager.messages}),
        delivery_record=delivery, inspection_record=inspection.record)
    owner._pending_coverage_audit = old_pending
    parent_messages = [{'role': 'system', 'content': objective}, {'role': 'assistant', 'content': 'Original executor remains active.'}]
    document = {'continuationVersion': 1, 'executorId': 'implementation_' + 'b' * 32, 'attempt': 2,
        'candidateDigest': candidate, 'specDigest': implementation_spec_digest(spec),
        'messagesDigest': continuation_digest(parent_messages), 'reviewState': owner.state, 'completionFeedback': None}
    continuation = HostImplementationContinuation.from_document(document, spec=spec, messages=parent_messages)
    value = {'purpose': 'acceptance', 'criterionIds': [units[0]['id']], 'scenario': 'Generic omitted guard case',
        'code': 'def test_generic():\n    assert 1 == 1\n', 'correctsProbeId': 'not-an-allowed-inspection-field'}
    refused = execute('review_test', value)
    assert not refused.ok
    # Preserve the actual model-visible native message including a host note;
    # recovery never reconstructs this result from a shorter tool log.
    manager.messages[-1]['content'] += '\nSystem note: the unchanged request was rejected.'
    late = {'messagesRecord': retain('coverage-audit-messages', {'auditId': audit_id, **bindings, 'messages': manager.messages}),
        'inspectionRecord': inspection.record, 'deliveryRecord': delivery}
    return locals()


def recover(case, **overrides):
    late = overrides.pop('late', case['late'])
    return HostCoverageAuditRecovery.from_records(continuation=overrides.pop('continuation', case['continuation']),
        spec=case['spec'], messages_record=late['messagesRecord'], inspection_record=late['inspectionRecord'],
        delivery_record=late['deliveryRecord'], source_archive_digest='sha256:' + 'c' * 64,
        persist=overrides.pop('persist', case['persist']), **overrides)


def test_original_checkpoint_rejects_missing_actual_error_delivery(tmp_path):
    case = _case(tmp_path)
    with pytest.raises(ValueError, match='current coverage call lacks delivered provenance'):
        build_coverage_audit_state(authority_record=case['authority'], documents_record=case['documents'],
            messages_record=case['late']['messagesRecord'], delivery_record=case['late']['deliveryRecord'],
            inspection_record=case['late']['inspectionRecord'])


def test_typed_recovery_preserves_original_owner_messages_reads_and_resumes_ready(tmp_path):
    case = _case(tmp_path)
    document_before = deepcopy(case['document'])
    pending_before = deepcopy(case['old_pending'])
    late_before = deepcopy(case['late'])
    recovery = recover(case)
    host = HostImplementationContinuation.for_coverage_recovery(case['document'], spec=case['spec'],
        messages=case['parent_messages'], recovery=recovery)
    assert host.document == document_before == case['document']
    assert host.executor_id == case['continuation'].executor_id and host.attempt == 2
    assert host.feedback == case['continuation'].feedback
    assert case['old_pending'] == pending_before and case['late'] == late_before
    restored = host.restore_review(case['spec'])
    state = restored.state['pendingCoverageAudit']
    assert validate_coverage_audit_state(state, objective=case['objective']) == state
    assert state['auditId'] == pending_before['auditId']
    assert state['messagesRecord'] == late_before['messagesRecord']
    assert state['inspectionRecord'] == late_before['inspectionRecord']
    pair = record_document(state['deliveryRecord'])['pairs'][0]
    actual = record_document(late_before['messagesRecord'])['messages'][-2:]
    assert pair == {'call': actual[0], 'result': actual[1]}
    assert pair['result']['isError'] is True and 'System note:' in pair['result']['content']
    assert restored.state['probes'] == case['document']['reviewState']['probes'] == []
    # Restore the retained source ledger and dialogue. The next tool is ready,
    # with no repeated read or new independent epoch, and no completion approval.
    case['manager'].messages = record_document(state['messagesRecord'])['messages']
    inspection = CoverageInspection(**case['inspection_kwargs'], record=state['inspectionRecord'])
    registry = inspection.registry(ToolRegistry([read_file_tool]))
    assert registry.find('completion_coverage_audit_submit') is None
    ready = {'status': 'ready_for_evidence', 'observations': [{'requirementId': case['units'][0]['id'],
        'status': 'checked', 'sourceEvidence': [{'path': 'counter.py', 'startLine': 1, 'endLine': 2}],
        'expectedBehavior': 'Increment by one.', 'observedBehavior': 'Read the actual return value + 1 body.', 'probeIds': []}], 'findings': []}
    result = case['execute']('completion_coverage_inspection_submit', ready, registry)
    assert result.ok and json.loads(result.output)['grantsCoverage'] is False
    assert inspection.value['reads'] == record_document(late_before['inspectionRecord'])['reads']
    assert inspection.value['challenges'] == [] and inspection.value['phase'] == 'inspection'
    receipt = record_document(recovery.receipt_record)
    assert receipt['checkpointDigest'] == continuation_digest(document_before)
    assert receipt['oldPendingDigest'] == continuation_digest(pending_before)
    assert receipt['addedErrorDeliveries'][0]['toolUseId'] == actual[0]['toolUseId']
    assert 'rawJson' not in json.dumps(receipt) and 'oldPending' not in receipt
    assert len(json.dumps(receipt)) < 6000
    # Revalidation is deterministic with read-only persistence returning exactly
    # the two already-pinned references; no original record is edited.
    saved = {kind: path for kind, path in case['records'] if 'recovery' in kind}
    def read_only_persist(kind, document):
        assert json.loads(saved[kind].read_text()) == document
        return str(saved[kind])
    replay = recover(case, persist=read_only_persist)
    assert replay.receipt_record == recovery.receipt_record
    assert replay.pending_state == recovery.pending_state


@pytest.mark.parametrize('mutation', ['owner', 'candidate', 'parent_messages', 'old_pending', 'late_binding',
    'nonprefix', 'success_missing', 'forged_read', 'forged_challenge', 'phase', 'late_pair_changed', 'foreign_record'])
def test_recovery_rejects_owner_authority_credit_and_history_changes(tmp_path, mutation):
    case = _case(tmp_path)
    recovery = recover(case)
    if mutation in {'owner', 'candidate', 'parent_messages', 'old_pending'}:
        document = deepcopy(case['document'])
        if mutation == 'owner':
            document['executorId'] = 'implementation_' + 'd' * 32
        elif mutation == 'candidate':
            document['candidateDigest'] = 'sha256:' + 'd' * 64
        elif mutation == 'parent_messages':
            document['messagesDigest'] = 'sha256:' + 'd' * 64
        else:
            document['reviewState']['pendingCoverageAudit']['messagesRecord'] = case['late']['messagesRecord']
        with pytest.raises(ValueError):
            recovery.validate_for(document, spec=case['spec'])
        return
    late = deepcopy(case['late'])
    if mutation == 'late_binding':
        value = record_document(late['messagesRecord'])
        value['candidateDigest'] = 'sha256:' + 'd' * 64
        late['messagesRecord'] = host_record(late['messagesRecord']['ref'], canonical(value))
    elif mutation in {'nonprefix', 'success_missing'}:
        value = record_document(late['messagesRecord'])
        if mutation == 'nonprefix':
            value['messages'][0]['content'] += ' changed history'
        else:
            value['messages'][-1]['isError'] = False
        late['messagesRecord'] = host_record(late['messagesRecord']['ref'], canonical(value))
    elif mutation in {'forged_read', 'forged_challenge', 'phase'}:
        value = record_document(late['inspectionRecord'])
        if mutation == 'forged_read':
            row = deepcopy(value['reads'][0])
            row['call']['toolUseId'] = row['result']['toolUseId'] = 'invented'
            value['reads'].append(row)
        elif mutation == 'forged_challenge':
            value['challenges'].append('probe_unexecuted')
        else:
            value['result'] = {'status': 'ready_for_evidence', 'observations': [{}], 'findings': []}
        late['inspectionRecord'] = host_record(late['inspectionRecord']['ref'], canonical(value))
    elif mutation == 'late_pair_changed':
        value = record_document(late['deliveryRecord'])
        messages = record_document(late['messagesRecord'])['messages']
        value['pairs'] = [{'call': messages[-2], 'result': {**messages[-1], 'content': 'rewritten diagnostic'}}]
        late['deliveryRecord'] = host_record(late['deliveryRecord']['ref'], canonical(value))
    else:
        late['messagesRecord']['ref'] = str(tmp_path / 'unrelated' / 'messages.json')
    before = len(case['records'])
    with pytest.raises(ValueError):
        recover(case, late=late)
    assert len(case['records']) == before


@pytest.mark.parametrize('change', [None, 'probe', 'candidate', 'receipt_ref', 'receipt_body', 'is_error', 'guard'])
def test_challenge_result_must_match_exact_validated_owner_receipt(change):
    from minicode.coverage_audit_recovery import _challenge_result_matches
    from minicode.completion_evidence_view import EvidenceViews
    from minicode.reviewer_verification import ReviewerProbe
    # This narrow test checks result-to-ledger identity only. The outer recovery
    # validates actual receipts via ImplementationCompletionReview.from_state.
    probe = ReviewerProbe.create(criterion_ids=['requirement_1'], scenario='Generic receipt identity',
        code='def test_counter():\n    assert 1 == 1\n')
    candidate = 'sha256:' + 'e' * 64
    wrapped = {'probeId': probe.probe_id, 'candidateDigest': candidate, 'receiptRef': '/host/001-probe-receipt.json',
        'receipt': {'status': 'passed', 'sourceDigest': probe.source_digest}}
    output = EvidenceViews(candidate_digest=candidate).view(wrapped)
    if change == 'probe':
        output['probeId'] = 'other_probe'
    elif change == 'candidate':
        output['candidateDigest'] = 'sha256:' + 'f' * 64
    elif change == 'receipt_ref':
        output['receiptRef'] = '/host/002-other-receipt.json'
    elif change == 'receipt_body':
        output['receipt']['sourceDigest'] = 'sha256:' + 'f' * 64
    pair = {'result': {'content': json.dumps(output) + '\nSystem note: retained host diagnostic.',
                       'isError': change == 'is_error'}}
    if change == 'guard':
        pair['result']['content'] = 'Historical probe correction is unavailable during inspection.'
        pair['result']['isError'] = True
    assert _challenge_result_matches(pair, probe, wrapped, candidate) is (change is None)
