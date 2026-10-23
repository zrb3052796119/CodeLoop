"""Read-only audit counterexamples; never import a runner or execute frozen code."""
from copy import deepcopy
import json
from pathlib import Path
import random
import shutil
import zipfile

import pytest

from scripts import audit_multi_agent_trigger as audit

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / 'artifacts/multi-agent-trigger-2026-10-23/materials/pack-v3'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def material_fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'materials').mkdir(exist_ok=True)
    for name in ('source-manifest.json', 'offline-proof.json', 'reference-fixes.json'):
        shutil.copyfile(PACK / name, root / 'materials' / name)
    shutil.copyfile(PACK / 'tasks.json', root / 'tasks.json')
    tasks = json.loads((root / 'tasks.json').read_text())
    manifest = {'taskSha256': audit.legacy.digest((root / 'tasks.json').read_bytes()),
                'publicBaselineHashes': {c['id']: {n: audit.legacy.digest(s.encode()) for n, s in sorted({**tasks['shared_files'], **c['files']}.items())} for c in tasks['cases']},
                'oracleHashes': {c['id']: audit.legacy.digest(c['oracle_source'].encode()) for c in tasks['cases']}}
    return manifest, tasks


def experiment(root):
    # Reuse only a read-only auditor test fixture, never the runner.
    from tests.test_multi_agent_gen2_audit import experiment as old_fixture
    manifest, _old_tasks, files = old_fixture(root)
    material_manifest, tasks = material_fixture(root)
    manifest.update(material_manifest)
    harness = list(files)
    harness = [p.removeprefix('scripts/') for p in harness if p.startswith('scripts/')]
    harness += ['run_multi_agent_trigger.py', 'audit_multi_agent_trigger.py', 'analyze_multi_agent_trigger.py', 'prepare_multi_agent_trigger_tasks.py']
    roles = {r: 0 for r in ('plan', 'explore', 'general')}
    runner = (f"PROTOCOL_VERSION={audit.PROTOCOL!r}\nCASES={audit.CASES!r}\nHARNESS_FILES={tuple(harness)!r}\n"
              f"GATE_POLICY={audit.GATE_POLICY!r}\nREQUEST_EVIDENCE_POLICY={audit.GUIDANCE_POLICY['version']!r}\n"
              f"LIMITS={audit.LIMITS!r}\nMODEL='glm-5.3-flash'\nSEED=20261023\nROLE_TURN_LIMITS={roles!r}\n")
    for name in harness:
        files.setdefault('scripts/' + name, b'# frozen evidence fixture, never execute\n')
    files['scripts/run_multi_agent_trigger.py'] = runner.encode()
    for name in ('minicode/coding_progress.py', 'minicode/prompt.py', 'minicode/tools/task.py'):
        files[name] = (ROOT / name).read_bytes()
    with zipfile.ZipFile(root / 'execution-host.zip', 'w') as bundle:
        for name, content in sorted(files.items()):
            bundle.writestr(name, content)
    inventory = [{'path': name, 'size': len(content), 'sha256': audit.legacy.digest(content)} for name, content in sorted(files.items())]
    write(root / 'execution-host-inventory.json', {'schemaVersion': 1, 'files': inventory, 'treeSha256': audit.canonical_digest(inventory)})
    preflight = json.loads((root / 'public-preflight/receipt.json').read_text())
    template = preflight['cases'][0]
    preflight['cases'] = [{**deepcopy(template), 'case': c['id'], 'argv': c['verification_argv']} for c in tasks['cases']]
    preflight['taskDocumentSha256'] = audit.canonical_digest(tasks)
    write(root / 'public-preflight/receipt.json', preflight)
    schedule = [{'case': c, 'arm': 'auto', 'repeat': 1, 'id': f'{c}-r1-auto'} for c in audit.CASES]
    random.Random(20261023).shuffle(schedule)
    manifest.update(protocolVersion=audit.PROTOCOL, phase='trigger-gen2', runner='scripts/run_multi_agent_trigger.py', arms=['auto'], schedule=schedule,
                    gatePolicy=audit.GATE_POLICY, triggerExpectations=audit.EXPECTATIONS, guidanceEvidencePolicy=audit.GUIDANCE_POLICY,
                    requestEvidencePolicy=audit.GUIDANCE_POLICY['version'], implementationPhasePolicy='production-continuous-default',
                    harnessFiles=harness, executionHostTreeSha256=audit.canonical_digest(inventory))
    manifest.pop('explicitRecipe')
    production = sorted(p for p in files if p.startswith('minicode/') and p.endswith('.py'))
    manifest['productionSha256'] = audit.legacy.digest(b''.join(p.encode() + b'\0' + files[p] for p in production))
    manifest['harnessSha256'] = audit.legacy.digest(b''.join(p.encode() + b'\0' + files['scripts/' + p] for p in harness))
    for key, name in [('executionHostArchiveSha256', 'execution-host.zip'), ('executionHostInventorySha256', 'execution-host-inventory.json'), ('publicPreflightSha256', 'public-preflight/receipt.json')]:
        manifest[key] = audit.legacy.digest((root / name).read_bytes())
    manifest['registrationFiles'] = {p.relative_to(root).as_posix(): audit.legacy.digest(p.read_bytes()) for p in root.rglob('*') if p.is_file() and p.name != 'manifest.json'}
    write(root / 'manifest.json', manifest)
    return manifest, tasks, files


def codes(examiner):
    return {i['code'] for i in examiner.issues}


def test_frozen_eight_case_pack_is_valid_without_running_any_source(tmp_path):
    root = tmp_path / 'run'
    experiment(root)
    summary = audit.Audit(root, allow_partial=True).run()
    assert summary['valid'], summary['issues']
    assert summary['expectedTrials'] == 8 and summary['auditedTrials'] == 0
    assert {i['code'] for i in summary['issues']} == {'incomplete_results'}
    assert audit.Audit(root).run()['valid'] is False


@pytest.mark.parametrize('mutation, expected', [('actual_drift', 'target_baseline_not_proven'), ('setup', 'target_baseline_not_proven'), ('exit2', 'target_baseline_not_proven'), ('labels', 'oracle_check_contract_changed'), ('reference', 'source_blob_hashes')])
def test_independent_synthetic_proof_rejects_drift_and_setup_errors(tmp_path, mutation, expected):
    manifest, tasks = material_fixture(tmp_path)
    proof_path = tmp_path / 'materials/offline-proof.json'
    proof = json.loads(proof_path.read_text())
    if mutation == 'reference':
        path = tmp_path / 'materials/reference-fixes.json'
        refs = json.loads(path.read_text())
        refs['cases'][0]['files']['minicode/feed.py'] += '# changed\n'
        write(path, refs)
    elif mutation == 'setup':
        proof[0]['baselineOracle']['stdout'] = 'ImportError'
    elif mutation == 'exit2':
        proof[0]['baselineOracle']['returncode'] = 2
    else:
        key = 'baselineOracle' if mutation == 'actual_drift' else 'referenceOracle'
        value = json.loads(proof[0][key]['stdout'])
        if mutation == 'actual_drift':
            value['checks'][0]['actual'] = value['checks'][0]['expected']
        else:
            value['checks'][0]['label'] = 'another_contract'
        proof[0][key]['stdout'] = json.dumps(value)
    write(proof_path, proof)
    examiner = audit.Audit(tmp_path)
    examiner.provenance(manifest, tasks)
    assert expected in codes(examiner)


def parent(call=1, args=None, event='completed'):
    return {'call': call, 'actorId': 'main', 'role': 'main', 'subagentId': None, 'event': event,
            'toolCalls': [] if args is None else [{'id': 't1', 'name': 'task', 'arguments': args}]}


@pytest.mark.parametrize('args, launch, ambiguous', [({}, 1, 0), ({'action': 'run'}, 1, 0), ({'action': 'spawn', 'agent_type': 'explore'}, 1, 0), ({'action': 'poll', 'subagent_id': 'sub_owned'}, 0, 0), ({'action': 'cancel', 'subagent_id': 'sub_owned'}, 0, 0), ({'action': 'poll'}, 0, 1), ({'action': 'poll', 'subagent_id': 'sub_owned', 'prompt': 'start'}, 0, 1), ({'action': 'status'}, 0, 1), ({'action': None}, 0, 1), ({'action': []}, 0, 1), ([], 0, 1)])
def test_all_task_intent_is_conservatively_classified(args, launch, ambiguous):
    value = audit.classify_task_requests([parent(args=args)])
    assert value['launchRequests'] == launch
    assert value['ambiguousTaskRequests'] == ambiguous
    assert value['launchClassificationComplete'] is (ambiguous == 0)


def test_pending_or_failed_parent_response_cannot_prove_negative_zero():
    for event in ('started', 'failed'):
        result = audit.classify_task_requests([parent(event=event)])
        assert result['launchClassificationComplete'] is False
        assert result['unresolvedParentCalls'] == [1]


def child(identity='child', parent_id=None, kind='explore', container=False):
    return {'subagentId': identity, 'parentSubagentId': parent_id, 'agentType': kind,
            'role': 'executor' if parent_id else kind, 'isContainer': container, 'returned': True,
            'startedEpoch': 10, 'returnedEpoch': 20, 'deliveredEpoch': 21, 'toolOk': False,
            'deliveryEvidence': 'task_result_owner_ack', 'resultSha256': audit.legacy.digest(b'partial result')}


def requests():
    return [parent(args={'agent_type': 'explore'}), {'call': 2, 'subagentId': 'child', 'event': 'completed'}]


def test_partial_result_counts_only_after_real_execution_and_owner_ack():
    run = child()
    assert audit.trigger_evidence([run], requests(), {'child'})['deliveredTrigger'] is True
    assert audit.trigger_evidence([run], requests(), set())['deliveredTrigger'] is False
    assert audit.trigger_evidence([run], [parent(args={})], {'child'})['deliveredTrigger'] is False
    failed = requests(); failed[1]['event'] = 'failed'
    assert audit.trigger_evidence([run], failed, {'child'})['deliveredTrigger'] is False


def async_child():
    run = child()
    snapshot = {'subagentId': 'child', 'agentType': 'explore', 'terminal': True, 'status': 'failed',
                'result': {'ok': False, 'output': 'partial result', 'truncated': False}}
    run.update(deliveryEvidence='async_lifecycle_owner_ack', asyncDeliverySnapshot=snapshot,
               asyncDeliverySnapshotSha256=audit.canonical_digest(snapshot), asyncDeliveryOwnerActorId='main')
    return run


@pytest.mark.parametrize('mutation', ['noack', 'owner', 'identity', 'type', 'status', 'hash', 'result', 'early'])
def test_async_poll_completion_wrong_owner_or_wrong_receipt_never_counts(mutation):
    run, delivered = async_child(), {'child'}
    assert audit.trigger_evidence([run], requests(), delivered)['deliveredTrigger'] is True
    if mutation == 'noack': delivered.clear()
    elif mutation == 'owner': run['asyncDeliveryOwnerActorId'] = 'foreign'
    elif mutation == 'identity': run['asyncDeliverySnapshot']['subagentId'] = 'other'
    elif mutation == 'type': run['asyncDeliverySnapshot']['agentType'] = 'workflow'
    elif mutation == 'status': run['asyncDeliverySnapshot']['status'] = 'running'
    elif mutation == 'hash': run['asyncDeliverySnapshotSha256'] = '0' * 64
    elif mutation == 'result': run['resultSha256'] = '0' * 64
    elif mutation == 'early': run['deliveredEpoch'] = 19
    assert audit.trigger_evidence([run], requests(), delivered)['deliveredTrigger'] is False


def test_executor_delivery_must_bind_its_actual_root_workflow():
    workflow = child('wf', kind='workflow', container=True)
    workflow.update(startedEpoch=5, returnedEpoch=25, deliveredEpoch=26)
    leaf = child(parent_id='wf', kind='general')
    assert audit.trigger_evidence([workflow, leaf], requests(), {'wf'})['deliveredTrigger'] is True
    leaf['parentSubagentId'] = 'other'
    assert audit.trigger_evidence([workflow, leaf], requests(), {'wf'})['deliveredTrigger'] is False
    leaf['parentSubagentId'] = 'wf'; workflow['returnedEpoch'] = 19
    assert audit.trigger_evidence([workflow, leaf], requests(), {'wf'})['deliveredTrigger'] is False


def guidance():
    return {'system': 'STATIC SYSTEM', 'task': 'STATIC TASK', 'dynamic': 'OPTIONAL HOST SUGGESTION'}


def body(hint=True):
    return {'model': 'glm-5.3-flash', 'temperature': 0, 'max_tokens': 8192,
            'messages': [{'role': 'system', 'content': 'STATIC SYSTEM'}, {'role': 'user', 'content': '[Coding progress checkpoint] OPTIONAL HOST SUGGESTION' if hint else 'ordinary task'}],
            'tools': [{'function': {'name': 'task', 'description': 'STATIC TASK'}}, {'function': {'name': 'edit_file', 'description': 'edit'}}]}


def test_hint_is_actual_parent_input_not_copied_native_context_or_tool_description():
    item = audit.guidance_observation(body(), parent(args={'action': 'poll'}), guidance())
    assert item['dynamicHintPresent'] and item['staticSystemGuidePresent'] and item['staticTaskGuidePresent'] and item['directWriteAvailable']
    assert item['responseTools'] == ['task'] and item['launchRequests'] == 0
    native = body(); native['tools'] = [{'function': {'name': 'completion_review_submit'}}]
    item = audit.guidance_observation(native, parent(), guidance())
    assert not item['dynamicHintPresent'] and item['hintCopiedToRestrictedView']
    description = body(False); description['tools'][0]['function']['description'] += ' OPTIONAL HOST SUGGESTION'
    assert not audit.guidance_observation(description, parent(), guidance())['dynamicHintPresent']


def capture(root, call=1, hint=True):
    record = {**parent(call), 'agentType': None, 'startedEpoch': 10, 'seconds': 2, 'routeSha256': 'route', 'maxOutputTokens': 8192, 'toolNames': ['task', 'edit_file']}
    payload = {'schemaVersion': 1, 'callId': call, 'actor': {k: record.get(k) for k in ('actorId', 'subagentId', 'role', 'agentType')}, 'capturedAtEpoch': 11, 'routeSha256': 'route', 'body': body(hint)}
    payload['bodySha256'] = audit.canonical_digest(payload['body'])
    path = root / f'parent-requests/{call}.json'; write(path, payload)
    record.update(parentRequestPath=f'parent-requests/{call}.json', parentRequestSha256=audit.legacy.digest(path.read_bytes()), parentRequestBodySha256=payload['bodySha256'])
    return record


def test_physical_parent_input_hash_and_call_binding_are_required(tmp_path):
    record = capture(tmp_path)
    examiner = audit.Audit(tmp_path); examiner.guidance = guidance()
    manifest = {'model': 'glm-5.3-flash', 'limits': audit.LIMITS}
    observed = examiner.parent_requests(tmp_path, [record], manifest, 'trial')
    assert examiner.issues == []
    assert observed['dynamicHintObserved'] and observed['firstDynamicHintCall'] == 1
    path = tmp_path / 'parent-requests/1.json'
    payload = json.loads(path.read_text()); payload['body']['messages'][1]['content'] = 'changed'
    write(path, payload)
    examiner.parent_requests(tmp_path, [record], manifest, 'trial')
    assert {'parent_request_file_hash', 'parent_request_body_hash', 'parent_request_ledger_body_hash'} <= codes(examiner)


def test_no_dynamic_hint_is_valid_and_replayed_hint_has_one_first_observation(tmp_path):
    examiner = audit.Audit(tmp_path); examiner.guidance = guidance()
    manifest = {'model': 'glm-5.3-flash', 'limits': audit.LIMITS}
    records = [capture(tmp_path, 1, False), capture(tmp_path, 2), capture(tmp_path, 3)]
    assert examiner.parent_requests(tmp_path, records[:1], manifest, 'trial')['dynamicHintObserved'] is False
    examiner.issues.clear()
    observed = examiner.parent_requests(tmp_path, records, manifest, 'trial')
    assert examiner.issues == []
    assert observed['firstDynamicHintCall'] == 2 and len(observed['requests']) == 3


def test_auditor_import_graph_and_guidance_are_read_only_literals():
    import ast
    for name in ('audit_multi_agent_trigger.py', 'analyze_multi_agent_trigger.py'):
        tree = ast.parse((ROOT / 'scripts' / name).read_text())
        imported = [n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not any('run_multi_agent' in n for n in imported)


def test_native_readonly_hallucination_is_separate_but_later_root_task_absence_does_not_hide_launch():
    native = parent(args={'agent_type': 'explore'})
    native['toolNames'] = ['completion_review_submit', 'read_file']
    result = audit.classify_task_requests([native])
    assert result['launchRequests'] == 0 and result['nativeReviewCalls'] == [1]
    root = deepcopy(native); root['toolNames'] = ['read_file']
    assert audit.classify_task_requests([root])['launchRequests'] == 1


def test_admission_refusal_remains_launch_but_not_delivered_trigger():
    result = audit.trigger_evidence([], [parent(args={'agent_type': 'workflow'})], set())
    assert result['launchRequests'] == 1
    assert result['deliveredTrigger'] is False


def test_missing_parent_capture_and_foreign_file_are_invalid(tmp_path):
    record = capture(tmp_path)
    examiner = audit.Audit(tmp_path); examiner.guidance = guidance()
    (tmp_path / 'parent-requests/1.json').unlink()
    examiner.parent_requests(tmp_path, [record], {'model': 'glm-5.3-flash', 'limits': audit.LIMITS}, 'trial')
    assert {'parent_request_nonregular', 'parent_request_coverage'} <= codes(examiner)


def test_cancelled_snapshot_without_a_returned_result_cannot_count_adoption():
    run = async_child()
    run['asyncDeliverySnapshot'].update(status='cancelled', result=None)
    run['asyncDeliverySnapshotSha256'] = audit.canonical_digest(run['asyncDeliverySnapshot'])
    assert not audit.delivery_valid(run, {'child'})


def test_cancelled_owner_ack_without_worker_return_is_valid_observation_not_adoption(tmp_path):
    run = async_child()
    run['returned'] = False
    run.pop('returnedEpoch'); run.pop('resultSha256')
    run['asyncDeliverySnapshot'].update(status='cancelled', result=None)
    run['asyncDeliverySnapshotSha256'] = audit.canonical_digest(run['asyncDeliverySnapshot'])
    assert audit.owner_ack_valid(run, {'child'})
    assert not audit.delivery_valid(run, {'child'})
    evidence = {'runs': [run], 'deliveredIds': ['child']}
    write(tmp_path / 'collaboration.json', evidence)
    records = requests(); records[1].update(actorId='child', agentType='explore', role='explore')
    computed = audit.prior.direct_collaboration([run], records, ['child'])
    examiner = audit.Audit(tmp_path)
    examiner.parent_requests = lambda *_args: {}
    examiner.collaboration(tmp_path, {'id': 'trial', **computed}, records, {})
    assert examiner.issues == []
    assert examiner._trigger['actualChildIds'] == ['child'] and not examiner._trigger['deliveredTrigger']


def queued_ack():
    snapshot = {'subagentId': 'queued', 'agentType': 'explore', 'terminal': True, 'status': 'cancelled', 'result': None}
    return {'subagentId': 'queued', 'agentType': 'explore', 'ownerActorId': 'main', 'ackEpoch': 11,
            'snapshot': snapshot, 'snapshotSha256': audit.canonical_digest(snapshot)}


def test_queued_cancel_ack_without_any_worker_record_is_valid_but_never_adoption(tmp_path):
    ack = queued_ack()
    evidence = {'runs': [], 'deliveredIds': ['queued'], 'asyncTerminalAcks': [ack]}
    write(tmp_path / 'collaboration.json', evidence)
    records = [parent(args={'action': 'spawn', 'agent_type': 'explore'})]
    examiner = audit.Audit(tmp_path)
    examiner.parent_requests = lambda *_args: {}
    computed = audit.prior.direct_collaboration([], records, ['queued'])
    examiner.collaboration(tmp_path, {'id': 'trial', **computed}, records, {})
    assert examiner.issues == []
    assert examiner._trigger['queuedCancellationAckIds'] == ['queued']
    assert examiner._trigger['actualChildIds'] == [] and not examiner._trigger['deliveredTrigger']
    assert examiner._trigger['launchRequests'] == 1  # still a negative-case misfire
    assert 'startedEpoch' not in ack and 'returned' not in ack


@pytest.mark.parametrize('mutation', ['noack', 'owner', 'physical', 'status', 'hash', 'identity', 'result', 'unsupported_type'])
def test_orphan_terminal_ack_is_narrowly_bound_to_unstarted_root_cancellation(mutation):
    ack, delivered, records = queued_ack(), {'queued'}, []
    if mutation == 'noack': delivered.clear()
    elif mutation == 'owner': ack['ownerActorId'] = 'other'
    elif mutation == 'physical': records.append({'subagentId': 'queued', 'event': 'started'})
    elif mutation == 'status': ack['snapshot']['status'] = 'completed'
    elif mutation == 'hash': ack['snapshotSha256'] = '0' * 64
    elif mutation == 'identity': ack['snapshot']['subagentId'] = 'other'
    elif mutation == 'result': ack['snapshot']['result'] = {'ok': True, 'output': 'fake', 'truncated': False}
    elif mutation == 'unsupported_type':
        ack['agentType'] = ack['snapshot']['agentType'] = 'workflow'
        ack['snapshotSha256'] = audit.canonical_digest(ack['snapshot'])
    assert not audit.queued_cancel_ack_valid(ack, delivered, records)
