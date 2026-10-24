"""Paid execution requires real host diagnostics, not just a passing exit code."""

import hashlib
import json
from pathlib import Path
import sys

import pytest

from scripts import public_verification_fixtures as preflight


def reporting_preflight_fixture(document, output, *, runtime=None, require_pytest_report=False):
    """Synthetic receipt for manifest binding only; never execution evidence."""
    from test_implementation_progress_live import _preflight_fixture
    report = _preflight_fixture(document, output, runtime=runtime)
    if require_pytest_report:
        report['pytestReportRequired'] = True
        for item in report['cases']:
            cases = [{'nodeId': 'test_public.py::test_baseline', 'outcome': 'passed', 'failureKind': None}]
            junit_digest = 'sha256:' + '1' * 64
            encoded = json.dumps({'junitDigest': junit_digest, 'cases': cases}, ensure_ascii=False,
                                 sort_keys=True, separators=(',', ':')).encode()
            item['pytestReport'] = {'collected': 1, 'passed': 1, 'failed': 0, 'errors': 0, 'skipped': 0,
                'reportDigest': junit_digest,
                'caseReport': {'digest': 'sha256:' + hashlib.sha256(encoded).hexdigest(), 'cases': cases}}
        (output / 'receipt.json').write_text(json.dumps(report))
    return report


def _document():
    return {'cases': [{'id': 'synthetic-public-smoke',
        'files': {'source.py': 'VALUE = 4\n',
                  'test_public.py': 'from source import VALUE\ndef test_baseline():\n    assert VALUE == 4\n'},
        'verification_argv': ['python3', '-m', 'pytest', '-q', 'test_public.py'],
        'authorized_paths': ['source.py', 'test_public.py']}]}


@pytest.mark.parametrize('mutation', ['missing_requirement', 'missing_report', 'missing_cases', 'bad_digest', 'failed_case'])
def test_required_preflight_rejects_absent_or_inconsistent_machine_evidence(tmp_path, mutation):
    document = _document()
    receipt = reporting_preflight_fixture(document, tmp_path, require_pytest_report=True)
    path = tmp_path / 'receipt.json'
    preflight.validate_public_preflight(document, path, hashlib.sha256(path.read_bytes()).hexdigest(),
                                        require_pytest_report=True)
    if mutation == 'missing_requirement':
        receipt.pop('pytestReportRequired')
    elif mutation == 'missing_report':
        receipt['cases'][0].pop('pytestReport')
    elif mutation == 'missing_cases':
        receipt['cases'][0]['pytestReport'].pop('caseReport')
    elif mutation == 'bad_digest':
        receipt['cases'][0]['pytestReport']['caseReport']['digest'] = 'sha256:' + '0' * 64
    else:
        receipt['cases'][0]['pytestReport']['passed'] = 0
    path.write_text(json.dumps(receipt))
    with pytest.raises(preflight.PublicFixtureError, match='incomplete or mismatched'):
        preflight.validate_public_preflight(document, path, hashlib.sha256(path.read_bytes()).hexdigest(),
                                            require_pytest_report=True)


def test_required_preflight_blocks_missing_report_even_if_command_reports_pass(tmp_path, monkeypatch):
    from minicode import workflow_runner

    class MissingReportVerifier:
        def run(self, **kwargs):
            check = kwargs['checks'][0]
            kwargs['evidence_sink']('public', {'status': 'passed', 'exitCode': 0, 'argv': list(check.argv)})
            kwargs['pytest_report_sink'](None)
            return {'status': 'passed'}

    monkeypatch.setattr(workflow_runner, '_deterministic_verifier_for_context',
                        lambda *_args, **_kwargs: MissingReportVerifier())
    receipt = preflight.run_public_preflight(_document(), tmp_path, require_pytest_report=True)
    assert receipt['passed'] is False
    assert receipt['cases'][0]['reason'] == 'host_pytest_report_unavailable'


@pytest.mark.skipif(sys.platform != 'darwin', reason='real macOS verification sandbox')
def test_real_preflight_runs_machine_report_through_scope_guard_and_sandbox(tmp_path, monkeypatch):
    from minicode.verifier_sandbox import DarwinSeatbeltVerifierSandbox

    # The host test environment provisions pytest; Apple's /usr/bin/python3
    # shim may select a different interpreter without it inside Seatbelt.
    executable = Path(sys.executable).resolve()
    monkeypatch.setenv('MINI_CODE_VERIFIER_TRUSTED_EXECUTABLES', f'python3={executable}')
    document = _document()
    document['cases'][0]['files']['test_public.py'] += (
        '    import sys\n    from pathlib import Path\n'
        f'    assert Path(sys.executable).resolve() == Path({str(executable)!r})\n'
        f'    assert sys.version_info[:3] == {tuple(sys.version_info[:3])!r}\n'
    )
    receipt = preflight.run_public_preflight(document, tmp_path,
        runtime={'_verifierSandboxBackend': DarwinSeatbeltVerifierSandbox()},
        require_pytest_report=True)
    assert receipt['passed'], receipt
    assert receipt['cases'][0]['evidence'][0]['resolvedExecutable'] == str(executable)
    assert receipt['cases'][0]['pytestReport']['caseReport']['cases'] == [
        {'nodeId': 'test_public.py::test_baseline', 'outcome': 'passed', 'failureKind': None}]
    path = tmp_path / 'receipt.json'
    preflight.validate_public_preflight(document, path, hashlib.sha256(path.read_bytes()).hexdigest(),
                                        require_pytest_report=True)
