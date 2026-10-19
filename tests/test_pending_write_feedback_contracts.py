"""Pending writer feedback permits repair, never an unearned completion.

Audit fixtures use real isolated pytest and file edits; their worker stop is
synthetic. The separate loop integration owns budget/session delivery proof.
"""

import json
from dataclasses import replace

import pytest
from test_executor_checked_candidate import _candidate_documents
from test_scheduler_checked_candidate import _candidate_run, _documents, _replay
from test_workflow_continuation_contracts import (
    chain_documents,
    continuation_trace,
    validate_chain,
)

from minicode.executor_verification import ExecutorCheckCapability
from minicode.multi_agent_contracts import (
    ContractValidationError,
    parse_execution_candidate,
    parse_role_result,
)
from minicode.scheduler_audit import SchedulerAuditValidationError
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.workflow_runner import _WorkflowRunSession

PENDING = "executor_write_feedback_pending"


@pytest.mark.parametrize("failed", [False, True])
def test_pending_writer_candidate_keeps_real_check_status_and_unassessed_steps(failed):
    assignment, order, checks, candidate, documents = _candidate_documents()
    candidate["handoffReason"] = PENDING
    if failed:
        receipt = next(iter(documents.values()))["receipt"]
        receipt["status"] = "failed"
        receipt["evidence"].update(status="failed", exitCode=1)
    parsed = parse_execution_candidate(assignment, candidate, implementation_order=order,
                                       required_checks=checks, receipt_documents=documents)
    assert parsed == candidate and parsed["status"] == "awaiting_verification"
    assert "stepResults" not in parsed
    with pytest.raises(ContractValidationError):
        parse_role_result(assignment, {key: value for key, value in candidate.items() if key != "resultType"},
                          implementation_order=order)


def _passing_pending_chain():
    trace = continuation_trace(repairs=1)
    documents = chain_documents(trace)
    execution = next(item for item in trace["transitions"]
                     if item["stage"] == "executor" and item["outcome"] == "completed")
    candidate = documents[execution["artifactId"]]
    candidate.update(resultType="ExecutionCandidate.v1", status="awaiting_verification", handoffReason=PENDING)
    verification = next(item for item in trace["transitions"]
                        if item["stage"] == "verifier" and item["outcome"] == "failed")
    verification.update(outcome="inconclusive", errorCode="acceptance_evidence_incomplete")
    result = documents[verification["artifactId"]]
    result.update(resultType="VerificationResult.v1", status="passed")
    for check in result["checks"]:
        check.update(status="passed", exitCode=0)
        documents[check["artifactRef"]].update(status="passed", exitCode=0)
    return trace, documents, candidate, verification, result


def test_passing_verification_can_return_exact_pending_candidate_for_repair():
    trace, documents, _, _, result = _passing_pending_chain()
    assert len(validate_chain(trace, documents)) == 1
    assert result["status"] == "passed"
    assert all(check["status"] == "passed" and check["exitCode"] == 0 for check in result["checks"])


@pytest.mark.parametrize("forgery", [
    "ordinary_candidate", "native_result", "assessed", "completed_transition", "wrong_cause",
    "missing_evidence", "timeout", "failed_evidence", "bool_exit", "missing_check", "inconclusive",
])
def test_passing_checks_alone_cannot_authorize_repair(forgery):
    trace, documents, candidate, transition, result = _passing_pending_chain()
    evidence_id = result["checks"][0]["artifactRef"]
    if forgery == "ordinary_candidate": candidate["handoffReason"] = "executor_budget_exceeded"
    elif forgery == "native_result": candidate["resultType"] = "ExecutionResult.v1"
    elif forgery == "assessed": candidate["status"] = "completed"
    elif forgery == "completed_transition": transition.update(outcome="completed", errorCode=None)
    elif forgery == "wrong_cause": transition["errorCode"] = "frozen_diff_evidence_missing"
    elif forgery == "missing_evidence": documents.pop(evidence_id)
    elif forgery == "timeout": documents[evidence_id]["failureReason"] = "timeout"
    elif forgery == "failed_evidence": documents[evidence_id].update(status="failed", exitCode=1)
    elif forgery == "bool_exit": documents[evidence_id]["exitCode"] = False
    elif forgery == "missing_check": result["checks"] = []
    elif forgery == "inconclusive": result["status"] = "inconclusive"
    with pytest.raises(SchedulerAuditValidationError):
        validate_chain(trace, documents)


def test_full_audit_rejects_pending_candidate_as_final_commit_source(tmp_path, monkeypatch):
    result, audit, *_ = _candidate_run(tmp_path, monkeypatch)
    assert result.ok and audit.trace["committed"]
    artifacts = []
    for artifact in audit.artifacts:
        document = json.loads(artifact.content)
        if document.get("resultType") == "ExecutionCandidate.v1":
            document["handoffReason"] = PENDING
            artifact = replace(artifact, content=json.dumps(document, sort_keys=True, separators=(",", ":")))
        artifacts.append(artifact)
    with pytest.raises(SchedulerAuditValidationError, match="pending Executor write feedback"):
        _replay(audit, artifacts=tuple(artifacts))


def test_passing_real_checks_with_unseen_edit_failure_stop_before_review_and_commit(tmp_path, monkeypatch):
    capture = ExecutorCheckCapability.capture_checked_candidate
    snapshots = []

    def capture_after_real_failed_edit(self, *, context):
        # Reproduce a final ordinary edit failure on an already valid patch.
        # Fresh receipts describe the real current bytes, not the desired edit.
        failed = ToolRegistry([self.with_write_feedback(edit_file_tool)]).execute("edit_file", {
            "path": "allowed.txt", "old": "anchor-that-does-not-exist", "new": "after",
        }, context)
        assert not failed.ok
        for number in range(1, len(self._checks) + 1):
            assert self._run({"check": number}, context).ok
        snapshot = capture(self, context=context)
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(ExecutorCheckCapability, "capture_checked_candidate", capture_after_real_failed_edit)
    monkeypatch.setattr(_WorkflowRunSession, "admit_implementation_repair", lambda *_args: False)
    try:
        result, audit, target, checks, roles = _candidate_run(tmp_path, monkeypatch)
    finally:
        # This observation is outside run_assigned_worker's protective except:
        # the frozen baseline must fail for refusing this actual candidate,
        # rather than merely for its downstream audit being absent.
        assert snapshots and snapshots[-1] is not None, "Unseen write feedback must retain a repair-only candidate."
    assert not result.ok and not audit.trace["committed"]
    assert target.read_text() == "before" and "reviewer" not in roles
    assert checks == [0, 0, 0, 0], "Initial, automatic post-error, explicit retry and fresh formal checks must really pass."
    documents = _documents(audit)
    candidate = next(value for value in documents.values() if value.get("resultType") == "ExecutionCandidate.v1")
    verification = next(value for value in documents.values() if value.get("resultType") == "VerificationResult.v1")
    assert candidate["handoffReason"] == PENDING
    assert verification["status"] == "passed", "Do not invent a failed process to force repair."
    terminal = next(item for item in audit.trace["transitions"]
                    if item["stage"] == "verifier" and item["outcome"] != "dispatched")
    assert (terminal["outcome"], terminal["errorCode"]) == ("inconclusive", "acceptance_evidence_incomplete")
    assert not any(value.get("resultType") == "AcceptanceEvidenceMatrix.v1" for value in documents.values())
    assert _replay(audit) == audit
