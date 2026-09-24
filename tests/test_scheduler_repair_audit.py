"""Audit replays every implementation revision and rejects stale evidence."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from minicode.scheduler_audit import SchedulerAuditValidationError, project_scheduler_audit
import minicode.scheduler_audit as audit_module
from minicode.workflow_workspace import WorkflowIsolationError, WorkflowWorkspaceTransaction
from test_workflow_implementation_repair import _repair_run


def replay(audit, *, artifacts=None, trace=None):
    return project_scheduler_audit(
        profile=audit.profile,
        plan=audit.plan,
        trace=audit.trace if trace is None else trace,
        artifacts=audit.artifacts if artifacts is None else artifacts,
        contract_mode="v2",
    )


@pytest.mark.parametrize("rejection", ["verifier", "reviewer"])
def test_repair_audit_replays_complete_revision_chain(tmp_path, monkeypatch, rejection):
    result, _roles, _inputs, audits = _repair_run(tmp_path, monkeypatch, rejection=rejection)
    assert result.ok, result.output
    assert len(audits) == 1
    audit = audits[0]
    assert replay(audit).trace == audit.trace
    documents = [json.loads(artifact.content) for artifact in audit.artifacts]
    assert sum(item.get("resultType") == "ExecutionResult.v1" for item in documents) == 2


@pytest.mark.parametrize("forgery", [
    "stale_verification_assignment", "stale_matrix_diff", "repair_source",
    "repair_previous_patch", "missing_repair_artifact", "old_failure_status",
    "repair_order_action",
])
def test_repair_audit_rejects_rebound_or_missing_historical_evidence(
    tmp_path, monkeypatch, forgery
):
    result, _roles, _inputs, audits = _repair_run(tmp_path, monkeypatch, rejection="reviewer")
    assert result.ok, result.output
    audit = audits[0]
    artifacts = list(audit.artifacts)
    documents = {item.artifact_id: json.loads(item.content) for item in artifacts}
    executions = [
        documents[item["artifactId"]] for item in audit.trace["transitions"]
        if item["stage"] == "executor" and item["outcome"] == "completed"
    ]
    verifications = [
        item["artifactId"] for item in audit.trace["transitions"]
        if item["stage"] == "verifier" and item["outcome"] == "completed"
    ]
    repair_id = next(key for key, value in documents.items() if value.get("resultType") == "ImplementationRepairRequest.v1")
    if forgery == "missing_repair_artifact":
        artifacts = [item for item in artifacts if item.artifact_id != repair_id]
    else:
        if forgery == "stale_verification_assignment":
            target = verifications[0]
            documents[target]["executionAssignmentId"] = executions[-1]["assignmentId"]
        elif forgery == "stale_matrix_diff":
            matrices = [key for key, value in documents.items() if value.get("resultType") == "AcceptanceEvidenceMatrix.v1"]
            target = next(key for key in matrices if documents[key]["verificationArtifactRef"] == verifications[-1])
            older = next(key for key in matrices if documents[key]["verificationArtifactRef"] == verifications[0])
            documents[target]["frozenDiffRef"] = documents[older]["frozenDiffRef"]
        elif forgery == "old_failure_status":
            target = documents[repair_id]["sourceArtifactRef"]
            documents[target]["verdict"] = "approved"
        elif forgery == "repair_order_action":
            target = executions[-1]["orderId"]
            documents[target]["steps"][0]["action"] = "Unapproved replacement work"
        else:
            target = repair_id
            key = "sourceArtifactRef" if forgery == "repair_source" else "previousPatchDigest"
            documents[target][key] = verifications[-1] if forgery == "repair_source" else executions[-1]["changeSet"]["patchDigest"]
        artifacts = [
            replace(item, content=json.dumps(documents[target], sort_keys=True, separators=(",", ":")))
            if item.artifact_id == target else item
            for item in artifacts
        ]
    with pytest.raises(SchedulerAuditValidationError):
        replay(audit, artifacts=tuple(artifacts))


def test_unchanged_repair_has_a_replayable_failed_audit(tmp_path, monkeypatch):
    result, _roles, _inputs, audits = _repair_run(tmp_path, monkeypatch, rejection="verifier", always_bad=True)
    assert not result.ok
    assert len(audits) == 1
    assert replay(audits[0]) == audits[0]


def test_failed_repair_preparation_has_a_replayable_failed_audit(tmp_path, monkeypatch):
    def unavailable(_self, _frozen):
        raise WorkflowIsolationError("repair workspace unavailable")

    monkeypatch.setattr(WorkflowWorkspaceTransaction, "resume_for_repair", unavailable)
    result, _roles, _inputs, audits = _repair_run(tmp_path, monkeypatch, rejection="verifier")
    assert not result.ok
    assert len(audits) == 1
    assert replay(audits[0]) == audits[0]


def test_commit_ready_binds_latest_repair_verification_and_matrix(tmp_path, monkeypatch):
    result, _roles, _inputs, audits = _repair_run(tmp_path, monkeypatch, rejection="reviewer")
    assert result.ok
    audit = audits[0]
    artifacts = {item.artifact_id: item for item in audit.artifacts}
    documents = {key: json.loads(item.content) for key, item in artifacts.items()}
    verification_ids = [item["artifactId"] for item in audit.trace["transitions"] if item["stage"] == "verifier" and item["outcome"] == "completed"]
    review_id = next(item["artifactId"] for item in audit.trace["transitions"] if item["stage"] == "reviewer" and item["outcome"] == "completed")
    request = next(item for item in documents.values() if item.get("resultType") == "WorkflowRequestArtifact.v1")
    matrix = next(item for item in documents.values() if item.get("resultType") == "AcceptanceEvidenceMatrix.v1" and item["verificationArtifactRef"] == verification_ids[-1])
    diff = documents[matrix["frozenDiffRef"]]
    fields = dict(
        workflow_id=audit.workflow_id, task_id=audit.task_id, rule_id=audit.plan.rule_id,
        request_digest=request["requestDigest"], base_digest=diff["baseDigest"], patch_digest=diff["patchDigest"],
        verification_digest=audit_module._artifact_content_digest(artifacts[verification_ids[-1]]),
        review_digest=audit_module._artifact_content_digest(artifacts[review_id]),
        approval_digest=None, artifact_set_digest=audit_module._artifact_set_digest(audit.artifacts),
    )
    ready = audit_module.project_scheduler_commit_ready(**fields)
    audit_module._validate_commit_ready_against_audit(ready, audit)
    fields["verification_digest"] = audit_module._artifact_content_digest(artifacts[verification_ids[0]])
    stale = audit_module.project_scheduler_commit_ready(**fields)
    with pytest.raises(SchedulerAuditValidationError):
        audit_module._validate_commit_ready_against_audit(stale, audit)


def test_truncated_failed_diff_stops_before_repair_and_retains_failed_audit(
    tmp_path, monkeypatch
):
    write_text = Path.write_text

    def large_first_candidate(path, data, *args, **kwargs):
        if path.name == "allowed.txt" and data == "almost":
            data = "almost" + "x" * 20_000
        return write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", large_first_candidate)
    result, roles, _inputs, audits = _repair_run(tmp_path, monkeypatch, rejection="verifier")
    assert not result.ok
    assert roles.count("executor") == 1
    assert "reviewer" not in roles
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "before"
    assert len(audits) == 1
    audit = audits[0]
    diffs = [json.loads(item.content) for item in audit.artifacts if json.loads(item.content).get("diffVersion") == 1]
    assert [item["complete"] for item in diffs] == [False]
    assert not any(item["outcome"] == "repairing" for item in audit.trace["transitions"])
    assert replay(audit) == audit
