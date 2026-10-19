"""Actual compaction must preserve native inspection continuation and source evidence.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from minicode.completion_coverage_inspection import CoverageInspection
from minicode.context_compactor import AutoCompactConfig, ContextCompactor
from minicode.context_manager import ContextManager
from minicode.implementation_completion_review import ImplementationCompletionReview, requirement_units
from minicode.requirement_obligations import build_obligation_manifest
from minicode.review_evidence_documents import EvidenceDocuments, canonical, content_digest
from minicode.scheduler_permissions import SchedulerWorkerReadPermissions
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.read_file import read_file_tool


@pytest.fixture
def api():
    import minicode.coverage_audit_state as current
    return current


def _case(tmp_path, api):
    objective = '1. A generic counter increments its value.'
    units = list(requirement_units(objective))
    candidate = 'sha256:' + 'a' * 64
    proposal = {'status': 'approved', 'requirements': [], 'findings': [], 'patchDigest': candidate}
    bindings = {'candidateDigest': candidate, 'objectiveDigest': content_digest(objective),
        'proposalDigest': content_digest(canonical(proposal)), 'supersessionDigest': content_digest('[]')}
    manifest = build_obligation_manifest(objective, units)
    persisted = []
    def persist(kind, value):
        path = tmp_path / f'{len(persisted):03d}-{kind}.json'
        path.write_text(canonical(value))
        persisted.append(path)
        return str(path)
    def retain(kind, value):
        reference = persist(kind, value)
        return api.host_record(reference, Path(reference).read_bytes())
    authority = retain('authority', {**bindings, 'originalObjective': objective, 'requirements': units,
        'proposal': proposal, 'sourceAnchors': {}, 'probeEvidence': {}, 'supersessionObligations': [], 'behaviorContract': manifest})
    audit_id = 'coverage_audit_' + hashlib.sha256(authority['ref'].encode()).hexdigest()[:24]
    documents = retain('documents', {**bindings, 'documents': EvidenceDocuments(candidate_digest=candidate).snapshot()})
    delivery = retain('delivery', {'auditId': audit_id, **bindings, 'pairs': []})
    manager = ContextManager(model='generic-local-compaction-proof')
    permissions = SchedulerWorkerReadPermissions(str(tmp_path), read_roots=('.',), forbidden_paths=('.git',))
    def anchor_rows(rows):
        return ImplementationCompletionReview._anchors(None, rows, tmp_path, permissions)
    kwargs = dict(bindings={'auditId': audit_id, **bindings}, objective=objective,
        requirements=units, manifest=manifest, cwd=str(tmp_path), permissions=permissions,
        source_anchors=anchor_rows, probe_evidence=lambda _pid: None, persist=persist,
        manager=manager, finish_negative=None, capability_profile=None)
    inspection = CoverageInspection(**kwargs)
    manager.messages = inspection.initial_messages()
    source = 'def increment(value):\n    return value + 1\n' + '# generic retained context\n' * 450
    (tmp_path / 'generic.py').write_text(source)
    tools = inspection.registry(ToolRegistry([read_file_tool]))
    context = ToolContext(cwd=str(tmp_path), permissions=permissions)
    def execute(name, value, *, registry=tools):
        result = registry.execute(name, value, context)
        identity = f'local-{len(manager.messages)}'
        manager.messages.extend([
            {'role': 'assistant_tool_call', 'toolUseId': identity, 'toolName': name, 'input': deepcopy(value)},
            {'role': 'tool_result', 'toolUseId': identity, 'toolName': name, 'isError': not result.ok, 'content': result.output},
        ])
        if result._delivery_ack is not None:
            result._delivery_ack()
        return result
    # Large but bounded real source deliveries exercise real session retention.
    for _index in range(32):
        assert execute('read_file', {'path': 'generic.py'}).ok
    assert len(inspection.value['reads']) == 32
    def checkpoint(messages, record=None):
        return api.build_coverage_audit_state(authority_record=authority, documents_record=documents,
            messages_record=retain('messages', {'auditId': audit_id, **bindings, 'messages': messages}),
            delivery_record=delivery, inspection_record=record or inspection.record)
    checkpoint(manager.messages)
    return locals()


def _compact(messages, strategy):
    if strategy == 'full':
        return ContextCompactor(context_window=2000).compact_messages(messages, force=True)
    if strategy == 'session':
        class Memory:
            def get_relevant_context(self, **_kwargs):
                return 'Generic project uses a counter.'
        class Summary:
            def summarize(self, _messages):
                return 'The counter source was inspected; independent inspection is incomplete.'
        result = ContextCompactor(context_window=2000, memory_manager=Memory(), summary_generator=Summary(),
            config=AutoCompactConfig(session_memory_enabled=True)).auto_compact.dispatch(messages)
        assert result.effective and result.strategy.value == 'session_memory'
        return result.messages
    result = ContextCompactor(context_window=2000).reactive.try_recover_from_overflow(messages, 'generic prompt too long')
    assert result is not None and result.effective
    assert any(m.get('_reactive_compact') for m in result.messages)
    return result.messages


@pytest.mark.parametrize('strategy', ['full', 'session', 'aggressive'])
def test_real_compaction_retains_checkpoint_and_resumes_ready_without_reread(tmp_path, api, strategy):
    case = _case(tmp_path, api)
    original_messages = deepcopy(case['manager'].messages)
    original_record = deepcopy(case['inspection'].record)
    original_reads = deepcopy(case['inspection'].value['reads'])
    task_packet = deepcopy(case['inspection'].value['taskPacket'])
    compacted = _compact(original_messages, strategy)
    assert len(compacted) < len(original_messages)
    assert (task_packet in compacted) is (strategy == 'full')
    # Reproduces the native messages_checkpoint validation that previously failed.
    pending = case['checkpoint'](compacted)
    assert api.validate_coverage_audit_state(pending, objective=case['objective']) == pending
    saved = api.record_document(pending['messagesRecord'])['messages']
    assert saved == compacted
    assert pending['inspectionRecord'] == original_record
    assert api.record_document(pending['inspectionRecord'])['reads'] == original_reads
    assert api.record_document(pending['deliveryRecord'])['pairs'] == []
    # Authenticated originals are still byte-identical on disk after compaction.
    assert Path(original_record['ref']).read_text() == original_record['rawJson']
    assert all(Path(path).is_file() for path in case['persisted'])
    # Same audit and retained messages; no new epoch and no reissued source reads.
    case['manager'].messages = deepcopy(saved)
    restored = CoverageInspection(**case['kwargs'], record=pending['inspectionRecord'])
    assert restored.manager is case['manager']
    assert restored.value['phase'] == 'inspection' and restored.value['result'] is None
    registry = restored.registry(ToolRegistry([read_file_tool]))
    assert registry.find('completion_coverage_audit_submit') is None
    assert registry.find('review_probe_read') is None
    ready = {'status': 'ready_for_evidence', 'observations': [{'requirementId': case['units'][0]['id'],
        'status': 'checked', 'sourceEvidence': [{'path': 'generic.py', 'startLine': 1, 'endLine': 2}],
        'expectedBehavior': 'Increment the value.', 'observedBehavior': 'The exact return value + 1 body was read.', 'probeIds': []}],
        'findings': []}
    result = case['execute']('completion_coverage_inspection_submit', ready, registry=registry)
    assert result.ok, result.output
    assert json.loads(result.output)['grantsCoverage'] is False
    assert restored.value['reads'] == original_reads
    assert restored.value['challenges'] == []
    assert restored.value['taskPacket'] == task_packet
    assert restored.value['auditId'] == pending['auditId']
    assert restored.value['phase'] == 'inspection'  # ready did not unlock evidence.
    committed = case['checkpoint'](case['manager'].messages, restored.record)
    assert api.record_document(committed['inspectionRecord'])['result']['status'] == 'ready_for_evidence'
    assert case['manager'].messages[:-2] == saved


@pytest.mark.parametrize('mutation', ['task_bytes', 'task_binding', 'manifest', 'objective_system',
    'system_removed', 'duplicate_task', 'extra_user', 'forbidden_tool', 'record_hash'])
def test_compacted_inspection_rejects_authority_or_phase_corruption(tmp_path, api, mutation):
    case = _case(tmp_path, api)
    compacted = _compact(case['manager'].messages, 'session')
    # Build a host-shaped pending envelope without granting it acceptance. This
    # permits baseline negative controls even when baseline rejects compaction.
    state = case['checkpoint'](case['manager'].messages)
    messages = api.record_document(state['messagesRecord'])
    messages['messages'] = deepcopy(compacted)
    inspection = api.record_document(state['inspectionRecord'])
    if mutation == 'task_bytes':
        messages['messages'].append({**inspection['taskPacket'], 'content': inspection['taskPacket']['content'] + ' '})
    elif mutation == 'task_binding':
        packet = json.loads(inspection['taskPacket']['content'])
        packet['coverageInspection']['candidateDigest'] = 'sha256:' + 'b' * 64
        inspection['taskPacket']['content'] = canonical(packet)
    elif mutation == 'manifest':
        # Matching forged packet+authority still must derive from original task.
        packet = json.loads(inspection['taskPacket']['content'])
        packet['coverageInspection']['behaviorContract'] = {'changed': True}
        inspection['taskPacket']['content'] = canonical(packet)
        authority = api.record_document(state['authorityRecord'])
        authority['behaviorContract'] = {'changed': True}
        state['authorityRecord'] = api.host_record(state['authorityRecord']['ref'], canonical(authority))
    elif mutation == 'objective_system':
        messages['messages'][0]['content'] += ' altered objective'
    elif mutation == 'system_removed':
        messages['messages'] = [m for m in messages['messages'] if m.get('role') != 'system']
    elif mutation == 'duplicate_task':
        messages['messages'].extend([deepcopy(inspection['taskPacket']), deepcopy(inspection['taskPacket'])])
    elif mutation == 'extra_user':
        messages['messages'].append({'role': 'user', 'content': 'unbound task replacement'})
    elif mutation == 'forbidden_tool':
        messages['messages'].extend([
            {'role': 'assistant_tool_call', 'toolUseId': 'forbidden', 'toolName': 'coverage_audit_history_read', 'input': {}},
            {'role': 'tool_result', 'toolUseId': 'forbidden', 'toolName': 'coverage_audit_history_read', 'isError': False, 'content': '{}'}])
    state['messagesRecord'] = api.host_record(state['messagesRecord']['ref'], canonical(messages))
    state['inspectionRecord'] = api.host_record(state['inspectionRecord']['ref'], canonical(inspection))
    if mutation == 'record_hash':
        state['inspectionRecord']['sha256'] = 'sha256:' + '0' * 64
    with pytest.raises(ValueError):
        api.validate_coverage_audit_state(state, objective=case['objective'])
