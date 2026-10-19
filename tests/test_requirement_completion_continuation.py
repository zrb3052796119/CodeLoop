"""Offline recovery contracts use generic temporary candidates and real journals."""
import json
import socket

import pytest

from scripts import run_requirement_completion_continuation as continuation
from minicode.run_events import emit_event_safely
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run

OWNER = 'implementation_' + 'a' * 32
DIGEST = 'sha256:' + 'b' * 64


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('offline recovery tests cannot call models')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def predecessor(tmp_path):
    source = tmp_path / 'predecessor'
    root = source / 'private' / continuation.TRIAL_ID
    workspace = tmp_path / 'existing'
    workspace.mkdir()
    (workspace / 'example.py').write_text('CURRENT = 2\n')
    messages = [{'role': 'system', 'content': 'original'},
        {'role': 'user', 'content': 'public original'},
        {'role': 'system', 'content': 'compacted previous work', '_compact_boundary': True},
        {'role': 'assistant_tool_call', 'tool_name': 'edit_file', 'tool_call_id': 'edit1', 'content': '{}'},
        {'role': 'tool_result', 'tool_call_id': 'edit1', 'content': 'actual edit retained'},
        {'role': 'user', 'content': 'public review remains incomplete', '_implementation_completion_feedback': True}]
    write(root / 'interrupted-transcript.json', messages)
    write(root / 'execution-evidence.json', {'source': 'parent_runtime_context_manager', 'available': True, 'errors': [],
        'sha256': continuation.sha_file(root / 'interrupted-transcript.json'), 'messageCount': len(messages)})
    write(root / 'execution.json', {'errorClass': 'KeyboardInterrupt'})
    write(root / 'process-cleanup.json', {'cleanupComplete': True})
    write(root / 'workspace.json', {'path': str(workspace)})
    write(root / 'baseline-files.json', {'example.py': ['file', 'baseline-hash']})
    (root / 'transport.sqlite').write_bytes(b'generic opaque predecessor ledger')
    write(root / 'transport-config.json', {})
    write(root / 'completion-review/review_a/00001-probe-source.json', {'opaque': 'test fixture'})
    probe = root / 'completion-review/review_a/00001-probe-source.json'
    write(root / 'completion-review-archive.json', {'archiveComplete': True, 'errors': [], 'files': [{
        'file': str(probe.relative_to(root)), 'sha256': continuation.sha_file(probe), 'bytes': probe.stat().st_size}]})
    case = {'id': continuation.diagnostic.CASE, 'prompt': 'Implement a generic public requirement.',
        'authorized_paths': ['example.py'], 'verification_argv': ['python3', '-m', 'pytest', '-q', 'test_public.py'],
        'files': {'example.py': 'BASELINE = 1\n'}, 'oracle_source': 'DO NOT FEED THIS SYNTHETIC PRIVATE SENTINEL'}
    write(source / 'tasks.json', {'cases': [case], 'shared_files': {}})
    write(source / 'public-cases.json', {'cases': [{'id': case['id']}]})
    write(source / 'source-provenance.json', {})
    write(source / 'public-preflight/receipt.json', {})
    (source / 'frozen-code.zip').write_bytes(b'opaque frozen test code')
    write(source / 'manifest.json', {'schedule': continuation.diagnostic.schedule(), 'limits': continuation.LIMITS,
        'taskSha256': continuation.sha_file(source / 'tasks.json'),
        'frozenCodeArchiveSha256': continuation.sha_file(source / 'frozen-code.zip')})
    from minicode.implementation_execution import implementation_candidate_digest
    from minicode.tooling import ToolContext
    digest = implementation_candidate_digest(continuation.implementation_spec(case), ToolContext(cwd=str(workspace), permissions=None))
    journal = RunJournal(workspace, data_dir=root / 'journal')
    with observe_run(workspace=workspace, source='headless', title='generic interrupted implementation', journal_factory=lambda _: journal) as obs:
        for attempt in (1, 2):
            emit_event_safely(obs, 'implementation.completion_review_started', payload={
                'executorId': OWNER, 'attempt': attempt, 'patchDigest': digest})
            if attempt == 1:
                emit_event_safely(obs, 'implementation.completion_decided', payload={
                    'completionVersion': 1, 'executorId': OWNER, 'attempt': attempt, 'patchDigest': digest,
                    'status': 'changes_required', 'continuesSameExecutor': True,
                    'requirements': [], 'findings': []})
    return source, root, workspace, messages


def test_prepare_retains_actual_context_current_candidate_and_identity(tmp_path):
    source, root, workspace, messages = predecessor(tmp_path)
    originals = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    output = tmp_path / 'resume'
    continuation.prepare(output, source)
    checkpoint, inputs, restored = continuation.load_checkpoint(output)
    assert checkpoint['identity']['executorId'] == OWNER
    assert checkpoint['identity']['attempt'] == 2
    assert checkpoint['identity']['lastAttemptInterrupted'] is True
    assert restored == messages
    assert (output / 'private/parent-context.json').read_bytes() == (root / 'interrupted-transcript.json').read_bytes()
    assert (workspace / 'example.py').read_text() == 'CURRENT = 2\n'
    assert checkpoint['freshBaselineHydration'] is False
    assert checkpoint['privateJudgeUsedForRecovery'] is False
    assert 'PRIVATE SENTINEL' not in (output / 'checkpoint.json').read_text()
    assert 'PRIVATE SENTINEL' not in (output / 'private/parent-context.json').read_text()
    assert all(path.read_bytes() == value for path, value in originals.items())
    assert (output / 'private/parent-context.json').stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match='empty'):
        continuation.prepare(output, source)


@pytest.mark.parametrize('tamper', ['candidate', 'context', 'journal', 'review', 'spec'])
def test_resume_rejects_drift_before_execution(tmp_path, tamper):
    source, root, workspace, _ = predecessor(tmp_path)
    output = tmp_path / 'resume'
    continuation.prepare(output, source)
    if tamper == 'candidate':
        (workspace / 'example.py').write_text('different')
    elif tamper == 'context':
        (output / 'private/parent-context.json').write_text('[]')
    elif tamper == 'journal':
        path = next((root / 'journal').rglob('events.ndjson'))
        path.write_text('')
    elif tamper == 'review':
        (root / 'completion-review/review_a/00001-probe-source.json').write_text('{}')
    else:
        checkpoint = json.loads((output / 'checkpoint.json').read_text())
        checkpoint['spec']['objective'] = 'new task'
        write(output / 'checkpoint.json', checkpoint)
    with pytest.raises(ValueError):
        continuation.load_checkpoint(output)


def test_missing_actual_context_and_unclean_process_block_prepare(tmp_path):
    source, root, _, _ = predecessor(tmp_path)
    write(root / 'process-cleanup.json', {'cleanupComplete': False})
    with pytest.raises(ValueError, match='cleanup'):
        continuation.prepare(tmp_path / 'resume', source)
    assert not (tmp_path / 'resume').exists()


def test_candidate_symlink_cannot_expand_scope(tmp_path):
    source, root, workspace, _ = predecessor(tmp_path)
    (workspace / 'example.py').unlink()
    outside = tmp_path / 'outside'
    outside.write_text('private')
    (workspace / 'example.py').symlink_to(outside)
    with pytest.raises(ValueError, match='escapes'):
        continuation.prepare(tmp_path / 'resume', source)


def test_combined_metering_keeps_predecessor_unknown_usage_and_both_segments(tmp_path):
    from scripts.evaluation_transport import SharedLedger, TransportConfig
    roots = [tmp_path / 'old', tmp_path / 'new']
    for index, root in enumerate(roots):
        root.mkdir()
        config = TransportConfig(ledger_path=str((root / 'transport.sqlite').resolve()), expected_model='glm-5.3-flash',
            expected_route_sha256='a' * 64, deadline_epoch=None, max_model_calls=None, max_total_tokens=None,
            max_output_tokens=131072, connect_timeout_seconds=30, idle_timeout_seconds=120, request_timeout_seconds=None)
        ledger = SharedLedger(config)
        call = ledger.start({'model': 'glm-5.3-flash'}, 1000)
        usage = {'source': 'provider', 'input_tokens': 100, 'output_tokens': 20, 'cache_read_tokens': 30}
        ledger.update(call, {'event': 'completed', 'usage': usage})
        if index == 0:
            failed = ledger.start({'model': 'glm-5.3-flash'}, 1000)
            ledger.update(failed, {'event': 'failed'})
        write(root / 'execution.json', {'durationSeconds': index + 2, 'errorClass': 'KeyboardInterrupt' if index == 0 else None})
    before = continuation.sha_file(roots[0] / 'transport.sqlite')
    metrics = continuation.combined_metering(roots[:1], roots[1])
    assert metrics['modelCalls'] == 3 and metrics['failedCalls'] == 1
    assert metrics['unknownUsageCalls'] == 1
    assert metrics['totalTokens'] is None and metrics['estimatedCostCny'] is None
    assert metrics['knownTokenLowerBound'] == 240
    assert metrics['durationSeconds'] == 5
    assert len(metrics['segments']) == 2
    assert continuation.sha_file(roots[0] / 'transport.sqlite') == before


def test_missing_production_restore_blocks_freeze_before_execution(tmp_path, monkeypatch):
    from minicode.implementation_completion_review import ImplementationCompletionReview
    source, _, _, _ = predecessor(tmp_path)
    output = tmp_path / 'resume'
    continuation.prepare(output, source)
    monkeypatch.setattr(ImplementationCompletionReview, 'from_archive', None, raising=False)
    with pytest.raises(RuntimeError, match='restoration'):
        continuation.freeze(output)
    assert not (output / 'manifest.json').exists()
    assert not (output / 'private' / continuation.TRIAL_ID / 'transport.sqlite').exists()


def test_unlisted_review_file_cannot_enter_restored_state(tmp_path):
    source, root, _, _ = predecessor(tmp_path)
    output = tmp_path / 'resume'
    continuation.prepare(output, source)
    write(root / 'completion-review/review_a/00002-review.json', {'status': 'approved'})
    with pytest.raises(ValueError, match='unlisted'):
        continuation.load_checkpoint(output)


def test_saved_parent_enters_real_runtime_and_loop_without_reset_and_checkpoints(tmp_path, monkeypatch):
    from minicode.agent_budget import AgentTurnBudget
    from minicode.agent_runtime import AgentTurnRuntime
    from minicode.context_manager import ContextManager
    from minicode.implementation_completion_review import ImplementationCompletionReview
    from minicode.tooling import ToolRegistry
    from minicode.tools.write_file import write_file_tool
    from minicode.types import ModelAdapter
    source, _, workspace, messages = predecessor(tmp_path)
    output = tmp_path / 'resume'
    continuation.prepare(output, source)
    _, inputs, saved = continuation.load_checkpoint(output)
    spec = continuation.implementation_spec(inputs['case'])
    continuation.private_json(output / 'private/review-state.json', ImplementationCompletionReview(spec).state)
    capability = continuation.restored_continuation(output, spec, saved)
    error = KeyboardInterrupt('generic local interruption')
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
    registry = ToolRegistry([write_file_tool])
    runtime = AgentTurnRuntime(workspace=workspace, runtime={}, tools=registry, permissions=None,
        memory_manager=None, model=model, skill_routing=None, system_prompt='unused new initial prompt',
        context_manager=ContextManager(model='default'), allow_user_interaction=False,
        agent_budget=AgentTurnBudget(), implementation_spec=spec, implementation_continuation=capability,
        implementation_checkpoint_sink=continuation.checkpoint_writer(output / 'new-evidence'))
    monkeypatch.setattr('minicode.cybernetic_supervisor.SUPERVISOR_STATE_PATH', tmp_path / 'supervisor.json')
    with pytest.raises(KeyboardInterrupt) as caught:
        with continuation.diagnostic.ParentExecutionEvidenceArchive(output / 'new-evidence'), observe_run(
                workspace=workspace, source='headless', title='actual restored runtime', enabled=False) as observation:
            try:
                runtime.execute(saved, observation, defer_curator_maintenance=True)
            finally:
                runtime.dispose()
    assert caught.value is error and model.calls == 1
    stored = json.loads((output / 'new-evidence/implementation-checkpoint.json').read_bytes())
    assert stored['executorId'] == OWNER and stored['attempt'] == 2
    saved_after = json.loads((output / 'new-evidence/implementation-checkpoint-messages.json').read_bytes())
    assert any(m.get('content') == 'actual edit retained' for m in saved_after)
    assert (output / 'private/parent-context.json').read_bytes() == (source / 'private' / continuation.TRIAL_ID / 'interrupted-transcript.json').read_bytes()
    assert not (output / 'new-evidence/response.txt').exists()
    assert (workspace / 'example.py').read_text() == 'CURRENT = 2\n'
