"""Explicit zero-provider host failure recovery through real native checkpoints."""
from copy import deepcopy
from dataclasses import asdict
import json
import socket

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import ContextManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.tooling import ToolRegistry
from minicode.tools.read_file import read_file_tool
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep
from scripts import run_requirement_completion_checkpoint as runner
from scripts.evaluation_transport import SharedLedger, TransportConfig
from test_completion_coverage_recovery_bridge import interrupted_audit  # noqa: F401
from test_requirement_completion_checkpoint import cloned_script_tree, seal_terminal, write
from test_requirement_completion_revalidation import approved, audit_step, inspection_step  # noqa: F401


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        pytest.fail('host failure tests cannot contact a model provider')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


@pytest.fixture
def host_failure(interrupted_audit, tmp_path, monkeypatch):  # noqa: F811
    import minicode.completion_coverage_audit as coverage

    source, original_root, workspace, original, spec, _messages, commands = interrupted_audit
    cloned_script_tree(tmp_path, monkeypatch)
    failed = tmp_path / 'host-failed'
    runner.prepare(failed, source)
    runner.freeze(failed)
    root = failed / 'private' / runner.TRIAL_ID
    root.mkdir()
    for name in ('workspace.json', 'baseline-files.json'):
        runner.private_write(root / name, (original_root / name).read_bytes())
    config = TransportConfig(ledger_path=str(root / 'transport.sqlite'), expected_model='glm-5.3-flash',
        expected_route_sha256='a' * 64, deadline_epoch=None, max_model_calls=None, max_total_tokens=None,
        max_output_tokens=131072, connect_timeout_seconds=30, idle_timeout_seconds=600, request_timeout_seconds=None)
    ledger = SharedLedger(config)
    write(root / 'transport-config.json', asdict(config))
    messages = json.loads((failed / 'private/parent-context.json').read_bytes())
    typed = runner.restored_continuation(failed, spec, messages)
    original_audit = coverage.audit_completion_coverage.__wrapped__
    failure = RuntimeError('generic local host initialization failure')

    def unavailable_audit(**_kwargs):
        raise failure

    monkeypatch.setattr(coverage, 'audit_completion_coverage', unavailable_audit)

    class NoProvider:
        model_id = 'local-host-failure'

        def next(self, *_args, **_kwargs):
            pytest.fail('the host failure must precede every model request')

    tools = ToolRegistry([read_file_tool, write_file_tool])
    journal = RunJournal(workspace, data_dir=root / 'journal')
    with pytest.raises(RuntimeError) as caught, runner.diagnostic.CompletionEvidenceArchive(root), observe_run(
            workspace=workspace, source='headless', title='generic host failure',
            journal_factory=lambda _: journal) as observation:
        run_agent_turn(model=NoProvider(), tools=tools, messages=messages, cwd=str(workspace),
            context_manager=ContextManager(model=NoProvider.model_id),
            runtime={'model': NoProvider.model_id, 'reflectionMode': 'rule'}, agent_budget=AgentTurnBudget(),
            implementation_spec=spec, implementation_task=True, implementation_continuation=typed,
            implementation_checkpoint_sink=runner.checkpoint_writer(root), event_sink=observation,
            max_steps=None, enable_work_chain=False, enable_implementation_phases=False, defer_curator_maintenance=True)
    assert caught.value is failure
    monkeypatch.setattr(coverage, 'audit_completion_coverage', original_audit)
    document = json.loads((root / 'implementation-checkpoint.json').read_bytes())
    assert document['executorId'] == original['executorId']
    assert document['reviewState']['pendingCoverageAudit'] == typed.document['reviewState']['pendingCoverageAudit']
    write(root / 'execution.json', {'errorClass': type(failure).__name__, 'durationSeconds': 1})
    write(root / 'error.json', {'class': type(failure).__name__, 'frames': []})
    write(root / 'process-cleanup.json', {'cleanupComplete': True})
    seal_terminal(failed, root)
    result = json.loads((failed / 'results.json').read_bytes())
    result['trials'][0].update(classification='not-delivered', scopePassed=True,
        unauthorizedChanges=[], verificationInducedChanges=[])
    write(failed / 'results.json', result)
    return failed, root, workspace, document, spec, commands, tools, ledger


def test_explicit_host_failure_preserves_native_state_and_resumes_audit(host_failure, tmp_path):
    failed, root, workspace, document, spec, commands, tools, _ledger = host_failure
    originals = {p: p.read_bytes() for p in failed.rglob('*') if p.is_file()}
    candidate = (workspace / 'example.py').read_bytes()
    output = tmp_path / 'resumed'
    with pytest.raises(ValueError, match='not an evidenced transport interruption'):
        runner.prepare(output, failed)
    assert not output.exists()
    runner.prepare(output, failed, resume_host_failure=True)
    contract, _, messages = runner.load_checkpoint(output)
    typed = runner.restored_continuation(output, spec, messages)
    assert typed.document == document and not typed.requires_revalidation
    assert contract['predecessorClassification'] == 'host-failed'
    assert contract['terminalEvidence']['predecessorClassification'] == 'not-delivered'
    assert contract['terminalEvidence']['originalErrorClass'] == 'RuntimeError'
    assert contract['terminalEvidence']['providerCalls'] == 0
    assert len(contract['predecessorLedgerRoots']) == 2
    assert (output / 'private/host-continuation.json').read_bytes() == (root / 'implementation-checkpoint.json').read_bytes()
    assert (output / 'private/parent-context.json').read_bytes() == (root / 'implementation-checkpoint-messages.json').read_bytes()
    runner.freeze(output)
    runner.validate(output)
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    before_checks = len(commands)
    roles, captures = [], []

    class ResumedModel:
        model_id = 'local-host-failure'
        calls = 0

        def next(self, actual, **kwargs):
            self.calls += 1
            if self.calls > 12:
                raise KeyboardInterrupt('local protocol failed to finish')
            names = set(kwargs['tools_override'].list_all())
            if not roles:
                assert 'completion_coverage_audit_submit' in names
                assert any(m.get('toolUseId') == 'retained-audit-page' for m in actual)
            assert 'completion_review_submit' not in names
            inspection = inspection_step(actual, kwargs['tools_override'])
            if inspection is not None:
                roles.append('inspection')
                return inspection
            if 'completion_coverage_audit_submit' in names:
                roles.append('coverage')
                return audit_step(actual)
            roles.append('parent')
            return AgentStep(type='assistant', kind='final', content='Public behavior verified.')

    run_agent_turn(model=ResumedModel(), tools=tools, messages=messages, cwd=str(workspace),
        context_manager=ContextManager(model=ResumedModel.model_id),
        runtime={'model': ResumedModel.model_id, 'reflectionMode': 'rule'}, agent_budget=AgentTurnBudget(),
        implementation_spec=spec, implementation_task=True, implementation_continuation=typed,
        implementation_checkpoint_sink=lambda doc, msgs: captures.append((deepcopy(doc), deepcopy(msgs))),
        max_steps=None, enable_work_chain=False, enable_implementation_phases=False, defer_curator_maintenance=True)
    assert captures[-1][0]['executorId'] == document['executorId']
    assert captures[-1][0]['reviewState']['reviews'][-1]['status'] == 'approved'
    assert len(commands) > before_checks
    assert (workspace / 'example.py').read_bytes() == candidate


@pytest.mark.parametrize('tamper', ['checkpoint', 'candidate', 'messages', 'provider_call',
                                  'execution_cleanup', 'judge_cleanup', 'scope', 'unfinished'])
def test_host_failure_rejects_unauthenticated_or_executed_state(host_failure, tmp_path, tamper):
    failed, root, workspace, _doc, _spec, _commands, _tools, ledger = host_failure
    if tamper == 'checkpoint':
        (root / 'implementation-checkpoint-receipt.json').unlink()
    elif tamper == 'candidate':
        (workspace / 'example.py').write_text('CURRENT = 3\n')
    elif tamper == 'messages':
        (root / 'implementation-checkpoint-messages.json').write_text('[]')
    elif tamper == 'provider_call':
        identity = ledger.start({'model': 'glm-5.3-flash'}, 1)
        ledger.update(identity, {'event': 'failed', 'errorClass': 'RuntimeError'})
    elif tamper == 'execution_cleanup':
        write(root / 'process-cleanup.json', {'cleanupComplete': False})
    elif tamper == 'unfinished':
        (failed / 'results.json').unlink()
    else:
        result = json.loads((failed / 'results.json').read_bytes())
        if tamper == 'judge_cleanup':
            result['trials'][0]['judgement']['cleanupComplete'] = False
        else:
            result['trials'][0]['scopePassed'] = False
        write(failed / 'results.json', result)
    output = tmp_path / 'refused'
    with pytest.raises((ValueError, FileNotFoundError)):
        runner.prepare(output, failed, resume_host_failure=True)
    assert not output.exists()
