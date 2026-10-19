"""Host checked candidates preserve facts, without manufacturing completion.

These focused tests validate capabilities/contracts; the separate workflow
integration exercises actual pytest, budget termination, V/R and promotion.
"""

import copy
import hashlib
import json
import sys
import threading
import time
from dataclasses import FrozenInstanceError, replace

import pytest

from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.multi_agent_contracts import (
    ContractValidationError, implementation_receipt_ref, parse_execution_candidate, parse_role_result,
)
from minicode.tooling import ToolContext, ToolResult
from test_executor_completion_check_gate import _gated, _check_all, _submit
from test_executor_result_submission import _bound


def _candidate_documents():
    assignment, original_order, _, _, _ = _bound()
    checks = (VerificationCheck("public", "tests", ("python3", "-m", "pytest", "-q", "tests/test_auth.py"), 20),
              VerificationCheck("compat", "tests", ("python3", "-m", "pytest", "-q", "tests/test_compat.py"), 20))
    order = replace(original_order, verification_requests=tuple(" ".join(check.argv) for check in checks))
    digest = "sha256:" + "b" * 64
    documents = {}
    for sequence, check in enumerate(checks, 1):
        ref = implementation_receipt_ref(assignment.assignment_id, order.order_id, digest, check.check_id)
        documents[ref] = {
            "receiptVersion": 1, "resultType": "ImplementationCheckReceipt.v1",
            "workflowId": assignment.workflow_id, "taskId": assignment.task_id,
            "assignmentId": assignment.assignment_id, "orderId": order.order_id,
            "receipt": {
                "checkId": check.check_id, "argv": list(check.argv), "patchDigest": digest,
                "status": "passed", "verification": "implementation_feedback", "writeback": "pending",
                "observationSequence": sequence,
                "evidence": {
                    "evidenceVersion": 1, "checkId": check.check_id, "kind": check.kind,
                    "argv": list(check.argv), "resolvedExecutable": sys.executable,
                    "commandDigest": "sha256:" + hashlib.sha256("\0".join(check.argv).encode()).hexdigest(),
                    "status": "passed", "exitCode": 0, "durationMs": 10, "failureReason": None,
                    "stdout": DeterministicVerifier._stream_evidence(b"2 passed\n"),
                    "stderr": DeterministicVerifier._stream_evidence(b""),
                },
            },
        }
    candidate = {
        "candidateVersion": 1, "resultType": "ExecutionCandidate.v1",
        "assignmentId": assignment.assignment_id, "orderId": order.order_id,
        "status": "awaiting_verification", "handoffReason": "executor_budget_exceeded",
        "unassessedStepIds": [step.step_id for step in order.steps],
        "changeSet": {"workspaceRef": "isolated://checked/workspace", "baseDigest": order.base_digest,
                      "patchDigest": digest, "files": [{"path": "minicode/auth.py", "action": "modified"}]},
        "implementationReceiptRefs": list(documents),
    }
    return assignment, order, checks, candidate, documents


def test_checked_candidate_keeps_all_steps_unassessed_and_cannot_be_model_submitted():
    assignment, order, checks, candidate, documents = _candidate_documents()
    before = copy.deepcopy((candidate, documents))
    parsed = parse_execution_candidate(assignment, candidate, implementation_order=order,
                                       required_checks=checks, receipt_documents=documents)
    assert parsed == candidate and (candidate, documents) == before
    assert parsed["status"] == "awaiting_verification"
    assert "stepResults" not in parsed and "selfChecks" not in parsed
    with pytest.raises(ContractValidationError):
        parse_role_result(assignment, {key: value for key, value in candidate.items() if key != "resultType"},
                          implementation_order=order)
    _, _, store, channel, _ = _bound()
    with pytest.raises(Exception, match="invalid Executor declaration"):
        channel.write(channel.write_key, json.dumps(candidate), author="executor")
    assert store.read(channel.write_key) is None


@pytest.mark.parametrize("failed_count,exit_code", [(1, 1), (1, 4), (2, 1)])
def test_completed_failed_checks_are_pending_evidence_not_completed_steps(failed_count, exit_code):
    assignment, order, checks, candidate, documents = _candidate_documents()
    for document in list(documents.values())[:failed_count]:
        receipt = document["receipt"]
        receipt["status"] = "failed"
        receipt["evidence"].update(status="failed", exitCode=exit_code)
    before = copy.deepcopy(documents)
    parsed = parse_execution_candidate(assignment, candidate, implementation_order=order,
                                       required_checks=checks, receipt_documents=documents)
    assert parsed == candidate and documents == before
    assert parsed["status"] == "awaiting_verification"
    assert parsed["unassessedStepIds"] == [step.step_id for step in order.steps]
    assert "stepResults" not in parsed and "selfChecks" not in parsed


@pytest.mark.parametrize("invalid", [
    "completed", "assessed_step", "empty_patch", "outside_scope", "stale_patch", "missing_check",
    "missing_receipt", "wrong_owner", "wrong_argv", "failed", "nonzero", "missing_stream_bytes",
    "corrupt_stream", "old_sequence", "wrong_order_commands", "failed_zero_exit",
    "failed_negative_exit", "failed_bool_exit", "failure_reason", "inconclusive", "unknown",
])
def test_checked_candidate_rejects_unbound_or_incomplete_evidence(invalid):
    assignment, order, checks, candidate, documents = _candidate_documents()
    first_ref = candidate["implementationReceiptRefs"][0]
    document = documents[first_ref]
    receipt = document["receipt"]
    if invalid == "completed": candidate["status"] = "completed"
    elif invalid == "assessed_step": candidate["unassessedStepIds"].pop()
    elif invalid == "empty_patch": candidate["changeSet"]["files"] = []
    elif invalid == "outside_scope": candidate["changeSet"]["files"][0]["path"] = "other.py"
    elif invalid == "stale_patch": receipt["patchDigest"] = "sha256:" + "c" * 64
    elif invalid == "missing_check": candidate["implementationReceiptRefs"].pop()
    elif invalid == "missing_receipt": documents.pop(first_ref)
    elif invalid == "wrong_owner": document["assignmentId"] = "asg_other_worker"
    elif invalid == "wrong_argv": receipt["argv"] = ["echo", "passed"]
    elif invalid == "failed": receipt["status"] = "failed"
    elif invalid == "nonzero": receipt["evidence"]["exitCode"] = 1
    elif invalid == "missing_stream_bytes": receipt["evidence"]["stdout"].pop("previewBase64")
    elif invalid == "corrupt_stream": receipt["evidence"]["stdout"]["digest"] = "sha256:" + "f" * 64
    elif invalid == "old_sequence": receipt["observationSequence"] = 0
    elif invalid == "wrong_order_commands": order = replace(order, verification_requests=("echo passed",))
    elif invalid.startswith("failed_") or invalid == "failure_reason":
        receipt["status"] = "failed"
        receipt["evidence"].update(status="failed", exitCode={
            "failed_zero_exit": 0, "failed_negative_exit": -1, "failed_bool_exit": True,
            "failure_reason": 1,
        }[invalid])
        if invalid == "failure_reason": receipt["evidence"]["failureReason"] = "check_timeout"
    elif invalid in {"inconclusive", "unknown"}:
        receipt["status"] = invalid
        receipt["evidence"].update(status=invalid, exitCode=None)
    with pytest.raises(ContractValidationError):
        parse_execution_candidate(assignment, candidate, implementation_order=order,
                                  required_checks=checks, receipt_documents=documents)


def test_capture_retains_full_immutable_receipts_after_capability_revocation(tmp_path):
    cap, registry, context, *_ = _gated(tmp_path)
    original = cap._run_check

    def check(item, worker_context):
        return {**original(item, worker_context), "evidence": {
            "stdout": DeterministicVerifier._stream_evidence(b"entire actual feedback\n"),
            "stderr": DeterministicVerifier._stream_evidence(b""),
        }}

    cap._run_check = check
    displayed = _check_all(registry, context)
    assert "previewBase64" not in displayed[0].output, "display projection remains compact"
    snapshot = cap.capture_checked_candidate(context=context)
    assert snapshot is not None and len(snapshot.receipts_json) == 2
    assert "previewBase64" in snapshot.receipts_json[0], "capture precedes display-only projection"
    serialized = snapshot.receipts_json
    cap.close()
    assert snapshot.receipts_json == serialized
    assert cap.capture_checked_candidate(context=context) is None
    assert not registry.execute("executor_check", {"check": 1}, context).ok
    with pytest.raises(FrozenInstanceError):
        snapshot.patch_digest = "changed"


def test_failed_check_can_be_captured_but_cannot_native_complete(tmp_path):
    cap, registry, context, state, store, channel, *_ = _gated(tmp_path)
    state["b"] = "failed"
    results = _check_all(registry, context)
    assert results[0].ok and not results[1].ok
    assert cap.completion_pending, "the native completion gate still requires every check to pass"
    snapshot = cap.capture_checked_candidate(context=context)
    assert snapshot is not None
    assert [json.loads(value)["status"] for value in snapshot.receipts_json] == ["passed", "failed"]
    result = _submit(registry, context)
    assert not result.ok and not result._implementation_submitted
    assert store.read(channel.write_key) is None
    pending = cap.capture_checked_candidate(context=context)
    assert pending is not None and pending.handoff_reason == "executor_submission_feedback_pending"
    assert pending.patch_digest == snapshot.patch_digest
    assert pending.receipts_json == snapshot.receipts_json, "Rejected completion preserves facts, not completion authority"


@pytest.mark.parametrize("check_status", ["passed", "failed"])
def test_unobserved_write_error_with_fresh_receipts_is_repair_only(tmp_path, check_status):
    cap, registry, context, state, store, channel, *_ = _gated(tmp_path)
    _check_all(registry, context)
    cap.observe_tool_result("edit_file", {"path": "app.py"},
                            ToolResult(ok=False, output="anchor not found"), context=context)
    assert cap.capture_checked_candidate(context=context) is None, "the write error invalidated old receipts"
    state["b"] = check_status
    _check_all(registry, context)
    snapshot = cap.capture_checked_candidate(context=context)
    assert snapshot is not None
    assert snapshot.handoff_reason == "executor_write_feedback_pending"
    assert [json.loads(value)["status"] for value in snapshot.receipts_json] == ["passed", check_status]
    assert cap.completion_pending, "checks cannot acknowledge a write error on behalf of the owner"
    rejected = _submit(registry, context)
    assert not rejected.ok and not rejected._implementation_submitted
    assert store.read(channel.write_key) is None


@pytest.mark.parametrize("condition", ["missing", "inconclusive", "unknown", "wrong_owner", "write_error", "active_write", "abandoned", "closed", "declined_submit", "changed_bytes", "expired"])
def test_capture_never_promotes_unready_or_revoked_worker_state(tmp_path, condition):
    cap, registry, context, state, store, channel, target, *_ = _gated(tmp_path)
    if condition in {"inconclusive", "unknown"}: state["b"] = condition
    if condition != "missing": _check_all(registry, context)
    if condition == "wrong_owner": context = ToolContext(cwd=str(tmp_path))
    elif condition == "write_error": cap.observe_tool_result("edit_file", {"path": "app.py"}, ToolResult(ok=False, output="failed"), context=context)
    elif condition == "active_write": cap._active_writes += 1
    elif condition == "abandoned":
        event = threading.Event()
        event.set()
        context = replace(context, _tool_abandoned=event)
    elif condition == "closed": cap.close()
    elif condition == "declined_submit": channel.record_validation_error(ValueError("invalid declaration"))
    elif condition == "changed_bytes": target.write_text("changed after checks\n")
    elif condition == "expired":
        from minicode.task_deadline import TaskDeadlineExceeded
        context = replace(context, _task_deadline_monotonic=time.monotonic() - 1)
        with pytest.raises(TaskDeadlineExceeded):
            cap.capture_checked_candidate(context=context)
        return
    snapshot = cap.capture_checked_candidate(context=context)
    if condition == "declined_submit":
        assert snapshot is not None and snapshot.handoff_reason == "executor_submission_feedback_pending"
        assert len(snapshot.receipts_json) == 2
        assert all(json.loads(item)["patchDigest"] == snapshot.patch_digest for item in snapshot.receipts_json)
        assert store.read(channel.write_key) is None, "Rejected declarations remain unaccepted"
    else:
        assert snapshot is None
