"""Authenticate swallowed parent API failures without changing old outcomes."""

import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_compactor import LLMSummaryGenerator
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep
from scripts import run_requirement_completion_checkpoint as runner
from scripts.evaluation_transport import EvaluationStreamError, SharedLedger, TransportConfig
from test_requirement_completion_checkpoint import cloned_script_tree, seal_terminal, terminal, write


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        pytest.fail('terminal recovery tests cannot contact a model provider')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


@pytest.fixture
def swallowed_failure(tmp_path, monkeypatch):
    """Real parent failure/finally/checkpoint and separate native summary event."""
    import minicode.workflow_runner as workflow

    source, root, workspace, old = terminal(tmp_path)
    (workspace / 'test_public.py').write_text('from example import CURRENT\ndef test_public():\n    assert CURRENT == 2\n')
    (workspace / 'tests').mkdir()
    (workspace / 'tests/test_eval_regression.py').write_text('def test_regression():\n    assert 2 + 3 == 5\n')
    case = json.loads((source / 'tasks.json').read_bytes())['cases'][0]
    spec = runner.implementation_spec(case)
    messages = json.loads((root / 'implementation-checkpoint-messages.json').read_bytes())
    host = HostImplementationContinuation.capture(spec=spec, context=ToolContext(cwd=str(workspace)),
        messages=messages, executor_id=old.executor_id, attempt=old.attempt, review_state=old.document['reviewState'])
    for name in ('implementation-checkpoint.json', 'implementation-checkpoint-messages.json',
                 'implementation-checkpoint-receipt.json', 'transport.sqlite'):
        (root / name).unlink()
    shutil.rmtree(root / 'journal')
    config = TransportConfig(ledger_path=str((root / 'transport.sqlite').resolve()), expected_model='glm-5.3-flash',
        expected_route_sha256='a' * 64, deadline_epoch=None, max_model_calls=None, max_total_tokens=None,
        max_output_tokens=131072, connect_timeout_seconds=30, idle_timeout_seconds=600, request_timeout_seconds=None)
    ledger = SharedLedger(config)

    def command(argv, **kwargs):
        return subprocess.run([sys.executable, *argv[1:]], **kwargs)

    monkeypatch.setattr(workflow, '_deterministic_verifier_for_context',
        lambda *_a, **_k: DeterministicVerifier(command_runner=command))
    monkeypatch.setattr(PermissionManager, 'ensure_command', lambda *_a, **_k: None)

    def incomplete(self, *, checks_receipt, **_kwargs):
        assert checks_receipt['status'] == 'passed'
        return self._result('changes_required', checks_receipt['patchDigest'],
            ['The scripted independent review identifies an outstanding public requirement.'], canContinue=True)

    original_assess = ImplementationCompletionReview.assess
    monkeypatch.setattr(ImplementationCompletionReview, 'assess', incomplete)

    class LocalModel:
        model_id = 'local-terminal-failure'
        calls = 0
        summary = False

        def next(self, _messages, **kwargs):
            self.calls += 1
            tools = kwargs.get('tools_override')
            names = tools.list_all() if tools is not None else ['write_file']
            identity = ledger.start({'model': 'glm-5.3-flash', 'actorId': 'main', 'role': 'main',
                'parentToolOperationId': None, 'subagentId': None, 'toolNames': list(names)}, 1000)
            started = time.time()
            if self.calls == 1:
                ledger.update(identity, {'event': 'completed', 'providerStartedEpoch': started,
                    'providerTerminalEpoch': time.time(), 'usage': {'source': 'provider',
                        'input_tokens': 100, 'output_tokens': 20, 'cache_read_tokens': 0}})
                return AgentStep(type='assistant', kind='final', content='The generic task is complete.')
            error = (ModelCallDeadlineExceeded('local summary deadline') if self.summary
                else EvaluationStreamError('provider HTTP status 429'))
            ledger.update(identity, {'event': 'failed', 'providerStartedEpoch': started,
                'providerTerminalEpoch': time.time(), 'errorClass': type(error).__name__,
                'errorPhase': 'stream' if self.summary else 'headers', 'httpStatus': 200 if self.summary else 429})
            raise error

    model = LocalModel()
    journal = RunJournal(workspace, data_dir=root / 'journal')
    with observe_run(workspace=workspace, source='headless', title='generic native API partial',
            journal_factory=lambda _: journal) as observation:
        result = run_agent_turn(model=model, tools=ToolRegistry([write_file_tool]), messages=messages,
            cwd=str(workspace), implementation_spec=spec, implementation_task=True,
            implementation_continuation=host, implementation_checkpoint_sink=runner.checkpoint_writer(root),
            runtime={'model': model.model_id, 'reflectionMode': 'rule'}, agent_budget=AgentTurnBudget(),
            event_sink=observation, max_steps=None, enable_work_chain=False,
            enable_implementation_phases=False, defer_curator_maintenance=True)
        assert result[-1]['_partial'] and result[-1]['_host_implementation_receipt']
        # This uses the actual compactor's purpose-tagged start/failure path.
        # Its later physical error must not replace the ordinary parent error.
        model.summary = True
        assert LLMSummaryGenerator(model, event_sink=observation)._call_model_once(
            [{'role': 'user', 'content': 'Summarize retained observations.'}], None,
            deadline_monotonic=time.monotonic() + 20, propagate_deadline=False) is None
    assert model.calls == 3
    monkeypatch.setattr(ImplementationCompletionReview, 'assess', original_assess)
    document = json.loads((root / 'implementation-checkpoint.json').read_bytes())
    write(root / 'execution.json', {'errorClass': None, 'durationSeconds': 1})
    write(source / 'checkpoint.json', {'identity': {key: host.document[key]
        for key in ('executorId', 'attempt', 'candidateDigest')}})
    manifest = json.loads((source / 'manifest.json').read_bytes())
    manifest['limits'] = dict(runner.LIMITS)
    write(source / 'manifest.json', manifest)
    seal_terminal(source, root)
    results = json.loads((source / 'results.json').read_bytes())
    results['trials'][0].update(classification='not-delivered', scopePassed=True,
        unauthorizedChanges=[], verificationInducedChanges=[])
    write(source / 'results.json', results)
    return source, root, workspace, document, spec, ledger


def test_explicit_parent_failure_resumes_original_owner_and_excludes_later_summary(swallowed_failure, tmp_path, monkeypatch):
    source, root, workspace, document, spec, _ledger = swallowed_failure
    originals = {path: path.read_bytes() for path in source.rglob('*') if path.is_file()}
    output = tmp_path / 'resumed'
    with pytest.raises(ValueError, match='natural terminal lacks'):
        runner.prepare(output, source)
    assert not output.exists()
    runner.prepare(output, source, resume_model_failure=True)
    contract, _inputs, saved = runner.load_checkpoint(output)
    typed = runner.restored_continuation(output, spec, saved)
    assert typed.document == document and not typed.requires_revalidation
    assert contract['modelFailureContinuation'] == {'version': 1, 'kind': 'authenticated_parent_model_failure'}
    evidence = contract['terminalEvidence']['partialTransportEvidence']
    assert evidence['physicalCall'] == 2 and evidence['excludedCompactionPhysicalCalls'] == [3]
    assert evidence['errorClass'] == 'EvaluationStreamError' and evidence['physicalHttpStatus'] == 429
    assert evidence['associationMethod'] == 'legacy_unique_time_envelope'
    assert evidence['directOperationIdAvailable'] is False
    assert contract['terminalEvidence']['lastPhysicalCall']['errorClass'] == 'ModelCallDeadlineExceeded'
    assert contract['terminalEvidence']['originalErrorClass'] is None
    assert contract['predecessorClassification'] == 'transport-blocked'
    assert (output / 'private/parent-context.json').read_bytes() == (root / 'implementation-checkpoint-messages.json').read_bytes()
    assert (workspace / 'example.py').read_text() == 'CURRENT = 2\n'
    cloned_script_tree(tmp_path, monkeypatch)
    runner.freeze(output)
    runner.validate(output)
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert not (output / 'private' / runner.TRIAL_ID / 'transport.sqlite').exists()
    changed = json.loads((output / 'checkpoint.json').read_bytes())
    del changed['modelFailureContinuation']
    write(output / 'checkpoint.json', changed)
    with pytest.raises(ValueError, match='natural terminal lacks'):
        runner.load_checkpoint(output)


def _journal_change(root, edit):
    path = next((root / 'journal').glob('dashboard/workspaces/*/runs/run_*/events.ndjson'))
    events = [json.loads(line) for line in path.read_text().splitlines()]
    edit(events)
    path.write_text(''.join(json.dumps(row) + '\n' for row in events))


def _ledger_change(root, call, edit):
    with sqlite3.connect(root / 'transport.sqlite') as db:
        row = json.loads(db.execute('SELECT data FROM requests WHERE call=?', (call,)).fetchone()[0])
        edit(row)
        db.execute('UPDATE requests SET data=? WHERE call=?', (json.dumps(row), call))


@pytest.mark.parametrize('damage', ['ambiguous_physical', 'time_mismatch', 'error_class', 'actor', 'operation_id',
    'missing_checkpoint', 'candidate', 'approved', 'inflight', 'missing_failure', 'later_ordinary_success',
    'partial_missing', 'error_message', 'scope', 'cleanup', 'unmapped_later_call'])
def test_unauthenticated_or_nonterminal_provider_failure_cannot_resume(swallowed_failure, tmp_path, damage):
    source, root, workspace, document, spec, ledger = swallowed_failure
    if damage in {'ambiguous_physical', 'unmapped_later_call'}:
        with sqlite3.connect(root / 'transport.sqlite') as db:
            data = db.execute('SELECT data FROM requests WHERE call=?', (2 if damage == 'ambiguous_physical' else 3,)).fetchone()[0]
            row = json.loads(data)
            if damage == 'unmapped_later_call':
                row.update(providerStartedEpoch=row['providerTerminalEpoch'] + 10,
                    providerTerminalEpoch=row['providerTerminalEpoch'] + 11)
            db.execute("INSERT INTO requests(status,charge_tokens,data) VALUES ('failed',0,?)", (json.dumps(row),))
    elif damage == 'time_mismatch':
        _ledger_change(root, 2, lambda row: row.update(providerStartedEpoch=row['providerTerminalEpoch'] + 10,
            providerTerminalEpoch=row['providerTerminalEpoch'] + 11))
    elif damage in {'error_class', 'actor', 'operation_id'}:
        key, value = {'error_class': ('errorClass', 'HTTPError'), 'actor': ('actorId', 'other-executor'),
            'operation_id': ('modelOperationId', 'modelop_' + 'f' * 32)}[damage]
        _ledger_change(root, 2, lambda row: row.update({key: value}))
    elif damage == 'missing_checkpoint':
        (root / 'implementation-checkpoint-receipt.json').unlink()
    elif damage == 'candidate':
        (workspace / 'example.py').write_text('CURRENT = 999\n')
    elif damage == 'approved':
        _journal_change(root, lambda rows: next(row['payload'] for row in reversed(rows)
            if row['type'] == 'implementation.completion_decided').update(status='approved'))
    elif damage == 'inflight':
        ledger.start({'model': 'glm-5.3-flash'}, 1)
    elif damage == 'missing_failure':
        _journal_change(root, lambda rows: rows.remove(next(row for row in reversed(rows)
            if row['type'] == 'model.failed' and row['payload'].get('purpose') is None)))
    elif damage == 'later_ordinary_success':
        def ordinary_success(rows):
            for row in rows:
                if row['type'] in {'model.started', 'model.failed'} and row['payload'].get('purpose') == 'context_compaction':
                    del row['payload']['purpose']
                    if row['type'] == 'model.failed':
                        row['type'] = 'model.completed'
        _journal_change(root, ordinary_success)
        _ledger_change(root, 3, lambda row: row.update(event='completed'))
    elif damage in {'partial_missing', 'error_message'}:
        messages = json.loads((root / 'implementation-checkpoint-messages.json').read_bytes())
        if damage == 'partial_missing':
            messages[-1].pop('_host_implementation_receipt')
        else:
            messages[-2]['content'] = 'Model API error (HTTPError): unrelated error'
        host = HostImplementationContinuation.capture(spec=spec, context=ToolContext(cwd=str(workspace)),
            messages=messages, executor_id=document['executorId'], attempt=document['attempt'],
            review_state=document['reviewState'])
        for name in ('implementation-checkpoint.json', 'implementation-checkpoint-messages.json',
                     'implementation-checkpoint-receipt.json'):
            (root / name).unlink()
        runner.checkpoint_writer(root)(host.document, messages)
    else:
        result = json.loads((source / 'results.json').read_bytes())
        if damage == 'scope':
            result['trials'][0]['scopePassed'] = False
        else:
            result['trials'][0]['judgement']['cleanupComplete'] = False
        write(source / 'results.json', result)
    output = tmp_path / 'refused'
    with pytest.raises((ValueError, FileNotFoundError)):
        runner.prepare(output, source, resume_model_failure=True)
    assert not output.exists()


def test_explicit_model_recovery_mode_cannot_be_combined_with_other_authorities(swallowed_failure, tmp_path):
    source, *_rest = swallowed_failure
    for values in ({'revalidate_approved': True}, {'resume_host_failure': True}, {'recover_host_bootstrap': True}):
        with pytest.raises(ValueError, match='only one'):
            runner.prepare(tmp_path / 'refused', source, resume_model_failure=True, **values)
