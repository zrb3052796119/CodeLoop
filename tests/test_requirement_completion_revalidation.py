"""Real native checkpoints; local model adapter and pytest, no paid provider."""
from copy import deepcopy
import json
import shutil
import socket
import sqlite3
import sys

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import ContextManager
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.implementation_completion_review import requirement_units
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.tooling import ToolRegistry
from minicode.tools.read_file import read_file_tool
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep
from scripts import run_requirement_completion_checkpoint as runner
from test_requirement_completion_checkpoint import terminal, write, cloned_script_tree


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        pytest.fail('approved revalidation tests must never contact a provider')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


def call(identity, name, **value):
    return AgentStep(type='tool_calls', calls=[{'id': identity, 'toolName': name, 'input': value}])


def packet(messages):
    return next(json.loads(m['content']) for m in messages
        if m.get('role') == 'user' and '"retainedProbes"' in m.get('content', ''))


def submit(probe_id, ids):
    return call('approve', 'completion_review_submit', status='approved', findings=[], requirements=[
        {'id': identity, 'status': 'covered', 'sourceEvidence': [{'path': 'example.py', 'startLine': 1, 'endLine': 1}],
         'probeIds': [probe_id], 'rationale': 'The executable public consumer confirms the requested value.'}
        for identity in ids])



def inspection_step(messages, registry):
    if registry.find('completion_coverage_inspection_submit') is None:
        return None
    assert registry.find('completion_coverage_audit_submit') is None
    authority = next(json.loads(m['content'])['coverageInspection'] for m in messages
        if m.get('role') == 'user' and '"coverageInspection"' in m.get('content', ''))
    read = next((m for m in messages if m.get('role') == 'tool_result'
        and m.get('toolUseId') == 'inspection-consumer'), None)
    if read is None:
        return call('inspection-consumer', 'read_file', path='example.py')
    assert read.get('isError') is False and read.get('toolName') == 'read_file'
    assert 'FILE: example.py\n' in read['content']
    source = read['content'].split('TRUNCATED:', 1)[1].split('\n', 1)[1]
    assert source.strip()
    return call('inspection-ready', 'completion_coverage_inspection_submit',
        status='ready_for_evidence', findings=[], observations=[{
            'requirementId': authority['requirements'][0]['id'], 'status': 'uncertain',
            'sourceEvidence': [{'path': 'example.py', 'startLine': 1, 'endLine': 1}],
            'expectedBehavior': authority['requirements'][0]['text'],
            'observedBehavior': 'Read the actual public module; executable behavior remains to be reconciled.',
            'probeIds': []}])


def audit_step(messages):
    authority = next(json.loads(m['content']) for m in messages
        if m.get('role') == 'user' and '"evidenceIndex"' in m.get('content', ''))
    probe = authority['evidenceIndex'][0]
    assert probe['status'] == 'passed' and probe['active'] and probe['purpose'] == 'acceptance'
    results = {m.get('toolUseId') for m in messages if m.get('role') == 'tool_result'}
    if 'audit-consumer' not in results:
        return call('audit-consumer', 'read_file', path='example.py')
    if 'audit-probe' not in results:
        return call('audit-probe', 'review_probe_read', probeId=probe['probeId'],
            receiptView='current', offset=0, limit=16000)
    return call('audit-approve', 'completion_coverage_audit_submit', status='approved', findings=[],
        requirements=[{'id': row['id'], 'status': 'supported',
            'reason': 'The real public constant is read from the delivered module and asserted by the independent test.',
            'witnesses': [{'probeId': probe['probeId'], 'startLine': 3, 'endLine': 3}]}
            for row in authority['requirements']])


@pytest.fixture
def approved(tmp_path, monkeypatch):
    import minicode.workflow_runner as workflow

    source, root, workspace, _ = terminal(tmp_path)
    task = json.loads((source / 'tasks.json').read_bytes())
    case = task['cases'][0]
    case['authorized_paths'] += ['test_public.py', 'tests/test_eval_regression.py']
    write(source / 'tasks.json', task)
    manifest = json.loads((source / 'manifest.json').read_bytes())
    manifest['taskSha256'] = runner.sha_file(source / 'tasks.json')
    write(source / 'manifest.json', manifest)
    spec = runner.implementation_spec(case)
    (workspace / 'test_public.py').write_text('from example import CURRENT\ndef test_public():\n    assert CURRENT == 2\n')
    (workspace / 'tests').mkdir()
    (workspace / 'tests/test_eval_regression.py').write_text('from example import CURRENT\ndef test_regression():\n    assert CURRENT == 2\n')
    commands = []

    class Sandbox:
        def wrap_command(self, *, argv, cwd, **_kwargs):
            commands.append({'argv': list(argv), 'cwd': str(cwd)})
            return argv

    monkeypatch.setattr(workflow, '_deterministic_verifier_for_context',
        lambda *_a, **_k: DeterministicVerifier(sandbox_backend=Sandbox(),
            explicit_trusted_executables={'python3': sys.executable}))
    monkeypatch.setattr(PermissionManager, 'ensure_command', lambda *_a, **_k: None)
    for name in ('implementation-checkpoint.json', 'implementation-checkpoint-messages.json',
                 'implementation-checkpoint-receipt.json', 'completion-review-archive.json'):
        (root / name).unlink()
    shutil.rmtree(root / 'completion-review')
    shutil.rmtree(root / 'journal')
    ids = [r['id'] for r in requirement_units(spec.objective)]

    class InitialModel:
        model_id = 'local-native-revalidation-test'
        parent_calls = review_calls = inspection_calls = 0

        def next(self, messages, **kwargs):
            inspection = inspection_step(messages, kwargs['tools_override'])
            if inspection is not None:
                self.inspection_calls += 1
                return inspection
            names = set(kwargs['tools_override'].list_all())
            if 'completion_coverage_audit_submit' in names:
                return audit_step(messages)
            if 'completion_review_submit' in names:
                self.review_calls += 1
                if self.review_calls == 1:
                    return call('probe', 'review_test', purpose='acceptance', criterionIds=ids,
                        scenario='The delivered public consumer exposes the requested value',
                        code='from example import CURRENT\ndef test_value():\n    assert CURRENT == 2\n')
                assert self.review_calls == 2
                evidence = json.JSONDecoder().raw_decode(next(m['content'] for m in messages
                    if m.get('role') == 'tool_result' and m.get('toolUseId') == 'probe'))[0]
                assert evidence['receipt']['status'] == 'passed'
                return submit(evidence['probeId'], ids)
            self.parent_calls += 1
            if self.parent_calls == 1:
                return call('retained-source', 'read_file', path='example.py')
            assert self.parent_calls == 2
            return AgentStep(type='assistant', kind='final', content='The public requirement has been implemented.')

    model = InitialModel()
    tools = ToolRegistry([read_file_tool, write_file_tool])
    journal = RunJournal(workspace, data_dir=root / 'journal')
    with runner.diagnostic.CompletionEvidenceArchive(root), observe_run(
            workspace=workspace, source='headless', title='approved public implementation',
            journal_factory=lambda _: journal) as observation:
        run_agent_turn(model=model, tools=tools, messages=[{'role': 'user', 'content': spec.objective}],
            cwd=str(workspace), context_manager=ContextManager(model=model.model_id),
            runtime={'model': model.model_id, 'reflectionMode': 'rule'}, agent_budget=AgentTurnBudget(),
            implementation_spec=spec, implementation_task=True,
            implementation_checkpoint_sink=runner.checkpoint_writer(root),
            event_sink=observation, enable_work_chain=False, enable_implementation_phases=False,
            max_steps=None, defer_curator_maintenance=True)
    assert model.parent_calls == 2 and model.review_calls == 2 and model.inspection_calls == 2
    document = json.loads((root / 'implementation-checkpoint.json').read_bytes())
    messages = json.loads((root / 'implementation-checkpoint-messages.json').read_bytes())
    assert document['attempt'] == 1 and document['reviewState']['reviews'][-1]['status'] == 'approved'
    assert any(m.get('toolUseId') == 'retained-source' and m.get('role') == 'tool_result' for m in messages)
    write(source / 'checkpoint.json', {'identity': {'executorId': document['executorId'], 'attempt': 0}})
    write(root / 'execution.json', {'errorClass': None, 'durationSeconds': 1})
    return source, root, workspace, document, messages, spec, commands, tools


def seal(source, root, *, success):
    write(source / 'results.json', {'manifestSha256': runner.sha_file(source / 'manifest.json'),
        'expectedTrials': 1, 'trials': [{'id': runner.TRIAL_ID, 'success': success,
            'errorClass': None, 'processCleanup': {'cleanupComplete': True},
            'judgement': {'cleanupComplete': True}}]})


@pytest.mark.parametrize('prior_success', [False, True])
def test_explicit_revalidation_uses_real_approval_independent_of_old_result(approved, tmp_path, monkeypatch, prior_success):
    source, root, workspace, original, messages, spec, commands, tools = approved
    seal(source, root, success=prior_success)
    before = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    candidate_before = (workspace / 'example.py').read_bytes()
    output = tmp_path / 'revalidation'
    with pytest.raises(ValueError):
        runner.prepare(output, source)
    assert not output.exists(), 'Ordinary interruption recovery must still reject approvals.'
    cloned_script_tree(tmp_path, monkeypatch)
    runner.prepare(output, source, revalidate_approved=True)
    checkpoint, inputs, actual_messages = runner.load_checkpoint(output)
    request = json.loads((output / 'private/approved-revalidation.json').read_bytes())
    assert request['reason'] == 'public_requirement_revalidation'
    assert set(request) == {'revalidationVersion', 'reason', 'checkpointDigest', 'executorId', 'attempt',
                           'candidateDigest', 'specDigest', 'messagesDigest', 'approvalEvidenceRef', 'approvalDigest'}
    assert checkpoint['predecessorClassification'] == 'approved-revalidation'
    assert checkpoint['terminalEvidence']['hiddenJudgeUsed'] is False
    assert 'oracle' not in json.dumps(request).lower() and 'success' not in json.dumps(request).lower()
    assert inputs['messages'] == actual_messages == messages
    assert (output / 'private/host-continuation.json').read_bytes() == (root / 'implementation-checkpoint.json').read_bytes()
    assert (output / 'private/parent-context.json').read_bytes() == (root / 'implementation-checkpoint-messages.json').read_bytes()
    restored = runner.restored_continuation(output, spec, actual_messages)
    assert restored.document == original and restored.requires_revalidation
    assert restored.revalidation_request == request
    runner.freeze(output)
    runner.validate(output)
    assert (output / 'private/approved-revalidation.json').stat().st_mode & 0o777 == 0o600
    assert all(p.read_bytes() == data for p, data in before.items())
    old_count = len(commands)
    resumed_checkpoints, roles = [], []

    class ResumedModel:
        model_id = 'local-native-revalidation-test'

        def next(self, current, **kwargs):
            names = set(kwargs['tools_override'].list_all())
            if not roles:
                assert 'completion_review_submit' in names
                assert not {'write_file', 'edit_file', 'run_command'} & names
                assert len(commands) == old_count + 3, 'Two public checks and the retained probe must really rerun.'
            inspection = inspection_step(current, kwargs['tools_override'])
            if inspection is not None:
                roles.append('inspection')
                return inspection
            if 'completion_coverage_audit_submit' in names:
                roles.append('audit')
                return audit_step(current)
            if 'completion_review_submit' in names:
                roles.append('review')
                current_packet = packet(current)
                assert current_packet['retainedProbes'][0]['status'] == 'passed'
                return submit(current_packet['retainedProbes'][0]['probeId'],
                    [r['id'] for r in requirement_units(spec.objective)])
            roles.append('parent')
            assert any(m.get('toolUseId') == 'retained-source' and m.get('role') == 'tool_result' for m in current)
            return AgentStep(type='assistant', kind='final', content='The public requirements have been revalidated.')

    run_agent_turn(model=ResumedModel(), tools=tools, messages=actual_messages,
        cwd=str(workspace), context_manager=ContextManager(model=ResumedModel.model_id),
        runtime={'model': ResumedModel.model_id, 'reflectionMode': 'rule'},
        implementation_spec=spec, implementation_continuation=restored,
        implementation_checkpoint_sink=lambda document, actual: resumed_checkpoints.append((deepcopy(document), deepcopy(actual))),
        agent_budget=AgentTurnBudget(), implementation_task=True, max_steps=None,
        enable_work_chain=False, enable_implementation_phases=False, defer_curator_maintenance=True)
    assert roles[0] == 'review' and roles.count('review') == 1
    assert roles.count('inspection') == 2
    current = resumed_checkpoints[-1][0]
    assert current['executorId'] == original['executorId'] and current['attempt'] > original['attempt']
    assert current['candidateDigest'] == original['candidateDigest']
    assert current['reviewState']['reviews'][0] == original['reviewState']['reviews'][0]
    assert current['reviewState']['reviews'][-1]['status'] == 'approved'
    assert (workspace / 'example.py').read_bytes() == candidate_before


@pytest.mark.parametrize('tamper', ['cleanup', 'active', 'candidate', 'messages', 'archive', 'native_approval', 'journal'])
def test_approved_revalidation_rejects_unbound_or_unfinished_predecessor(approved, tmp_path, tamper):
    source, root, workspace, _document, _messages, _spec, _commands, _tools = approved
    seal(source, root, success=True)
    if tamper == 'cleanup':
        write(root / 'process-cleanup.json', {'cleanupComplete': False})
    elif tamper == 'active':
        from scripts.evaluation_transport import SharedLedger, TransportConfig
        with sqlite3.connect(root / 'transport.sqlite') as db:
            configuration = json.loads(db.execute('SELECT config FROM settings WHERE id=1').fetchone()[0])
        ledger = SharedLedger(TransportConfig(**configuration))
        ledger.start({'model': 'glm-5.3-flash'}, 10)
    elif tamper == 'candidate':
        (workspace / 'example.py').write_text('CURRENT = 3\n')
    elif tamper == 'messages':
        (root / 'implementation-checkpoint-messages.json').write_text('[]')
    elif tamper == 'archive':
        manifest = json.loads((root / 'completion-review-archive.json').read_bytes())
        path = root / next(row['file'] for row in manifest['files'] if row['file'].endswith('-review.json'))
        path.write_text('{}')
    elif tamper == 'native_approval':
        path = root / 'implementation-checkpoint.json'
        document = json.loads(path.read_bytes())
        document['reviewState']['reviews'][-1]['status'] = 'changes_required'
        write(path, document)
        receipt = json.loads((root / 'implementation-checkpoint-receipt.json').read_bytes())
        receipt['documentSha256'] = runner.sha_file(path)
        write(root / 'implementation-checkpoint-receipt.json', receipt)
    else:
        path = next((root / 'journal').rglob('events.ndjson'))
        events = [json.loads(line) for line in path.read_text().splitlines()]
        for event in events:
            if event['type'] == 'implementation.completion_decided':
                event['payload']['executorId'] = 'implementation_' + 'f' * 32
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
    with pytest.raises(ValueError):
        runner.prepare(tmp_path / 'denied', source, revalidate_approved=True)
    assert not (tmp_path / 'denied').exists()


def test_frozen_revalidation_request_cannot_gain_private_feedback(approved, tmp_path):
    source, root, _workspace, _document, _messages, _spec, _commands, _tools = approved
    seal(source, root, success=False)
    output = tmp_path / 'revalidation'
    runner.prepare(output, source, revalidate_approved=True)
    path = output / 'private/approved-revalidation.json'
    request = json.loads(path.read_bytes())
    request['oracleFeedback'] = 'generic forbidden feedback sentinel'
    write(path, request)
    checkpoint = json.loads((output / 'checkpoint.json').read_bytes())
    checkpoint['approvedRevalidation']['sha256'] = runner.sha_file(path)
    write(output / 'checkpoint.json', checkpoint)
    with pytest.raises(ValueError, match='does not bind'):
        runner.load_checkpoint(output)
