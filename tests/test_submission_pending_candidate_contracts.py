"""A rejected declaration remains repair-only, even with passing real checks.

Contract/audit tests use a fixture worker budget stop. Real loop admission and
same-session declaration recovery are covered by the separate integration.
"""

import json
from dataclasses import replace

import pytest
from test_executor_checked_candidate import _candidate_documents
from test_pending_write_feedback_contracts import _passing_pending_chain
from test_scheduler_checked_candidate import _candidate_run, _documents, _replay
from test_workflow_continuation_contracts import validate_chain

from minicode.executor_result_submission import create_executor_submit_tool
from minicode.executor_verification import ExecutorCheckCapability
from minicode.multi_agent_contracts import (
    ContractValidationError,
    parse_execution_candidate,
    parse_role_result,
)
from minicode.scheduler_audit import SchedulerAuditValidationError
from minicode.tooling import ToolRegistry
from minicode.workflow_runner import _WorkflowRunSession

PENDING = "executor_submission_feedback_pending"


@pytest.mark.parametrize("failed", [False, True])
def test_submission_pending_is_unassessed_host_candidate_with_current_complete_checks(failed):
    assignment, order, checks, candidate, documents = _candidate_documents()
    candidate["handoffReason"] = PENDING
    if failed:
        receipt = next(iter(documents.values()))["receipt"]
        receipt["status"] = "failed"
        receipt["evidence"].update(status="failed", exitCode=1)
    assert parse_execution_candidate(assignment, candidate, implementation_order=order,
                                    required_checks=checks, receipt_documents=documents) == candidate
    assert candidate["status"] == "awaiting_verification" and "stepResults" not in candidate
    with pytest.raises(ContractValidationError):
        parse_role_result(assignment, candidate, implementation_order=order)


@pytest.mark.parametrize("forgery", ["unknown_reason", "completed", "assessed", "missing_receipt",
    "stale_patch", "inconclusive", "failed_zero", "wrong_command", "wrong_owner", "outside_scope"])
def test_submission_pending_does_not_relax_candidate_contract(forgery):
    assignment, order, checks, candidate, documents = _candidate_documents()
    candidate["handoffReason"] = PENDING
    receipt_document = next(iter(documents.values()))
    receipt = receipt_document["receipt"]
    if forgery == "unknown_reason": candidate["handoffReason"] = "model_said_finished"
    elif forgery == "completed": candidate["status"] = "completed"
    elif forgery == "assessed": candidate["stepResults"] = []
    elif forgery == "missing_receipt": documents.clear()
    elif forgery == "stale_patch": receipt["patchDigest"] = "sha256:" + "f" * 64
    elif forgery == "inconclusive": receipt["status"] = "inconclusive"
    elif forgery == "failed_zero":
        receipt["status"] = "failed"
        receipt["evidence"].update(status="failed", exitCode=0)
    elif forgery == "wrong_command": receipt["argv"] = ["true"]
    elif forgery == "wrong_owner": receipt_document["assignmentId"] = "asg_foreign"
    elif forgery == "outside_scope": candidate["changeSet"]["files"][0]["path"] = "outside.txt"
    with pytest.raises(ContractValidationError):
        parse_execution_candidate(assignment, candidate, implementation_order=order,
                                  required_checks=checks, receipt_documents=documents)


def _passing_submission_chain():
    trace, documents, candidate, transition, verification = _passing_pending_chain()
    candidate["handoffReason"] = PENDING
    return trace, documents, candidate, transition, verification


def test_fresh_passed_verifier_can_request_submission_feedback_repair():
    trace, documents, candidate, _, verification = _passing_submission_chain()
    assert len(validate_chain(trace, documents)) == 1
    assert verification["status"] == "passed" and candidate["status"] == "awaiting_verification"


@pytest.mark.parametrize("forgery", ["ordinary_candidate", "worker_completed", "verifier_completed",
    "wrong_error", "missing_evidence", "timeout", "inconclusive", "changed_check_status",
    "previous_patch", "missing_continuation"])
def test_submission_pending_repair_keeps_exact_fresh_verification_chain(forgery):
    trace, documents, candidate, transition, verification = _passing_submission_chain()
    evidence_id = verification["checks"][0]["artifactRef"]
    if forgery == "ordinary_candidate": candidate["handoffReason"] = "executor_budget_exceeded"
    elif forgery == "worker_completed": candidate.update(resultType="ExecutionResult.v1", status="completed")
    elif forgery == "verifier_completed": transition.update(outcome="completed", errorCode=None)
    elif forgery == "wrong_error": transition["errorCode"] = "verification_passed"
    elif forgery == "missing_evidence": documents.pop(evidence_id)
    elif forgery == "timeout": documents[evidence_id]["failureReason"] = "timeout"
    elif forgery == "inconclusive": verification["status"] = "inconclusive"
    elif forgery == "changed_check_status": documents[evidence_id].update(status="failed", exitCode=1)
    else:
        repair = next(d for d in documents.values() if d.get("resultType") == "ImplementationRepairRequest.v1")
        if forgery == "previous_patch": repair["previousPatchDigest"] = "sha256:" + "f" * 64
        else: repair.pop("continuation")
    with pytest.raises(SchedulerAuditValidationError):
        validate_chain(trace, documents)


def _same_patch_submission_chain():
    trace, documents, candidate, transition, verification = _passing_submission_chain()
    executions = [item for item in trace["transitions"]
                  if item["stage"] == "executor" and item["outcome"] == "completed"]
    successor = documents[executions[1]["artifactId"]]
    successor.update(resultType="ExecutionResult.v1", status="completed")
    successor["changeSet"]["patchDigest"] = candidate["changeSet"]["patchDigest"]
    return trace, documents, successor, transition, verification


def test_same_patch_submission_correction_requires_new_native_result_in_audit_chain():
    trace, documents, successor, _, _ = _same_patch_submission_chain()
    assert len(validate_chain(trace, documents)) == 1
    assert successor["resultType"] == "ExecutionResult.v1"


@pytest.mark.parametrize("forgery", ["pending_successor", "noncompleted_successor", "legacy_repair", "failed_prior_checks"])
def test_same_patch_submission_correction_cannot_infer_model_completion(forgery):
    trace, documents, successor, transition, verification = _same_patch_submission_chain()
    if forgery == "pending_successor":
        successor.update(resultType="ExecutionCandidate.v1", status="awaiting_verification", handoffReason=PENDING)
    elif forgery == "noncompleted_successor":
        successor["status"] = "blocked"
    elif forgery == "legacy_repair":
        next(d for d in documents.values() if d.get("resultType") == "ImplementationRepairRequest.v1").pop("continuation")
        next(t for t in trace["transitions"] if t["outcome"] == "repairing")["errorCode"] = "implementation_repair_requested"
    else:
        transition.update(outcome="failed", errorCode="verification_failed")
        verification["status"] = "failed"
        for check in verification["checks"]:
            check.update(status="failed", exitCode=1)
            documents[check["artifactRef"]].update(status="failed", exitCode=1)
    with pytest.raises(SchedulerAuditValidationError, match="unchanged submission-feedback repair"):
        validate_chain(trace, documents)


@pytest.mark.parametrize("approve", [False, True])
def test_full_audit_rejects_submission_pending_matrix_review_or_commit(tmp_path, monkeypatch, approve):
    result, audit, *_ = _candidate_run(tmp_path, monkeypatch, approve=approve)
    assert result.ok is approve and audit.trace["committed"] is approve
    assert any(d.get("resultType") == "AcceptanceEvidenceMatrix.v1" for d in _documents(audit).values())
    assert any(t["stage"] == "reviewer" for t in audit.trace["transitions"])
    artifacts = []
    for artifact in audit.artifacts:
        document = json.loads(artifact.content)
        if document.get("resultType") == "ExecutionCandidate.v1":
            document["handoffReason"] = PENDING
            artifact = replace(artifact, content=json.dumps(document, sort_keys=True, separators=(",", ":")))
        artifacts.append(artifact)
    with pytest.raises(SchedulerAuditValidationError):
        _replay(audit, artifacts=tuple(artifacts))


def test_real_rejected_submit_and_passing_checks_stop_before_matrix_review_commit(tmp_path, monkeypatch):
    capture = ExecutorCheckCapability.capture_checked_candidate
    snapshots, rejected = [], []

    def capture_after_invalid_declaration(self, *, context):
        channel = self._submission_channel
        submit = create_executor_submit_tool(channel._assignment, channel._order, channel)
        registry = self.bind_registry(ToolRegistry([submit]))
        result = registry.execute("executor_submit", {
            "status": "completed", "steps": [{"step": 1, "status": "completed"}],
            "deviations": [{"step": 1, "reason": "first diagnostic", "impact": "low"},
                           {"step": 1, "reason": "second diagnostic", "impact": "low"}], "blockers": [],
        }, context)
        rejected.append(result)
        assert not result.ok and "duplicated" in result.output
        assert channel._inner._store.read(channel.write_key) is None
        snapshot = capture(self, context=context)
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(ExecutorCheckCapability, "capture_checked_candidate", capture_after_invalid_declaration)
    monkeypatch.setattr(_WorkflowRunSession, "admit_implementation_repair", lambda *_args: False)
    try:
        result, audit, parent, checks, roles = _candidate_run(tmp_path, monkeypatch)
    finally:
        assert snapshots and snapshots[-1] is not None, "Rejected declaration lost the checked repair-only candidate"
    assert rejected and not result.ok and not audit.trace["committed"]
    assert parent.read_text() == "before" and roles == ["analyst", "executor"]
    assert checks == [0, 0], "Passing implementation feedback still requires a fresh formal Verifier"
    documents = _documents(audit)
    candidate = next(d for d in documents.values() if d.get("resultType") == "ExecutionCandidate.v1")
    verification = next(d for d in documents.values() if d.get("resultType") == "VerificationResult.v1")
    assert candidate["handoffReason"] == PENDING
    assert verification["status"] == "passed"
    boundary = next(t for t in audit.trace["transitions"] if t["stage"] == "verifier" and t["outcome"] != "dispatched")
    assert (boundary["outcome"], boundary["errorCode"]) == ("inconclusive", "acceptance_evidence_incomplete")
    assert not any(d.get("resultType") == "AcceptanceEvidenceMatrix.v1" for d in documents.values())
    assert _replay(audit) == audit
