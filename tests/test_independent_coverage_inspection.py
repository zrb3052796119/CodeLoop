from copy import deepcopy
import json
import io
from pathlib import Path

import pytest
import test_completion_coverage_audit as native
from minicode.completion_coverage_audit import audit_completion_coverage
from minicode.coverage_audit_state import record_document, validate_coverage_audit_state


def scripted(monkeypatch, case, actions):
    requests = []
    class Response(io.BytesIO):
        status = 200
    def transport(request, *, timeout):
        body = json.loads(request.data)
        requests.append(body)
        assert len(requests) <= len(actions)
        action = actions[len(requests) - 1]
        name, value = action(body) if callable(action) else action
        message = {"role": "assistant", "content": None, "tool_calls": [{"id": f"inspection-{len(requests)}",
            "type": "function", "function": {"name": name, "arguments": json.dumps(value)}}]}
        return Response(json.dumps({"choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}}).encode())
    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)
    return requests


def assess(case, *, state=None, sink=None, negative=None):
    return audit_completion_coverage(objective=native.OBJECTIVE, requirements=case['units'], proposal=case['proposal'],
        candidate_digest=case['candidate_digest'], model=case['adapter'], tools=case['tools'], cwd=str(case['root']),
        permissions=case['permissions'], host=case['host'], event_sink=None, persist=case['persist'], checkpoint=lambda: None,
        probe_evidence=lambda pid: case['evidence'] if pid == case['probe'].probe_id else None,
        source_anchors=lambda rows: case['owner']._anchors(rows, case['root'], case['permissions']),
        continuation_state=state, state_sink=sink, accepted_negative_sink=negative)


def read_source():
    return ('read_file', {'path': 'stock.py', 'start_line': 1, 'end_line': 5})


def inspection(case, *, status='ready_for_evidence', row_status='checked'):
    return {'status': status, 'observations': [{'requirementId': case['units'][1]['id'], 'status': row_status,
        'sourceEvidence': [{'path': 'stock.py', 'startLine': 4, 'endLine': 5}],
        'expectedBehavior': 'release adds a nonzero amount.', 'observedBehavior': 'The actual candidate body was read.', 'probeIds': []}],
        'findings': [] if status == 'ready_for_evidence' else ['The release implementation does not add the amount.']}


def test_inspection_requires_real_read_then_same_context_discloses_existing_evidence(tmp_path, monkeypatch):
    case = native._case(tmp_path, monkeypatch)
    ready = inspection(case)
    requests = scripted(monkeypatch, case, [('completion_coverage_inspection_submit', ready), read_source(),
        ('completion_coverage_inspection_submit', ready), native._read(case), ('completion_coverage_audit_submit', case['valid'])])
    result = assess(case)
    assert result['status'] == 'approved'
    assert result['inspectionVersion'] == 1
    from minicode.implementation_completion_review import _valid_inspection_approval
    assert _valid_inspection_approval(result, case['candidate_digest'])
    old_approval = {key: value for key, value in result.items() if key not in {'inspectionRecord', 'inspectionVersion'}}
    assert not _valid_inspection_approval(old_approval, case['candidate_digest'])
    assert not _valid_inspection_approval(result, 'sha256:' + '0' * 64)
    assert result['behaviorEvidenceVersion'] == 1
    assert len(requests) == 5
    for request in requests[:3]:
        names = {row['function']['name'] for row in request['tools']}
        assert not {'completion_coverage_audit_submit', 'review_probe_read', 'review_evidence_read', 'completion_evidence_read', 'coverage_audit_history_read'} & names
        assert '"proposal"' not in json.dumps(request['messages'])
    assert any('coverage_inspection_invalid' in (row.get('content') or '') for row in requests[1]['messages'])
    assert any(row.get('role') == 'tool' and 'Actual' not in row.get('content', '') and 'return n + amount' in row.get('content', '')
               for row in requests[-1]['messages'])
    saved = record_document(result['inspectionRecord'])
    assert saved['phase'] == 'reconciliation'
    assert len(saved['reads']) == 1
    assert saved['result']['status'] == 'ready_for_evidence'


def test_source_backed_missing_commits_before_sink_and_never_discloses_proposal(tmp_path, monkeypatch):
    case = native._case(tmp_path, monkeypatch, product=native.PRODUCT.replace('n + amount', 'n'),
                        code=native.WEAK[1])
    requests = scripted(monkeypatch, case, [read_source(),
        ('completion_coverage_inspection_submit', inspection(case, status='changes_required', row_status='missing'))])
    accepted = []
    def sink(value):
        assert Path(value['evidenceRef']).is_file()
        assert json.loads(Path(value['evidenceRef']).read_text())['requirements'][0]['sourceEvidence']
        accepted.append(deepcopy(value))
    result = assess(case, negative=sink)
    assert result == accepted[0] and result['status'] == 'changes_required'
    assert result['requirements'][0]['sourceEvidence'][0]['startLine'] == 4
    assert len(requests) == 2 and result['supersessions'] == []
    assert all('"proposal"' not in json.dumps(request['messages']) for request in requests)


def test_interrupted_inspection_resumes_actual_source_read_and_candidate_bound_state(tmp_path, monkeypatch):
    case = native._case(tmp_path, monkeypatch)
    states = []
    def interrupt(_body):
        raise KeyboardInterrupt('independent inspection pause')
    scripted(monkeypatch, case, [read_source(), interrupt])
    with pytest.raises(KeyboardInterrupt):
        assess(case, sink=lambda value: states.append(deepcopy(value)))
    pending = next(value for value in reversed(states) if value is not None)
    assert pending['coverageAuditStateVersion'] == 3
    assert record_document(pending['inspectionRecord'])['phase'] == 'inspection'
    assert validate_coverage_audit_state(pending, objective=native.OBJECTIVE) == pending
    assert len(record_document(pending['inspectionRecord'])['reads']) == 1
    bad = deepcopy(pending)
    bad['candidateDigest'] = 'sha256:' + '0' * 64
    with pytest.raises(ValueError):
        validate_coverage_audit_state(bad, objective=native.OBJECTIVE)
    requests = scripted(monkeypatch, case, [('completion_coverage_inspection_submit', inspection(case)),
        native._read(case), ('completion_coverage_audit_submit', case['valid'])])
    result = assess(case, state=pending)
    assert result['status'] == 'approved'
    assert len(requests) == 3
    assert any(row.get('role') == 'tool' and 'return n + amount' in row.get('content', '') for row in requests[0]['messages'])


def test_uncertain_inspection_keeps_phase_and_real_reads_for_resume(tmp_path, monkeypatch):
    case = native._case(tmp_path, monkeypatch)
    states = []
    scripted(monkeypatch, case, [read_source(), ('completion_coverage_inspection_submit',
        inspection(case, status='inconclusive', row_status='uncertain'))])
    result = assess(case, sink=lambda value: states.append(deepcopy(value)))
    assert result['status'] == 'inconclusive' and result['canContinue'] is False
    pending = states[-1]
    assert pending is not None
    before = record_document(pending['messagesRecord'])['messages']
    assert any(m.get('toolName') == 'completion_coverage_inspection_submit' and m.get('role') == 'tool_result' for m in before)
    assert len(record_document(pending['inspectionRecord'])['reads']) == 1
    requests = scripted(monkeypatch, case, [('completion_coverage_inspection_submit', inspection(case)),
        native._read(case), ('completion_coverage_audit_submit', case['valid'])])
    assert assess(case, state=pending)['status'] == 'approved'
    assert len(requests) == 3


def test_disclosure_storage_interrupt_retains_committed_inspection_for_retry(tmp_path, monkeypatch):
    case = native._case(tmp_path, monkeypatch)
    states = []
    original = case['persist']
    committed = []
    fired = []
    def fail_disclosure(kind, document):
        result = original(kind, document)
        if kind == 'coverage-inspection' and document['phase'] == 'reconciliation':
            committed.append(result)
        if kind == 'coverage-audit-messages' and committed and not fired:
            fired.append(True)
            raise KeyboardInterrupt('between phase record and context publication')
        return result
    case['persist'] = fail_disclosure
    scripted(monkeypatch, case, [read_source(), ('completion_coverage_inspection_submit', inspection(case))])
    with pytest.raises(KeyboardInterrupt):
        assess(case, sink=lambda value: states.append(deepcopy(value)))
    pending = states[-1]
    metadata = record_document(pending['inspectionRecord'])
    assert metadata['phase'] == 'inspection' and metadata['result']['status'] == 'ready_for_evidence'
    assert not any(m.get('role') == 'user' and '"proposal"' in m.get('content', '') for m in record_document(pending['messagesRecord'])['messages'])
    assert validate_coverage_audit_state(pending, objective=native.OBJECTIVE) == pending
    case['persist'] = original
    requests = scripted(monkeypatch, case, [native._read(case), ('completion_coverage_audit_submit', case['valid'])])
    assert assess(case, state=pending)['status'] == 'approved'
    assert len(requests) == 2


def test_legacy_native_draft_migrates_new_context_without_old_read_credit(tmp_path, monkeypatch):
    # Native pre-inspection audit fixture created in an isolated temporary git
    # root; preserves actual delivered source pages and a rejected draft.
    old = json.loads((Path(__file__).parent / 'fixtures/coverage_inspection_legacy.json').read_text())
    (tmp_path / 'new').mkdir()
    case = native._case(tmp_path / 'new', monkeypatch)
    assert case['candidate_digest'] == old['candidateDigest']
    case['proposal'] = old['proposal']
    case['evidence'] = old['evidence']
    states = []
    def stop(_body):
        raise KeyboardInterrupt('new independent epoch pending')
    first = scripted(monkeypatch, case, [stop])
    with pytest.raises(KeyboardInterrupt):
        assess(case, state=old['state'], sink=lambda state: states.append(deepcopy(state)))
    migrated = states[-1]
    assert record_document(migrated['legacyAuditRecord']) == old['state']
    assert migrated['auditId'] != old['state']['auditId']
    assert record_document(migrated['inspectionRecord'])['reads'] == []
    assert record_document(migrated['deliveryRecord'])['pairs'] == []
    assert [message['role'] for message in first[0]['messages']] == ['system', 'user']
    assert '"proposal"' not in json.dumps(first[0]['messages'])
    assert validate_coverage_audit_state(migrated, objective=native.OBJECTIVE) == migrated
    # Even after the independent source pass, old complete source pages do not
    # let this new context claim witnesses without reading them itself.
    requests = scripted(monkeypatch, case, [read_source(), ('completion_coverage_inspection_submit', inspection(case)),
        ('completion_coverage_audit_submit', case['valid']), native._read(case),
        ('completion_coverage_audit_submit', case['valid'])])
    result = assess(case, state=migrated)
    assert result['status'] == 'approved' and len(requests) == 5
    error = next(m['content'] for m in reversed(requests[3]['messages']) if m.get('role') == 'tool')
    assert 'read the exact complete probe source' in error
