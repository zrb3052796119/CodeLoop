"""Real approved pytest execution over a disposable frozen-candidate clone."""

from dataclasses import replace
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import sys

import pytest

from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.permissions import PermissionManager
from minicode.review_probe_execution import (
    derive_review_probe_check, run_isolated_review_probe, select_review_probe_check,
    validate_review_probe_execution_binding,
)
from minicode.reviewer_verification import ReviewerProbe
from minicode.workflow_workspace import WorkflowWorkspaceTransaction


@pytest.fixture
def probe_case(tmp_path, monkeypatch, request):
    target = tmp_path / "candidate.py"
    target.write_text("value = 0\n")
    initial_test = "from candidate import value\ndef test_existing(): assert value >= 0\n"
    (tmp_path / "test_public.py").write_text(initial_test)
    collision = getattr(request, "param", None)
    if collision is not None:
        reserved = tmp_path / "__minicode_review_probe__.py"
        if collision == "file":
            reserved.write_text("# existing candidate file\n")
        elif collision == "directory":
            reserved.mkdir()
        else:
            reserved.symlink_to("candidate.py" if collision == "symlink" else "absent.py")
    transaction = WorkflowWorkspaceTransaction(str(tmp_path))
    (transaction.cwd / "candidate.py").write_text("value = 1\n")
    frozen = transaction.freeze(allowed_paths=("candidate.py",), paths_relative_to_cwd=True)
    calls = []

    class Sandbox:
        def wrap_command(self, *, argv, **_kwargs):
            calls.append(tuple(argv))
            return argv

    verifier = DeterministicVerifier(sandbox_backend=Sandbox(),
                                    explicit_trusted_executables={"python": sys.executable})
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    check = VerificationCheck("approved", "tests", ("python", "-m", "pytest", "-q", "test_public.py"), 10)
    options = dict(check=check, verifier=verifier, test_path="test_public.py",
                   read_roots=(".",), forbidden_paths=(".git", ".env"),
                   run_kwargs={"workflow_id": "wf_" + "a" * 32, "task_id": "task_probe",
                       "execution_assignment_id": "asg_probe", "patch_digest": frozen.patch_digest,
                       "permissions": PermissionManager(str(tmp_path), prompt=None),
                       "permission_cwd": str(tmp_path)})
    try:
        yield transaction, frozen, target, initial_test, calls, options
    finally:
        transaction.close()


def _probe(code):
    return ReviewerProbe.create(criterion_ids=["behavior"], scenario="independent public behavior", code=code)


@pytest.mark.parametrize("argv", [
    ("python", "-m", "pytest", "-q"),
    ("python", "-m", "pytest", "-q", "test_public.py", "test_other.py"),
    ("python", "-m", "pytest", "-q", "test_public.py", "-k", "existing"),
    ("python", "-m", "pytest", "-q", "test_public.py::test_existing"),
])
def test_selector_never_broadens_command_or_adds_test_target(argv):
    check = VerificationCheck("approved", "tests", argv, 10)
    assert select_review_probe_check((check,), ("test_public.py",)) is None


def test_selector_returns_the_exact_authorized_check_and_target(probe_case):
    *_state, options = probe_case
    check = options["check"]
    selected = select_review_probe_check((check,), ("other.py", "test_public.py"))
    assert selected == (check, "test_public.py") and selected[0] is check
    assert select_review_probe_check((check,), ("other.py",)) is None


@pytest.mark.parametrize("code", [
    "import pytest\n@pytest.mark.skip(reason='not executed')\ndef test_skipped(): assert False\n",
    "def test_uncollected(): assert False\ntest_uncollected.__test__ = False\n",
    "import pytest\n@pytest.mark.xfail(reason='not passed')\ndef test_xfailed(): assert False\n",
    "import nonexistent_review_probe_module\ndef test_unavailable(): assert True\n",
    "import pytest\n@pytest.fixture\ndef broken(): raise RuntimeError('setup failed')\n"
    "def test_setup(broken): assert True\n",
])
def test_skip_zero_collection_and_xfail_do_not_establish_coverage(probe_case, code):
    _transaction, frozen, _target, _initial_test, _calls, options = probe_case
    result = run_isolated_review_probe(probe=_probe(code), frozen=frozen, **options)
    assert result["status"] == "inconclusive"


def test_printing_a_fake_pass_count_cannot_turn_all_skipped_into_coverage(probe_case):
    _transaction, frozen, _target, _initial_test, _calls, options = probe_case
    options["check"] = replace(options["check"], argv=("python", "-m", "pytest", "-q", "-s", "test_public.py"))
    probe = _probe("import pytest\nprint('1 passed')\n@pytest.mark.skip(reason='not executed')\n"
                   "def test_skipped(): assert False\n")
    result = run_isolated_review_probe(probe=probe, frozen=frozen, **options)
    assert result["evidence"]["exitCode"] == 0
    assert "1 skipped" in result["evidence"]["stdout"]["preview"]
    assert result["status"] == "inconclusive", "Model-controlled output is not a test execution receipt."


def test_modified_source_in_clone_is_rejected_and_original_files_survive(probe_case):
    transaction, frozen, target, initial_test, _calls, options = probe_case
    probe = _probe("from pathlib import Path\ndef test_wrongly_rewrites_source():\n"
                   "    Path('candidate.py').write_text('value = 3\\n')\n    assert True\n")
    result = run_isolated_review_probe(probe=probe, frozen=frozen, **options)
    assert result["status"] == "inconclusive" and result["reason"] == "review_probe_modified_candidate_files"
    assert target.read_text() == "value = 0\n"
    assert (transaction.cwd / "candidate.py").read_text() == "value = 1\n"
    assert (transaction.cwd / "test_public.py").read_text() == initial_test


def test_helper_refuses_target_not_bound_to_the_authorized_command(probe_case):
    _transaction, frozen, _target, _initial_test, calls, options = probe_case
    options["test_path"] = "candidate.py"
    with pytest.raises(ValueError, match="authoriz|target|command"):
        run_isolated_review_probe(probe=_probe("def test_actual(): assert True\n"), frozen=frozen, **options)
    assert calls == []


def test_real_failure_then_repaired_candidate_uses_same_probe_and_same_argv(probe_case):
    transaction, frozen, target, initial_test, calls, options = probe_case
    probe = _probe("from candidate import value\ndef test_required(): assert value == 3\n")
    first = run_isolated_review_probe(probe=probe, frozen=frozen, **options)
    assert first["status"] == "failed" and first["evidence"]["exitCode"] == 1
    transaction.resume_for_repair(frozen)
    (transaction.cwd / "candidate.py").write_text("value = 3\n")
    new_frozen = transaction.freeze(allowed_paths=("candidate.py",), paths_relative_to_cwd=True)
    options["run_kwargs"] = {**options["run_kwargs"], "patch_digest": new_frozen.patch_digest}
    second = run_isolated_review_probe(probe=probe, frozen=new_frozen, **options)
    assert second["status"] == "passed" and second["evidence"]["exitCode"] == 0
    assert first["probeId"] == second["probeId"] and first["patchDigest"] != second["patchDigest"]
    derived, target_path = derive_review_probe_check(options["check"], options["test_path"])
    assert target_path == "__minicode_review_probe__.py"
    assert first["evidence"]["argv"] == second["evidence"]["argv"] == list(derived.argv)
    for receipt in (first, second):
        validate_review_probe_execution_binding(receipt, (options["check"],), probe_source_digest=probe.source_digest)
        assert receipt["executionBinding"]["baseCheck"]["argv"] == list(options["check"].argv)
    assert len(calls) == 2
    assert calls[0][:-3] == calls[1][:-3] == (str(Path(sys.executable).resolve()), *derived.argv[1:])
    assert all(argv[-3] == "-p" and argv[-2].startswith("_minicode_pytest_") for argv in calls)
    assert all(argv[-1].startswith("--junitxml=") for argv in calls)
    assert calls[0][-1] != calls[1][-1], "Each execution has its own host diagnostic report."
    assert target.read_text() == "value = 0\n"
    assert (transaction.cwd / "test_public.py").read_text() == initial_test


@pytest.mark.parametrize("observation", [
    "import ast\nfrom pathlib import Path\ndef test_original():\n"
    "    tree = ast.parse(Path('test_public.py').read_text())\n"
    "    assert any(isinstance(node, ast.FunctionDef) and node.name == 'test_existing' for node in tree.body)\n",
    "def test_original():\n    import test_public\n"
    "    assert callable(getattr(test_public, 'test_existing', None))\n    test_public.test_existing()\n",
])
def test_probe_can_read_and_import_original_candidate_test(probe_case, observation):
    transaction, frozen, _target, initial_test, _calls, options = probe_case
    probe = _probe(observation)
    result = run_isolated_review_probe(probe=probe, frozen=frozen, **options)
    assert result["status"] == "passed"
    assert result["testReport"]["passed"] == 1
    assert result["executionBinding"]["originalTargetDigest"] == (
        "sha256:" + hashlib.sha256(initial_test.encode()).hexdigest())
    assert (transaction.cwd / "test_public.py").read_text() == initial_test
    assert not (transaction.cwd / "__minicode_review_probe__.py").exists()
    validate_review_probe_execution_binding(result, (options["check"],), probe_source_digest=probe.source_digest)


@pytest.mark.parametrize("probe_case", ["file", "directory", "symlink", "dangling"], indirect=True)
def test_host_probe_target_collision_is_rejected_before_execution(probe_case):
    transaction, frozen, _target, initial_test, calls, options = probe_case
    with pytest.raises(ValueError, match="collid"):
        run_isolated_review_probe(probe=_probe("def test_actual(): assert True\n"), frozen=frozen, **options)
    assert not calls
    assert (transaction.cwd / "test_public.py").read_text() == initial_test


def test_probe_command_derivation_preserves_the_sole_authorized_target_context():
    check = VerificationCheck("nested", "tests", ("python3", "-m", "pytest", "-q", "tests/unit/test_public.py", "--tb=short"), 37)
    derived, target = derive_review_probe_check(check, "tests/unit/test_public.py")
    assert target == "tests/unit/__minicode_review_probe__.py"
    assert derived == replace(check, argv=("python3", "-m", "pytest", "-q", target, "--tb=short"))
    with pytest.raises(ValueError, match="collid"):
        derive_review_probe_check(derived, target)


def test_actual_derived_execution_uses_exact_original_permission_grant(probe_case):
    _transaction, frozen, _target, _initial_test, calls, options = probe_case
    allowed = options["check"].argv
    requests, checkpoints = [], []

    class PermissionGrant:
        def ensure_command(self, program, args, cwd):
            assert (program, *args) == allowed
            assert cwd == options["run_kwargs"]["permission_cwd"]
            requests.append((program, *args))

        def ensure_operation_active(self):
            checkpoints.append(True)

    options["run_kwargs"]["permissions"] = PermissionGrant()
    result = run_isolated_review_probe(probe=_probe("def test_actual(): assert True\n"), frozen=frozen, **options)
    assert result["status"] == "passed"
    assert requests == [allowed] and checkpoints
    assert "__minicode_review_probe__.py" in calls[0]
    assert "test_public.py" not in calls[0]


def test_binding_rejects_changed_command_candidate_source_and_authority(probe_case):
    _transaction, frozen, _target, _initial_test, _calls, options = probe_case
    probe = _probe("def test_actual(): assert True\n")
    result = run_isolated_review_probe(probe=probe, frozen=frozen, **options)
    validate_review_probe_execution_binding(result, (options["check"],), probe_source_digest=probe.source_digest)
    validate_review_probe_execution_binding(result)  # Structure alone grants no external authority.
    with pytest.raises(ValueError, match="authorized"):
        validate_review_probe_execution_binding(result, ())
    with pytest.raises(ValueError, match="source"):
        validate_review_probe_execution_binding(result, probe_source_digest="sha256:" + "f" * 64)
    for field, value in [
        ("probeTarget", "test_other.py"), ("originalTarget", "test_other.py"),
        ("patchDigest", "sha256:" + "f" * 64), ("originalTargetDigest", "unbound"),
        ("probeSourceDigest", "unbound"), ("bindingVersion", True),
    ]:
        changed = deepcopy(result)
        changed["executionBinding"][field] = value
        with pytest.raises(ValueError):
            validate_review_probe_execution_binding(changed, (options["check"],))
    for side in ("baseCheck", "derivedCheck"):
        for field, value in [("argv", ["python", "-m", "pytest", "test_other.py"]),
                             ("commandDigest", "sha256:" + "f" * 64), ("timeoutSeconds", 38)]:
            changed = deepcopy(result)
            changed["executionBinding"][side][field] = value
            with pytest.raises(ValueError):
                validate_review_probe_execution_binding(changed, (options["check"],))
    changed = deepcopy(result)
    changed["evidence"]["argv"] = list(options["check"].argv)
    with pytest.raises(ValueError, match="evidence"):
        validate_review_probe_execution_binding(changed, (options["check"],))
    changed = deepcopy(result)
    del changed["executionBinding"]
    with pytest.raises(ValueError, match="malformed"):
        validate_review_probe_execution_binding(changed, (options["check"],))


def _report_bytes():
    return (b'<testsuites><testsuite tests="4" failures="1" errors="1" skipped="1">'
            b'<testcase name="pass"/><testcase name="fail"><failure/></testcase>'
            b'<testcase name="error"><error/></testcase>'
            b'<testcase name="skip"><skipped/></testcase></testsuite></testsuites>')


def test_report_counts_and_digest_come_from_the_bounded_machine_file(tmp_path):
    from minicode.deterministic_verifier import _read_pytest_report

    path = tmp_path / "report.xml"
    data = _report_bytes()
    path.write_bytes(data)
    assert _read_pytest_report(path) == {
        "collected": 4, "passed": 1, "failed": 1, "errors": 1, "skipped": 1,
        "reportDigest": "sha256:" + hashlib.sha256(data).hexdigest(),
    }


@pytest.mark.parametrize("kind", ["missing", "symlink", "hardlink", "directory", "fifo",
                                 "oversized", "malformed", "wrong-count", "entities"])
def test_report_reader_rejects_untrusted_or_inconsistent_files(tmp_path, kind):
    from minicode.deterministic_verifier import _MAX_PYTEST_REPORT_BYTES, _read_pytest_report

    path = tmp_path / "report.xml"
    data = _report_bytes()
    if kind in {"symlink", "hardlink"}:
        source = tmp_path / "actual.xml"
        source.write_bytes(data)
        if kind == "symlink":
            path.symlink_to(source)
        else:
            os.link(source, path)
    elif kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind != "missing":
        if kind == "oversized":
            data = b" " * (_MAX_PYTEST_REPORT_BYTES + 1)
        elif kind == "malformed":
            data = b"<testsuites>"
        elif kind == "wrong-count":
            data = data.replace(b'tests="4"', b'tests="5"')
        elif kind == "entities":
            data = b'<!DOCTYPE testsuites [<!ENTITY x "anything">]>' + data
        path.write_bytes(data)
    assert _read_pytest_report(path) is None
