"""Offline only: exact source reuse, evidence classification, and real local runtime."""
from contextlib import ExitStack
from copy import deepcopy
import importlib.util
import inspect
import json
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from scripts import native_review_bounded_validation as runner
from scripts import audit_native_review_bounded as audit
from test_multi_agent_trigger_runner import RUNTIME, ScriptedResponse, task_document


def test_exact_original_objects_and_private_source_proof(tmp_path):
    output = tmp_path / 'prepared'
    runner.prepare(output)
    tasks = runner.shared.read_json(output / 'tasks.json')
    original = runner.shared.read_json(runner.SOURCE / 'tasks.json')
    assert tasks['cases'] == [next(c for c in original['cases'] if c['id'] == name) for name in runner.CASES]
    assert tasks['shared_files'] == original['shared_files']
    manifest = {'taskSha256': runner.shared.sha((output / 'tasks.json').read_bytes()),
        'schedule': runner.schedule_for(), 'registrationFiles': {p.relative_to(output).as_posix(): runner.shared.sha(p.read_bytes())
            for p in output.rglob('*') if p.is_file()}}
    examiner = audit.Audit(output, allow_partial=True)
    examiner.exact_reuse(manifest, tasks)
    assert examiner.issues == []
    tasks['cases'][0]['prompt'] += '\nUse a child agent.'
    with pytest.raises(ValueError, match='differs'):
        runner.validate_offline_proof(output, tasks)


def test_contract_and_gate_do_not_inherit_eight_case_adoption():
    contract = runner.static_contract()
    examiner = audit.Audit(ROOT, allow_partial=True)
    assert examiner.design(contract, dict.fromkeys(runner.CASES)) == ['auto']
    assert examiner.issues == []
    assert len(contract['schedule']) == 2
    assert contract['gatePolicy']['budgetExhaustedPasses'] is False
    assert 'triggerRateDenominator' not in contract['gatePolicy']
    assert contract['reviewPolicy']['mode'] == 'bounded'
    assert 'PRIVATE' not in runner.prompt_for_arm('PUBLIC', 'auto')


def test_mixed_working_tree_never_becomes_host_implicitly():
    with pytest.raises(ValueError, match='working tree'):
        runner.host_files()


@pytest.mark.parametrize('mode', ['run', 'child'])
def test_no_registration_means_no_model_runtime(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(runner, 'runner', lambda *_a, **_k: pytest.fail('runtime must not start'))
    with pytest.raises(ValueError, match='preregistered-commit'):
        runner.dispatch(tmp_path, mode, None)


def fixture_events(terminal='approved'):
    events = []
    def add(kind, moment, payload):
        events.append({'sequence': len(events) + 1, 'kind': kind, 'epoch': 1000 + moment,
            'monotonic': moment, 'payload': payload})
    add('observer_started', 1, {'mode': 'bounded', 'trialId': 'stock'})
    add('chain_created', 10, {'agentDepth': 0, 'deadline': 250, 'ancestorDeadline': 600, 'closingSeconds': 88.5})
    binding = {'executorId': 'implementation_' + 'a' * 32, 'attempt': 1, 'patchDigest': 'sha256:' + 'b' * 64}
    add('host_event', 10, {'eventType': 'implementation.completion_review_started', 'data': binding})
    add('phase_selected', 10, {'agentDepth': 0, 'phase': 'review', 'deadline': 100, 'chainDeadline': 250, 'stageCalls': 8})
    add('phase_selected', 30, {'agentDepth': 0, 'phase': 'inspection', 'deadline': 180, 'chainDeadline': 250, 'stageCalls': 8})
    add('phase_selected', 50, {'agentDepth': 0, 'phase': 'reconciliation', 'deadline': 110, 'chainDeadline': 250, 'stageCalls': 4})
    status = {'approved': 'approved', 'rejected': 'changes_required', 'budget_exhausted': 'inconclusive', None: 'inconclusive'}[terminal]
    data = {**binding, 'status': status, 'requirementsAccepted': terminal == 'approved'}
    if terminal:
        data['completionReview'] = terminal
    if terminal == 'budget_exhausted':
        data['reason'] = 'completion_review_budget_exhausted'
    add('host_event', 70, {'eventType': 'implementation.completion_decided', 'data': data})
    add('host_event', 71, {'eventType': 'task.outcome', 'data': {'completionSucceeded': terminal == 'approved', 'outcomeStatus': 'success' if terminal == 'approved' else 'partial'}})
    add('observer_finished', 72, {'mode': 'bounded', 'trialId': 'stock'})
    requests = [{'call': i + 1, 'startedEpoch': 1000 + moment, 'event': 'completed', 'seconds': 10,
        'toolNames': [name], 'usage': {'source': 'provider', 'input_tokens': 100, 'output_tokens': 20}}
        for i, (moment, name) in enumerate([(11, 'completion_review_submit'), (31, 'completion_coverage_inspection_submit'), (51, 'completion_coverage_audit_submit')])]
    return events, requests


@pytest.mark.parametrize('terminal,passes', [('approved', True), ('rejected', True), ('budget_exhausted', False), (None, False)])
def test_actual_decision_gate_is_separate_from_repair_success(terminal, passes):
    events, requests = fixture_events(terminal)
    review = audit.native_observation(events, requests, {'durationSeconds': 72, 'errorClass': None})
    assert review['terminalConsistent']
    rows = [{'case': c, 'success': True, 'usageComplete': True, 'unknownUsageCalls': 0,
        'nativeReview': review if i == 0 else {}} for i, c in enumerate(runner.CASES)]
    result = audit.assess({'valid': True, 'mode': 'final', 'expectedTrials': 2, 'auditedTrials': 2, 'trials': rows})
    assert result['passed'] is passes
    assert review['stages']['review']['activeSeconds'] == 20
    assert review['stages']['inspection']['physicalRequests'] == 1


@pytest.mark.parametrize('mutation', ['unknown_usage', 'partial_audit', 'invalid_audit', 'no_physical', 'no_accepted_final', 'wrong_start', 'late_decision'])
def test_missing_or_invalid_evidence_cannot_pass(mutation):
    events, requests = fixture_events()
    if mutation == 'no_physical':
        requests.clear()
    elif mutation == 'no_accepted_final':
        events[-2]['payload']['data']['completionSucceeded'] = False
    elif mutation == 'wrong_start':
        events[2]['payload']['data'] = {**events[2]['payload']['data'], 'attempt': 9}
    elif mutation == 'late_decision':
        events[1]['payload']['deadline'] = 60
    review = audit.native_observation(events, requests, {'durationSeconds': 72, 'errorClass': None})
    rows = [{'case': c, 'usageComplete': mutation != 'unknown_usage', 'unknownUsageCalls': int(mutation == 'unknown_usage'), 'nativeReview': review} for c in runner.CASES]
    result = audit.assess({'valid': mutation != 'invalid_audit', 'mode': 'partial' if mutation == 'partial_audit' else 'final', 'expectedTrials': 2, 'auditedTrials': 2, 'trials': rows})
    assert result['passed'] is (mutation == 'unknown_usage')
    if mutation == 'unknown_usage':
        assert result['usageVerification'] == 'unverifiable'


def test_observer_preserves_real_allowance_return_and_exception(tmp_path):
    from minicode.agent_budget import AgentTurnBudget
    from minicode import completion_review_limits as limits
    from minicode.task_deadline import TaskDeadlineExceeded
    host = SimpleNamespace(_agent_depth=0, _task_deadline_monotonic=time.monotonic() + 600,
        _agent_budget=AgentTurnBudget(max_total_tokens=None, max_model_calls=40, max_cost_usd=None))
    observer = runner.NativeObservation(tmp_path / 'events.jsonl')
    with ExitStack() as stack:
        observer.install(stack)
        host._completion_review_limits = limits.create_review_limits(host)
        limits.select_review_phase(host, 'review')
        assert host._completion_review_limits['stages']['review']['calls'] == 8
        host._completion_review_limits['stages']['review']['deadline'] = time.monotonic() - 1
        with pytest.raises(TaskDeadlineExceeded):
            limits.select_review_phase(host, 'review')
    records = [json.loads(s) for s in observer.path.read_text().splitlines()]
    assert [r['kind'] for r in records] == ['chain_created', 'phase_selected', 'phase_selected']


def test_real_runtime_retains_native_failure_and_physical_usage(tmp_path, monkeypatch):
    from minicode import openai_adapter, workflow_runner
    from minicode.deterministic_verifier import DeterministicVerifier
    class LocalSandbox:
        def wrap_command(self, *, argv, cwd, **kwargs):
            return argv
    monkeypatch.setattr(workflow_runner, '_deterministic_verifier_for_context', lambda *_a, **_k:
        DeterministicVerifier(sandbox_backend=LocalSandbox(), explicit_trusted_executables={'python3': sys.executable}))
    output = tmp_path / 'run'
    output.mkdir()
    doc = task_document()
    doc['cases'] = [next(c for c in doc['cases'] if c['id'] == n) for n in runner.CASES]
    runner.shared.write_json(output / 'tasks.json', doc)
    manifest = {**runner.static_contract(), 'routeSha256': 'a' * 64,
        'resolvedTransportRouteSha256': runner.trigger.transport.route_fingerprint(RUNTIME),
        'productionSha256': 'c' * 64, 'harnessSha256': 'd' * 64,
        'taskSha256': runner.shared.sha((output / 'tasks.json').read_bytes()),
        'publicVerificationFixturePolicy': runner.PUBLIC_FIXTURE_POLICY,
        'publicVerificationFixtures': [], 'publicPreflightSha256': 'e' * 64}
    runner.shared.write_json(output / 'manifest.json', manifest)
    monkeypatch.setattr(runner, 'validate', lambda *_a, **_k: manifest)
    engine = runner.runner(output)
    inner = inspect.getclosurevars(engine.execute_trial).nonlocals['original']
    monkeypatch.setitem(inner.__globals__, 'validate_public_preflight', lambda *_a, **_k: None)
    monkeypatch.setattr(engine, 'frozen_runtime', lambda: {**RUNTIME, 'subagentRoutingEnabled': False,
        'reflectionSynthesizerMode': 'rule', 'memoryHybridEnabled': False, 'allowRemoteMemoryEmbedding': False,
        'intentSemanticClassifier': {'enabled': False}, 'skillCandidateReranker': {'enabled': False},
        'agentTurnBudget': {'maxTokens': 500000, 'maxModelCalls': 40, 'maxCostUsd': '5'}})
    monkeypatch.setattr(engine, 'route_hash', lambda _: 'a' * 64)
    monkeypatch.setattr(engine, 'production_sha', lambda: 'c' * 64)
    monkeypatch.setattr(engine, 'harness_sha', lambda: 'd' * 64)
    sent = []
    actions = [('write_file', {'path': 'tests/test_eval_regression.py', 'content': 'from minicode.a import VALUE\ndef test_changed(): assert VALUE == 2\n'}),
        ('edit_file', {'path': 'minicode/a.py', 'old': 'VALUE = 1', 'new': 'VALUE = 2'})]
    def send(request, **kwargs):
        body = json.loads(request.data)
        sent.append(body)
        if len(sent) > 3:
            raise OSError('offline native review unavailable')
        if len(sent) <= 2:
            name, value = actions[len(sent) - 1]
            return ScriptedResponse({'tool_calls': [{'index': 0, 'id': 'call-' + str(len(sent)), 'function': {'name': name, 'arguments': json.dumps(value)}}]})
        return ScriptedResponse({'content': 'Fixed VALUE and added a regression.'})
    monkeypatch.setattr(openai_adapter, 'open_verified_url', send)
    trial = runner.schedule_for()[0]['id']
    root = output / 'private' / trial
    root.mkdir(parents=True)
    workspace = None
    try:
        engine.execute_trial(output, trial)
        workspace = Path(runner.shared.read_json(root / 'workspace.json')['path'])
        assert not (root / 'error.json').exists()
        assert (workspace / 'minicode/a.py').read_text() == 'VALUE = 2\n'
        assert not any('PRIVATE_ORACLE_' in p.read_text(errors='ignore') for p in workspace.rglob('*') if p.is_file())
        ledger = runner.trigger.transport.SharedLedger(runner.trigger.transport.TransportConfig(**runner.shared.read_json(root / 'transport-config.json'))).snapshot()
        events = [json.loads(s) for s in (root / 'native-review-observation.jsonl').read_text().splitlines()]
        result = audit.native_observation(events, ledger['requests'], {'durationSeconds': 30, 'errorClass': None})
        assert result['chainCount'] == 1
        assert result['nativePhysicalRequests'] > 0 and result['nativeClassificationComplete']
        assert result['terminal'] is None and not result['acceptedFinal']
        assert ledger['unknownUsageCalls'] == len(sent) - 3 > 0
        assert not ledger['usageComplete']
        assert any(e['kind'] == 'host_event' and e['payload']['eventType'] == 'implementation.completion_decided' for e in events)
    finally:
        if workspace is None and (root / 'workspace.json').exists():
            workspace = Path(runner.shared.read_json(root / 'workspace.json')['path'])
        if workspace:
            shutil.rmtree(workspace, ignore_errors=True)


def test_rejected_within_review_limits_survives_later_parent_timeout():
    events, requests = fixture_events('rejected')
    result = audit.native_observation(events, requests, {'durationSeconds': 600.1, 'errorClass': 'HardTimeout'})
    assert result['terminal'] == 'rejected' and result['decisionWithinChain']
    assert not result['acceptedFinal']


def test_relaxed_phase_seconds_and_late_phase_decision_are_not_budget_proof():
    events, requests = fixture_events('rejected')
    events[3]['payload']['deadline'] = 200
    result = audit.native_observation(events, requests, {'durationSeconds': 72})
    assert not result['stageCapsConsistent']
    events, requests = fixture_events('rejected')
    events[5]['payload']['deadline'] = 60
    result = audit.native_observation(events, requests, {'durationSeconds': 72})
    assert not result['decisionWithinChain']


def test_new_review_exhaustion_cannot_be_hidden_by_earlier_rejection():
    events, requests = fixture_events('rejected')
    events = events[:-2]
    previous = deepcopy(events[-1]['payload']['data'])
    previous.update(attempt=2, status='inconclusive', completionReview='budget_exhausted',
        reason='completion_review_budget_exhausted', requirementsAccepted=False)
    binding = {k: previous[k] for k in ('executorId', 'attempt', 'patchDigest')}
    for event_type, data in [('implementation.completion_review_started', binding), ('implementation.completion_decided', previous)]:
        events.append({'sequence': len(events) + 1, 'kind': 'host_event', 'epoch': 1080 + len(events),
            'monotonic': 80 + len(events), 'payload': {'eventType': event_type, 'data': data}})
    result = audit.native_observation(events, requests, {'durationSeconds': 100})
    assert result['terminal'] == 'budget_exhausted' and not result['acceptedFinal']


def test_public_check_failure_is_not_a_new_native_review_terminal():
    events, requests = fixture_events('rejected')
    events = events[:-2]
    previous = deepcopy(events[-1]['payload']['data'])
    previous.update(attempt=2, reason='public_checks_not_current_and_passed')
    events.append({'sequence': len(events) + 1, 'kind': 'host_event', 'epoch': 1090, 'monotonic': 90,
        'payload': {'eventType': 'implementation.completion_decided', 'data': previous}})
    result = audit.native_observation(events, requests, {'durationSeconds': 100})
    assert result['terminal'] == 'rejected' and result['nativeReviewActuallyExecuted']


@pytest.mark.parametrize('mutation,expected', [('same', True), ('candidate', False), ('findings', False), ('budget_exhausted', False)])
def test_same_candidate_cached_negative_needs_exact_prior_native_authority(mutation, expected):
    events, requests = fixture_events('rejected')
    events = events[:-2]
    value = deepcopy(events[-1]['payload']['data'])
    value.update(attempt=2, evidenceDirectory='/new/storage')
    if mutation == 'candidate':
        value['patchDigest'] = 'sha256:' + 'c' * 64
    elif mutation == 'findings':
        value['findings'] = ['a new unsupported claim']
    elif mutation == 'budget_exhausted':
        value.update(status='inconclusive', completionReview='budget_exhausted', reason='completion_review_budget_exhausted')
    binding = {k: value[k] for k in ('executorId', 'attempt', 'patchDigest')}
    for kind, data in [('implementation.completion_review_started', binding), ('implementation.completion_decided', value)]:
        events.append({'sequence': len(events) + 1, 'kind': 'host_event', 'epoch': 1080 + len(events), 'monotonic': 80 + len(events),
            'payload': {'eventType': kind, 'data': data}})
    result = audit.native_observation(events, requests, {'durationSeconds': 100})
    assert result['nativeReviewActuallyExecuted'] is expected
    assert result['nativeAuthorityReused'] is expected


def test_delayed_exact_cached_negative_uses_real_native_authority_time():
    events, requests = fixture_events('rejected')
    events = events[:-2]
    value = deepcopy(events[-1]['payload']['data'])
    value.update(attempt=2, evidenceDirectory='/later/storage')
    binding = {k: value[k] for k in ('executorId', 'attempt', 'patchDigest')}
    for kind, data in [('implementation.completion_review_started', binding), ('implementation.completion_decided', value)]:
        events.append({'sequence': len(events) + 1, 'kind': 'host_event', 'epoch': 1300 + len(events),
            'monotonic': 300 + len(events), 'payload': {'eventType': kind, 'data': data}})
    result = audit.native_observation(events, requests, {'durationSeconds': 600.1, 'errorClass': 'HardTimeout'})
    assert result['nativeAuthorityReused'] and result['delayedHandoff']
    assert result['decisionWithinChain'] and result['terminal'] == 'rejected'
