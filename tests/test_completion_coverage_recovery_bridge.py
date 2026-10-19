"""Recover authenticated actual auditor context without rewriting parent history."""
from copy import deepcopy
from functools import wraps
import json
from pathlib import Path
import shutil
import socket

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import ContextManager
from minicode.implementation_completion_review import requirement_units
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.types import AgentStep
from scripts import run_requirement_completion_checkpoint as runner
from test_requirement_completion_checkpoint import cloned_script_tree, seal_terminal, write
from test_requirement_completion_revalidation import approved, audit_step, call, inspection_step, packet, submit  # noqa: F401


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        pytest.fail('coverage recovery bridge tests cannot contact a provider')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


@pytest.fixture
def interrupted_audit(approved, monkeypatch):  # noqa: F811
    import minicode.completion_coverage_audit as coverage

    source, root, workspace, original, messages, spec, commands, tools = approved
    old_messages = deepcopy(messages)
    continuation = HostImplementationContinuation.for_revalidation(original, spec=spec, messages=messages,
        request=runner.revalidation_request(original))
    for name in ('implementation-checkpoint.json', 'implementation-checkpoint-messages.json',
                 'implementation-checkpoint-receipt.json', 'completion-review-archive.json'):
        (root / name).unlink()
    shutil.rmtree(root / 'completion-review')
    shutil.rmtree(root / 'journal')
    original_audit = coverage.audit_completion_coverage

    @wraps(original_audit)
    def legacy_audit(**kwargs):
        # Reproduce the earlier host boundary: the real auditor persisted its
        # actual finally messages, but the parent had no native state sink yet.
        kwargs.pop('state_sink', None)
        return original_audit(**kwargs)

    monkeypatch.setattr(coverage, 'audit_completion_coverage', legacy_audit)
    failure = KeyboardInterrupt('interrupt only this local native auditor')

    class LocalModel:
        model_id = 'local-native-coverage-recovery'

        def next(self, actual, **kwargs):
            inspection = inspection_step(actual, kwargs['tools_override'])
            if inspection is not None:
                return inspection
            names = set(kwargs['tools_override'].list_all())
            if 'completion_coverage_audit_submit' in names:
                if any(m.get('toolUseId') == 'retained-audit-page' for m in actual):
                    raise failure
                authority = next(json.loads(m['content']) for m in actual
                    if m.get('role') == 'user' and '"evidenceIndex"' in m.get('content', ''))
                return call('retained-audit-page', 'review_evidence_read',
                    documentId=authority['evidenceIndex'][0]['sourceDocumentId'])
            if 'completion_review_submit' in names:
                current = packet(actual)
                return submit(current['retainedProbes'][0]['probeId'],
                    [row['id'] for row in requirement_units(spec.objective)])
            pytest.fail('explicit revalidation must enter the independent review first')

    journal = RunJournal(workspace, data_dir=root / 'journal')
    with pytest.raises(KeyboardInterrupt) as caught, runner.diagnostic.CompletionEvidenceArchive(root), observe_run(
            workspace=workspace, source='headless', title='interrupted independent audit',
            journal_factory=lambda _: journal) as observation:
        run_agent_turn(model=LocalModel(), tools=tools, messages=messages, cwd=str(workspace),
            context_manager=ContextManager(model=LocalModel.model_id),
            runtime={'model': LocalModel.model_id, 'reflectionMode': 'rule'}, agent_budget=AgentTurnBudget(),
            implementation_spec=spec, implementation_task=True, implementation_continuation=continuation,
            implementation_checkpoint_sink=runner.checkpoint_writer(root), event_sink=observation,
            max_steps=None, enable_work_chain=False, enable_implementation_phases=False, defer_curator_maintenance=True)
    assert caught.value is failure
    document = json.loads((root / 'implementation-checkpoint.json').read_bytes())
    assert document['executorId'] == original['executorId']
    assert document['attempt'] == original['attempt'] + 1
    assert not document['reviewState'].get('pendingCoverageAudit')
    write(root / 'execution.json', {'errorClass': 'KeyboardInterrupt', 'durationSeconds': 1})
    write(root / 'error.json', {'class': 'KeyboardInterrupt', 'frames': []})
    seal_terminal(source, root)
    return source, root, workspace, document, spec, old_messages, commands


def test_prepare_recovers_actual_coverage_context_and_preserves_native_bytes(interrupted_audit, tmp_path, monkeypatch):
    source, root, workspace, original, spec, _old_messages, _commands = interrupted_audit
    originals = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    candidate = (workspace / 'example.py').read_bytes()
    cloned_script_tree(tmp_path, monkeypatch)
    output = tmp_path / 'next'
    runner.prepare(output, source)
    checkpoint, _, messages = runner.load_checkpoint(output)
    assert checkpoint['coverageAuditRestoration']['kind'] == 'actual_native_coverage_audit_context'
    restored = runner.restored_continuation(output, spec, messages)
    pending = restored.document['reviewState']['pendingCoverageAudit']
    assert pending['status'] == 'pending'
    assert pending['coverageAuditStateVersion'] == 3
    inspection = json.loads(pending['inspectionRecord']['rawJson'])
    assert inspection['phase'] == 'reconciliation'
    assert inspection['result']['status'] == 'ready_for_evidence'
    directory = runner.verify_archive(root)
    for key in ('inspectionRecord', 'deliveryRecord', 'messagesRecord'):
        retained = pending[key]
        archived = directory / Path(retained['ref']).name
        assert retained['rawJson'].encode() == archived.read_bytes()
        assert retained['sha256'] == 'sha256:' + runner.sha_file(archived)
    assert any(pair['call']['toolUseId'] == 'retained-audit-page'
               for pair in json.loads(pending['deliveryRecord']['rawJson'])['pairs'])
    actual = json.loads(pending['messagesRecord']['rawJson'])['messages']
    assert any(m.get('role') == 'tool_result' and m.get('toolUseId') == 'retained-audit-page' for m in actual)
    assert (output / 'private/host-continuation.json').read_bytes() == (root / 'implementation-checkpoint.json').read_bytes()
    assert (output / 'private/parent-context.json').read_bytes() == (root / 'implementation-checkpoint-messages.json').read_bytes()
    assert restored.document['executorId'] == original['executorId']
    assert restored.document['messagesDigest'] == original['messagesDigest']
    assert (output / 'private/pending-coverage-audit.json').stat().st_mode & 0o777 == 0o600
    runner.freeze(output)
    runner.validate(output)
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert (workspace / 'example.py').read_bytes() == candidate
    import minicode.completion_coverage_audit as coverage
    monkeypatch.setattr(coverage, 'audit_completion_coverage', coverage.audit_completion_coverage.__wrapped__)
    captured, roles = [], []
    retained = next(m for m in actual if m.get('role') == 'tool_result'
        and m.get('toolUseId') == 'retained-audit-page')

    class ResumedModel:
        model_id = 'local-native-coverage-recovery'
        calls = 0

        def next(self, current, **kwargs):
            self.calls += 1
            if self.calls > 12:
                raise KeyboardInterrupt('local recovery protocol did not terminate')
            names = set(kwargs['tools_override'].list_all())
            if not roles:
                assert 'completion_coverage_audit_submit' in names
                assert not {'write_file', 'completion_review_submit'} & names
                assert retained in current, 'The actual delivered auditor tool result must survive.'
            assert 'completion_review_submit' not in names, 'Do not regenerate the ordinary author proposal.'
            if 'completion_coverage_audit_submit' in names:
                roles.append('coverage')
                return audit_step(current)
            roles.append('parent')
            return AgentStep(type='assistant', kind='final', content='Public requirements are verified.')

    from minicode.tooling import ToolRegistry
    from minicode.tools.read_file import read_file_tool
    from minicode.tools.write_file import write_file_tool
    run_agent_turn(model=ResumedModel(), tools=ToolRegistry([read_file_tool, write_file_tool]), messages=messages,
        cwd=str(workspace), context_manager=ContextManager(model=ResumedModel.model_id),
        runtime={'model': ResumedModel.model_id, 'reflectionMode': 'rule'}, agent_budget=AgentTurnBudget(),
        implementation_spec=spec, implementation_task=True, implementation_continuation=restored,
        implementation_checkpoint_sink=lambda doc, msgs: captured.append((deepcopy(doc), deepcopy(msgs))),
        max_steps=None, enable_work_chain=False, enable_implementation_phases=False, defer_curator_maintenance=True)
    assert roles[0] == 'coverage'
    assert captured[-1][0]['executorId'] == original['executorId']
    assert captured[-1][0]['reviewState']['reviews'][-1]['status'] == 'approved'
    assert captured[-1][0]['reviewState'].get('pendingCoverageAudit') is None
    assert (workspace / 'example.py').read_bytes() == candidate


def reseal_archive(root):
    manifest = json.loads((root / 'completion-review-archive.json').read_bytes())
    directory = root / Path(manifest['files'][0]['file']).parent
    review_id = manifest['files'][0]['reviewId']
    manifest['files'] = [{'file': str(path.relative_to(root)), 'reviewId': review_id,
        'sha256': runner.sha_file(path), 'bytes': path.stat().st_size} for path in sorted(directory.iterdir())]
    write(root / 'completion-review-archive.json', manifest)


@pytest.mark.parametrize('tamper', ['archive_bytes', 'audit_identity', 'documents', 'duplicate_documents',
                                  'sidecar', 'candidate', 'parent_messages'])
def test_coverage_bridge_rejects_unbound_or_changed_context(interrupted_audit, tmp_path, tamper):
    source, root, workspace, _original, _spec, _messages, _commands = interrupted_audit
    output = tmp_path / 'next'
    if tamper == 'sidecar':
        runner.prepare(output, source)
        path = output / 'private/pending-coverage-audit.json'
        value = json.loads(path.read_bytes())
        # Even a new sidecar hash cannot authorize invented child messages.
        value['messagesRecord']['rawJson'] += ' '
        write(path, value)
        checkpoint = json.loads((output / 'checkpoint.json').read_bytes())
        checkpoint['coverageAuditRestoration']['sha256'] = runner.sha_file(path)
        write(output / 'checkpoint.json', checkpoint)
        with pytest.raises(ValueError, match='actual child context'):
            runner.load_checkpoint(output)
        return
    if tamper == 'candidate':
        (workspace / 'example.py').write_text('CURRENT = 3\n')
    elif tamper == 'parent_messages':
        (root / 'implementation-checkpoint-messages.json').write_text('[]')
    else:
        directory = runner.verify_archive(root)
        if tamper in {'archive_bytes', 'audit_identity'}:
            path = sorted(directory.glob('*-coverage-audit-messages.json'))[-1]
            value = json.loads(path.read_bytes())
            value['auditId'] = 'coverage_audit_' + '0' * 24
            write(path, value)
        else:
            path = next(directory.glob('*-coverage-audit-evidence-documents.json'))
            if tamper == 'duplicate_documents':
                # Keep it between authority and the final message by allocating
                # an unused fractional slot from an existing observation name.
                observation = next(p for p in directory.glob('*-coverage-audit-observation.json')
                    if p.name > path.name)
                target = directory / (observation.name.split('-')[0] + '-coverage-audit-evidence-documents.json')
                target.write_bytes(path.read_bytes())
            else:
                value = json.loads(path.read_bytes())
                value['proposalDigest'] = 'sha256:' + '0' * 64
                write(path, value)
        if tamper != 'archive_bytes':
            reseal_archive(root)
    with pytest.raises(ValueError):
        runner.prepare(output, source)
    assert not output.exists()


def test_existing_native_coverage_state_wins_without_replacing_context(interrupted_audit):
    _source, root, _workspace, document, _spec, _messages, _commands = interrupted_audit
    pending = runner.recover_coverage_audit(root, document)
    assert pending is not None
    document['reviewState']['pendingCoverageAudit'] = pending
    before = deepcopy(document)
    assert runner.recover_coverage_audit(root, document) is None
    assert document == before


@pytest.mark.parametrize('with_delivery', [False, True])
def test_true_v1_archive_without_inspection_sidecars_remains_compatible(tmp_path, with_delivery):
    old = json.loads((Path(__file__).parent / 'fixtures/coverage_inspection_legacy.json').read_bytes())['state']
    assert old['coverageAuditStateVersion'] == 1
    if not with_delivery:
        old.pop('deliveryRecord')
    root = tmp_path / 'archive'
    directory = root / 'completion-review' / 'review_fixture'
    directory.mkdir(parents=True)
    files = []
    for key in ('authorityRecord', 'documentsRecord', 'messagesRecord', 'deliveryRecord'):
        if key not in old:
            continue
        saved = old[key]
        path = directory / Path(saved['ref']).name
        path.write_bytes(saved['rawJson'].encode())
        files.append({'file': str(path.relative_to(root)), 'reviewId': 'review_fixture',
                      'sha256': runner.sha_file(path), 'bytes': path.stat().st_size})
    write(root / 'completion-review-archive.json', {'archiveComplete': True, 'errors': [], 'files': files})
    authority = json.loads(old['authorityRecord']['rawJson'])
    document = {'candidateDigest': old['candidateDigest'], 'reviewState': {
        'objective': authority['originalObjective'],
        'evidenceDirectory': str(Path(old['authorityRecord']['ref']).parent)}}
    assert runner.recover_coverage_audit(root, document) == old
