"""Real challenge receipt must survive native inspection delivery/checkpoint."""
from copy import deepcopy
import json
import subprocess

import pytest
from minicode.completion_coverage_audit import audit_completion_coverage
from minicode.coverage_audit_state import record_document, coverage_delivery_pairs, validate_coverage_audit_state
from minicode.permissions import PermissionManager
from minicode.review_probe_execution import run_isolated_review_probe
from minicode.reviewer_verification import ReviewerProbe, ReviewerProbeLedger
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.workflow_workspace import WorkflowWorkspaceTransaction
from test_completion_coverage_audit import _case, OBJECTIVE, _profile
from test_independent_coverage_inspection import scripted, read_source, inspection


@pytest.mark.parametrize('passes', [True, False], ids=['passed', 'failed'])
@pytest.mark.parametrize('reject_first', [False, True], ids=['clean', 'rejected'])
def test_native_inspection_challenge_is_delivered_and_resumable(tmp_path, monkeypatch, passes, reject_first):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    case = _case(tmp_path, monkeypatch)
    value = {'purpose': 'acceptance', 'criterionIds': [u['id'] for u in case['units']],
             'scenario': 'Independent new challenge', 'code': case['probe'].code if passes else
             case['probe'].code.replace('== 3', '== 999')}
    probe = ReviewerProbe.create(criterion_ids=value['criterionIds'], scenario=value['scenario'], code=value['code'])
    evidence = {case['probe'].probe_id: case['evidence']}
    def challenge(value, context):
        with WorkflowWorkspaceTransaction(str(case['root'])) as transaction:
            frozen = transaction.freeze(allowed_paths=case['spec'].write_paths, paths_relative_to_cwd=True)
            receipt = run_isolated_review_probe(probe=probe, frozen=frozen, check=case['check'], verifier=case['verifier'],
                test_path='test_public.py', read_roots=('.',), forbidden_paths=('.git', '.env'),
                run_kwargs={'workflow_id': 'wf_' + 'b' * 32, 'task_id': 'task_challenge_delivery',
                    'execution_assignment_id': 'asg_challenge_delivery', 'patch_digest': frozen.patch_digest,
                    'permissions': PermissionManager(str(case['root']), prompt=None), 'permission_cwd': str(case['root'])})
            receipt = ReviewerProbeLedger().record(probe, frozen.patch_digest, receipt)
        assert receipt['status'] == ('passed' if passes else 'failed')
        wrapped = {'probeId': probe.probe_id, 'candidateDigest': case['candidate_digest'],
                   'receipt': receipt, 'receiptRef': case['persist']('pytest-receipt', receipt)}
        evidence[probe.probe_id] = {'source': probe.to_dict(), 'receipt': wrapped, 'purpose': 'acceptance', 'active': True}
        return ToolResult(ok=passes, output=json.dumps(wrapped))
    tools = ToolRegistry([*case['tools'].list(), ToolDefinition(name='review_test', description='Execute independent challenge',
        input_schema={'type': 'object', 'properties': {key: {} for key in
            ('purpose', 'criterionIds', 'scenario', 'code', 'correctsProbeId', 'correctionReason', 'sourceEvidence')},
            'required': ['purpose', 'criterionIds', 'scenario', 'code'], 'additionalProperties': False}, validator=lambda value: value,
        run=challenge, capability_profile=_profile())])
    original_schema = deepcopy(next(t for t in tools.list() if t.name == 'review_test').input_schema)
    states = []
    rejected_value = {'purpose': 'acceptance', **{key: case['probe'].to_dict()[key]
        for key in ('criterionIds', 'scenario', 'code')},
        'sourceEvidence': [{'path': 'stock.py', 'startLine': 1, 'endLine': 5}]}
    def first(body):
        schema = next(t['function']['parameters'] for t in body['tools'] if t['function']['name'] == 'review_test')
        assert set(schema['properties']) == {'purpose', 'criterionIds', 'scenario', 'code'}
        assert schema['required'] == original_schema['required']
        assert schema['additionalProperties'] is False
        if reject_first:
            previous = states[-1]
            pair = coverage_delivery_pairs(previous)[-1]
            assert pair[0]['input'] == rejected_value and pair[1]['isError']
            assert json.JSONDecoder().raw_decode(pair[1]['content'])[0]['disallowedFields'] == ['sourceEvidence']
            assert record_document(previous['inspectionRecord'])['challenges'] == []
        return 'review_test', value
    def action(_body):
        raise KeyboardInterrupt('after challenge delivered')
    def resumed(body):
        assert json.JSONDecoder().raw_decode(next(m['content'] for m in body['messages'] if m.get('role') == 'tool'
            and 'coverage_inspection_invalid_probe_fields' in m.get('content', '')))[0]['disallowedFields'] == ['sourceEvidence']
        return read_source()
    actions = [('review_test', rejected_value)] if reject_first else []
    current_witness = json.loads(json.dumps(case['valid']).replace(case['probe'].probe_id, probe.probe_id))
    final_action = ('completion_coverage_audit_submit', current_witness) if passes else action
    continuation = [resumed, ('completion_coverage_inspection_submit', inspection(case)),
        ('completion_coverage_audit_submit', case['valid']), final_action] if reject_first else []
    # Keep one transport sequence across resume so native call IDs stay unique.
    calls = scripted(monkeypatch, case, [*actions, first, action, *continuation])
    with pytest.raises(KeyboardInterrupt, match='after challenge delivered'):
        audit_completion_coverage(objective=OBJECTIVE, requirements=case['units'], proposal=case['proposal'],
            candidate_digest=case['candidate_digest'], model=case['adapter'], tools=tools, cwd=str(case['root']),
            permissions=case['permissions'], host=case['host'], event_sink=None, persist=case['persist'], checkpoint=lambda: None,
            probe_evidence=lambda pid: evidence.get(pid),
            source_anchors=lambda anchors: case['owner']._anchors(anchors,case['root'],case['permissions']),
            state_sink=lambda state: states.append(deepcopy(state)))
    assert len(calls) == (3 if reject_first else 2)
    assert next(t for t in tools.list() if t.name == 'review_test').input_schema == original_schema
    assert probe.probe_id in evidence
    pending = states[-1]
    assert validate_coverage_audit_state(pending, objective=OBJECTIVE) == pending
    assert probe.probe_id in record_document(pending['inspectionRecord'])['challenges']
    delivered = [pair for pair in coverage_delivery_pairs(pending) if pair[0]['toolName'] == 'review_test']
    assert len(delivered) == (2 if reject_first else 1)
    assert delivered[-1][1]['isError'] is (not passes)
    if not reject_first:
        return
    assert delivered[0][0]['input'] == rejected_value and delivered[0][1]['isError']
    assert record_document(pending['inspectionRecord'])['challenges'] == [probe.probe_id]
    prior_messages = record_document(pending['messagesRecord'])['messages']
    # The rejected input matched a retained old probe. Recording its error must
    # not grant that probe source credit when this same audit later reconciles.
    prior_call_count = len(calls)
    def resume():
        return audit_completion_coverage(objective=OBJECTIVE, requirements=case['units'], proposal=case['proposal'],
            candidate_digest=case['candidate_digest'], model=case['adapter'], tools=tools, cwd=str(case['root']),
            permissions=case['permissions'], host=case['host'], event_sink=None, persist=case['persist'], checkpoint=lambda: None,
            probe_evidence=lambda pid: evidence.get(pid),
            source_anchors=lambda anchors: case['owner']._anchors(anchors,case['root'],case['permissions']),
            continuation_state=pending, state_sink=lambda state: states.append(deepcopy(state)))
    if passes:
        assert resume()['status'] == 'approved'
    else:
        with pytest.raises(KeyboardInterrupt, match='after challenge delivered'):
            resume()
    restored = next(state for state in reversed(states) if state is not None)
    assert validate_coverage_audit_state(restored, objective=OBJECTIVE) == restored
    # The phase switch may refresh a host-derived Task Ledger; actual messages
    # and all native call/result bytes must remain in order and unchanged.
    actual_prior = [m for m in prior_messages if not m.get('_task_ledger')]
    actual_restored = [m for m in record_document(restored['messagesRecord'])['messages'] if not m.get('_task_ledger')]
    assert actual_restored[:len(actual_prior)] == actual_prior
    assert record_document(pending['messagesRecord'])['messages'] == prior_messages
    assert coverage_delivery_pairs(restored)[:2] == delivered
    assert record_document(restored['inspectionRecord'])['challenges'] == [probe.probe_id]
    assert len(calls) - prior_call_count == 4
    failure = next(m['content'] for m in reversed(calls[-1]['messages']) if m.get('role') == 'tool')
    assert 'read the exact complete probe source' in failure
