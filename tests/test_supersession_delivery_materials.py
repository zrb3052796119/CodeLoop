"""Real pytest traces remain evidence, without compulsory whole-tree delivery."""
from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from minicode.completion_coverage_audit import audit_completion_coverage
from minicode.coverage_audit_state import (
    coverage_delivery_pairs, host_record, record_document, restore_evidence_documents,
    validate_coverage_audit_state,
)
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.probe_failure_adjudication import ProbeFailureAdjudicator
from minicode.probe_supersession import make_supersession
from minicode.review_evidence_documents import canonical, content_digest
from minicode.review_probe_execution import run_isolated_review_probe
from minicode.reviewer_verification import ReviewerProbe, ReviewerProbeLedger
from minicode.workflow_workspace import WorkflowWorkspaceTransaction
from test_completion_coverage_audit import _case, _native, OBJECTIVE, PROBE


def packet(body):
    return next(json.loads(m['content']) for m in body['messages'] if m['role'] == 'user')


def last(body):
    return json.JSONDecoder().raw_decode(next(m['content'] for m in reversed(body['messages']) if m['role'] == 'tool'))[0]


def fixture(tmp_path, monkeypatch, *, count=8):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    product = ('def hold(n, amount):\n    for hop in HOPS:\n        n = hop(n)\n    return n - amount\n'
               'def release(n, amount):\n    return n + amount\n')
    product += ''.join(f'def hop_{i}(n):\n    return n\n' for i in range(count))
    product += 'HOPS = (' + ','.join(f'hop_{i}' for i in range(count)) + ',)\n'
    code = PROBE if count < 1000 else '\n'.join(PROBE.replace('test_inventory', f'test_inventory_{i}') for i in range(3))
    case = _case(tmp_path, monkeypatch, product=product, code=code)
    old = ReviewerProbe.create(criterion_ids=case['probe'].criterion_ids,
        scenario=case['probe'].scenario, code=case['probe'].code.replace('== 5', '== 999'))

    class Sandbox:
        def wrap_command(self, *, argv, **kwargs):
            return argv

    verifier = DeterministicVerifier(sandbox_backend=Sandbox(),
        explicit_trusted_executables={'python3': sys.executable})
    with WorkflowWorkspaceTransaction(str(case['root'])) as transaction:
        frozen = transaction.freeze(allowed_paths=case['spec'].write_paths, paths_relative_to_cwd=True)
        receipt = run_isolated_review_probe(probe=old, frozen=frozen, check=case['check'], verifier=verifier,
            test_path='test_public.py', read_roots=('.',), forbidden_paths=('.git', '.env'),
            run_kwargs={'workflow_id': 'wf_' + 'b' * 32, 'task_id': 'task_original',
                'execution_assignment_id': 'asg_original', 'patch_digest': frozen.patch_digest,
                'permissions': PermissionManager(str(case['root']), prompt=None), 'permission_cwd': str(case['root'])})
        receipt = ReviewerProbeLedger().record(old, frozen.patch_digest, receipt)
    assert receipt['status'] == 'failed' and receipt['testReport']['failed'] >= 1
    wrapped = {'probeId': old.probe_id, 'candidateDigest': case['candidate_digest'], 'receipt': receipt,
        'receiptRef': case['persist']('original-pytest-receipt', receipt)}
    source_ref = case['persist']('original-probe-source', old.to_dict())
    wrapped['sourceRef'] = source_ref
    adjudication = {'verdict': 'probe_error', 'reason': 'The release assertion expects 999 although adding 2 to 3 yields 5.',
        'objectiveEvidence': [{'startLine': 2, 'endLine': 2}],
        'sourceEvidence': [{'path': 'stock.py', 'startLine': 5, 'endLine': 6}],
        'replacement': {'criterionIds': list(case['probe'].criterion_ids),
            'scenario': case['probe'].scenario, 'code': case['probe'].code}}
    _native(monkeypatch, case, [('completion_probe_adjudication_submit', adjudication)])
    judgment = ProbeFailureAdjudicator(case['spec']).assess(probe=old.to_dict(), receipt=wrapped,
        context=case['host'], model=case['adapter'], tools=case['tools'], agent_budget=None,
        event_sink=None, cancellation_token=None, deadline_monotonic=None)
    assert judgment['verdict'] == 'probe_error'
    judgment['adjudicationRef'] = case['persist']('probe-adjudication', judgment)
    request = make_supersession(old.to_dict(), case['probe'].to_dict(), judgment, wrapped, case['wrapped'])
    obligation = {'id': request['supersessionId'], 'request': request,
        'oldSource': old.to_dict(), 'newSource': case['probe'].to_dict(), 'authority': judgment,
        'originalReceipt': wrapped, 'currentReceipt': case['wrapped'], 'effectiveProbeId': case['probe'].probe_id,
        'replacementChain': [], 'oldEverPassed': False}
    valid = deepcopy(case['valid'])
    valid['supersessions'] = [{'id': obligation['id'], 'status': 'accepted',
        'invalidityReason': 'The original expected 999 conflicts with the independently checked addition requirement.',
        'requirements': [{key: row[key] for key in ('id', 'reason', 'witnesses')} for row in valid['requirements']]}]
    case.update(old=old, original=wrapped, obligation=obligation, valid=valid)
    return case


def run(case, *, state=None, sink=None):
    evidence = {case['old'].probe_id: {'source': case['old'].to_dict(), 'receipt': case['original'],
        'purpose': 'acceptance', 'active': True}, case['probe'].probe_id: case['evidence']}
    return audit_completion_coverage(objective=OBJECTIVE, requirements=case['units'], proposal=case['proposal'],
        candidate_digest=case['candidate_digest'], model=case['adapter'], tools=case['tools'],
        cwd=str(case['root']), permissions=case['permissions'], host=case['host'], event_sink=None,
        persist=case['persist'], checkpoint=lambda: None, probe_evidence=evidence.get,
        source_anchors=lambda anchors: case['owner']._anchors(anchors, case['root'], case['permissions']),
        supersession_obligations=[case['obligation']], continuation_state=state, state_sink=sink)


def reading_action(case, *, after=None):
    # Use only the host-issued index and cursors on the actual wire.
    required, seen = [], {}

    def action(body):
        p = packet(body)
        if not required:
            required.extend(row['sourceDocumentId'] for row in p['evidenceIndex'])
            index = p['supersessionIndex']
            for message in body['messages']:
                marker = '{"coverageAuditContinuation":'
                content = message.get('content') or ''
                if message.get('role') == 'system' and marker in content:
                    resumed = json.JSONDecoder().raw_decode(content[content.index(marker):])[0]['coverageAuditContinuation']
                    if resumed.get('supersessionDeliveryContract'):
                        index = resumed['supersessionDeliveryContract']['materials']
            required.extend(row['obligationDocumentId'] for row in index)
            # Old source is not necessarily part of the normal proposal index;
            # the mandatory material embeds its exact complete source.
        if any(m['role'] == 'tool' for m in body['messages']):
            page = last(body)
            if 'documentId' in page:
                did = page['documentId']
                seen[did] = page
                for dep in page['requiredDocumentIds']:
                    if dep not in required:
                        required.append(dep)
        for did in required:
            if did not in seen:
                return 'review_evidence_read', {'documentId': did}
            if seen[did]['nextCursor']:
                return 'review_evidence_read', {'cursor': seen[did]['nextCursor']}
        return after(body) if after else ('completion_coverage_audit_submit', case['valid'])
    return action


def test_large_real_receipts_are_optional_but_exact_material_and_originals_reach_native_wire(tmp_path, monkeypatch):
    case = fixture(tmp_path, monkeypatch, count=3500)
    original = deepcopy(case['obligation'])
    material, stages = {}, []
    reader = reading_action(case)

    def action(body):
        p = packet(body)
        if not stages:
            stages.append('original')
            trace = p['evidenceIndex'][0]['currentReceiptView']['executionReport']['documentRef']
            material['traceRef'] = trace
            return 'completion_evidence_read', {'documentId': trace}
        if stages == ['original']:
            page = last(body)
            original_trace = canonical(case['receipt']['testReport']['executionReport'])
            assert page['text'] == original_trace[:len(page['text'])]
            assert page['totalChars'] == len(original_trace) and page['nextCursor']
            stages[:] = ['filtered']
            trace = material['traceRef']
            return 'completion_evidence_read', {'documentId': trace,
                'filters': {'consumer': {'path': 'stock.py', 'qualname': 'hold'}}}
        if stages == ['filtered']:
            selected = json.loads(last(body)['text'])
            assert any(c['qualname'] == 'hold' for row in selected['cases'] for c in row['calls'])
            assert all(not c['qualname'].startswith('hop_') for row in selected['cases'] for c in row['calls'])
            stages.append('reading')
        assert last(body).get('documentType') != 'execution_receipt', \
            'Mandatory supersession reading entered a full execution receipt.'
        return reader(body)

    calls = _native(monkeypatch, case, [action] * 20)
    result = run(case)
    assert result['status'] == 'approved' and len(calls) < 12, (len(calls), result.get('findings'), last(calls[-1]))
    assert case['obligation'] == original
    assert result['supersessions'][0]['obligationDigest'] == content_digest(canonical(original))
    saved = {kind: json.loads(path.read_text()) for kind, path in case['saved']
        if kind in {'coverage-audit-evidence-documents', 'coverage-audit-review-materials', 'coverage-audit-authority'}}
    docs = saved['coverage-audit-evidence-documents']['documents']
    trace_docs = [d for d in docs if d['documentType'] == 'execution_receipt']
    assert sum(math.ceil(len(d['text']) / 8000) for d in trace_docs) > 900
    contract = saved['coverage-audit-review-materials']
    assert all(d['requiredDocumentIds'] == [] for d in contract['documents'])
    body = json.loads(contract['documents'][0]['text'])
    assert body['oldSource'] == case['old'].to_dict() and body['newSource'] == case['probe'].to_dict()
    assert body['authority'] == original['authority']
    assert body['originalObligationDigest'] == content_digest(canonical(original))
    assert len(canonical(body)) < 15000
    assert all('"executionReport"' not in d['text'] for d in contract['documents'])
    assert saved['coverage-audit-authority']['supersessionObligations'] == [original]
    assert result['requirements'][0]['witnesses'][0]['executedAssertions']


def test_unread_materials_and_nonassertion_witness_cannot_accept_supersession(tmp_path, monkeypatch):
    case = fixture(tmp_path, monkeypatch)
    selected = {}
    bad = deepcopy(case['valid'])
    bad['supersessions'][0]['requirements'][0]['witnesses'][0].update(startLine=1, endLine=2)

    def read_source(body):
        assert 'independent invalidity evidence' in last(body)['message']
        selected.update(packet(body))
        return 'review_evidence_read', {'documentId': selected['evidenceIndex'][0]['sourceDocumentId']}

    def read_material(body):
        assert 'independent invalidity evidence' in last(body)['message']
        return 'review_evidence_read', {'documentId': selected['supersessionIndex'][0]['obligationDocumentId']}

    def end(body):
        rejected = last(body)
        assert rejected['errorCode'] == 'coverage_audit_invalid' and rejected['fieldErrors']
        assert 'assert' in canonical(rejected['fieldErrors'])
        return 'completion_coverage_audit_submit', {'status': 'inconclusive', 'requirements': [],
            'findings': ['The supplied source range does not witness the required assertion.']}

    _native(monkeypatch, case, [('completion_coverage_audit_submit', case['valid']), read_source,
        ('completion_coverage_audit_submit', case['valid']), read_material,
        ('completion_coverage_audit_submit', bad), end])
    result = run(case)
    assert result['status'] == 'inconclusive' and not result['supersessions']


def test_complete_material_cannot_replace_original_trace_validation(tmp_path, monkeypatch):
    case = fixture(tmp_path, monkeypatch)
    changed = []

    def corrupt_and_submit(body):
        if not changed:
            changed.append(True)
            # Keep the claimed status/source/witness unchanged; corrupt only
            # the actual execution tree behind its retained authenticated hash.
            report = case['receipt']['testReport']['executionReport']
            report['cases'][0]['calls'][0]['count'] += 1
            return 'completion_coverage_audit_submit', case['valid']
        assert last(body)['errorCode'] == 'coverage_audit_invalid'
        return 'completion_coverage_audit_submit', {'status': 'inconclusive', 'requirements': [],
            'findings': ['The original execution evidence no longer authenticates.']}

    calls = _native(monkeypatch, case, [reading_action(case, after=corrupt_and_submit)] * 10)
    result = run(case)
    assert result['status'] == 'inconclusive' and changed and len(calls) < 10
    assert not result['supersessions']


def native_resume(monkeypatch, case, actions, prefix):
    from minicode import openai_adapter
    requests = _native(monkeypatch, case, actions)
    original = openai_adapter.open_verified_url

    def distinct(request, **kwargs):
        response = original(request, **kwargs)
        body = json.loads(response.getvalue())
        for call in body['choices'][0]['message'].get('tool_calls', []):
            call['id'] = prefix + '-' + call['id']
        return type(response)(json.dumps(body).encode())
    monkeypatch.setattr(openai_adapter, 'open_verified_url', distinct)
    return requests


def legacy_pending(case, monkeypatch):
    states, captured = [], {}

    def read_receipt(body):
        captured['id'] = packet(body)['evidenceIndex'][0]['currentReceiptDocumentId']
        return 'review_evidence_read', {'documentId': captured['id']}

    def invalid(body):
        captured['page'] = last(body)
        assert captured['page']['nextCursor']
        return 'completion_coverage_audit_submit', case['valid']

    def abort(body):
        captured['draftId'] = last(body)['draftId']
        raise KeyboardInterrupt('retain actual raw receipt page and rejected draft')

    _native(monkeypatch, case, [read_receipt, invalid, abort])
    with pytest.raises(KeyboardInterrupt):
        run(case, sink=lambda value: states.append(deepcopy(value)))
    state = states[-1]
    material = record_document(state['reviewMaterialsRecord'])
    # Construct the historical v1 host-envelope format from the exact original
    # authority/documents and actual delivered page/rejected draft. This local
    # compatibility fixture is not claimed to be a historical model run.
    legacy = deepcopy(state)
    legacy.pop('reviewMaterialsRecord')
    legacy['coverageAuditStateVersion'] = 1
    saved = record_document(state['messagesRecord'])
    initial_message = next(m for m in saved['messages'] if m['role'] == 'user')
    initial = json.loads(initial_message['content'])
    initial.pop('supersessionDeliveryContract')
    originals = {row['id']: row['originalObligationDocumentId'] for row in material['supersessions']}
    for row in initial['supersessionIndex']:
        row['obligationDocumentId'] = originals[row['id']]
    initial_message['content'] = canonical(initial)
    reference = case['persist']('legacy-pending-context', saved)
    legacy['messagesRecord'] = host_record(reference, Path(reference).read_bytes())
    validate_coverage_audit_state(legacy, objective=OBJECTIVE)
    return legacy, captured


def test_v1_migration_and_second_resume_keep_authority_messages_pages_and_draft(tmp_path, monkeypatch):
    case = fixture(tmp_path, monkeypatch, count=50)
    legacy, captured = legacy_pending(case, monkeypatch)
    before = deepcopy(legacy)
    messages = record_document(legacy['messagesRecord'])['messages']
    delivered = coverage_delivery_pairs(legacy)
    migrated = []

    def abort(body):
        marker = '{"coverageAuditContinuation":'
        content = next(m['content'] for m in body['messages'] if marker in (m.get('content') or ''))
        envelope = json.JSONDecoder().raw_decode(content[content.index(marker):])[0]['coverageAuditContinuation']
        contract = envelope['supersessionDeliveryContract']
        assert contract['migratedFromStateVersion'] == 1
        assert contract['preservedOriginalAuthorityRef'] == legacy['authorityRecord']['ref']
        assert envelope['latestUnacceptedDraft']['draftId'] == captured['draftId']
        raise KeyboardInterrupt('retain the explicitly migrated host contract before new tool delivery')

    native_resume(monkeypatch, case, [abort], 'migration')
    with pytest.raises(KeyboardInterrupt):
        run(case, state=legacy, sink=lambda value: migrated.append(deepcopy(value)))
    state = migrated[-1]
    assert legacy == before and state['coverageAuditStateVersion'] == 2
    assert state['auditId'] == legacy['auditId']
    for key in ('authorityRecord', 'documentsRecord'):
        assert state[key] == legacy[key]
    assert record_document(state['messagesRecord'])['messages'][:len(messages)] == messages
    assert coverage_delivery_pairs(state) == delivered
    restored = restore_evidence_documents(state)
    assert captured['id'] in restored.missing([captured['id']]), 'One old receipt page is not full delivery.'
    page = restored.read({'cursor': captured['page']['nextCursor']}, deliver=False)
    assert page['offset'] == len(captured['page']['text'])
    calls = native_resume(monkeypatch, case, [reading_action(case)] * 10, 'second-resume')
    result = run(case, state=state)
    assert result['status'] == 'approved' and len(calls) < 10
    original_ref = result.get('authorityProvenance', {}).get('originalAuthorityRef', result['authorityRef'])
    assert original_ref == legacy['authorityRecord']['ref']


@pytest.mark.parametrize('change', ['obligation', 'authority'])
def test_rehashed_material_sidecar_cannot_change_host_derived_authority(tmp_path, monkeypatch, change):
    case = fixture(tmp_path, monkeypatch, count=50)
    state, _captured = legacy_pending(case, monkeypatch)
    states = []
    native_resume(monkeypatch, case, [lambda body: (_ for _ in ()).throw(KeyboardInterrupt())], 'material')
    with pytest.raises(KeyboardInterrupt):
        run(case, state=state, sink=lambda value: states.append(deepcopy(value)))
    corrupt = deepcopy(states[-1])
    contract = record_document(corrupt['reviewMaterialsRecord'])
    if change == 'obligation':
        contract['supersessions'][0]['obligationDigest'] = 'sha256:' + '0' * 64
    else:
        body = json.loads(contract['documents'][0]['text'])
        body['authority']['reason'] = 'A model-authored replacement summary.'
        contract['documents'][0]['text'] = canonical(body)
    ref = corrupt['reviewMaterialsRecord']['ref']
    corrupt['reviewMaterialsRecord'] = host_record(ref, canonical(contract))
    with pytest.raises(ValueError, match='review materials differ from original host authority'):
        validate_coverage_audit_state(corrupt, objective=OBJECTIVE)
