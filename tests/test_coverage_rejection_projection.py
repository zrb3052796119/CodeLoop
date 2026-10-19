"""Native wire, real tool truncation, delivery/compaction and explicit draft repair."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from minicode.coverage_audit_state import coverage_delivery_pairs, record_document, host_record, restore_evidence_documents
from minicode.coverage_audit_submission import CoverageDrafts
from minicode.review_evidence_documents import canonical
from minicode.tooling import _LARGE_OUTPUT_THRESHOLD
from test_completion_coverage_audit import _coverage_packet
from test_coverage_audit_resume import case as resume_case, native, run_audit, outputs, decode


@pytest.fixture
def case(tmp_path, monkeypatch):
    return resume_case.__wrapped__(tmp_path, monkeypatch)


def large_invalid(case, number=1):
    value = deepcopy(case['valid'])
    value['findings'] = [str(number) + '\x01"\\\n' * 8000]
    value['requirements'][0]['witnesses'][0]['endLine'] = 99999
    return value


def unique_transport(monkeypatch):
    import minicode.openai_adapter as adapter
    raw = adapter.open_verified_url
    def unique(request, **kwargs):
        response = raw(request, **kwargs)
        payload = json.loads(response.getvalue())
        payload['choices'][0]['message']['tool_calls'][0]['id'] += '-continued'
        return type(response)(json.dumps(payload).encode())
    monkeypatch.setattr(adapter, 'open_verified_url', unique)


def test_native_large_rejection_pages_resume_and_explicit_repair(case, monkeypatch):
    states, observed = [], {}
    invalid = large_invalid(case)
    def initial(body, count):
        if count == 1:
            observed['sourceId'] = _coverage_packet(body)['evidenceIndex'][0]['sourceDocumentId']
            return 'completion_coverage_audit_submit', invalid
        visible = body['messages'][-1]['content']
        view = decode(visible)
        assert len(visible) < _LARGE_OUTPUT_THRESHOLD
        assert view['displayOnly'] and view['draftStatus'] == 'unaccepted'
        observed['view'] = view
        raise KeyboardInterrupt('only checkpoint; never claim delivery')
    native(monkeypatch, initial)
    with pytest.raises(KeyboardInterrupt):
        run_audit(case, sink=lambda s: states.append(deepcopy(s)))
    state = states[-1]
    call, result = coverage_delivery_pairs(state)[-1]
    record = record_document(result['_coverage_rejection_record'])
    full = json.loads(record['nativeOutput'])
    assert len(record['nativeOutput']) > _LARGE_OUTPUT_THRESHOLD
    assert record['deliveredOutput'] == result['content']
    assert full['draftId'] == observed['view']['draftId']
    drafts = CoverageDrafts({k: state[k] for k in ('candidateDigest', 'objectiveDigest', 'proposalDigest', 'supersessionDigest')}, state['auditId'])
    with pytest.raises(ValueError, match='host original binding'):
        drafts.restore_rejection(call['input'], result['content'])
    drafts.restore_rejection(call['input'], result['content'], rejection_record=result['_coverage_rejection_record'], tool_use_id=call['toolUseId'])
    assert drafts.get(drafts.latest)['submission'] == invalid
    assert restore_evidence_documents(state).missing([observed['sourceId']]) == [observed['sourceId']]
    page_text, phase = [], ['display']
    resumed_states = []
    def continued(body, count):
        if count == 1:
            notice = next(decode(m['content'][m['content'].index('{"coverageAuditContinuation":'):])['coverageAuditContinuation']
                for m in body['messages'] if m['role'] == 'system' and 'coverageAuditContinuation' in m.get('content', ''))
            assert notice['latestUnacceptedDraft']['displayOnly']
            assert len(json.dumps(notice['latestUnacceptedDraft'])) < _LARGE_OUTPUT_THRESHOLD
            return 'completion_evidence_read', {'documentId': observed['view']['documentRef']}
        value = outputs(body)[-1]
        if phase[0] == 'display':
            assert len(body['messages'][-1]['content']) < _LARGE_OUTPUT_THRESHOLD
            assert value['displayOnly'] and 'completeDelivered' not in value
            page_text.append(value['text'])
            if value['nextCursor']:
                return 'completion_evidence_read', {'cursor': value['nextCursor']}
            assert ''.join(page_text) == canonical(full)
            phase[0] = 'no-credit'
            # Even a now-correct draft must fail until actual source pages are read.
            return 'completion_coverage_audit_submit', {'draftId': full['draftId'], 'updates': [
                {'path': '/findings', 'value': []}, {'path': '/requirements/0/witnesses/0/endLine', 'value': 4}]}
        if phase[0] == 'no-credit':
            assert value['errorCode'] == 'coverage_audit_invalid' and value['missingDocumentIds']
            observed['fixedDraftId'] = value['draftId']
            phase[0] = 'source'
            return 'review_evidence_read', {'documentId': observed['sourceId']}
        if value.get('nextCursor'):
            return 'review_evidence_read', {'cursor': value['nextCursor']}
        return 'completion_coverage_audit_submit', {'draftId': observed['fixedDraftId'], 'updates': [{'path': '/findings', 'value': []}]}
    native(monkeypatch, continued)
    unique_transport(monkeypatch)
    decision = run_audit(case, state=state, sink=lambda s: resumed_states.append(deepcopy(s)))
    assert decision['status'] == 'approved'
    assert resumed_states[-1] is None
    assert record_document(state['messagesRecord'])['messages'] == record_document(resumed_states[0]['messagesRecord'])['messages'][:len(record_document(state['messagesRecord'])['messages'])]


def test_three_native_rejections_seal_strategy_note_then_actual_compaction(case, monkeypatch):
    import minicode.context_manager as context_module
    from minicode.context_compactor import ContextCompactor, MicrocompactEngine
    original = context_module.ContextManager
    managers, states, observed = [], [], {}
    def factory(*args, **kwargs):
        manager = original(*args, **kwargs)
        managers.append(manager)
        return manager
    monkeypatch.setattr(context_module, 'ContextManager', factory)
    def action(body, count):
        if count == 1:
            observed['sourceId'] = _coverage_packet(body)['evidenceIndex'][0]['sourceDocumentId']
        if count <= 3:
            return 'completion_coverage_audit_submit', large_invalid(case, count)
        if count == 4:
            assert '[System note: error[strategy_switch_required]' in body['messages'][-1]['content']
            observed['thirdVisible'] = body['messages'][-1]['content']
        if count <= 35:
            return 'review_evidence_read', {'documentId': observed['sourceId']}
        before = deepcopy(managers[-1].messages)
        micro = MicrocompactEngine().run_time_based_microcompact(before, now=10**12)
        assert micro.success
        third = next(m for m in micro.messages if m.get('role') == 'tool_result' and m.get('toolUseId') == 'resume-3')
        assert third['_microcompacted'] and third['_coverage_rejection_record']
        observed['micro'] = deepcopy(micro.messages)
        compacted = ContextCompactor(context_window=2000).compact_messages(before, force=True)
        assert len(compacted) < len(before)
        assert not any(m.get('toolUseId') == 'resume-3' for m in compacted)
        managers[-1].messages[:] = compacted
        observed['active'] = deepcopy(compacted)
        raise KeyboardInterrupt('keep real compacted context and original delivery separately')
    native(monkeypatch, action)
    with pytest.raises(KeyboardInterrupt):
        run_audit(case, sink=lambda s: states.append(deepcopy(s)))
    state = states[-1]
    assert record_document(state['messagesRecord'])['messages'] == observed['active']
    pairs = coverage_delivery_pairs(state)
    assert len(pairs) == 35
    assert record_document(pairs[2][1]['_coverage_rejection_record'])['deliveredOutput'] == observed['thirdVisible']
    assert restore_evidence_documents(state).missing([observed['sourceId']])
    # Genuine microcompaction preserves the original diagnostic provenance.
    micro_state = deepcopy(state)
    saved = record_document(state['messagesRecord'])
    saved['messages'] = observed['micro']
    path = case['persist']('micro-compaction-control', saved)
    micro_state['messagesRecord'] = host_record(path, Path(path).read_bytes())
    assert coverage_delivery_pairs(micro_state) == pairs
    restore_evidence_documents(micro_state)
    target = next(m for m in saved['messages'] if m.get('role') == 'tool_result' and m.get('toolUseId') == 'resume-3')
    target.pop('_coverage_rejection_record')
    path = case['persist']('micro-compaction-tamper', saved)
    micro_state['messagesRecord'] = host_record(path, Path(path).read_bytes())
    with pytest.raises(ValueError, match='differs from delivered provenance'):
        coverage_delivery_pairs(micro_state)
    def stop(body, count):
        notice = next(decode(m['content'][m['content'].index('{"coverageAuditContinuation":'):])['coverageAuditContinuation']
            for m in body['messages'] if m['role'] == 'system' and 'coverageAuditContinuation' in m.get('content', ''))
        assert notice['latestUnacceptedDraft']['draftId'] == decode(observed['thirdVisible'])['draftId']
        assert notice['latestUnacceptedDraft']['displayOnly']
        raise KeyboardInterrupt('actual provider received resumed compacted context')
    native(monkeypatch, stop)
    with pytest.raises(KeyboardInterrupt):
        run_audit(case, state=state)


@pytest.mark.parametrize('change', ['candidate', 'input', 'identity', 'native', 'display', 'suffix', 'draft'])
def test_bound_rejection_tampering_still_rejected(tmp_path, change):
    from minicode.coverage_audit_submission import rejection_delivery_document, rejection_view
    from minicode.completion_evidence_view import EvidenceViews
    bindings = {key: 'sha256:' + 'a' * 64 for key in ('candidateDigest', 'objectiveDigest', 'proposalDigest', 'supersessionDigest')}
    original = {'status': 'approved', 'requirements': [], 'findings': ['x']}
    drafts = CoverageDrafts(bindings, 'audit')
    draft = drafts.retain(original, original)
    value = {'errorCode': 'coverage_audit_invalid', 'draftId': draft['draftId'], 'draftStatus': 'unaccepted', 'message': 'x' * 60000}
    native_output = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    display = json.dumps(rejection_view(value, EvidenceViews(candidate_digest=bindings['candidateDigest'])), ensure_ascii=False, sort_keys=True, allow_nan=False)
    document = rejection_delivery_document(bindings=bindings, audit_id='audit', original_input=original,
        tool_use_id='call', native_output=native_output, display_output=display, delivered_output=display, projection='native_view_v1')
    if change == 'candidate':
        document['candidateDigest'] = 'other'
    elif change == 'input':
        document['inputDigest'] = 'other'
    elif change == 'identity':
        document['toolUseId'] = 'other'
    elif change == 'native':
        document['nativeOutput'] += ' '
    elif change == 'display':
        document['displayOutput'] += ' '
    elif change == 'suffix':
        document['deliveredOutput'] += '\n\n[System note: invented permission]'
    else:
        value['draftId'] = 'coverage_draft_forged'
        document['nativeOutput'] = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        document['displayOutput'] = json.dumps(rejection_view(value, EvidenceViews(candidate_digest=bindings['candidateDigest'])), ensure_ascii=False, sort_keys=True, allow_nan=False)
        document['deliveredOutput'] = document['displayOutput']
    path = tmp_path / 'bound.json'
    path.write_text(canonical(document))
    with pytest.raises(ValueError):
        CoverageDrafts(bindings, 'audit').restore_rejection(original, document['deliveredOutput'], rejection_record=host_record(str(path), path.read_bytes()), tool_use_id='call')


def test_malformed_display_without_host_binding_remains_rejected():
    with pytest.raises(ValueError):
        CoverageDrafts({'candidateDigest': 'candidate'}, 'audit').restore_rejection({}, '{"errorCode":"coverage_audit_invalid","message":"bad\njson"}')
