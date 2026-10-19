"""Offline native checkpoint recovery contracts; no real model invocation."""
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import stat

import pytest

from scripts import run_requirement_completion_checkpoint as runner

ROOT = Path(__file__).resolve().parents[1]
NAME = 'run_requirement_completion_checkpoint.py'
DRAFT = ROOT / 'scripts' / NAME
spec = importlib.util.spec_from_file_location('native_checkpoint_generic_fixture', ROOT / 'tests/test_requirement_completion_continuation.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
write = fixtures.write


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail('local checkpoint contracts cannot call a provider or network')
    monkeypatch.setattr(socket.socket, 'connect', fail)
    monkeypatch.setattr(socket, 'create_connection', fail)


def physical_ledger(root, *, unknown=False, error_class='EvaluationIdleTimeout'):
    from scripts.evaluation_transport import SharedLedger, TransportConfig
    path = root / 'transport.sqlite'
    if path.exists():
        path.unlink()
    config = TransportConfig(ledger_path=str(path.resolve()), expected_model='glm-5.3-flash',
        expected_route_sha256='a' * 64, deadline_epoch=None, max_model_calls=None, max_total_tokens=None,
        max_output_tokens=131072, connect_timeout_seconds=30, idle_timeout_seconds=120, request_timeout_seconds=None)
    ledger = SharedLedger(config)
    call = ledger.start({'model': 'glm-5.3-flash'}, 1000)
    ledger.update(call, {'event': 'completed', 'usage': {'source': 'provider', 'input_tokens': 100,
        'output_tokens': 20, 'cache_read_tokens': 30}})
    if unknown:
        call = ledger.start({'model': 'glm-5.3-flash'}, 1000)
        ledger.update(call, {'event': 'failed', 'errorClass': error_class, 'errorPhase': 'stream',
            'seconds': 125, 'lastByteSeconds': 5, 'streamDone': False, 'httpStatus': 200})
    return ledger


def seal_terminal(source, root):
    execution = json.loads((root / 'execution.json').read_bytes())
    write(source / 'results.json', {'manifestSha256': runner.sha_file(source / 'manifest.json'),
        'expectedTrials': 1, 'trials': [{'id': runner.TRIAL_ID, 'success': False,
            'errorClass': execution['errorClass'], 'processCleanup': {'cleanupComplete': True},
            'judgement': {'cleanupComplete': True}}]})


def terminal(tmp_path, *, error_class=None, stop_decision=True):
    from minicode.implementation_continuation import HostImplementationContinuation
    from minicode.implementation_completion_review import ImplementationCompletionReview
    from minicode.permissions import PermissionManager
    from minicode.tooling import ToolContext
    from minicode.run_events import emit_event_safely
    from minicode.run_journal import RunJournal
    from minicode.run_lifecycle import observe_run
    source, root, workspace, messages = fixtures.predecessor(tmp_path)
    (workspace / 'read_only_dependency.py').write_text('VALUE = 1\n')
    case = json.loads((source / 'tasks.json').read_bytes())['cases'][0]
    implementation = runner.implementation_spec(case)
    with runner.patch('minicode.permissions._read_permission_store', return_value={}):
        context = ToolContext(cwd=str(workspace), permissions=PermissionManager(str(workspace), prompt=None))
        host = HostImplementationContinuation.capture(spec=implementation, context=context, messages=messages,
            executor_id=fixtures.OWNER, attempt=2, review_state=ImplementationCompletionReview(implementation).state)
    runner.checkpoint_writer(root)(host.document, messages)
    shutil.rmtree(root / 'journal')
    journal = RunJournal(workspace, data_dir=root / 'journal')
    with observe_run(workspace=workspace, source='headless', title='generic terminal', journal_factory=lambda _: journal) as obs:
        for attempt in (1, 2):
            emit_event_safely(obs, 'implementation.completion_review_started', payload={
                'executorId': fixtures.OWNER, 'attempt': attempt, 'patchDigest': host.document['candidateDigest']})
            if attempt == 1 or stop_decision:
                emit_event_safely(obs, 'implementation.completion_decided', payload={
                    'completionVersion': 1, 'executorId': fixtures.OWNER, 'attempt': attempt,
                    'patchDigest': host.document['candidateDigest'], 'status': 'changes_required' if attempt == 1 else 'inconclusive',
                    'canContinue': attempt == 1, 'continuesSameExecutor': attempt == 1,
                    'requirements': [], 'findings': []})
    physical_ledger(root, unknown=error_class is not None, error_class=error_class)
    write(root / 'execution.json', {'errorClass': error_class, 'durationSeconds': 10})
    if error_class:
        write(root / 'error.json', {'class': error_class, 'frames': []})
    write(source / 'checkpoint.json', {'identity': {'executorId': fixtures.OWNER, 'attempt': 1}})
    manifest = json.loads((source / 'manifest.json').read_bytes())
    manifest.update(model='glm-5.3-flash', temperature=0, reasoningEffortPolicy='high', routeSha256='a' * 64,
        resolvedTransportRouteSha256='a' * 64, requestPolicy={'maxOutputTokens': 131072}, roleTurnLimits={},
        commonRequirementSha256='b' * 64, explicitRecipeSha256='c' * 64)
    write(source / 'manifest.json', manifest)
    seal_terminal(source, root)
    return source, root, workspace, host


def cloned_script_tree(tmp_path, monkeypatch):
    # Freeze real production/harness source bytes in a generic disposable tree.
    # Nothing is installed in the shared scripts/ directory.
    clone = tmp_path / 'code'
    for src in (ROOT / 'minicode').rglob('*.py'):
        dest = clone / src.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    for name in runner.HARNESS_FILES:
        dest = clone / 'scripts' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DRAFT if name == NAME else ROOT / 'scripts' / name, dest)
    monkeypatch.setattr(runner, 'ROOT', clone)
    monkeypatch.setattr(runner, 'ENTRY', clone / 'scripts' / NAME)
    return clone


@pytest.mark.parametrize('file_name', ['example.py', 'read_only_dependency.py'])
@pytest.mark.parametrize('when', ['before_prepare', 'before_freeze'])
def test_full_candidate_drift_rejected_before_freeze(tmp_path, file_name, when):
    source, _, workspace, _ = terminal(tmp_path)
    output = tmp_path / 'new'
    if when == 'before_freeze':
        runner.prepare(output, source)
    (workspace / file_name).write_text('CHANGED = 3\n')
    with pytest.raises(ValueError, match='candidate changed'):
        if when == 'before_prepare':
            runner.prepare(output, source)
        else:
            runner.freeze(output)
    assert not (output / 'manifest.json').exists()
    assert not (output / 'private' / runner.TRIAL_ID / 'transport.sqlite').exists()


def test_native_prepare_freeze_validate_and_real_runtime(tmp_path, monkeypatch):
    from minicode.agent_budget import AgentTurnBudget
    from minicode.agent_runtime import AgentTurnRuntime
    from minicode.context_manager import ContextManager
    from minicode.run_lifecycle import observe_run
    from minicode.tooling import ToolRegistry
    from minicode.tools.write_file import write_file_tool
    from minicode.types import ModelAdapter
    source, root, workspace, host = terminal(tmp_path)
    output = tmp_path / 'followup'
    old_entry = ROOT / 'scripts/run_requirement_completion_continuation.py'
    original_hash = runner.sha_file(old_entry)
    originals = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    cloned_script_tree(tmp_path, monkeypatch)
    runner.prepare(output, source)
    runner.freeze(output)
    manifest = runner.validate(output)
    checkpoint, inputs, saved = runner.load_checkpoint(output)
    implementation = runner.implementation_spec(inputs['case'])
    restored = runner.restored_continuation(output, implementation, saved)
    assert restored.document == host.document
    assert checkpoint['migrationRestoration'] == 'fresh_migration_from_retained_review_state_no_child_context_saved'
    assert manifest['runner'] == 'scripts/' + NAME
    config = runner.segment_transport_config(output / 'new', {'model': 'glm-5.3-flash'}, manifest, 1)
    assert config.idle_timeout_seconds == 600 and config.connect_timeout_seconds == 30
    assert config.max_model_calls is None and config.max_total_tokens is None and config.deadline_epoch is None
    assert config.request_timeout_seconds is None and config.max_output_tokens == 131072
    error = KeyboardInterrupt('generic local checkpoint test')
    class LocalModel(ModelAdapter):
        calls = 0
        def next(self, actual, **kwargs):
            self.calls += 1
            assert any(m.get('_compact_boundary') for m in actual)
            assert any(m.get('content') == 'actual edit retained' for m in actual)
            assert not any('BASELINE' in m.get('content', '') for m in actual)
            assert (workspace / 'example.py').read_text() == 'CURRENT = 2\n'
            raise error
    model = LocalModel()
    runtime = AgentTurnRuntime(workspace=workspace, runtime={}, tools=ToolRegistry([write_file_tool]), permissions=None,
        memory_manager=None, model=model, skill_routing=None, system_prompt='unused fresh prompt',
        context_manager=ContextManager(model='default'), allow_user_interaction=False, agent_budget=AgentTurnBudget(),
        implementation_spec=implementation, implementation_continuation=restored,
        implementation_checkpoint_sink=runner.checkpoint_writer(output / 'new-evidence'))
    monkeypatch.setattr('minicode.cybernetic_supervisor.SUPERVISOR_STATE_PATH', tmp_path / 'supervisor.json')
    with pytest.raises(KeyboardInterrupt) as caught:
        with runner.diagnostic.ParentExecutionEvidenceArchive(output / 'new-evidence'), observe_run(
                workspace=workspace, source='headless', title='restored parent', enabled=False) as observation:
            try:
                runtime.execute(saved, observation, defer_curator_maintenance=True)
            finally:
                runtime.dispose()
    assert caught.value is error and model.calls == 1
    after = json.loads((output / 'new-evidence/implementation-checkpoint.json').read_bytes())
    assert (after['executorId'], after['attempt'], after['candidateDigest']) == (
        host.document['executorId'], host.document['attempt'], host.document['candidateDigest'])
    after_messages = json.loads((output / 'new-evidence/implementation-checkpoint-messages.json').read_bytes())
    assert any(m.get('content') == 'actual edit retained' for m in after_messages)
    assert all(p.read_bytes() == value for p, value in originals.items())
    assert runner.sha_file(old_entry) == original_hash
    assert (output / 'private/parent-context.json').read_bytes() == (root / 'implementation-checkpoint-messages.json').read_bytes()
    assert not (output / 'new-evidence/response.txt').exists()


@pytest.mark.parametrize('error_class', ['EvaluationIdleTimeout', 'TimeoutError', 'EvaluationStreamError'])
def test_transport_exception_without_decision_is_valid_terminal(tmp_path, error_class):
    source, _, _, host = terminal(tmp_path, error_class=error_class, stop_decision=False)
    output = tmp_path / 'followup'
    runner.prepare(output, source)
    checkpoint, _, _ = runner.load_checkpoint(output)
    assert checkpoint['predecessorClassification'] == 'transport-blocked'
    terminal_ = checkpoint['terminalEvidence']
    assert terminal_['originalErrorClass'] == error_class
    assert terminal_['lastCompletionEvent']['type'] == 'implementation.completion_review_started'
    assert terminal_['lastCompletionEvent']['attempt'] == host.document['attempt']
    assert terminal_['lastPhysicalCall']['errorClass'] == error_class


def host_verification_blocked_terminal(tmp_path, monkeypatch):
    """Real scope-error stack plus native checkpoint; no provider attempt exists."""
    from contextlib import ExitStack
    import traceback
    from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
    from minicode.permissions import PermissionManager
    from minicode.review_probe_execution import run_isolated_review_probe
    from minicode.reviewer_verification import ReviewerProbe
    from minicode.workflow_workspace import WorkflowWorkspaceTransaction
    from scripts.evaluation_transport import SharedLedger, TransportConfig
    from scripts.run_three_arm_real import install_verification_guards
    from scripts.three_arm_scope import ScopeDenied, VerificationScope

    source, root, workspace, host = terminal(tmp_path, error_class='ScopeDenied', stop_decision=False)
    (root / 'transport.sqlite').unlink()
    SharedLedger(TransportConfig(ledger_path=str((root / 'transport.sqlite').resolve()),
        expected_model='glm-5.3-flash', expected_route_sha256='a' * 64,
        deadline_epoch=None, max_model_calls=None, max_total_tokens=None,
        max_output_tokens=131072, connect_timeout_seconds=30, idle_timeout_seconds=600,
        request_timeout_seconds=None))
    check = VerificationCheck('generic_public', 'tests', ('python3', '-m', 'pytest', '-q', 'example.py'), 20)
    probe = ReviewerProbe.create(criterion_ids=['req_generic'], scenario='Generic independently checked behavior',
        code='def test_contract():\n    assert True\n')
    with monkeypatch.context() as legacy_guard:
        # Reproduce the former harness boundary: it inspects the actual derived
        # argv before any mapping to its already authorized base command.
        legacy_guard.setattr('minicode.review_probe_execution.review_probe_authorized_checks',
            lambda checks, *_args: checks)
        with ExitStack() as stack, WorkflowWorkspaceTransaction(str(workspace)) as transaction:
            install_verification_guards(stack, VerificationScope(check.argv), workspace.resolve())
            frozen = transaction.freeze(allowed_paths=('example.py',), paths_relative_to_cwd=True)
            with pytest.raises(ScopeDenied) as caught:
                run_isolated_review_probe(probe=probe, frozen=frozen, check=check, test_path='example.py',
                    verifier=DeterministicVerifier(), read_roots=('.',), forbidden_paths=('.git', '.env'),
                    run_kwargs={'workflow_id': 'wf_scope_test', 'task_id': 'task_scope_test',
                        'execution_assignment_id': 'asg_scope_test', 'patch_digest': frozen.patch_digest,
                        'permissions': PermissionManager(str(workspace), prompt=None),
                        'permission_cwd': str(workspace)})
    frames = [{'file': frame.filename, 'line': frame.lineno, 'name': frame.name}
              for frame in traceback.extract_tb(caught.value.__traceback__)]
    assert [frame['name'] for frame in frames[-4:]] == [
        'run_isolated_review_probe', 'verify', 'require_verification', 'require_command']
    write(root / 'error.json', {'class': 'ScopeDenied', 'frames': frames})
    write(source / 'checkpoint.json', {'identity': {key: host.document[key]
        for key in ('executorId', 'attempt', 'candidateDigest')}})
    seal_terminal(source, root)
    return source, root, workspace, host


def test_zero_call_host_verification_block_resumes_without_becoming_transport_failure(tmp_path, monkeypatch):
    source, root, workspace, host = host_verification_blocked_terminal(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in source.rglob('*') if path.is_file()}
    output = tmp_path / 'resume_host_boundary'
    runner.prepare(output, source)
    checkpoint, _, messages = runner.load_checkpoint(output)
    assert checkpoint['predecessorClassification'] == 'host-verification-blocked'
    terminal_ = checkpoint['terminalEvidence']
    assert terminal_['originalErrorClass'] == 'ScopeDenied'
    assert terminal_['lastPhysicalCall'] is None
    assert terminal_['lastCompletionEvent']['type'] == 'implementation.completion_review_started'
    assert terminal_['cleanup'] == {'execution': True, 'judgement': True}
    assert terminal_['hiddenJudgeUsed'] is False
    for key in ('executorId', 'attempt', 'candidateDigest'):
        assert checkpoint['identity'][key] == host.document[key]
    assert messages == json.loads((root / 'implementation-checkpoint-messages.json').read_bytes())
    assert (workspace / 'example.py').read_text() == 'CURRENT = 2\n'
    assert all(path.read_bytes() == raw for path, raw in before.items())


@pytest.mark.parametrize('damage', ['ordinary_error', 'physical_call', 'stack_file', 'stack_order',
    'stack_function', 'missing_review', 'entry_candidate', 'entry_owner', 'candidate_drift'])
def test_unproven_host_verification_block_cannot_resume(tmp_path, monkeypatch, damage):
    source, root, workspace, _host = host_verification_blocked_terminal(tmp_path, monkeypatch)
    if damage == 'ordinary_error':
        execution = json.loads((root / 'execution.json').read_bytes())
        execution['errorClass'] = 'ValueError'
        write(root / 'execution.json', execution)
        error = json.loads((root / 'error.json').read_bytes())
        error['class'] = 'ValueError'
        write(root / 'error.json', error)
        seal_terminal(source, root)
    elif damage == 'physical_call':
        physical_ledger(root, error_class='ScopeDenied')
    elif damage.startswith('stack_'):
        error = json.loads((root / 'error.json').read_bytes())
        if damage == 'stack_file':
            error['frames'][-1]['file'] = '/untrusted/source/three_arm_scope.py'
        elif damage == 'stack_order':
            error['frames'][-1], error['frames'][-2] = error['frames'][-2], error['frames'][-1]
        else:
            error['frames'][-1]['name'] = 'unrelated_command'
        write(root / 'error.json', error)
    elif damage == 'missing_review':
        shutil.rmtree(root / 'journal')
    elif damage in {'entry_candidate', 'entry_owner'}:
        entry = json.loads((source / 'checkpoint.json').read_bytes())
        entry['identity']['candidateDigest' if damage == 'entry_candidate' else 'executorId'] = (
            'sha256:' + 'f' * 64 if damage == 'entry_candidate' else 'implementation_' + 'f' * 32)
        write(source / 'checkpoint.json', entry)
    else:
        (workspace / 'example.py').write_text('CHANGED = 9\n')
    output = tmp_path / 'must_not_resume'
    with pytest.raises(ValueError):
        runner.prepare(output, source)
    assert not output.exists()


@pytest.mark.parametrize('damage', ['missing_journal', 'stale_digest', 'wrong_attempt', 'approved',
    'not_stopping', 'missing_error', 'mismatched_error', 'unknown_error', 'missing_result', 'incomplete_result'])
def test_invalid_terminal_fails_before_preparation(tmp_path, damage):
    is_error = damage in {'missing_error', 'mismatched_error', 'unknown_error'}
    source, root, _, _ = terminal(tmp_path, error_class='EvaluationIdleTimeout' if is_error else None,
        stop_decision=not is_error)
    if damage == 'missing_journal':
        shutil.rmtree(root / 'journal')
    elif damage in {'stale_digest', 'wrong_attempt', 'approved', 'not_stopping'}:
        path = next((root / 'journal').glob('dashboard/workspaces/*/runs/run_*/events.ndjson'))
        events = [json.loads(line) for line in path.read_text().splitlines()]
        relevant = [event for event in events if event['type'].startswith('implementation.completion_')]
        if damage == 'stale_digest':
            relevant[-2]['payload']['patchDigest'] = 'sha256:' + 'f' * 64
        elif damage == 'wrong_attempt':
            relevant[-2]['payload']['attempt'] = 1
        elif damage == 'approved':
            relevant[-1]['payload']['status'] = 'approved'
        else:
            relevant[-1]['payload']['canContinue'] = True
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
    elif damage == 'missing_error':
        (root / 'error.json').unlink()
    elif damage == 'mismatched_error':
        write(root / 'error.json', {'class': 'ValueError'})
    elif damage == 'unknown_error':
        write(root / 'execution.json', {'errorClass': 'ValueError', 'durationSeconds': 10})
        write(root / 'error.json', {'class': 'ValueError'})
        seal_terminal(source, root)
    elif damage == 'missing_result':
        (source / 'results.json').unlink()
    else:
        write(source / 'results.json', {'trials': []})
    with pytest.raises((ValueError, FileNotFoundError)):
        runner.prepare(tmp_path / 'followup', source)
    assert not (tmp_path / 'followup').exists()


def test_no_decision_and_no_original_error_is_not_transport_terminal(tmp_path):
    source, _, _, _ = terminal(tmp_path, stop_decision=False)
    with pytest.raises(ValueError, match='exact saved host'):
        runner.prepare(tmp_path / 'followup', source)


def test_three_physical_ledgers_keep_unknown_usage_and_authenticated_lineage(tmp_path):
    source, root, _, _ = terminal(tmp_path)
    ancestor = tmp_path / 'original-v3'
    ancestor_root = ancestor / 'private' / runner.TRIAL_ID
    ancestor_root.mkdir(parents=True)
    physical_ledger(ancestor_root, unknown=True)
    write(ancestor_root / 'execution.json', {'durationSeconds': 30, 'errorClass': 'KeyboardInterrupt'})
    write(ancestor / 'manifest.json', {'crossProcessContinuation': False})
    previous_checkpoint = json.loads((source / 'checkpoint.json').read_bytes())
    previous_checkpoint.update(predecessorDirectory=str(ancestor), predecessorArtifactSha256={
        str((ancestor_root / 'transport.sqlite').relative_to(ancestor)): runner.sha_file(ancestor_root / 'transport.sqlite')})
    write(source / 'checkpoint.json', previous_checkpoint)
    manifest = json.loads((source / 'manifest.json').read_bytes())
    manifest.update(crossProcessContinuation=True, predecessorDirectory=str(ancestor),
        continuationCheckpointSha256=runner.sha_file(source / 'checkpoint.json'))
    write(source / 'manifest.json', manifest)
    seal_terminal(source, root)
    output = tmp_path / 'followup'
    runner.prepare(output, source)
    checkpoint, _, _ = runner.load_checkpoint(output)
    assert checkpoint['predecessorLedgerRoots'] == [str(ancestor_root), str(root)]
    current = output / 'private' / runner.TRIAL_ID
    current.mkdir(parents=True)
    physical_ledger(current, unknown=True)
    write(current / 'execution.json', {'durationSeconds': 20, 'errorClass': None})
    roots = [ancestor_root, root, current]
    before = {path: runner.sha_file(path / 'transport.sqlite') for path in roots}
    metrics = runner.combined_metering([Path(p) for p in checkpoint['predecessorLedgerRoots']], current)
    assert metrics['modelCalls'] == 5 and metrics['failedCalls'] == 2
    assert metrics['unknownUsageCalls'] == 2 and metrics['knownTokenLowerBound'] == 360
    assert metrics['totalTokens'] is None and metrics['estimatedCostCny'] is None
    assert metrics['durationSeconds'] == 60
    assert [row['root'] for row in metrics['segments']] == [str(path) for path in roots]
    assert all(runner.sha_file(path / 'transport.sqlite') == digest for path, digest in before.items())
    ledger = runner.sqlite3.connect(str(ancestor_root / 'transport.sqlite'))
    ledger.execute('UPDATE requests SET charge_tokens=charge_tokens+1 WHERE call=1')
    ledger.commit()
    try:
        # The committed update is in WAL while the writer remains open.
        assert runner.sha_file(ancestor_root / 'transport.sqlite') == before[ancestor_root]
        with pytest.raises(ValueError, match='ancestor logical ledger changed'):
            runner.load_checkpoint(output)
    finally:
        ledger.close()


def test_missing_production_restore_blocks_freeze(tmp_path, monkeypatch):
    from minicode.implementation_completion_review import ImplementationCompletionReview
    source, _, _, _ = terminal(tmp_path)
    output = tmp_path / 'followup'
    runner.prepare(output, source)
    monkeypatch.setattr(ImplementationCompletionReview, 'from_state', None)
    with pytest.raises((RuntimeError, TypeError)):
        runner.freeze(output)
    assert not (output / 'manifest.json').exists()


def test_old_entrypoint_remains_separate():
    from scripts import run_requirement_completion_continuation as legacy
    assert runner.ENTRY.name == 'run_requirement_completion_checkpoint.py'
    assert legacy.ENTRY.name == 'run_requirement_completion_continuation.py'
    assert legacy.PROTOCOL == 'requirement-completion-cross-process-continuation-v1'
    assert legacy.DEFAULT_OUTPUT.name == 'memory-explicit-v3-continuation-v1'
    assert runner.DEFAULT_PREDECESSOR == legacy.DEFAULT_OUTPUT


def test_only_idle_changes_and_cumulative_budgets_remain_none():
    prior = runner.continuous.LIMITS
    current = runner.transport_change_policy(prior)
    assert {key for key in prior if current[key] != prior[key]} == {'idleTimeoutSeconds'}
    assert current['idleTimeoutSeconds'] == 600
    assert all(current[key] is None for key in ('maxTokens', 'maxModelCalls', 'maxCostUsd', 'wallSeconds', 'requestTimeoutSeconds'))
    config = runner.segment_transport_config(Path('/tmp/generic-unused'), {'model': 'glm-5.3-flash'},
        {'resolvedTransportRouteSha256': 'a' * 64}, 1)
    data = asdict(config)
    assert data['idle_timeout_seconds'] == 600
    assert data['request_timeout_seconds'] is None and data['deadline_epoch'] is None
    assert runner.transport_change_policy(current) == current
    assert runner.transport_change_record(current) == {'field': 'idleTimeoutSeconds', 'before': 600,
        'after': 600, 'classification': 'transport policy retained'}
    for changed in ({**current, 'maxTokens': 1}, {**current, 'idleTimeoutSeconds': 300}):
        with pytest.raises(ValueError):
            runner.transport_change_policy(changed)


def proxy_partial_terminal(tmp_path, monkeypatch, *, reason='Tunnel connection failed: 503 Service Unavailable',
                           phase='headers', interrupted=False):
    """Actual parent failure/finally messages and journal; no invented events."""
    import urllib.error
    from minicode.agent_budget import AgentTurnBudget
    from minicode.agent_runtime import AgentTurnRuntime
    from minicode.context_manager import ContextManager
    from minicode.run_journal import RunJournal
    from minicode.run_lifecycle import observe_run
    from minicode.tooling import ToolRegistry
    from minicode.tools.write_file import write_file_tool
    from minicode.types import ModelAdapter
    from scripts.evaluation_transport import SharedLedger, TransportConfig
    source, root, workspace, host = terminal(tmp_path)
    original_messages = json.loads((root / 'implementation-checkpoint-messages.json').read_bytes())
    for name in ('implementation-checkpoint.json', 'implementation-checkpoint-messages.json',
                 'implementation-checkpoint-receipt.json', 'transport.sqlite'):
        (root / name).unlink()
    shutil.rmtree(root / 'journal')
    config = TransportConfig(ledger_path=str((root / 'transport.sqlite').resolve()), expected_model='glm-5.3-flash',
        expected_route_sha256='a' * 64, deadline_epoch=None, max_model_calls=None, max_total_tokens=None,
        max_output_tokens=131072, connect_timeout_seconds=30, idle_timeout_seconds=600, request_timeout_seconds=None)
    ledger = SharedLedger(config)
    target_error = (KeyboardInterrupt('generic parent interruption') if interrupted
                    else urllib.error.URLError(OSError(reason)))
    class LocalFailure(ModelAdapter):
        def next(self, messages, **kwargs):
            call = ledger.start({'model': 'glm-5.3-flash', 'actorId': 'main', 'role': 'main',
                'parentToolOperationId': None}, 1000)
            ledger.update(call, {'event': 'failed', 'errorClass': type(target_error).__name__, 'errorPhase': phase,
                'seconds': 0.09, 'httpStatus': None})
            raise target_error
    # Reproduce the frozen predecessor's old classification, not the newly fixed retry path.
    monkeypatch.setattr('minicode.agent_loop._is_transient_model_transport_error', lambda _: False)
    monkeypatch.setattr('minicode.cybernetic_supervisor.SUPERVISOR_STATE_PATH', tmp_path / 'supervisor.json')
    case = json.loads((source / 'tasks.json').read_bytes())['cases'][0]
    runtime = AgentTurnRuntime(workspace=workspace, runtime={}, tools=ToolRegistry([write_file_tool]), permissions=None,
        memory_manager=None, model=LocalFailure(), skill_routing=None, system_prompt='unused',
        context_manager=ContextManager(model='default'), allow_user_interaction=False, agent_budget=AgentTurnBudget(),
        implementation_spec=runner.implementation_spec(case), implementation_continuation=host,
        implementation_checkpoint_sink=runner.checkpoint_writer(root))
    journal = RunJournal(workspace, data_dir=root / 'journal')
    original_exception = None
    with observe_run(workspace=workspace, source='headless', title='actual legacy proxy partial', journal_factory=lambda _: journal) as obs:
        try:
            runtime.execute(original_messages, obs, defer_curator_maintenance=True)
        except KeyboardInterrupt as error:
            if not interrupted:
                raise
            original_exception = error
        finally:
            runtime.dispose()
    if interrupted:
        assert original_exception is target_error
        write(root / 'execution.json', {'errorClass': type(original_exception).__name__, 'durationSeconds': 10})
        write(root / 'error.json', {'class': type(original_exception).__name__, 'frames': []})
    manifest = json.loads((source / 'manifest.json').read_bytes())
    manifest['limits'] = dict(runner.LIMITS)
    write(source / 'manifest.json', manifest)
    write(source / 'checkpoint.json', {'identity': {key: host.document[key]
        for key in ('executorId', 'attempt', 'candidateDigest')}})
    seal_terminal(source, root)
    return source, root, workspace, host


def test_real_parent_proxy_partial_without_new_review_resumes_at_idle600(tmp_path, monkeypatch):
    source, root, _, host = proxy_partial_terminal(tmp_path, monkeypatch)
    output = tmp_path / 'followup'
    cloned_script_tree(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    runner.prepare(output, source)
    runner.freeze(output)
    manifest = runner.validate(output)
    checkpoint, _, messages = runner.load_checkpoint(output)
    assert checkpoint['identity']['completionEvents'] == []
    assert checkpoint['identity']['attempt'] == host.document['attempt']
    assert checkpoint['predecessorClassification'] == 'transport-blocked'
    assert checkpoint['terminalEvidence']['completionIdentitySource'] == 'unchanged_frozen_input_checkpoint'
    evidence = checkpoint['terminalEvidence']['partialTransportEvidence']
    assert evidence['originalErrorConvertedToPartial'] is True
    assert 'no model operation ID' in evidence['physicalAssociation']
    assert manifest['transportChange']['before'] == manifest['transportChange']['after'] == 600
    assert messages[-1]['_partial'] and messages[-1]['_host_implementation_receipt']
    assert (output / 'private/parent-context.json').read_bytes() == (root / 'implementation-checkpoint-messages.json').read_bytes()
    assert all(p.read_bytes() == raw for p, raw in before.items())


@pytest.mark.parametrize('damage', ['proxy502', 'stream_phase', 'model_operation', 'failure_kind', 'missing_journal', 'entry_attempt', 'entry_candidate'])
def test_arbitrary_provider_partial_cannot_use_proxy503_recovery(tmp_path, monkeypatch, damage):
    source, root, _, _ = proxy_partial_terminal(tmp_path, monkeypatch,
        reason='Tunnel connection failed: 502 Bad Gateway' if damage == 'proxy502' else 'Tunnel connection failed: 503 Service Unavailable',
        phase='stream' if damage == 'stream_phase' else 'headers')
    if damage in {'model_operation', 'failure_kind'}:
        path = next((root / 'journal').glob('dashboard/workspaces/*/runs/run_*/events.ndjson'))
        events = [json.loads(line) for line in path.read_text().splitlines()]
        last = next(event for event in reversed(events) if event['type'] == 'model.failed')
        last['payload']['operationId' if damage == 'model_operation' else 'failureKind'] = (
            'modelop_' + 'f' * 32 if damage == 'model_operation' else 'authentication_error')
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
    elif damage == 'missing_journal':
        shutil.rmtree(root / 'journal')
    elif damage in {'entry_attempt', 'entry_candidate'}:
        entry = json.loads((source / 'checkpoint.json').read_bytes())
        entry['identity']['attempt' if damage == 'entry_attempt' else 'candidateDigest'] = (
            1 if damage == 'entry_attempt' else 'sha256:' + 'f' * 64)
        write(source / 'checkpoint.json', entry)
    with pytest.raises(ValueError):
        runner.prepare(tmp_path / 'followup', source)
    assert not (tmp_path / 'followup').exists()


def test_four_segment_lineage_retains_unknown_physical_usage(tmp_path, monkeypatch):
    source, root, _, _ = proxy_partial_terminal(tmp_path, monkeypatch)
    ancestors = [tmp_path / 'v3', tmp_path / 'v1']
    for index, directory in enumerate(ancestors):
        trial = directory / 'private' / runner.TRIAL_ID
        trial.mkdir(parents=True)
        physical_ledger(trial, unknown=True)
        write(trial / 'execution.json', {'durationSeconds': 10, 'errorClass': None})
        manifest = {'crossProcessContinuation': bool(index)}
        if index:
            parent = ancestors[index - 1]
            ref = 'private/' + runner.TRIAL_ID + '/transport.sqlite'
            write(directory / 'checkpoint.json', {'predecessorDirectory': str(parent),
                'predecessorArtifactSha256': {ref: runner.sha_file(parent / ref)}})
            manifest.update(predecessorDirectory=str(parent),
                continuationCheckpointSha256=runner.sha_file(directory / 'checkpoint.json'))
        write(directory / 'manifest.json', manifest)
    entry = json.loads((source / 'checkpoint.json').read_bytes())
    ref = 'private/' + runner.TRIAL_ID + '/transport.sqlite'
    entry.update(predecessorDirectory=str(ancestors[-1]),
        predecessorArtifactSha256={ref: runner.sha_file(ancestors[-1] / ref)})
    write(source / 'checkpoint.json', entry)
    manifest = json.loads((source / 'manifest.json').read_bytes())
    manifest.update(crossProcessContinuation=True, predecessorDirectory=str(ancestors[-1]),
        continuationCheckpointSha256=runner.sha_file(source / 'checkpoint.json'))
    write(source / 'manifest.json', manifest)
    seal_terminal(source, root)
    output = tmp_path / 'followup'
    runner.prepare(output, source)
    checkpoint, _, _ = runner.load_checkpoint(output)
    current = output / 'private' / runner.TRIAL_ID
    current.mkdir(parents=True)
    physical_ledger(current)
    write(current / 'execution.json', {'durationSeconds': 10, 'errorClass': None})
    previous = [Path(path) for path in checkpoint['predecessorLedgerRoots']]
    assert previous == [*(path / 'private' / runner.TRIAL_ID for path in ancestors), root]
    metrics = runner.combined_metering(previous, current)
    assert len(metrics['segments']) == 4 and metrics['modelCalls'] == 6
    assert metrics['failedCalls'] == 3 and metrics['unknownUsageCalls'] == 3
    assert metrics['knownTokenLowerBound'] == 360
    assert metrics['totalTokens'] is None and metrics['estimatedCostCny'] is None


def test_operator_abort_without_new_review_keeps_actual_checkpoint_and_identity(tmp_path, monkeypatch):
    source, root, workspace, host = proxy_partial_terminal(tmp_path, monkeypatch, interrupted=True)
    before = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    output = tmp_path / 'next_segment'
    runner.prepare(output, source)
    checkpoint, _, messages = runner.load_checkpoint(output)
    assert checkpoint['identity']['completionEvents'] == []
    assert checkpoint['predecessorClassification'] == 'operator-aborted'
    assert checkpoint['terminalEvidence']['originalErrorClass'] == 'KeyboardInterrupt'
    assert checkpoint['terminalEvidence']['completionIdentitySource'] == 'unchanged_frozen_input_checkpoint'
    assert checkpoint['terminalEvidence']['cleanup'] == {'execution': True, 'judgement': True}
    for key in ('executorId', 'attempt', 'candidateDigest'):
        assert checkpoint['identity'][key] == host.document[key]
    assert any(m.get('content') == 'actual edit retained' for m in messages)
    assert any(m.get('_compact_boundary') for m in messages)
    assert (output / 'private/parent-context.json').read_bytes() == (root / 'implementation-checkpoint-messages.json').read_bytes()
    assert (workspace / 'example.py').read_text() == 'CURRENT = 2\n'
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert checkpoint['transportChange']['before'] == checkpoint['transportChange']['after'] == 600


@pytest.mark.parametrize('field', ['processCleanup', 'judgement'])
@pytest.mark.parametrize('state', [False, None, 'missing'])
def test_operator_abort_cannot_resume_with_incomplete_terminal_cleanup(tmp_path, monkeypatch, field, state):
    source, _, _, _ = proxy_partial_terminal(tmp_path, monkeypatch, interrupted=True)
    results = json.loads((source / 'results.json').read_bytes())
    if state == 'missing':
        results['trials'][0].pop(field)
    else:
        results['trials'][0][field] = {'cleanupComplete': state}
    write(source / 'results.json', results)
    with pytest.raises(ValueError, match='cleanup'):
        runner.prepare(tmp_path / 'next_segment', source)
    assert not (tmp_path / 'next_segment').exists()


def migration_spec():
    return runner.implementation_spec({'authorized_paths': ['example.py'],
        'prompt': 'Implement a generic behavior.', 'verification_argv': ['python3', '-m', 'pytest', '-q']})


def test_migration_archive_keeps_multiple_real_persistence_directories_private_and_separate(tmp_path):
    from minicode.completion_probe_migration import CompletionProbeMigration
    from minicode.implementation_completion_review import ImplementationCompletionReview
    implementation = migration_spec()
    owners = [CompletionProbeMigration(implementation), CompletionProbeMigration(implementation)]
    review = ImplementationCompletionReview(implementation)
    root = tmp_path / 'trial'
    with runner.diagnostic.CompletionEvidenceArchive(root), runner.MigrationEvidenceArchive(root):
        first = owners[0]._persist('completed-unit', {'opaque': 'PRIVATE_MIGRATION_SOURCE_A'})
        second = owners[1]._persist('continuation', {'opaque': 'PRIVATE_MIGRATION_MESSAGES_B'})
        review_ref = review._persist('review', {'opaque': 'PRIVATE_REVIEW_SOURCE'})
    archive = json.loads((root / 'migration-evidence-archive.json').read_bytes())
    ordinary = json.loads((root / 'completion-review-archive.json').read_bytes())
    assert archive['archiveComplete'] is True and archive['migrationDirectories'] == 2
    assert len(archive['files']) == 2 and archive['errors'] == []
    assert ordinary['reviewDirectories'] == 1 and len(ordinary['files']) == 1
    assert len({row['migrationId'] for row in archive['files']}) == 2
    originals = {Path(path).read_bytes() for path in (first, second)}
    archived = {runner.safe_path(root, row['file']).read_bytes() for row in archive['files']}
    assert archived == originals
    for row in archive['files']:
        target = runner.safe_path(root, row['file'])
        assert runner.sha_file(target) == row['sha256'] and target.stat().st_size == row['bytes']
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / 'migration-evidence').stat().st_mode) == 0o700
    assert stat.S_IMODE((root / 'migration-evidence-archive.json').stat().st_mode) == 0o600
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert not any('migration-evidence' in row['file'] for row in ordinary['files'])
    assert runner.safe_path(root, ordinary['files'][0]['file']).read_bytes() == Path(review_ref).read_bytes()
    assert 'PRIVATE_' not in json.dumps(archive)


def test_migration_archive_captures_real_finally_persist_and_preserves_original_error(tmp_path):
    from minicode.completion_probe_migration import CompletionProbeMigration
    root = tmp_path / 'trial'
    owner = CompletionProbeMigration(migration_spec())
    original = CompletionProbeMigration._persist
    sentinel = KeyboardInterrupt('generic interrupted migration')
    with pytest.raises(KeyboardInterrupt) as caught:
        with runner.MigrationEvidenceArchive(root):
            try:
                owner._persist('completed-unit', {'opaque': 'durable validated unit'})
                raise sentinel
            finally:
                final_ref = owner._persist('continuation', {'opaque': 'actual child context'})
    assert caught.value is sentinel
    assert CompletionProbeMigration._persist is original
    archive = json.loads((root / 'migration-evidence-archive.json').read_bytes())
    assert archive['archiveComplete'] is True and len(archive['files']) == 2
    row = next(row for row in archive['files'] if row['file'].endswith('-continuation.json'))
    assert runner.safe_path(root, row['file']).read_bytes() == Path(final_ref).read_bytes()


def test_migration_retention_failure_does_not_replace_result_or_original_error(tmp_path, monkeypatch):
    from minicode.completion_probe_migration import CompletionProbeMigration
    owner = CompletionProbeMigration(migration_spec())
    root = tmp_path / 'trial'
    archive = runner.MigrationEvidenceArchive(root)
    def fail_copy(*args):
        raise OSError('generic unavailable archive')
    monkeypatch.setattr(archive, '_copy', fail_copy)
    sentinel = RuntimeError('original execution failure')
    with pytest.raises(RuntimeError) as caught:
        with archive:
            path = owner._persist('continuation', {'opaque': 'retained only in production temp'})
            assert Path(path).is_file()
            raise sentinel
    assert caught.value is sentinel
    saved = json.loads((root / 'migration-evidence-archive.json').read_bytes())
    assert saved['archiveComplete'] is False
    assert saved['errors'] == [{'operation': 'copy', 'errorClass': 'OSError'}]


def test_migration_archive_does_not_overwrite_changed_evidence_or_prior_archive(tmp_path):
    from minicode.completion_probe_migration import CompletionProbeMigration
    owner = CompletionProbeMigration(migration_spec())
    root = tmp_path / 'trial'
    with runner.MigrationEvidenceArchive(root) as archive:
        path = owner._persist('completed-unit', {'opaque': 'original source'})
        first = next(iter(archive.records.values()))
        destination = runner.safe_path(root, first['file'])
        original = destination.read_bytes()
        Path(path).write_bytes(b'changed after production persistence')
        archive._record(owner, path)
        assert destination.read_bytes() == original
    manifest = (root / 'migration-evidence-archive.json').read_bytes()
    saved = json.loads(manifest)
    assert saved['archiveComplete'] is False and saved['errors'][0]['errorClass'] == 'ValueError'
    with pytest.raises(ValueError, match='already exists'):
        with runner.MigrationEvidenceArchive(root):
            pytest.fail('earlier archive must never be reused')
    assert (root / 'migration-evidence-archive.json').read_bytes() == manifest
    assert destination.read_bytes() == original


def test_migration_archive_rejects_symlink_source_without_interpreting_it(tmp_path):
    from minicode.completion_probe_migration import CompletionProbeMigration
    root = tmp_path / 'trial'
    owner = CompletionProbeMigration(migration_spec())
    with runner.MigrationEvidenceArchive(root) as archive:
        source = Path(owner._persist('completed-unit', {'opaque': 'original'}))
        original = source.read_bytes()
        source.unlink()
        outside = tmp_path / 'outside-private'
        outside.write_bytes(b'do not copy symlink contents')
        source.symlink_to(outside)
        archive._record(owner, str(source))
    saved = json.loads((root / 'migration-evidence-archive.json').read_bytes())
    assert saved['archiveComplete'] is False and len(saved['files']) == 1
    assert runner.safe_path(root, saved['files'][0]['file']).read_bytes() == original
    assert outside.read_bytes() == b'do not copy symlink contents'
