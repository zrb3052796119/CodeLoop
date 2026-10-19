"""Generic pre-transport retry lineage without invented native state or ledgers."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from scripts import run_requirement_completion_checkpoint as runner


REPO = next(path for path in Path(__file__).resolve().parents if (path / '.git').exists())
spec = importlib.util.spec_from_file_location('startup_native_fixtures', REPO / 'tests/test_requirement_completion_checkpoint.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
write = fixtures.write


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def fail(*_args, **_kwargs):
        pytest.fail('startup lineage tests cannot request a real model or network')
    monkeypatch.setattr(socket.socket, 'connect', fail)
    monkeypatch.setattr(socket, 'create_connection', fail)


def prepared(tmp_path, monkeypatch):
    source, original_root, workspace, host = fixtures.terminal(tmp_path,
        error_class='KeyboardInterrupt', stop_decision=False)
    monkeypatch.setattr(fixtures, 'DRAFT', Path(runner.__file__))
    clone = fixtures.cloned_script_tree(tmp_path, monkeypatch)
    monkeypatch.setattr(runner.shared, 'ROOT', clone)
    monkeypatch.setattr(runner.diagnostic, 'ROOT', clone)
    monkeypatch.setattr(runner.diagnostic, 'ENTRY', clone / 'scripts/run_requirement_completion_diagnostic.py')
    output = tmp_path / 'failed-startup'
    runner.prepare(output, source)
    runner.freeze(output)
    return source, original_root, workspace, host, output


def terminal(output, *, seconds=31.0):
    root = output / 'private' / runner.TRIAL_ID
    root.mkdir(parents=True, exist_ok=True)
    cleanup = {'rootPid': 999001, 'cleanupComplete': True, 'freezeComplete': True,
        'killConfirmComplete': True, 'survivorPids': [], 'unconfirmedPids': [],
        'evidenceErrors': [], 'processScanErrors': [], 'identityMismatches': []}
    write(root / 'process-cleanup.json', cleanup)
    row = {'id': runner.TRIAL_ID, 'success': False, 'errorClass': 'HardTimeout',
        'modelCalls': 0, 'processSeconds': seconds, 'processCleanup': cleanup, 'judgement': None}
    write(output / 'trials' / (runner.TRIAL_ID + '.json'), row)
    return row


def test_explicit_startup_retry_preserves_failed_segment_and_original_native_owner(tmp_path, monkeypatch):
    source, original_root, _workspace, host, failed = prepared(tmp_path, monkeypatch)
    terminal(failed)
    originals = {path: path.read_bytes() for path in failed.rglob('*') if path.is_file()}
    receipt = runner.startup_failure_evidence(failed)
    assert receipt['physicalLedgerCreated'] is receipt['nativeCheckpointCreated'] is False
    assert receipt['entrypointProof']['startMarkerLine'] < receipt['entrypointProof']['transportConstructorLine']
    next_output = tmp_path / 'retry'
    runner.prepare(next_output, source, startup_failure_from=failed)
    runner.freeze(next_output)
    runner.validate(next_output)
    checkpoint, inputs, messages = runner.load_checkpoint(next_output)
    assert checkpoint['predecessorDirectory'] == str(source)
    assert checkpoint['predecessorLedgerRoots'] == [str(original_root)]
    assert checkpoint['startupFailureSegments'] == [receipt]
    assert runner.inherited_startup_failures([next_output]) == [receipt]
    assert runner.restored_continuation(next_output, runner.implementation_spec(inputs['case']), messages).document == host.document
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert not (failed / 'private' / runner.TRIAL_ID / 'transport.sqlite').exists()
    assert not (failed / 'private' / runner.TRIAL_ID / 'implementation-checkpoint.json').exists()
    metrics = runner.combined_metering([original_root], failed / 'private' / runner.TRIAL_ID,
        current_startup_failure=receipt)
    assert len(metrics['segments']) == len(metrics['startupFailureSegments']) == 1
    assert metrics['startupFailureDurationSeconds'] == 31.0
    assert metrics['durationSeconds'] == 41.0
    assert metrics['modelCalls'] == 2  # One measured call plus one unknown predecessor call.
    assert metrics['unknownUsageCalls'] == 1
    assert metrics['startupFailureSegments'][0]['modelCalls'] == 0
    with (failed / 'private' / runner.TRIAL_ID / 'process-cleanup.json').open('a') as stream:
        stream.write(' ')
    with pytest.raises(ValueError, match='startup failure cumulative lineage changed'):
        runner.load_checkpoint(next_output)


@pytest.mark.parametrize('mutation', ['ledger_only_missing', 'workspace_entered', 'execution_started',
    'cleanup_incomplete', 'model_observed', 'transport_before_marker', 'different_prepared_parent'])
def test_missing_ledger_alone_never_authorizes_zero_usage_or_retry(tmp_path, monkeypatch, mutation):
    source, original_root, _workspace, _host, failed = prepared(tmp_path, monkeypatch)
    terminal(failed)
    root = failed / 'private' / runner.TRIAL_ID
    if mutation == 'ledger_only_missing':
        with pytest.raises(ValueError, match='missing its physical ledger'):
            runner.combined_metering([original_root], root)
        return
    if mutation in {'workspace_entered', 'execution_started'}:
        write(root / ('workspace.json' if mutation == 'workspace_entered' else 'execution-start.json'), {})
    elif mutation == 'cleanup_incomplete':
        cleanup = json.loads((root / 'process-cleanup.json').read_bytes())
        cleanup['cleanupComplete'] = False
        write(root / 'process-cleanup.json', cleanup)
    elif mutation == 'model_observed':
        path = failed / 'trials' / (runner.TRIAL_ID + '.json')
        row = json.loads(path.read_bytes())
        row['modelCalls'] = 1
        write(path, row)
    elif mutation == 'transport_before_marker':
        archive_path = failed / 'frozen-code.zip'
        with zipfile.ZipFile(archive_path) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        name = 'scripts/run_requirement_completion_checkpoint.py'
        text = entries[name].decode()
        text = text.replace('    transport = EvaluationTransport(transport_config)\n', '')
        text = text.replace('    write_json(root / "execution-start.json",',
            '    transport = EvaluationTransport(transport_config)\n    write_json(root / "execution-start.json",')
        entries[name] = text.encode()
        with zipfile.ZipFile(archive_path, 'w') as archive:
            for name, raw in entries.items():
                archive.writestr(name, raw)
        manifest = json.loads((failed / 'manifest.json').read_bytes())
        manifest['frozenCodeArchiveSha256'] = runner.sha_file(archive_path)
        write(failed / 'manifest.json', manifest)
    else:
        document_path = failed / 'private/parent-context.json'
        with document_path.open('a') as stream:
            stream.write(' ')
    with pytest.raises(ValueError):
        runner.prepare(tmp_path / 'rejected', source, startup_failure_from=failed)


def test_parent_finalizes_pretransport_failure_without_inventing_execution_or_ledger(tmp_path, monkeypatch):
    source, original_root, _workspace, _host, output = prepared(tmp_path, monkeypatch)
    root = output / 'private' / runner.TRIAL_ID
    state = {'results': 0}
    def wait(_proc, actual_root, **kwargs):
        assert actual_root == root and kwargs['setup_seconds'] is None
        terminal(output)
        return -9
    def finalize(_output, _trial, **kwargs):
        return terminal(output, seconds=kwargs['process_seconds'])
    def checkpoint_results(_output, _manifest):
        state['results'] += 1
    service = SimpleNamespace(wait_trial_process=wait, finalize_trial=finalize, checkpoint_results=checkpoint_results)
    monkeypatch.setattr(runner, 'services', lambda _: service)
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *_a, **_k: SimpleNamespace(pid=999001))
    row = runner.run_suite(output)
    assert row['classification'] == 'startup-failed' and row['transportInitialized'] is False
    assert row['success'] is False and state['results'] == 1
    assert row['predecessorDirectory'] == str(source)
    assert row['cumulativeMetering']['segments'][0]['root'] == str(original_root)
    assert len(row['cumulativeMetering']['startupFailureSegments']) == 1
    assert not (root / 'transport.sqlite').exists() and not (root / 'execution.json').exists()
    assert not (root / 'implementation-checkpoint.json').exists()
    # The final row can be authenticated after aggregation; no circular trial hash.
    assert runner.startup_failure_evidence(output)['errorClass'] == 'HardTimeout'


def test_second_pretransport_failure_retains_both_attempts_without_native_predecessor_fabrication(tmp_path, monkeypatch):
    source, original_root, _workspace, _host, first = prepared(tmp_path, monkeypatch)
    terminal(first, seconds=31.0)
    second = tmp_path / 'second-failed-startup'
    runner.prepare(second, source, startup_failure_from=first)
    runner.freeze(second)
    terminal(second, seconds=32.0)
    third = tmp_path / 'third-attempt'
    runner.prepare(third, source, startup_failure_from=second)
    checkpoint, _, _ = runner.load_checkpoint(third)
    assert checkpoint['predecessorDirectory'] == str(source)
    assert checkpoint['predecessorLedgerRoots'] == [str(original_root)]
    failures = checkpoint['startupFailureSegments']
    assert [row['sourceDirectory'] for row in failures] == [str(first), str(second)]
    assert sum(row['durationSeconds'] for row in failures) == 63.0
    assert runner.inherited_startup_failures([third]) == failures
    for output in (first, second):
        root = output / 'private' / runner.TRIAL_ID
        assert not (root / 'transport.sqlite').exists()
        assert not (root / 'implementation-checkpoint.json').exists()
