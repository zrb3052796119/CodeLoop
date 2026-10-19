"""Independent probe execution is immutable, current and owner-bound."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

import pytest

from minicode.deterministic_verifier import (
    DeterministicVerifier, VerificationCheck, _BoundedStreamCapture,
)
from minicode.permissions import PermissionManager
from minicode.reviewer_verification import (
    ReviewerCheckCapability, ReviewerProbe, ReviewerProbeLedger,
)
from minicode.task_deadline import TaskDeadlineExceeded
from minicode.tooling import ToolContext, ToolExecutionAbandoned, ToolRegistry
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from minicode.workflow_workspace import WorkflowWorkspaceTransaction
from test_reviewer_result_submission import _bound


PATCH = "sha256:" + "a" * 64
OTHER = "sha256:" + "c" * 64
INPUT = {"criterionIds": ["ac_behavior"], "scenario": "value must be three",
         "code": "from candidate import value\ndef test_required_value():\n    assert value == 3\n"}


def _receipt(probe, patch=PATCH, *, status="passed", exit_code=0):
    stream = _BoundedStreamCapture()
    stream.add(b"actual test feedback\n")
    argv = ["python", "-m", "pytest", "-q", "test_public.py"]
    return {"probeId": probe.probe_id, "patchDigest": patch, "status": status,
            "testReport": {"collected": 1, "passed": int(exit_code == 0), "failed": int(exit_code == 1),
                           "errors": int(exit_code not in (0, 1)), "skipped": 0, "reportDigest": PATCH},
            "evidence": {"evidenceVersion": 1, "checkId": "public", "kind": "tests",
                "argv": argv, "resolvedExecutable": sys.executable,
                "commandDigest": "sha256:" + hashlib.sha256("\0".join(argv).encode()).hexdigest(),
                "status": status, "exitCode": exit_code, "durationMs": 1,
                "failureReason": None, "stdout": stream.evidence(), "stderr": _BoundedStreamCapture().evidence()}}


def _case(*, callback=None, ledger=None):
    assignment, store, channel, _submit, _kwargs = _bound()
    calls = []

    def run(probe, context):
        calls.append(probe)
        return callback(probe, context) if callback else _receipt(probe)

    ledger = ledger or ReviewerProbeLedger()
    cap = ReviewerCheckCapability(assignment=assignment, channel=channel, ledger=ledger,
                                  patch_digest=PATCH, run_probe=run)
    context = ToolContext(_subagent_mailbox=channel)
    return cap, ledger, ToolRegistry([cap.tool]), context, calls, assignment, store, channel


@pytest.mark.parametrize("change", [
    {"argv": ["sh", "-c", "true"]}, {"path": "candidate.py"},
    {"criterionIds": ["absent"]}, {"criterionIds": ["ac_behavior", "ac_behavior"]},
    {"code": "def test_bad(:"}, {"code": "def helper(): pass"}, {"scenario": ""},
])
def test_only_authorized_criteria_and_standalone_test_source_reach_host(change):
    _cap, ledger, registry, context, calls, *_ = _case()
    result = registry.execute("review_test", {**INPUT, **change}, context)
    assert not result.ok and not calls and not ledger.probes()


@pytest.mark.parametrize("state", ["wrong-owner", "closed", "channel-closed", "globally-revoked"])
def test_probe_execution_requires_live_exact_owner(state):
    cap, ledger, registry, context, calls, _assignment, store, channel = _case()
    if state == "wrong-owner":
        context = replace(context, _subagent_mailbox=object())
    elif state == "closed":
        cap.close()
    elif state == "channel-closed":
        channel.close()
    else:
        store.revoke_pending_assignment_writes(authority=store._scheduler_authority)
    result = registry.execute("review_test", INPUT, context)
    assert not result.ok and not calls and not ledger.probes()


@pytest.mark.parametrize("control", ["deadline", "cancel", "abandoned"])
def test_original_deadline_cancellation_and_abandonment_stop_before_execution(control):
    cap, ledger, _registry, context, calls, *_ = _case()
    if control == "deadline":
        context = replace(context, _task_deadline_monotonic=time.monotonic() - 1)
        expected = TaskDeadlineExceeded
    elif control == "cancel":
        token = TurnCancellationToken("turn_" + "1" * 32)
        token.request()
        context = replace(context, _cancellation_token=token)
        expected = TurnCancellationRequested
    else:
        event = threading.Event()
        event.set()
        context = replace(context, _tool_abandoned=event)
        expected = ToolExecutionAbandoned
    with pytest.raises(expected):
        cap.tool.run(cap.tool.validator(INPUT), context)
    assert not calls and not ledger.probes()


@pytest.mark.parametrize("forgery", ["digest", "probe", "stream", "command", "exit-bool",
                                   "no-report", "false-count", "boolean-count"])
def test_callback_receipts_must_match_actual_probe_and_valid_command_evidence(forgery):
    def run(probe, _context):
        receipt = _receipt(probe)
        if forgery == "digest":
            receipt["patchDigest"] = OTHER
        if forgery == "probe":
            receipt["probeId"] = "another"
        if forgery == "stream":
            receipt["evidence"]["stdout"]["preview"] = "fabricated output"
        if forgery == "command":
            receipt["evidence"]["commandDigest"] = OTHER
        if forgery == "exit-bool":
            receipt["evidence"]["exitCode"] = False
        if forgery == "no-report":
            receipt["testReport"] = None
        if forgery == "false-count":
            receipt["testReport"]["passed"] = 2
        if forgery == "boolean-count":
            receipt["testReport"]["passed"] = True
        return receipt

    _cap, ledger, registry, context, calls, *_ = _case(callback=run)
    result = registry.execute("review_test", INPUT, context)
    assert not result.ok and len(calls) == 1 and not ledger.probes()
    with pytest.raises(ValueError):
        ledger.require_all_current_passed(PATCH)


def test_inconclusive_attempts_stay_visible_without_becoming_permanent_regressions():
    _cap, ledger, registry, context, *_ = _case(
        callback=lambda probe, _context: _receipt(probe, status="failed", exit_code=2))
    result = registry.execute("review_test", INPUT, context)
    assert not result.ok and json.loads(result.output)["status"] == "inconclusive"
    assert not ledger.probes()
    feedback = json.loads(ledger.feedback())
    assert feedback["probes"][0]["code"] == INPUT["code"]
    assert feedback["attempts"][0]["evidence"]["exitCode"] == 2
    with pytest.raises(ValueError):
        ledger.require_all_current_passed(PATCH)


def test_failed_probe_cannot_be_replaced_by_new_passing_source_or_stale_revision():
    cap, ledger, registry, context, *_ = _case(
        callback=lambda probe, _context: _receipt(probe, status="failed", exit_code=1))
    failed = registry.execute("review_test", INPUT, context)
    original = ledger.probes()[0]
    assert not failed.ok and json.loads(failed.output)["status"] == "failed"
    replacement = ReviewerProbe.create(criterion_ids=["ac_behavior"], scenario="different assertion",
                                       code="def test_other(): assert 1 == 1\n")
    ledger.record(replacement, PATCH, _receipt(replacement))
    with pytest.raises(ValueError, match="all retained"):
        ledger.require_all_current_passed(PATCH)
    with pytest.raises(ValueError, match="current"):
        ledger.approval_coverage(patch_digest=OTHER, criterion_id="ac_behavior", probe_ids=[replacement.probe_id])
    seen = []
    ledger.replay(patch_digest=OTHER, context=context,
                  run_probe=lambda probe, _ctx: (seen.append(probe) or _receipt(probe, OTHER)))
    assert seen == [original, replacement]
    assert original.code == INPUT["code"] and len(ledger.require_all_current_passed(OTHER)) == 2
    assert ledger.approval_coverage(patch_digest=OTHER, criterion_id="ac_behavior",
                                    probe_ids=[original.probe_id]) == (original.probe_id,)
    with pytest.raises(ValueError):
        ledger.approval_coverage(patch_digest=OTHER, criterion_id="ac_tests", probe_ids=[original.probe_id])
    returned = ledger.current_receipts(OTHER)
    returned[0]["status"] = "failed"
    assert len(ledger.require_all_current_passed(OTHER)) == 2
    cap.close()


def test_started_rerun_and_closed_callback_cannot_leave_older_pass_usable():
    cap, ledger, registry, context, *_ = _case()
    assert registry.execute("review_test", INPUT, context).ok

    def unavailable(_probe, _context):
        raise RuntimeError("execution unavailable")

    with pytest.raises(RuntimeError):
        ledger.replay(patch_digest=PATCH, context=context, run_probe=unavailable)
    with pytest.raises(ValueError):
        ledger.require_all_current_passed(PATCH)
    cap._run_probe = lambda probe, _ctx: (cap.close() or _receipt(probe))
    assert not registry.execute("review_test", INPUT, context).ok
    with pytest.raises(ValueError):
        ledger.require_all_current_passed(PATCH)


def test_ledger_cannot_transfer_coverage_to_another_workflow():
    _cap, ledger, _registry, _context, _calls, assignment, _store, channel = _case()
    with pytest.raises(ValueError, match="another workflow"):
        ReviewerCheckCapability(assignment=replace(assignment, workflow_id="other_workflow"),
            channel=channel, ledger=ledger, patch_digest=PATCH, run_probe=lambda *_: {})


def test_actual_isolated_pytest_failure_and_same_probe_replay_leave_candidate_untouched(tmp_path, monkeypatch):
    target = tmp_path / "candidate.py"
    original_test = "from candidate import value\ndef test_public(): assert value >= 0\n"
    target.write_text("value = 0\n")
    (tmp_path / "test_public.py").write_text(original_test)
    transaction = WorkflowWorkspaceTransaction(str(tmp_path))
    assignment, store, channel, _submit, _kwargs = _bound()
    ledger = ReviewerProbeLedger()
    commands = []

    class Sandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv

    verifier = DeterministicVerifier(sandbox_backend=Sandbox(),
                                    explicit_trusted_executables={"python": sys.executable})
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    check = VerificationCheck("review_check", "tests", ("python", "-m", "pytest", "-q", "test_public.py"), 10)
    try:
        (transaction.cwd / "candidate.py").write_text("value = 1\n")
        frozen = transaction.freeze(allowed_paths=("candidate.py",), paths_relative_to_cwd=True)

        def run(probe, context):
            evidence, reports = [], []
            with frozen.verification_workspace() as cwd:
                Path(cwd, "test_public.py").write_text(probe.code)
                result = verifier.run(checks=(check,), cwd=str(cwd), workflow_id=assignment.workflow_id,
                    task_id=assignment.task_id, execution_assignment_id=assignment.assignment_id,
                    patch_digest=frozen.patch_digest, permissions=PermissionManager(str(cwd), prompt=None),
                    permission_cwd=str(tmp_path),
                    evidence_sink=lambda _ref, document: evidence.append(document),
                    pytest_report_sink=reports.append)
            commands.append((probe.source_digest, frozen.patch_digest, evidence[0]["exitCode"]))
            return {"probeId": probe.probe_id, "patchDigest": frozen.patch_digest,
                    "status": result["status"], "evidence": evidence[0], "testReport": reports[0]}

        cap = ReviewerCheckCapability(assignment=assignment, channel=channel, ledger=ledger,
                                      patch_digest=frozen.patch_digest, run_probe=run)
        context = ToolContext(_subagent_mailbox=channel)
        failed = ToolRegistry([cap.tool]).execute("review_test", INPUT, context)
        assert not failed.ok and json.loads(failed.output)["status"] == "failed"
        assert (transaction.cwd / "candidate.py").read_text() == "value = 1\n"
        assert (transaction.cwd / "test_public.py").read_text() == original_test
        assert target.read_text() == "value = 0\n"
        cap.close()
        transaction.resume_for_repair(frozen)
        (transaction.cwd / "candidate.py").write_text("value = 3\n")
        frozen = transaction.freeze(allowed_paths=("candidate.py",), paths_relative_to_cwd=True)
        ledger.replay(patch_digest=frozen.patch_digest, run_probe=run, context=context)
        assert [entry[2] for entry in commands] == [1, 0]
        assert commands[0][0] == commands[1][0] and commands[0][1] != commands[1][1]
        assert len(ledger.require_all_current_passed(frozen.patch_digest)) == 1
        assert target.read_text() == "value = 0\n"
        assert (transaction.cwd / "test_public.py").read_text() == original_test
        assert store.read(channel.write_key) is None
    finally:
        transaction.close()
