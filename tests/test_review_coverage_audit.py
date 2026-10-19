"""Durable audit validates coverage itself, including legacy-note bypasses.

The base archive is produced by real isolated checks and commit. Additional
host coverage records are contract fixtures; independent probe execution has
its own workflow integration tests and is not claimed by this module.
"""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from minicode.multi_agent_contracts import (
    REVIEW_COVERAGE_CONSTRAINT_ID, REVIEW_COVERAGE_CONSTRAINT_TEXT, parse_assignment,
)
from minicode.scheduler_artifacts import SchedulerArtifact
import minicode.scheduler_audit as audit_module
from minicode.scheduler_audit import (
    SchedulerAuditJournal, SchedulerAuditValidationError, project_scheduler_audit,
)
from test_review_coverage_contracts import _coverage
from test_scheduler_checked_candidate import _candidate_run


def test_shared_handoff_guard_rejects_marked_approval_before_it_has_a_ref():
    from test_review_coverage_contracts import _marked_review

    assignment, review = _marked_review()
    with pytest.raises(SchedulerAuditValidationError, match="coverage"):
        audit_module.validate_review_coverage(
            review=review, assignment=assignment, documents={}, role_artifact_ids=set(),
            prior_reviewer_ids={assignment.assignment_id}, requested_checks=(),
        )


def test_shared_handoff_guard_requires_real_current_authorized_evidence():
    from test_review_coverage_contracts import _marked_review

    assignment, review = _marked_review()
    coverage, documents = _coverage(assignment, review)
    ref = "artifact_review_coverage_" + assignment.assignment_id
    review["coverageArtifactRef"] = ref
    documents[ref] = coverage
    receipt_ref = coverage["probes"][0]["receiptRef"]
    evidence = documents[receipt_ref]["evidence"]
    check = SimpleNamespace(check_id=evidence["checkId"], kind=evidence["kind"], argv=tuple(evidence["argv"]))

    def validate(values, **extra):
        return audit_module.validate_review_coverage(
            review=review, assignment=assignment, documents=values,
            role_artifact_ids=extra.pop("role_artifact_ids", set()),
            prior_reviewer_ids={assignment.assignment_id}, requested_checks=(check,),
            patch_digest=extra.pop("patch_digest", review["patchDigest"]), **extra,
        )

    assert validate(documents) is None
    with pytest.raises(SchedulerAuditValidationError, match="coverage"):
        validate({key: value for key, value in documents.items() if key != ref})
    with pytest.raises(SchedulerAuditValidationError, match="current"):
        validate(documents, patch_digest="sha256:" + "f" * 64)
    with pytest.raises(SchedulerAuditValidationError, match="worker result"):
        validate(documents, role_artifact_ids={receipt_ref})
    altered = copy.deepcopy(documents)
    raw = altered[receipt_ref]["evidence"]
    raw["argv"].append("--unapproved-option")
    raw["commandDigest"] = "sha256:" + hashlib.sha256("\0".join(raw["argv"]).encode()).hexdigest()
    with pytest.raises(SchedulerAuditValidationError, match="approved checks"):
        validate(altered)


def test_shared_handoff_guard_preserves_unmarked_legacy_approval():
    from test_reviewer_result_submission import _approval, _bound, _execute

    assignment, store, channel, tool, _ = _bound()
    assert _execute(tool, channel, _approval()).ok
    review = json.loads(store.read(channel.write_key).content)
    assert audit_module.validate_review_coverage(
        review=review, assignment=assignment, documents={}, role_artifact_ids={channel.write_key},
        prior_reviewer_ids={assignment.assignment_id}, requested_checks=(),
        patch_digest=review["patchDigest"],
    ) is None


def _artifacts(documents):
    return tuple(SchedulerArtifact(key, json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                 separators=(",", ":"))) for key, value in documents.items())


def _replay(audit, documents):
    return project_scheduler_audit(profile=audit.profile, plan=audit.plan, trace=audit.trace,
                                   artifacts=_artifacts(documents), contract_mode="v2")


def _marked_archive(tmp_path, monkeypatch):
    result, legacy, _target, _checks, _roles = _candidate_run(tmp_path, monkeypatch)
    assert result.ok and legacy.trace["committed"]
    documents = {artifact.artifact_id: json.loads(artifact.content) for artifact in legacy.artifacts}
    assert _replay(legacy, documents) == legacy, "unmarked historical contracts remain readable"
    assignment_doc = next(value for value in documents.values()
                          if value.get("resultType") == "AssignmentArtifact.v1" and value["stage"] == "reviewer")
    assignment_doc["assignment"]["constraints"].append({
        "id": REVIEW_COVERAGE_CONSTRAINT_ID, "kind": "policy", "text": REVIEW_COVERAGE_CONSTRAINT_TEXT,
    })
    assignment = parse_assignment(assignment_doc["assignment"])
    review_ref = next(entry["artifactId"] for entry in legacy.trace["transitions"]
                      if entry["stage"] == "reviewer" and entry["outcome"] == "completed")
    review = documents[review_ref]
    evidence = next(value for value in documents.values() if value.get("evidenceVersion") == 1)
    coverage, extra = _coverage(assignment, review, evidence=evidence)
    ref = "artifact_review_coverage_" + assignment.assignment_id
    review["coverageArtifactRef"] = ref
    documents.update(extra)
    documents[ref] = coverage
    return legacy, documents, assignment, review, coverage


@pytest.mark.parametrize("large_source", [False, True])
def test_marked_coverage_replays_and_round_trips_durable_audit(tmp_path, monkeypatch, large_source):
    legacy, documents, assignment, _review, coverage = _marked_archive(tmp_path, monkeypatch)
    if large_source:
        from minicode.reviewer_verification import ReviewerProbe
        from minicode.review_probe_source import review_probe_source_artifact_id

        old_probe = coverage["probes"][0]
        source = documents.pop(old_probe["sourceRef"])
        code = source["code"] + "# Complete independent fixture source; no truncation.\n" * 600
        source = ReviewerProbe.create(criterion_ids=source["criterionIds"],
                                      scenario=source["scenario"], code=code).to_dict()
        source_ref = review_probe_source_artifact_id(source["probeId"])
        receipt = documents.pop(old_probe["receiptRef"])
        receipt.update(probeId=source["probeId"], sourceRef=source_ref)
        material = "\0".join((assignment.workflow_id, assignment.assignment_id, source["probeId"], "1"))
        receipt_ref = "artifact_review_probe_run_" + hashlib.sha256(material.encode()).hexdigest()[:32]
        old_probe.update(probeId=source["probeId"], sourceRef=source_ref, receiptRef=receipt_ref)
        for criterion in coverage["criteria"]:
            criterion["probeIds"] = [source["probeId"]]
        documents.update({source_ref: source, receipt_ref: receipt})
        assert len(json.dumps(source).encode()) > 20 * 1024
    audit = _replay(legacy, documents)
    assert audit.trace["committed"]
    directory = tmp_path / "independent-audit"
    directory.mkdir()
    journal = SchedulerAuditJournal(directory)
    artifacts = {item.artifact_id: item for item in audit.artifacts}
    verifier_ref = next(item["artifactId"] for item in audit.trace["transitions"]
                        if item["stage"] == "verifier" and item["outcome"] == "completed")
    review_ref = next(item["artifactId"] for item in audit.trace["transitions"]
                      if item["stage"] == "reviewer" and item["outcome"] == "completed")
    request = next(value for value in documents.values() if value.get("resultType") == "WorkflowRequestArtifact.v1")
    diff = next(value for value in documents.values() if value.get("diffVersion") == 1)
    ready = audit_module.project_scheduler_commit_ready(
        workflow_id=audit.workflow_id, task_id=audit.task_id, rule_id=audit.plan.rule_id,
        request_digest=request["requestDigest"], base_digest=diff["baseDigest"], patch_digest=diff["patchDigest"],
        verification_digest=audit_module._artifact_content_digest(artifacts[verifier_ref]),
        review_digest=audit_module._artifact_content_digest(artifacts[review_ref]), approval_digest=None,
        artifact_set_digest=audit_module._artifact_set_digest(audit.artifacts),
    )
    journal.write_commit_ready(ready)
    journal.write(audit)
    assert journal.get(audit.workflow_id) == audit


def test_audit_rejects_missing_or_rebound_coverage_and_unapproved_commands(tmp_path, monkeypatch):
    legacy, originals, assignment, review, coverage = _marked_archive(tmp_path, monkeypatch)
    assert _replay(legacy, originals).trace["committed"]
    review_ref = next(key for key, value in originals.items() if value is review)
    coverage_ref = "artifact_review_coverage_" + assignment.assignment_id
    receipt_ref = coverage["probes"][0]["receiptRef"]
    for corruption in ("missing_ref", "missing_evidence", "stale_patch", "unapproved_command", "bad_marker"):
        documents = copy.deepcopy(originals)
        if corruption == "missing_ref":
            documents[review_ref].pop("coverageArtifactRef")
        elif corruption == "missing_evidence":
            documents.pop(coverage_ref)
        elif corruption == "stale_patch":
            documents[receipt_ref]["patchDigest"] = "sha256:" + "f" * 64
        elif corruption == "unapproved_command":
            raw = documents[receipt_ref]["evidence"]
            raw["argv"].append("--unapproved-option")
            raw["commandDigest"] = "sha256:" + hashlib.sha256("\0".join(raw["argv"]).encode()).hexdigest()
        else:
            doc = next(value for value in documents.values()
                       if value.get("resultType") == "AssignmentArtifact.v1" and value["stage"] == "reviewer")
            doc["assignment"]["constraints"][-1]["kind"] = "scope"
        with pytest.raises(SchedulerAuditValidationError):
            _replay(legacy, documents)


@pytest.mark.parametrize("same_probe,new_status", [(False, "failed"), (True, "failed"), (True, "inconclusive")])
def test_audit_cannot_drop_a_failed_probe_or_select_an_older_pass(tmp_path, monkeypatch, same_probe, new_status):
    legacy, documents, assignment, _review, coverage = _marked_archive(tmp_path, monkeypatch)
    assert _replay(legacy, documents).trace["committed"]
    probe = coverage["probes"][0]
    receipt = copy.deepcopy(documents[probe["receiptRef"]])
    receipt.update(sequence=2, status="failed")
    receipt["evidence"].update(status="failed", exitCode=1)
    receipt["testReport"].update(passed=0, failed=1)
    if new_status == "inconclusive":
        receipt.update(status="inconclusive", reason="test setup did not execute an assertion", testReport=None)
    if not same_probe:
        from minicode.reviewer_verification import ReviewerProbe

        original = documents[probe["sourceRef"]]
        source = ReviewerProbe.create(criterion_ids=original["criterionIds"],
                                      scenario="Another independent boundary.", code=original["code"]).to_dict()
        receipt["probeId"] = source["probeId"]
        source_ref = "artifact_review_probe_source_" + hashlib.sha256(receipt["probeId"].encode()).hexdigest()[:32]
        documents[source_ref] = source
        receipt["sourceRef"] = source_ref
    material = "\0".join((assignment.workflow_id, assignment.assignment_id, receipt["probeId"], "2"))
    ref = "artifact_review_probe_run_" + hashlib.sha256(material.encode()).hexdigest()[:32]
    documents[ref] = receipt
    with pytest.raises(SchedulerAuditValidationError, match="active probe|newer probe"):
        _replay(legacy, documents)
