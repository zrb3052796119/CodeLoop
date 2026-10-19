"""Generic explicit archive recovery: no project tasks, provider or hidden oracle."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import socket

import pytest

from scripts import run_requirement_completion_checkpoint as runner
from test_coverage_audit_recovery import _case


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*_args, **_kwargs):
        pytest.fail('coverage recovery must remain a local host operation')
    monkeypatch.setattr(socket.socket, 'connect', fail)
    monkeypatch.setattr(socket, 'create_connection', fail)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def archive_fixture(tmp_path):
    native = tmp_path / 'native'
    native.mkdir()
    case = _case(native)
    source = tmp_path / 'ancestor'
    root = source / 'private' / runner.TRIAL_ID
    root.mkdir(parents=True)
    runner.checkpoint_writer(root)(case['document'], case['parent_messages'])
    write(source / 'manifest.json', {'crossProcessContinuation': False})
    archived = root / 'completion-review/review_generic'
    archived.mkdir(parents=True)
    for _kind, path in case['records']:
        shutil.copyfile(path, archived / path.name)
    # The archive is opaque and includes unrelated successful host persistence.
    write(archived / '99999-observation.json', {'event': 'generic unrelated observation'})
    def seal():
        write(root / 'completion-review-archive.json', {'archiveVersion': 1,
            'archiveComplete': True, 'errors': [], 'files': [
                {'reviewId': 'review_generic', 'file': str(p.relative_to(root)),
                 'sha256': runner.sha_file(p), 'bytes': p.stat().st_size}
                for p in sorted(archived.iterdir())]})
    seal()
    return {**case, 'source': source, 'source_root': root, 'archived': archived, 'seal': seal}


def prepare(case, output, *, predecessor=None):
    output.mkdir()
    runner.private_json(output / 'private/host-continuation.json', case['document'])
    runner.private_json(output / 'private/parent-context.json', case['parent_messages'])
    predecessor = predecessor or case['source']
    contract = runner.prepare_coverage_recovery(output, case['source'], predecessor=predecessor,
        document=case['document'], spec=case['spec'], messages=case['parent_messages'])
    runner.private_json(output / 'checkpoint.json', {'predecessorDirectory': str(predecessor),
        'coverageAuditRecovery': contract})
    return contract


def restore(case, output):
    return runner.restored_continuation(output, case['spec'], case['parent_messages'])


def test_explicit_archive_recovery_keeps_original_bytes_and_recomputes_typed_overlay(tmp_path):
    from minicode.coverage_audit_state import record_document
    case = archive_fixture(tmp_path)
    original_files = {p: p.read_bytes() for p in case['source'].rglob('*') if p.is_file()}
    original_document = deepcopy(case['document'])
    output = tmp_path / 'prepared'
    contract = prepare(case, output)
    immutable = {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}
    first = restore(case, output)
    second = restore(case, output)
    assert first.document == second.document == original_document
    assert runner.recovered_host_document(output) == original_document
    pending = first.restore_review(case['spec']).state['pendingCoverageAudit']
    assert pending['messagesRecord'] == case['late']['messagesRecord']
    assert pending['inspectionRecord'] == case['late']['inspectionRecord']
    assert pending['authorityRecord'] == case['old_pending']['authorityRecord']
    pairs = record_document(pending['deliveryRecord'])['pairs']
    messages = record_document(case['late']['messagesRecord'])['messages']
    assert pairs == [{'call': messages[-2], 'result': messages[-1]}]
    assert pairs[0]['result']['isError'] is True
    assert 'System note:' in pairs[0]['result']['content']
    assert not (output / 'private/pending-coverage-audit.json').exists()
    descriptor = json.loads((output / contract['file']).read_bytes())
    assert descriptor['sourceDirectory'] == str(case['source'])
    assert set(descriptor['generatedRecords']) == set(runner._COVERAGE_RECOVERY_FILES)
    assert (output / contract['file']).stat().st_size < 6000
    receipt = json.loads((output / 'private/coverage-recovery-receipt.json').read_bytes())
    assert len(json.dumps(receipt)) < 7000 and 'rawJson' not in json.dumps(receipt)
    assert all(p.read_bytes() == raw for p, raw in original_files.items())
    assert all(p.read_bytes() == raw for p, raw in immutable.items())


def test_no_explicit_recovery_preserves_saved_snapshot(tmp_path):
    case = archive_fixture(tmp_path)
    output = tmp_path / 'ordinary'
    runner.private_json(output / 'private/host-continuation.json', case['document'])
    runner.private_json(output / 'checkpoint.json', {'predecessorDirectory': str(case['source'])})
    host = restore(case, output)
    assert host.restore_review(case['spec']).state['pendingCoverageAudit'] == case['old_pending']
    assert not (output / 'private/coverage-recovery.json').exists()


def test_selected_older_archive_keeps_the_immediate_predecessor_and_current_attempt(tmp_path):
    case = archive_fixture(tmp_path)
    immediate = tmp_path / 'immediate'
    write(immediate / 'checkpoint.json', {'predecessorDirectory': str(case['source']),
        'predecessorArtifactSha256': {'manifest.json': runner.sha_file(case['source'] / 'manifest.json')}})
    write(immediate / 'manifest.json', {'crossProcessContinuation': True,
        'predecessorDirectory': str(case['source']),
        'continuationCheckpointSha256': runner.sha_file(immediate / 'checkpoint.json')})
    case['document']['attempt'] += 1
    output = tmp_path / 'prepared'
    prepare(case, output, predecessor=immediate)
    restored = restore(case, output)
    assert restored.attempt == 3 and restored.document == case['document']
    checkpoint = json.loads((output / 'checkpoint.json').read_bytes())
    descriptor = json.loads((output / 'private/coverage-recovery.json').read_bytes())
    assert checkpoint['predecessorDirectory'] == str(immediate)
    assert descriptor['sourceDirectory'] == str(case['source'])
    assert runner.predecessor_chain(immediate) == [case['source'], immediate]


@pytest.mark.parametrize('mutation', ['foreign_ancestor', 'owner', 'candidate', 'checkpoint', 'receipt_owner_spoof',
    'unrelated_archive_bytes', 'unlisted_archive_file', 'terminal_decision', 'newest_same_audit_phase'])
def test_archive_authentication_rejects_wrong_lineage_tampering_or_terminal_state(tmp_path, mutation):
    from minicode.coverage_audit_state import record_document
    case = archive_fixture(tmp_path)
    source = case['source']
    predecessor = source
    receipt_path = case['source_root'] / 'implementation-checkpoint-receipt.json'
    if mutation == 'foreign_ancestor':
        predecessor = tmp_path / 'unrelated'
        write(predecessor / 'manifest.json', {'crossProcessContinuation': False})
    elif mutation in {'owner', 'candidate'}:
        receipt = json.loads(receipt_path.read_bytes())
        key = 'executorId' if mutation == 'owner' else 'candidateDigest'
        receipt[key] = 'implementation_' + 'd' * 32 if mutation == 'owner' else 'sha256:' + 'd' * 64
        write(receipt_path, receipt)
    elif mutation == 'checkpoint':
        with (case['source_root'] / 'implementation-checkpoint.json').open('a') as stream:
            stream.write(' ')
    elif mutation == 'receipt_owner_spoof':
        path = case['source_root'] / 'implementation-checkpoint.json'
        native = json.loads(path.read_bytes())
        native['executorId'] = 'implementation_' + 'd' * 32
        write(path, native)
        receipt = json.loads(receipt_path.read_bytes())
        receipt['documentSha256'] = runner.sha_file(path)
        write(receipt_path, receipt)
    elif mutation == 'unrelated_archive_bytes':
        write(case['archived'] / '99999-observation.json', {'event': 'changed unrelated record'})
    elif mutation == 'unlisted_archive_file':
        write(case['archived'] / '99998-observation.json', {'event': 'unlisted'})
    elif mutation == 'terminal_decision':
        write(case['archived'] / '99998-coverage-audit-decision.json',
            {'authorityRef': case['authority']['ref'], 'status': 'approved'})
        case['seal']()
    else:
        inspection = record_document(case['late']['inspectionRecord'])
        inspection['phase'] = 'reconciliation'
        write(case['archived'] / '99998-coverage-inspection.json', inspection)
        case['seal']()
    output = tmp_path / 'reject'
    output.mkdir()
    with pytest.raises(ValueError):
        runner.prepare_coverage_recovery(output, source, predecessor=predecessor,
            document=case['document'], spec=case['spec'], messages=case['parent_messages'])
    assert not (output / 'private/coverage-recovery.json').exists()


@pytest.mark.parametrize('target', ['descriptor', 'generated_delivery', 'generated_receipt', 'ancestor_archive'])
def test_read_only_reconstruction_rejects_changes_after_prepare(tmp_path, target):
    case = archive_fixture(tmp_path)
    output = tmp_path / 'prepared'
    prepare(case, output)
    path = {'descriptor': output / 'private/coverage-recovery.json',
        'generated_delivery': output / 'private/coverage-recovery-delivery.json',
        'generated_receipt': output / 'private/coverage-recovery-receipt.json',
        'ancestor_archive': case['archived'] / '99999-observation.json'}[target]
    with path.open('a') as stream:
        stream.write(' ')
    with pytest.raises(ValueError):
        restore(case, output)


def test_recovery_descriptor_cannot_coexist_with_other_rewriting_sidecars(tmp_path):
    case = archive_fixture(tmp_path)
    output = tmp_path / 'prepared'
    prepare(case, output)
    checkpoint = json.loads((output / 'checkpoint.json').read_bytes())
    checkpoint['approvedRevalidation'] = {'kind': 'not-a-recovery-input'}
    write(output / 'checkpoint.json', checkpoint)
    with pytest.raises(ValueError, match='invalid explicit coverage recovery contract'):
        runner.saved_coverage_recovery(output, spec=case['spec'], messages=case['parent_messages'])


def test_full_prepare_freeze_validate_pins_recovery_and_keeps_model_policy(tmp_path, monkeypatch):
    """Use real generic terminal/candidate/journal validation across all host stages."""
    from minicode.implementation_execution import implementation_candidate_digest
    from minicode.run_events import emit_event_safely
    from minicode.run_journal import RunJournal
    from minicode.run_lifecycle import observe_run
    from minicode.tooling import ToolContext
    repo = next(p for p in Path(__file__).resolve().parents
                if (p / 'tests/test_requirement_completion_checkpoint.py').is_file())
    module_spec = importlib.util.spec_from_file_location('coverage_harness_terminal_fixture',
        repo / 'tests/test_requirement_completion_checkpoint.py')
    fixtures = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(fixtures)
    source, root, workspace, _ = fixtures.terminal(tmp_path, error_class='KeyboardInterrupt', stop_decision=False)
    public_case = {'id': runner.diagnostic.CASE, 'prompt': '1. Increment a generic counter by one.',
        'authorized_paths': ['counter.py'], 'verification_argv': ['python3', '-m', 'pytest', '-q', 'test_public.py']}
    write(source / 'tasks.json', {'cases': [public_case], 'shared_files': {}})
    spec = runner.implementation_spec(public_case)
    (workspace / 'counter.py').write_text('def increment(value):\n    return value + 1\n')
    candidate = implementation_candidate_digest(spec, ToolContext(cwd=str(workspace), permissions=None))
    native = tmp_path / 'native-coverage'
    native.mkdir()
    case = _case(native, spec=spec, candidate=candidate, workspace=workspace)
    for name in ('implementation-checkpoint.json', 'implementation-checkpoint-messages.json',
                 'implementation-checkpoint-receipt.json'):
        (root / name).unlink()
    runner.checkpoint_writer(root)(case['document'], case['parent_messages'])
    shutil.rmtree(root / 'completion-review')
    archived = root / 'completion-review/review_generic'
    archived.mkdir(parents=True)
    for _kind, path in case['records']:
        shutil.copyfile(path, archived / path.name)
    write(root / 'completion-review-archive.json', {'archiveComplete': True, 'errors': [], 'files': [
        {'file': str(p.relative_to(root)), 'sha256': runner.sha_file(p), 'bytes': p.stat().st_size}
        for p in sorted(archived.iterdir())]})
    shutil.rmtree(root / 'journal')
    journal = RunJournal(workspace, data_dir=root / 'journal')
    with observe_run(workspace=workspace, source='headless', title='generic interrupted inspection',
                     journal_factory=lambda _: journal) as observation:
        for attempt in (1, 2):
            emit_event_safely(observation, 'implementation.completion_review_started', payload={
                'executorId': case['document']['executorId'], 'attempt': attempt, 'patchDigest': candidate})
            if attempt == 1:
                emit_event_safely(observation, 'implementation.completion_decided', payload={
                    'completionVersion': 1, 'executorId': case['document']['executorId'], 'attempt': 1,
                    'patchDigest': candidate, 'status': 'changes_required', 'canContinue': True,
                    'continuesSameExecutor': True, 'requirements': [], 'findings': []})
    write(source / 'checkpoint.json', {'identity': {'executorId': case['document']['executorId'], 'attempt': 1}})
    manifest = json.loads((source / 'manifest.json').read_bytes())
    manifest['taskSha256'] = runner.sha_file(source / 'tasks.json')
    write(source / 'manifest.json', manifest)
    fixtures.seal_terminal(source, root)
    originals = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    # Freeze actual sources plus the isolated proposed modules, not stubbed APIs.
    clone = tmp_path / 'code'
    overlay = Path(runner.__file__).resolve().parents[1]
    for path in (repo / 'minicode').rglob('*.py'):
        target = clone / path.relative_to(repo)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    for name in ('coverage_audit_recovery.py', 'implementation_continuation.py'):
        shutil.copyfile(overlay / 'minicode' / name, clone / 'minicode' / name)
    for name in runner.HARNESS_FILES:
        target = clone / 'scripts' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(overlay / 'scripts' / name, target)
    monkeypatch.setattr(runner, 'ROOT', clone)
    monkeypatch.setattr(runner, 'ENTRY', clone / 'scripts' / Path(runner.__file__).name)
    monkeypatch.setattr(runner.shared, 'ROOT', clone)
    monkeypatch.setattr(runner.diagnostic, 'ROOT', clone)
    monkeypatch.setattr(runner.diagnostic, 'ENTRY', clone / 'scripts/run_requirement_completion_diagnostic.py')
    output = tmp_path / 'prepared'
    runner.prepare(output, source, recover_coverage_audit_from=source)
    runner.freeze(output)
    frozen = runner.validate(output)
    checkpoint, inputs, saved_messages = runner.load_checkpoint(output)
    restored = runner.restored_continuation(output, spec, saved_messages)
    assert restored.document == case['document']
    assert restored.restore_review(spec).state['pendingCoverageAudit']['messagesRecord'] == case['late']['messagesRecord']
    assert checkpoint['predecessorLedgerRoots'] == [str(root)]
    assert frozen['model'] == 'glm-5.3-flash' and frozen['temperature'] == 0
    config = runner.segment_transport_config(output / 'not-run', {'model': 'glm-5.3-flash'}, frozen, 1)
    assert config.max_model_calls is config.max_total_tokens is config.deadline_epoch is None
    assert config.idle_timeout_seconds == 600 and config.connect_timeout_seconds == 30
    assert inputs['identity']['executorId'] == case['document']['executorId']
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    assert (output / 'private/host-continuation.json').read_bytes() == (root / 'implementation-checkpoint.json').read_bytes()
    with (output / 'private/coverage-recovery-delivery.json').open('a') as stream:
        stream.write(' ')
    with pytest.raises(ValueError, match='derived record changed'):
        runner.validate(output)
