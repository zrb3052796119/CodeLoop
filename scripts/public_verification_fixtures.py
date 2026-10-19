"""Fixed public benchmark inputs, never permission to copy runtime state.

The closed registration below identifies one already-published project fixture.
Bytes are bound from the immutable task archive, not read from a working tree.
The same verifier boundary covers direct feedback, Executor feedback and the
independent final Verifier. Fixture chmod is not OS-enforced write protection;
the existing Verifier process, network and filesystem-scope isolation remains.
"""
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
from unittest.mock import patch

_CASE = 'real-vision-budget'
_PATH = '.mini-code/agents/visual-judge.md'
_SHA256 = '112882e006b335964130255ed339fd28c54c847376e31d82faccf6a8205e4828'
PUBLIC_FIXTURE_POLICY = {
    'version': 'archived-public-verification-fixtures-v1',
    'registered': [{'case': 'real-vision-budget', 'path': '.mini-code/agents/visual-judge.md',
                    'sha256': '112882e006b335964130255ed339fd28c54c847376e31d82faccf6a8205e4828', 'bytes': 1430}],
    'source': 'frozen-task-public-files-only',
    'scope': 'disposable-verification-workspace-only',
    'requiresBeforeAfterDigest': True,
    'modelSelectable': False,
    'promotable': False,
}


class PublicFixtureError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublicVerificationFixture:
    case: str
    path: str
    sha256: str
    content: bytes

    def __post_init__(self):
        if ((self.case, self.path, self.sha256) != (_CASE, _PATH, _SHA256)
                or type(self.content) is not bytes or len(self.content) != 1430
                or hashlib.sha256(self.content).hexdigest() != self.sha256):
            raise PublicFixtureError('public fixture is not the registered archived input')

    def manifest(self):
        return {'case': self.case, 'path': self.path, 'sha256': self.sha256, 'bytes': len(self.content)}


def bind_public_fixtures(document: dict, case_id: str) -> tuple[PublicVerificationFixture, ...]:
    case = next((item for item in document['cases'] if item['id'] == case_id), None)
    if case is None:
        raise PublicFixtureError('public fixture case is not in the frozen task archive')
    if case_id != _CASE:
        return ()
    content = {**document.get('shared_files', {}), **case['files']}.get(_PATH)
    if not isinstance(content, str):
        raise PublicFixtureError('archived public fixture is missing')
    return (PublicVerificationFixture(case_id, _PATH, _SHA256, content.encode('utf-8')),)


def public_fixture_manifest(document: dict) -> list[dict]:
    return [fixture.manifest() for case in document['cases']
            for fixture in bind_public_fixtures(document, case['id'])]


def _fixture_digest(fixtures):
    return hashlib.sha256(json.dumps([f.manifest() for f in fixtures], sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _require_posix_fixture_io():
    if (os.name != 'posix' or not hasattr(os, 'O_DIRECTORY')
            or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_NONBLOCK')
            or os.open not in getattr(os, 'supports_dir_fd', ())):
        raise PublicFixtureError('public fixture projection requires POSIX no-follow descriptor-relative IO')


def _put_fixtures(cwd: Path, fixtures):
    _require_posix_fixture_io()
    # The mandatory sanitizer removes this whole directory. Never reuse or
    # inspect an existing runtime store, even if a fixture has the same name.
    private_root = cwd / '.mini-code'
    if os.path.lexists(private_root):
        raise PublicFixtureError('verification workspace contains protected runtime state')
    private_root.mkdir(mode=0o700)
    profile_root = private_root / 'agents'
    profile_root.mkdir(mode=0o700)
    for fixture in fixtures:
        target = cwd / fixture.path
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        with os.fdopen(os.open(target, flags, 0o400), 'wb') as stream:
            stream.write(fixture.content)
        target.chmod(0o400)


def _verify_fixtures(cwd: Path, fixtures):
    _require_posix_fixture_io()
    # Descriptor-relative opens keep post-check inspection from following a
    # replaced directory/file symlink outside the disposable copy. Nonblocking
    # file opens let fstat reject replacement FIFOs without waiting for a writer.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        fd = os.open(cwd, flags)
        stack.callback(os.close, fd)
        for directory in ('.mini-code', 'agents'):
            fd = os.open(directory, flags, dir_fd=fd)
            stack.callback(os.close, fd)
        for fixture in fixtures:
            file_fd = os.open(Path(fixture.path).name,
                              os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(file_fd, 'rb') as stream:
                metadata = os.fstat(stream.fileno())
                if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                        or metadata.st_mode & 0o222 or metadata.st_size != len(fixture.content)):
                    raise PublicFixtureError('public fixture identity or read-only mode changed')
                data = stream.read(len(fixture.content) + 1)
            if hashlib.sha256(data).hexdigest() != fixture.sha256:
                raise PublicFixtureError('public fixture digest changed')


@contextmanager
def bind_verifier_public_inputs(fixtures: tuple[PublicVerificationFixture, ...], *, receipt_sink):
    """Explicit trial-scoped host binding; the existing command guard runs first.

    An original check exception remains authoritative. A changed fixture after
    normal return fails closed before publishing any passing check evidence.
    The caller owns disposal of the verification workspace; parent/candidate
    paths are never targets of this projection.
    """
    if not fixtures:
        yield
        return
    if not callable(receipt_sink):
        raise PublicFixtureError('public fixture audit sink is required')
    if type(fixtures) is not tuple or len(fixtures) != 1 or any(type(f) is not PublicVerificationFixture for f in fixtures):
        raise PublicFixtureError('invalid host fixture binding')
    _require_posix_fixture_io()
    from minicode.deterministic_verifier import DeterministicVerifier
    original = DeterministicVerifier.run
    def run(verifier, **kwargs):
        raw_root = Path(kwargs['cwd'])
        root = raw_root.resolve(strict=True)
        permission_root = kwargs.get('permission_cwd')
        if permission_root is None or root == Path(permission_root).resolve():
            raise PublicFixtureError('public fixtures require a disposable verification copy')
        if raw_root.is_symlink() or any(check.kind != 'tests' for check in kwargs['checks']):
            raise PublicFixtureError('public fixtures are only public test inputs')
        _put_fixtures(root, fixtures)
        _verify_fixtures(root, fixtures)
        evidence = []
        sink = kwargs.get('evidence_sink')
        kwargs['evidence_sink'] = lambda key, document: evidence.append((key, document))
        result = original(verifier, **kwargs)
        _verify_fixtures(root, fixtures)
        for key, document in evidence:
            receipt_sink({'fixtureReceiptVersion': 1, 'workflowId': kwargs['workflow_id'],
                'executionAssignmentId': kwargs['execution_assignment_id'], 'patchDigest': kwargs['patch_digest'],
                'checkId': document['checkId'], 'evidenceArtifactRef': key, 'status': document['status'],
                'evidenceSha256': hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                'publicVerificationFixtures': [f.manifest() for f in fixtures],
                'publicFixtureInputsSha256': _fixture_digest(fixtures), 'beforeAfterDigestMatched': True})
            if sink is not None:
                sink(key, document)
        return result
    with patch.object(DeterministicVerifier, 'run', run):
        yield


def fixture_receipt_writer(path: Path):
    """Private host sidecar; the strict Verifier evidence document is unchanged."""
    lock = threading.Lock()
    def write(document):
        _require_posix_fixture_io()
        encoded = (json.dumps(document, sort_keys=True, separators=(',', ':')) + '\n').encode()
        if len(encoded) > 4096:
            raise PublicFixtureError('public fixture receipt exceeded its bound')
        path.parent.mkdir(parents=True, exist_ok=True)
        with lock:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                if os.write(fd, encoded) != len(encoded):
                    raise PublicFixtureError('public fixture receipt write incomplete')
                os.fsync(fd)
            finally:
                os.close(fd)
    return write


def _passing_pytest_report(report):
    from minicode.review_probe_correction import validate_pytest_case_report
    return (isinstance(report, dict) and validate_pytest_case_report(report)
            and report.get('collected', 0) > 0
            and report.get('passed') == report.get('collected')
            and all(report.get(key) == 0 for key in ('failed', 'errors', 'skipped')))


def run_public_preflight(document: dict, output: Path, *, runtime=None,
                         require_pytest_report=False) -> dict:
    """No model calls: original public argv on untouched sanitized task clones."""
    import tempfile
    import time
    from minicode.deterministic_verifier import VerificationCheck
    from minicode.tooling import ToolContext
    from minicode.workflow_runner import _deterministic_verifier_for_context
    from minicode.workflow_workspace import WorkflowWorkspaceTransaction
    from minicode.verifier_sandbox import resolve_verifier_host_capabilities, user_registered_trusted_executables
    from scripts.run_three_arm_real import install_verification_guards
    from scripts.three_arm_scope import VerificationScope

    output.mkdir(parents=True, exist_ok=True)
    results = []
    for case in document['cases']:
        bindings = bind_public_fixtures(document, case['id'])
        scope = VerificationScope(case['verification_argv'])
        with tempfile.TemporaryDirectory(prefix='minicode-public-preflight-') as temporary:
            root = Path(temporary).resolve() / 'workspace'
            root.mkdir()
            for name, content in {**document.get('shared_files', {}), **case['files']}.items():
                relative = Path(name)
                if relative.is_absolute() or '..' in relative.parts:
                    raise PublicFixtureError('invalid archived public input path')
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            backend, trusted = resolve_verifier_host_capabilities(runtime)
            context = ToolContext(cwd=str(root), _verifier_sandbox_backend=backend,
                _verifier_trusted_executables=trusted,
                _verifier_explicit_trusted_executables=user_registered_trusted_executables() or None)
            receipts, evidence, reports = [], [], []
            with ExitStack() as stack:
                stack.enter_context(bind_verifier_public_inputs(bindings, receipt_sink=receipts.append))
                install_verification_guards(stack, scope, root)
                with WorkflowWorkspaceTransaction(root) as transaction:
                    frozen = transaction.freeze(allowed_paths=tuple(case['authorized_paths']), paths_relative_to_cwd=True)
                    with frozen.verification_workspace() as clone:
                        verifier = _deterministic_verifier_for_context(context,
                            trusted_git_executable=transaction.trusted_git_executable,
                            untrusted_executable_root=str(root))
                        result = verifier.run(checks=(VerificationCheck('public_existing', 'tests', tuple(case['verification_argv']), 120),),
                            cwd=str(clone), permission_cwd=str(root), workflow_id='wf_public_preflight',
                            task_id='task_'+case['id'].replace('-', '_'), execution_assignment_id='asg_public_preflight', patch_digest=frozen.patch_digest,
                            deadline_monotonic=time.monotonic()+120, evidence_sink=lambda _key, item: evidence.append(item),
                            **({'pytest_report_sink': reports.append} if require_pytest_report else {}))
                item = {'case': case['id'], 'argv': case['verification_argv'], 'status': result['status'],
                        'verification': result, 'evidence': evidence, 'fixtureReceipts': receipts}
                if require_pytest_report:
                    item['pytestReport'] = reports[0] if len(reports) == 1 else None
                    if not _passing_pytest_report(item['pytestReport']):
                        item['status'] = 'inconclusive'
                        item['reason'] = 'host_pytest_report_unavailable'
                results.append(item)
    report = {'preflightVersion': 1, 'passed': bool(results) and all(r['status']=='passed' for r in results),
              'taskDocumentSha256': hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
              'policy': PUBLIC_FIXTURE_POLICY, 'fixtures': public_fixture_manifest(document), 'cases': results}
    if require_pytest_report:
        report['pytestReportRequired'] = True
    (output / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    return report


def validate_public_preflight(document: dict, path: Path, expected_sha256: str, *,
                              require_pytest_report=False):
    encoded = path.read_bytes()
    if hashlib.sha256(encoded).hexdigest() != expected_sha256:
        raise PublicFixtureError('frozen public preflight changed')
    report = json.loads(encoded)
    if (report.get('passed') is not True or report.get('policy') != PUBLIC_FIXTURE_POLICY
            or (require_pytest_report and (report.get('pytestReportRequired') is not True
                or any(not _passing_pytest_report(item.get('pytestReport'))
                       for item in report.get('cases', []))))
            or report.get('fixtures') != public_fixture_manifest(document)
            or report.get('taskDocumentSha256') != hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            or [r.get('case') for r in report.get('cases', [])] != [c['id'] for c in document['cases']]
            or any(r.get('status') != 'passed' or r.get('argv') != c['verification_argv']
                   or r.get('verification', {}).get('status') != 'passed'
                   or len(r.get('evidence', [])) != 1
                   or any(e.get('status') != 'passed' or e.get('exitCode') != 0 or e.get('argv') != c['verification_argv']
                          for e in r.get('evidence', []))
                   for r, c in zip(report['cases'], document['cases']))):
        raise PublicFixtureError('public baseline preflight is incomplete or mismatched')
