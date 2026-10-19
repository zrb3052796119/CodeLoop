"""Native HTTP tool calls resume authentic evidence and rejected drafts.

Only model decisions/transport are scripted; isolated pytest, native tools,
provider schema, delivered bytes, checkpoint validation and gates are real.
"""
from copy import deepcopy
import io
import json
import subprocess

import pytest

from minicode.completion_coverage_audit import audit_completion_coverage
from minicode.coverage_audit_state import record_document, host_record
from minicode.coverage_audit_submission import CoverageDrafts
from minicode.review_evidence_documents import EvidenceDocuments
from test_completion_coverage_audit import _case, PROBE, OBJECTIVE, _CoverageRequests, _inspection_action, _inspection_step, _coverage_packet


def run_audit(case, *, state=None, sink=None, model=None):
    return audit_completion_coverage(objective=OBJECTIVE, requirements=case['units'], proposal=case['proposal'],
        candidate_digest=case['candidate_digest'], model=model or case['adapter'], tools=case['tools'],
        cwd=str(case['root']), permissions=case['permissions'], host=case['host'], event_sink=None,
        persist=case['persist'], checkpoint=lambda: None, probe_evidence=lambda _pid: case['evidence'],
        source_anchors=lambda anchors: case['owner']._anchors(anchors, case['root'], case['permissions']),
        continuation_state=state, state_sink=sink)


def decode(text):
    return json.JSONDecoder().raw_decode(text)[0]


def outputs(body):
    values = []
    for message in body['messages']:
        if message['role'] != 'tool':
            continue
        if message['content'].startswith('FILE: '):
            continue  # Native candidate read_file output is plain text.
        value = decode(message['content'])
        if value.get('status') == 'ready_for_evidence' and value.get('grantsCoverage') is False:
            continue  # Nonterminal inspection receipt is retained in the wire.
        values.append(value)
    return values


def native(monkeypatch, action):
    calls = _CoverageRequests()

    class Response(io.BytesIO):
        status = 200

    def transport(request, *, timeout):
        body = json.loads(request.data)
        calls.all_requests.append(body)
        step = _inspection_action(body)
        if step is not None:
            calls.inspection_requests.append(body)
            identity = f'inspection-{len(calls.inspection_requests)}'
        else:
            calls.append(body)
            identity = f'resume-{len(calls)}'
            step = action(body, len(calls))
        name, value = step
        message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': identity,
            'type': 'function', 'function': {'name': name, 'arguments': json.dumps(value)}}]}
        return Response(json.dumps({'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 40}}).encode())

    monkeypatch.setattr('minicode.openai_adapter.open_verified_url', transport)
    return calls


@pytest.fixture
def case(tmp_path, monkeypatch):
    # Avoid copying the surrounding developer checkout into this generic fixture.
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    return _case(tmp_path, monkeypatch, code=PROBE + '# retained inventory evidence\n' * 450)


def seed(case, monkeypatch):
    states, captured = [], {}
    invalid = deepcopy(case['valid'])
    invalid['findings'] = ['Both quantity operations passed.']
    invalid['requirements'][0]['witnesses'][0]['endLine'] = 99999

    def action(body, count):
        if count == 1:
            packet = _coverage_packet(body)
            captured['sourceId'] = packet['evidenceIndex'][0]['sourceDocumentId']
            return 'review_evidence_read', {'documentId': captured['sourceId']}
        if count == 2:
            captured['page'] = outputs(body)[0]
            assert captured['page']['nextCursor'] and not captured['page']['completeDelivered']
            return 'completion_coverage_audit_submit', invalid
        captured['rejection'] = outputs(body)[-1]
        raise KeyboardInterrupt('retain actual partial source and rejected submission')

    calls = native(monkeypatch, action)
    with pytest.raises(KeyboardInterrupt):
        run_audit(case, sink=lambda value: states.append(deepcopy(value)))
    state = states[-1]
    assert state['status'] == 'pending' and len(calls) == 3
    return state, captured, calls


def test_real_native_resume_preserves_partial_cursor_and_patches_rejected_draft(case, monkeypatch):
    state, observed, _ = seed(case, monkeypatch)
    rejected = observed['rejection']
    pid = case['probe'].probe_id
    assert rejected['blockers']['findings'] == [{'index': 0, 'text': 'Both quantity operations passed.'}]
    assert rejected['fieldErrors'][0]['path'] == '/requirements/0/witnesses/0'
    assert rejected['fieldErrors'][0]['lineCount'] == len(case['probe'].code.splitlines())
    assert rejected['probeAssertions'][pid]['assertionIndex'] == [
        {'startLine': 3, 'endLine': 3}, {'startLine': 4, 'endLine': 4}]
    retained = record_document(state['messagesRecord'])['messages']
    states, actions = [], []

    def action(body, count):
        schema = next(t['function']['parameters'] for t in body['tools']
                      if t['function']['name'] == 'completion_coverage_audit_submit')
        assert schema['anyOf'] == [{'required': ['status', 'requirements', 'findings']}, {'required': ['draftId', 'updates']}]
        if count == 1:
            # Restored explicit native draft identity remains usable, but the
            # second error must still reject it; correcting findings isn't approval.
            notice = next(decode(m['content'][m['content'].index('{"coverageAuditContinuation":'):])['coverageAuditContinuation'] for m in body['messages']
                          if m['role'] == 'system' and 'coverageAuditContinuation' in m.get('content', ''))
            assert notice['latestUnacceptedDraft']['draftId'] == rejected['draftId']
            value = {'draftId': rejected['draftId'], 'updates': [{'path': '/findings', 'value': []}]}
            actions.append(('patch', value))
            return 'completion_coverage_audit_submit', value
        pages = [value for value in outputs(body) if value.get('documentId') == observed['sourceId']]
        latest = pages[-1]
        if latest['nextCursor'] is not None:
            assert count > 1
            actions.append(('cursor', latest['nextCursor']))
            return 'review_evidence_read', {'cursor': latest['nextCursor']}
        errors = [value for value in outputs(body) if value.get('errorCode') == 'coverage_audit_invalid']
        assert errors[-1]['blockers']['findings'] == []
        value = {'draftId': errors[-1]['draftId'], 'updates': [
            {'path': '/requirements/0/witnesses/0/endLine', 'value': 4}]}
        actions.append(('patch', value))
        return 'completion_coverage_audit_submit', value

    calls = native(monkeypatch, action)
    # Provider call IDs are unique in an actual continued model conversation.
    import minicode.openai_adapter as adapter
    raw = adapter.open_verified_url
    def unique(request, **kwargs):
        response = raw(request, **kwargs)
        payload = json.loads(response.getvalue())
        payload['choices'][0]['message']['tool_calls'][0]['id'] += '-continued'
        return type(response)(json.dumps(payload).encode())
    monkeypatch.setattr(adapter, 'open_verified_url', unique)
    result = run_audit(case, state=state, sink=lambda value: states.append(deepcopy(value)))
    assert result['status'] == 'approved' and result['authorityRef'] == state['authorityRecord']['ref']
    assert states[-1] is None
    pending = next(value for value in states if value is not None)
    assert pending['auditId'] == state['auditId']
    assert record_document(pending['messagesRecord'])['messages'][:len(retained)] == retained
    assert actions[1] == ('cursor', observed['page']['nextCursor'])
    assert not any('documentId' in json.loads(c['function']['arguments']) for body in calls
                   for m in body['messages'][-1:] for c in m.get('tool_calls', []))
    drafts = [json.loads(path.read_text()) for kind, path in case['saved'] if kind == 'coverage-audit-submission-draft']
    assert drafts[0]['submission']['findings'] == ['Both quantity operations passed.']
    assert drafts[1]['submission']['findings'] == []
    assert drafts[0]['submission']['requirements'][0]['witnesses'][0]['endLine'] == 99999


@pytest.mark.parametrize('tamper', ['candidate', 'raw_messages', 'page_text', 'cursor_unissued', 'draft_identity', 'current_receipt'])
def test_resume_tamper_rejected_before_model(case, monkeypatch, tamper):
    state, _, _ = seed(case, monkeypatch)
    altered = deepcopy(state)
    if tamper == 'candidate':
        altered['candidateDigest'] = 'sha256:' + '0' * 64
    elif tamper == 'raw_messages':
        altered['messagesRecord']['rawJson'] += ' '
    elif tamper == 'current_receipt':
        case['evidence']['receipt']['receiptRef'] += '-different'
    else:
        saved = record_document(altered['messagesRecord'])
        if tamper == 'draft_identity':
            item = next(m for m in saved['messages'] if m.get('role') == 'tool_result' and m['toolName'] == 'completion_coverage_audit_submit')
            value = decode(item['content'])
            value['draftId'] = 'coverage_draft_forged'
        else:
            item = next(m for m in saved['messages'] if m.get('role') == 'tool_result' and m['toolName'] == 'review_evidence_read')
            value = decode(item['content'])
            if tamper == 'page_text':
                value['text'] = value['text'].replace('inventory', 'counterfeit')
            else:
                call = next(m for m in saved['messages'] if m.get('role') == 'assistant_tool_call'
                            and m.get('toolName') == 'review_evidence_read')
                call['input'] = {'cursor': 'cursor_never_issued'}
        item['content'] = json.dumps(value)
        altered['messagesRecord'] = host_record(altered['messagesRecord']['ref'], json.dumps(saved))
    class NoModel:
        def next(self, *_args, **_kwargs):
            pytest.fail('invalid continuation reached the model')
    with pytest.raises(ValueError):
        run_audit(case, state=altered, model=NoModel())


def test_immutable_rejected_drafts_never_change_other_authority_or_auto_accept():
    drafts = CoverageDrafts({'candidateDigest': 'candidate'}, 'audit')
    initial = {'status': 'approved', 'requirements': [], 'findings': ['positive comment']}
    held = drafts.retain(initial, initial)
    corrected = drafts.resolve({'draftId': held['draftId'], 'updates': [{'path': '/findings', 'value': []}]})
    assert corrected['findings'] == [] and drafts.get(held['draftId'])['submission']['findings']
    for invalid in ({'draftId': 'unknown', 'updates': [{'path': '/findings', 'value': []}]},
                    {'draftId': held['draftId'], 'updates': [{'path': '/candidateDigest', 'value': 'other'}]},
                    {'draftId': held['draftId'], 'updates': [{'path': '/findings', 'value': []}], 'status': 'approved'}):
        with pytest.raises(ValueError):
            drafts.resolve(invalid)


def test_reconstructed_document_delivery_cannot_credit_unsent_ranges():
    original = EvidenceDocuments(candidate_digest='candidate', page_size=20)
    identity = original.add({'code': 'stock ' * 30}, 'probe_source', factor=False)
    first = original.read({'documentId': identity})
    last = original.page(identity, len(original.snapshot()[0]['text']) - 10)
    restored = EvidenceDocuments.from_snapshot(candidate_digest='candidate', documents=original.snapshot())
    restored.restore_delivery({'documentId': identity}, first)
    assert restored.missing([identity]) == [identity]
    with pytest.raises(ValueError, match='preceding host issue'):
        restored.restore_delivery({'cursor': 'cursor_unsent'}, last)
    assert restored.missing([identity]) == [identity]
    page = restored.read({'cursor': first['nextCursor']})
    assert page['offset'] == 20


def test_coverage_error_is_correctable_submission_not_unknown_transport():
    from minicode.agent_intelligence import ErrorCategory, ErrorClassifier
    classified = ErrorClassifier.classify(json.dumps({'errorCode': 'coverage_audit_invalid',
        'message': 'Correct listed fields', 'fieldErrors': []}), 'completion_coverage_audit_submit')
    assert classified.category is ErrorCategory.LOGIC


def test_legacy_partial_cursor_is_explicitly_reopened_without_invented_credit(case, monkeypatch):
    from dataclasses import replace
    from minicode.review_evidence_documents import LegacyEvidenceReader
    from minicode.tooling import ToolRegistry, ToolResult
    reader = LegacyEvidenceReader(EvidenceDocuments(candidate_digest=case['candidate_digest']))
    def legacy_read(value, _context):
        result = reader.read(value, lambda _pid, _view: {
            'source': case['probe'].to_dict(), 'receipts': [case['wrapped']]})
        return ToolResult(ok=True, output=json.dumps(result))
    case['tools'] = ToolRegistry([replace(tool, run=legacy_read) if tool.name == 'review_probe_read' else tool
                                 for tool in case['tools'].list()])
    states = []
    def initial(body, count):
        if count == 1:
            return 'review_probe_read', {'probeId': case['probe'].probe_id, 'receiptView': 'current'}
        raise KeyboardInterrupt('preserve old legacy stream')
    native(monkeypatch, initial)
    with pytest.raises(KeyboardInterrupt):
        run_audit(case, sink=lambda value: states.append(deepcopy(value)))
    state = states[-1]
    messages = record_document(state['messagesRecord'])['messages']
    old_page = decode(next(m['content'] for m in messages if m.get('role') == 'tool_result'
                           and m.get('toolName') == 'review_probe_read'))
    assert old_page['nextCursor'] and not old_page['completeDelivered']
    # Dispose of the old process-local reader. The host must not silently
    # reinterpret its cursor or award full-source credit from its first page.
    reader = LegacyEvidenceReader(EvidenceDocuments(candidate_digest=case['candidate_digest']))
    def resumed(body, count):
        if count == 1:
            return 'review_probe_read', {'cursor': old_page['nextCursor']}
        if count == 2:
            error = outputs(body)[-1]
            assert error['errorCode'] == 'coverage_evidence_cursor_reopen'
            return 'completion_coverage_audit_submit', case['valid']
        if count == 3:
            error = outputs(body)[-1]
            assert error['errorCode'] == 'coverage_audit_invalid' and error['missingDocumentIds']
            reopening = next(value for value in outputs(body) if value.get('errorCode') == 'coverage_evidence_cursor_reopen')
            return 'review_evidence_read', {'documentId': reopening['sourceDocumentId']}
        page = outputs(body)[-1]
        if page.get('nextCursor'):
            return 'review_evidence_read', {'cursor': page['nextCursor']}
        return 'completion_coverage_audit_submit', case['valid']
    native(monkeypatch, resumed)
    import minicode.openai_adapter as adapter
    raw = adapter.open_verified_url
    def unique(request, **kwargs):
        response = raw(request, **kwargs)
        payload = json.loads(response.getvalue())
        payload['choices'][0]['message']['tool_calls'][0]['id'] += '-legacy-resumed'
        return type(response)(json.dumps(payload).encode())
    monkeypatch.setattr(adapter, 'open_verified_url', unique)
    result = run_audit(case, state=state)
    assert result['status'] == 'approved'


def test_legacy_rejected_input_gets_explicit_new_draft_without_acceptance():
    original = {'status': 'approved', 'requirements': [], 'findings': ['positive']}
    drafts = CoverageDrafts({'candidateDigest': 'candidate'}, 'audit')
    drafts.restore_rejection(original, json.dumps({'errorCode': 'coverage_audit_invalid', 'message': 'legacy error'}))
    assert drafts.get(drafts.latest)['submission'] == original
    assert drafts.get(drafts.latest)['status'] == 'unaccepted'
    corrected = drafts.resolve({'draftId': drafts.latest, 'updates': [{'path': '/findings', 'value': []}]})
    assert corrected['findings'] == [] and drafts.get(drafts.latest)['submission'] == original


@pytest.mark.parametrize('bad_id', [[], {}, 7, None])
def test_malformed_draft_identity_is_recoverable_value_error(bad_id):
    drafts = CoverageDrafts({'candidateDigest': 'candidate'}, 'audit')
    value = {'draftId': bad_id, 'updates': [{'path': '/findings', 'value': []}]}
    with pytest.raises(ValueError, match='retained unaccepted draftId'):
        drafts.resolve(value)
    # An actual rejected malformed request creates no provenance dependency.
    drafts.restore_rejection(value, json.dumps({'errorCode': 'coverage_audit_invalid', 'message': 'invalid draft'}))
    assert drafts.latest is None


def test_actual_compaction_keeps_delivery_and_full_draft_outside_active_messages(case, monkeypatch):
    import minicode.context_manager as context_module
    from minicode.context_compactor import ContextCompactor
    from minicode.coverage_audit_state import coverage_delivery_pairs, restore_evidence_documents
    original_factory = context_module.ContextManager
    managers = []
    def factory(*args, **kwargs):
        manager = original_factory(*args, **kwargs)
        managers.append(manager)
        return manager
    monkeypatch.setattr(context_module, 'ContextManager', factory)
    states, observed = [], {}
    invalid = deepcopy(case['valid'])
    invalid['findings'] = ['Positive explanation must be moved explicitly.']
    invalid['requirements'][0]['witnesses'][0]['endLine'] = 99999
    def action(body, count):
        if count == 1:
            packet = _coverage_packet(body)
            observed['sourceId'] = packet['evidenceIndex'][0]['sourceDocumentId']
            return 'completion_coverage_audit_submit', invalid
        if count == 2:
            observed['fullId'] = outputs(body)[-1]['draftId']
        if count <= 32:
            return 'review_evidence_read', {'documentId': observed['sourceId']}
        if count == 33:
            return 'completion_coverage_audit_submit', {'draftId': observed['fullId'],
                'updates': [{'path': '/findings', 'value': []}]}
        manager = managers[-1]
        before = deepcopy(manager.messages)
        observed['inspectionTask'] = next(m for m in before
            if m.get('role') == 'user' and m.get('content', '').startswith('{"coverageInspection":'))
        compacted = ContextCompactor(context_window=2000).compact_messages(before, force=True)
        assert len(compacted) < len(before)
        assert not any(m.get('toolUseId') == 'resume-1' for m in compacted)
        assert any(m.get('toolUseId') == 'resume-33' for m in compacted)
        assert observed['inspectionTask'] not in compacted
        manager.messages[:] = compacted
        observed['active'] = deepcopy(compacted)
        raise KeyboardInterrupt('persist the actual compacted view, not invented full messages')
    native(monkeypatch, action)
    with pytest.raises(KeyboardInterrupt):
        run_audit(case, sink=lambda value: states.append(deepcopy(value)))
    state = states[-1]
    assert record_document(state['messagesRecord'])['messages'] == observed['active']
    inspection_record = record_document(state['inspectionRecord'])
    assert inspection_record['phase'] == 'reconciliation'
    assert inspection_record['taskPacket'] == observed['inspectionTask']
    history = coverage_delivery_pairs(state)
    assert history[0][0]['toolUseId'] == 'resume-1'
    assert history[-1][0]['toolUseId'] == 'resume-33'
    assert len(history) == 33
    restored = restore_evidence_documents(state)
    assert restored.missing([observed['sourceId']]) == [observed['sourceId']]
    cursor = decode(history[-2][1]['content'])['nextCursor']
    assert restored.read({'cursor': cursor})['offset'] == 8000
    def finish(body, count):
        if count == 1:
            notice = next(decode(m['content'][m['content'].index('{"coverageAuditContinuation":'):])['coverageAuditContinuation']
                for m in body['messages'] if m['role'] == 'system' and 'coverageAuditContinuation' in m.get('content', ''))
            observed['latest'] = notice['latestUnacceptedDraft']['draftId']
            assert observed['latest'] != observed['fullId']
            return 'review_evidence_read', {'cursor': cursor}
        page = outputs(body)[-1]
        if page.get('nextCursor'):
            return 'review_evidence_read', {'cursor': page['nextCursor']}
        return 'completion_coverage_audit_submit', {'draftId': observed['latest'], 'updates': [
            {'path': '/requirements/0/witnesses/0/endLine', 'value': 4}]}
    native(monkeypatch, finish)
    import minicode.openai_adapter as adapter
    raw = adapter.open_verified_url
    def unique(request, **kwargs):
        response = raw(request, **kwargs)
        payload = json.loads(response.getvalue())
        payload['choices'][0]['message']['tool_calls'][0]['id'] += '-after-compaction'
        return type(response)(json.dumps(payload).encode())
    monkeypatch.setattr(adapter, 'open_verified_url', unique)
    resumed_states = []
    result = run_audit(case, state=state, sink=lambda value: resumed_states.append(deepcopy(value)))
    assert result['status'] == 'approved'
    assert record_document(resumed_states[0]['messagesRecord'])['messages'][:len(observed['active'])] == observed['active']
    assert record_document(resumed_states[0]['inspectionRecord'])['taskPacket'] == observed['inspectionTask']


def test_executed_but_undelivered_page_has_no_recoverable_credit(case):
    from minicode.coverage_audit_state import coverage_delivery_pairs, restore_evidence_documents
    captured, states = {}, []
    class InterruptedTool:
        def next(self, messages, **kwargs):
            inspection = _inspection_step(messages, kwargs['tools_override'])
            if inspection is not None:
                return inspection
            packet = _coverage_packet({'messages': messages})
            document_id = packet['evidenceIndex'][0]['sourceDocumentId']
            tool = next(t for t in kwargs['tools_override'].list() if t.name == 'review_evidence_read')
            result = tool.run({'documentId': document_id}, case['host'])
            assert result.ok and result._delivery_ack is not None
            captured.update(documentId=document_id, page=decode(result.output))
            # No native call/result append and no delivery acknowledgement.
            raise KeyboardInterrupt('execution alone is not delivery')
    with pytest.raises(KeyboardInterrupt):
        run_audit(case, model=InterruptedTool(), sink=lambda value: states.append(deepcopy(value)))
    state = states[-1]
    assert coverage_delivery_pairs(state) == []
    restored = restore_evidence_documents(state)
    assert restored.missing([captured['documentId']]) == [captured['documentId']]
    with pytest.raises(ValueError, match='expired'):
        restored.read({'cursor': captured['page']['nextCursor']})
