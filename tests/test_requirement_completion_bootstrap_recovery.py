"""Native host bootstrap recovery; local adapter, real pytest, no provider."""
from copy import deepcopy
from dataclasses import asdict
import json
from itertools import count
from pathlib import Path
import socket
import sys
import urllib.error

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import ContextManager
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.implementation_completion_review import requirement_units
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.types import AgentStep
from scripts import run_requirement_completion_checkpoint as runner
from scripts.evaluation_transport import SharedLedger, TransportConfig
from scripts.three_arm_processes import _Process
from test_requirement_completion_checkpoint import cloned_script_tree, write
from test_requirement_completion_revalidation import approved, audit_step, inspection_step, packet, seal, submit  # noqa: F401


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        pytest.fail('bootstrap tests cannot contact a provider')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


@pytest.fixture
def bootstrap(approved, tmp_path, monkeypatch):  # noqa: F811
    import minicode.workflow_runner as workflow

    source, old_root, workspace, original, _messages, spec, commands, tools = approved
    seal(source, old_root, success=True)
    cloned_script_tree(tmp_path, monkeypatch)
    failed = tmp_path / 'bootstrap-failed'
    runner.prepare(failed, source, revalidate_approved=True)
    runner.freeze(failed)
    root = failed / 'private' / runner.TRIAL_ID
    root.mkdir()
    for name in ('workspace.json', 'baseline-files.json'):
        runner.private_write(root / name, (old_root / name).read_bytes())
    config = TransportConfig(ledger_path=str(root / 'transport.sqlite'), expected_model='glm-5.3-flash',
        expected_route_sha256='a' * 64, deadline_epoch=None, max_model_calls=None, max_total_tokens=None,
        max_output_tokens=131072, connect_timeout_seconds=30, idle_timeout_seconds=600, request_timeout_seconds=None)
    ledger = SharedLedger(config)
    write(root / 'transport-config.json', asdict(config))
    preparation_clock = count(step=0.01)
    monkeypatch.setattr(workflow, '_deterministic_verifier_for_context',
        lambda *_a, **_k: DeterministicVerifier(sandbox_backend=None,
            clock=lambda: next(preparation_clock),
            explicit_trusted_executables={'python3': sys.executable}))
    before_commands = len(commands)
    messages = json.loads((failed / 'private/parent-context.json').read_bytes())

    class UnavailableModel:
        model_id = 'local-bootstrap-mechanism'
        calls = 0

        def next(self, _messages, **_kwargs):
            self.calls += 1
            physical = ledger.start({'model': 'glm-5.3-flash'}, 10)
            ledger.update(physical, {'event': 'failed', 'errorClass': 'URLError', 'errorPhase': 'headers',
                'streamDone': False, 'seconds': 0.001, 'firstByteSeconds': None})
            raise urllib.error.URLError('local host transport unavailable')

    model = UnavailableModel()
    journal = RunJournal(workspace, data_dir=root / 'journal')
    with runner.diagnostic.CompletionEvidenceArchive(root), observe_run(
            workspace=workspace, source='headless', title='host bootstrap failed',
            journal_factory=lambda _: journal) as observation:
        run_agent_turn(model=model, tools=tools, messages=messages, cwd=str(workspace),
            context_manager=ContextManager(model=model.model_id), runtime={'model': model.model_id, 'reflectionMode': 'rule'},
            agent_budget=AgentTurnBudget(), implementation_spec=spec, implementation_task=True,
            implementation_continuation=runner.restored_continuation(failed, spec, messages),
            implementation_checkpoint_sink=runner.checkpoint_writer(root), event_sink=observation,
            max_steps=None, enable_work_chain=False, enable_implementation_phases=False, defer_curator_maintenance=True)
    assert model.calls == 1 and len(commands) == before_commands
    document = json.loads((root / 'implementation-checkpoint.json').read_bytes())
    feedback = json.loads(document['completionFeedback']['content'].split('\nCompletion evidence:\n', 1)[1])
    proofs = [check['evidence'] for check in feedback['checks']['checks']]
    assert proofs and all(proof['durationMs'] > 0 for proof in proofs)
    assert all(proof['failureReason'] == 'host_sandbox_unavailable'
               and proof['exitCode'] is None for proof in proofs)
    assert document['attempt'] == original['attempt'] + 1
    assert document['reviewState']['evidenceDirectory'] is None
    cleanup = {'rootPid': 900001, 'cleanupComplete': False,
        'processScanErrors': ['freeze:PermissionError', 'kill-confirm:PermissionError'],
        'unconfirmedPids': [900001], 'signalEvents': [{'sent': False}],
        'phaseTimings': {'freeze': {'scanCount': 0}, 'kill-confirm': {'scanCount': 0}}}
    write(root / 'process-cleanup.json', cleanup)
    (root / 'processes.jsonl').write_text(json.dumps({'pid': 900001, 'pgid': 900001}) + '\n'
        + json.dumps({'pid': 900002, 'pgid': 900001}) + '\n')
    write(root / 'execution.json', {'errorClass': None, 'durationSeconds': 0.1})
    write(failed / 'results.json', {'manifestSha256': runner.sha_file(failed / 'manifest.json'),
        'expectedTrials': 1, 'trials': [{'id': runner.TRIAL_ID, 'success': False, 'errorClass': None,
            'processCleanup': cleanup, 'judgement': None,
            'regressionTest': {'status': 'not_run_agent_cleanup_or_workspace'}}]})
    return failed, root, workspace, document, spec, commands, tools


def test_bootstrap_append_only_attestation_native_resume_and_real_checks(bootstrap, tmp_path, monkeypatch):
    import minicode.workflow_runner as workflow
    failed, root, workspace, document, spec, commands, tools = bootstrap
    originals = {p: p.read_bytes() for p in failed.rglob('*') if p.is_file()}
    candidate = (workspace / 'example.py').read_bytes()
    attestation = tmp_path / 'attest' / 'cleanup.json'
    monkeypatch.setattr('scripts.three_arm_processes._process_table',
        lambda: {123: _Process(123, 1, 123, 'S')})
    runner.attest_bootstrap_cleanup(failed, attestation)
    assert attestation.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match='append-only'):
        runner.attest_bootstrap_cleanup(failed, attestation)
    with pytest.raises(ValueError, match='cleanup incomplete'):
        runner.prepare(tmp_path / 'ordinary', failed)
    resumed = tmp_path / 'recovered'
    runner.prepare(resumed, failed, recover_host_bootstrap=True, cleanup_attestation=attestation)
    checkpoint, inputs, messages = runner.load_checkpoint(resumed)
    assert checkpoint['predecessorClassification'] == 'host-bootstrap-blocked'
    assert checkpoint['identity']['executorId'] == document['executorId']
    assert checkpoint['identity']['attempt'] == document['attempt']
    assert len(checkpoint['predecessorLedgerRoots']) == 2
    assert (resumed / 'private/parent-context.json').read_bytes() == (root / 'implementation-checkpoint-messages.json').read_bytes()
    assert (resumed / 'private/host-continuation.json').read_bytes() == (root / 'implementation-checkpoint.json').read_bytes()
    restored = runner.restored_continuation(resumed, spec, messages)
    assert restored.requires_revalidation and restored.document == document
    assert inputs['revalidationRequest']['reason'] == 'public_requirement_revalidation'
    runner.freeze(resumed)
    runner.validate(resumed)
    assert all(p.read_bytes() == data for p, data in originals.items())

    class Sandbox:
        def wrap_command(self, *, argv, cwd, **_kwargs):
            commands.append({'argv': list(argv), 'cwd': str(cwd)})
            return argv

    monkeypatch.setattr(workflow, '_deterministic_verifier_for_context',
        lambda *_a, **_k: DeterministicVerifier(sandbox_backend=Sandbox(),
            explicit_trusted_executables={'python3': sys.executable}))
    initial_count = len(commands)
    roles, captures, dispatches = [], [], []

    class ResumedModel:
        model_id = 'local-bootstrap-mechanism'
        calls = 0

        def next(self, actual, **kwargs):
            self.calls += 1
            if self.calls > 12:
                raise KeyboardInterrupt('local scripted adapter exceeded its expected native protocol')
            names = set(kwargs['tools_override'].list_all())
            dispatches.append((names, len(commands)))
            if not roles:
                assert 'completion_review_submit' in names and 'write_file' not in names
                assert len(commands) == initial_count + 3
            inspection = inspection_step(actual, kwargs['tools_override'])
            if inspection is not None:
                roles.append('inspection')
                return inspection
            if 'completion_coverage_audit_submit' in names:
                roles.append('audit')
                return audit_step(actual)
            if 'completion_review_submit' in names:
                roles.append('review')
                current = packet(actual)
                return submit(current['retainedProbes'][0]['probeId'], [r['id'] for r in requirement_units(spec.objective)])
            roles.append('parent')
            return AgentStep(type='assistant', kind='final', content='Public requirements verified.')

    run_agent_turn(model=ResumedModel(), tools=tools, messages=messages, cwd=str(workspace),
        context_manager=ContextManager(model=ResumedModel.model_id),
        runtime={'model': ResumedModel.model_id, 'reflectionMode': 'rule'}, agent_budget=AgentTurnBudget(),
        implementation_spec=spec, implementation_continuation=restored, implementation_task=True,
        implementation_checkpoint_sink=lambda doc, msgs: captures.append((deepcopy(doc), deepcopy(msgs))),
        max_steps=None, enable_work_chain=False, enable_implementation_phases=False, defer_curator_maintenance=True)
    assert captures[-1][0]['executorId'] == document['executorId']
    assert captures[-1][0]['attempt'] > document['attempt']
    assert captures[-1][0]['reviewState']['reviews'][-1]['status'] == 'approved'
    assert 'completion_review_submit' in dispatches[0][0] and 'write_file' not in dispatches[0][0]
    assert dispatches[0][1] == initial_count + 3
    assert roles == ['review', 'inspection', 'inspection', 'audit', 'audit', 'audit', 'parent']
    assert (workspace / 'example.py').read_bytes() == candidate


def test_bootstrap_rejects_invalid_duration_or_executed_check_evidence(bootstrap):
    failed, root, _workspace, document, spec, _commands, _tools = bootstrap
    messages = json.loads((root / 'implementation-checkpoint-messages.json').read_bytes())
    execution = json.loads((root / 'execution.json').read_bytes())
    # Real unexecuted receipts can include positive host preparation time.
    runner.bootstrap_terminal_evidence(failed, document, execution, messages, spec)
    for field, value in [('durationMs', -1), ('durationMs', False), ('durationMs', '0'),
                         ('durationMs', None), ('durationMs', 0.1), ('exitCode', 0),
                         ('status', 'passed'), ('failureReason', None), ('checkId', 'unbound'),
                         ('stdout', {'byteCount': 1, 'preview': 'x', 'previewBase64': 'eA=='}),
                         ('stderr', {'byteCount': 1, 'preview': 'x', 'previewBase64': 'eA=='})]:
        tampered = deepcopy(document)
        previous = tampered['completionFeedback']
        prefix, encoded = previous['content'].split('\nCompletion evidence:\n', 1)
        receipt = json.loads(encoded)
        receipt['checks']['checks'][0]['evidence'][field] = value
        replacement = {**previous, 'content': prefix + '\nCompletion evidence:\n' + json.dumps(receipt)}
        tampered['completionFeedback'] = replacement
        bound_messages = [replacement if message == previous else message for message in messages]
        with pytest.raises(ValueError, match='public verification actually ran or has another failure'):
            runner.bootstrap_terminal_evidence(failed, tampered, execution, bound_messages, spec)


@pytest.mark.parametrize('failure', ['scan', 'pid', 'group', 'reused_pid', 'candidate', 'context', 'approval', 'receipt'])
def test_bootstrap_refuses_residue_or_unbound_native_state(bootstrap, tmp_path, monkeypatch, failure):
    failed, root, workspace, _document, _spec, _commands, _tools = bootstrap
    attestation = tmp_path / 'attestation.json'
    monkeypatch.setattr('scripts.three_arm_processes._process_table',
        lambda: {123: _Process(123, 1, 123, 'S')})
    if failure == 'scan':
        def denied():
            raise PermissionError('no visibility')
        monkeypatch.setattr('scripts.three_arm_processes._process_table', denied)
    elif failure in {'pid', 'group', 'reused_pid'}:
        row = {'pid': _Process(900002, 1, 900001, 'S'),
            'group': _Process(900003, 1, 900001, 'S'),
            'reused_pid': _Process(900001, 1, 999999, 'S')}[failure]
        monkeypatch.setattr('scripts.three_arm_processes._process_table', lambda: {row.pid: row})
    elif failure == 'candidate':
        (workspace / 'example.py').write_text('CURRENT = 3\n')
    elif failure == 'context':
        (root / 'implementation-checkpoint-messages.json').write_text('[]')
    elif failure == 'approval':
        prior = json.loads((failed / 'checkpoint.json').read_bytes())
        archive = Path(prior['predecessorDirectory']) / 'private' / runner.TRIAL_ID / 'completion-review-archive.json'
        archive.write_text('{}')
    else:
        runner.attest_bootstrap_cleanup(failed, attestation)
        value = json.loads(attestation.read_bytes())
        value['identity']['attempt'] += 1
        write(attestation, value)
        with pytest.raises(ValueError, match='attestation'):
            runner.prepare(tmp_path / 'recovered', failed, recover_host_bootstrap=True, cleanup_attestation=attestation)
        return
    with pytest.raises((ValueError, PermissionError)):
        runner.attest_bootstrap_cleanup(failed, attestation)
    assert not attestation.exists()
