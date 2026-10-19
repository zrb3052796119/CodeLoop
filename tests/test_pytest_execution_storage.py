"""Real isolated execution plus private-storage corruption counterexamples."""
from copy import deepcopy
import json
import os

import pytest

from minicode.completion_behavior_evidence import executed_witness, source_definitions
from minicode.deterministic_verifier import _attach_pytest_case_report
from minicode.pytest_execution_evidence import _digest, observed_assertions, write_execution_report
from tests.test_pytest_execution_evidence import PRODUCT, execute


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def test_later_unrelated_calls_cannot_erase_executed_consumer_and_assertion(tmp_path, monkeypatch):
    product = PRODUCT + '\n'.join(f'\ndef noise_{i:04d}():\n    return {i}\n' for i in range(3000))
    code = ('from stock import Store, release\nimport stock\ndef test_stock():\n'
            '    assert release(Store(), 2, 3) == 5\n'
            '    for index in range(3000):\n'
            '        getattr(stock, f"noise_{index:04d}")()\n'
            '    assert True\n')
    receipt = execute(tmp_path, monkeypatch, code, product=product)
    report = receipt['testReport']
    execution = report['executionReport']
    assert len(canonical(execution)) > 1024 * 1024
    assert report['passed'] == 1 and report['failed'] == 0
    assert execution['cases'][0]['complete'] is True
    assert execution['cases'][0]['limitations'] == []
    definitions = source_definitions(tmp_path, {'release', 'Store.save'})
    matches, error = executed_witness(
        receipt, {'sourceDigest': receipt['sourceDigest']}, 4, 4,
        symbols=['release', 'Store.save'], definitions=definitions, exact=True)
    assert error is None and matches


def test_actual_collection_overflow_retains_original_but_grants_no_witness(tmp_path, monkeypatch):
    product = PRODUCT + '\n'.join(f'\ndef noise_{i:04d}():\n    return {i}\n' for i in range(4200))
    code = ('from stock import Store, release\nimport stock\ndef test_stock():\n'
            '    assert release(Store(), 2, 3) == 5\n'
            '    for index in range(4200):\n'
            '        getattr(stock, f"noise_{index:04d}")()\n')
    receipt = execute(tmp_path, monkeypatch, code, product=product)
    report = receipt['testReport']
    case = report['executionReport']['cases'][0]
    assert report['passed'] == 1 and report['failed'] == 0
    assert len(case['calls']) == 4096 and case['assertions']
    assert case['complete'] is False and case['limitations'] == ['call_report_capacity']
    assert observed_assertions(report, source_digest=receipt['sourceDigest'], start_line=4, end_line=4) == []
    definitions = source_definitions(tmp_path, {'release', 'Store.save'})
    matches, error = executed_witness(
        receipt, {'sourceDigest': receipt['sourceDigest']}, 4, 4,
        symbols=['release', 'Store.save'], definitions=definitions, exact=True)
    assert not matches and error


@pytest.fixture(scope='module')
def genuine_receipt(tmp_path_factory):
    with pytest.MonkeyPatch.context() as monkeypatch:
        return execute(tmp_path_factory.mktemp('execution-storage'), monkeypatch,
            'from stock import Store, release\ndef test_stock():\n    assert release(Store(), 2, 3) == 5\n')


def stored_report(tmp_path, receipt, *, execution=None):
    report = receipt['testReport']
    path = tmp_path / 'cases.json'
    path.write_bytes(canonical({'junitDigest': report['reportDigest'], 'cases': report['caseReport']['cases']}))
    manifest = write_execution_report(execution or report['executionReport'], path)
    counts = {key: value for key, value in report.items() if key not in ('caseReport', 'executionReport')}
    return path, counts, manifest


@pytest.mark.parametrize('tamper', ['missing', 'bytes', 'short', 'extra', 'symlink', 'hardlink',
    'manifest-json', 'manifest-noncanonical', 'manifest-extra', 'manifest-count', 'manifest-size',
    'manifest-digest', 'manifest-junit', 'manifest-execution'])
def test_private_shard_corruption_cannot_override_real_case_results(tmp_path, genuine_receipt, tamper):
    path, counts, manifest = stored_report(tmp_path, genuine_receipt)
    part = path.with_name(path.name + '.execution-0.jsonpart')
    manifest_path = path.with_name(path.name + '.execution.json')
    if tamper == 'missing':
        part.unlink()
    elif tamper == 'bytes':
        data = part.read_bytes()
        part.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    elif tamper == 'short':
        part.write_bytes(part.read_bytes()[:-1])
    elif tamper == 'extra':
        part.write_bytes(part.read_bytes() + b' ')
    elif tamper in ('symlink', 'hardlink'):
        other = tmp_path / 'other'
        if tamper == 'symlink':
            part.rename(other)
            part.symlink_to(other)
        else:
            os.link(part, other)
    elif tamper == 'manifest-json':
        manifest_path.write_bytes(b'{')
    elif tamper == 'manifest-noncanonical':
        manifest_path.write_bytes(b' ' + canonical(manifest))
    else:
        if tamper == 'manifest-extra':
            manifest['path'] = '../untrusted'
        elif tamper == 'manifest-count':
            manifest['chunkCount'] += 1
        elif tamper == 'manifest-size':
            manifest['byteCount'] += 1
        else:
            field = {'manifest-digest': 'digest', 'manifest-junit': 'junitDigest',
                     'manifest-execution': 'executionDigest'}[tamper]
            manifest[field] = 'sha256:' + 'f' * 64
        manifest_path.write_bytes(canonical(manifest))
    loaded = _attach_pytest_case_report(counts, path)
    assert loaded['reportDigest'] == genuine_receipt['testReport']['reportDigest']
    assert loaded['caseReport'] == genuine_receipt['testReport']['caseReport']
    assert loaded['passed'] == 1
    assert 'executionReport' not in loaded


@pytest.mark.parametrize('tamper', ['junit', 'missing-parent', 'execution-digest'])
def test_valid_storage_hash_cannot_replace_execution_binding(tmp_path, genuine_receipt, tamper):
    execution = deepcopy(genuine_receipt['testReport']['executionReport'])
    if tamper == 'junit':
        execution['junitDigest'] = 'sha256:' + 'b' * 64
    elif tamper == 'missing-parent':
        calls = execution['cases'][0]['calls']
        child = next(call for call in calls if call['parentCallId'] is not None)
        execution['cases'][0]['calls'] = [call for call in calls if call['callId'] != child['parentCallId']]
    execution['digest'] = _digest({key: execution[key] for key in ('version', 'junitDigest', 'cases')})
    if tamper == 'execution-digest':
        execution['digest'] = 'sha256:' + 'c' * 64
    path, counts, _manifest = stored_report(tmp_path, genuine_receipt, execution=execution)
    loaded = _attach_pytest_case_report(counts, path)
    assert loaded['caseReport'] == genuine_receipt['testReport']['caseReport']
    assert 'executionReport' not in loaded


def test_storage_preserves_incomplete_capture_and_legacy_inline_receipts(tmp_path, genuine_receipt):
    execution = deepcopy(genuine_receipt['testReport']['executionReport'])
    execution['cases'][0].update(complete=False, limitations=['trace_hook_replaced'])
    execution['digest'] = _digest({key: execution[key] for key in ('version', 'junitDigest', 'cases')})
    path, counts, _manifest = stored_report(tmp_path, genuine_receipt, execution=execution)
    loaded = _attach_pytest_case_report(counts, path)
    assert loaded['executionReport'] == execution
    assert observed_assertions(loaded, source_digest=genuine_receipt['sourceDigest'], start_line=1, end_line=3) == []
    original = genuine_receipt['testReport']
    legacy = tmp_path / 'legacy.json'
    legacy.write_bytes(canonical({'junitDigest': original['reportDigest'],
        'cases': original['caseReport']['cases'], 'executionReport': original['executionReport']}))
    assert _attach_pytest_case_report(counts, legacy) == original
