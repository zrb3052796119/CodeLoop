"""Explicit host corrections preserve prior attempts and genuine failures.

These are contract/audit fixtures, not claims that a model found the error.
The verifier's real exception classification is tested in its own module.
"""

import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from minicode import multi_agent_contracts as contracts
from minicode import scheduler_audit as audit
from minicode.reviewer_verification import ReviewerProbe
from minicode.review_probe_source import review_probe_source_artifact_id
from test_review_coverage_contracts import _coverage, _marked_review


def _case_report(receipt, kind):
    passed = kind is None
    receipt.update(status="passed" if passed else "failed", reason=None)
    receipt["evidence"].update(status=receipt["status"], exitCode=0 if passed else 1)
    report = receipt["testReport"]
    report.update(collected=1, passed=int(passed), failed=int(not passed), errors=0, skipped=0)
    cases = [{"nodeId": "tests/test_auth.py::test_boundary", "outcome": receipt["status"], "failureKind": kind}]
    encoded = json.dumps({"junitDigest": report["reportDigest"], "cases": cases},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    report["caseReport"] = {"digest": "sha256:" + hashlib.sha256(encoded).hexdigest(), "cases": cases}


def _receipt_ref(receipt):
    material = "\0".join((receipt["workflowId"], receipt["assignmentId"], receipt["probeId"], str(receipt["sequence"])))
    return "artifact_review_probe_run_" + hashlib.sha256(material.encode()).hexdigest()[:32]


def _correction_ref(correction):
    material = "\0".join(correction[key] for key in (
        "workflowId", "assignmentId", "oldProbeId", "newProbeId", "newReceiptRef"))
    return "artifact_review_probe_correction_" + hashlib.sha256(material.encode()).hexdigest()[:32]


def _fixture(*, replacement_kind=None, assignment=None, review=None, evidence=None):
    if assignment is None:
        assignment, review = _marked_review()
    coverage, initial = _coverage(assignment, review, evidence=evidence)
    template = initial[coverage["probes"][0]["receiptRef"]]
    ids = sorted(item.criterion_id for item in assignment.acceptance_criteria)
    documents, probes = {}, []
    for sequence, setup, kind in ((1, "'/absolute'", "exception"), (2, "'relative'", replacement_kind)):
        source = ReviewerProbe.create(criterion_ids=ids, scenario="Check relative-scope public behavior.",
                                      code=f"def test_boundary():\n    scope = {setup}\n    result = run(scope)\n    assert result == 3\n").to_dict()
        source_ref = review_probe_source_artifact_id(source["probeId"])
        receipt = copy.deepcopy(template)
        receipt.update(probeId=source["probeId"], sourceRef=source_ref, sequence=sequence)
        _case_report(receipt, kind)
        receipt_ref = _receipt_ref(receipt)
        documents.update({source_ref: source, receipt_ref: receipt})
        probes.append({"probeId": source["probeId"], "sourceRef": source_ref, "receiptRef": receipt_ref})
    old, new = probes
    correction = {
        "correctionVersion": 1, "resultType": "ReviewProbeCorrection.v1",
        "workflowId": assignment.workflow_id, "taskId": assignment.task_id,
        "assignmentId": assignment.assignment_id, "patchDigest": review["patchDigest"],
        "oldProbeId": old["probeId"], "newProbeId": new["probeId"],
        "oldSourceRef": old["sourceRef"], "newSourceRef": new["sourceRef"],
        "oldReceiptRefs": [old["receiptRef"]], "newReceiptRef": new["receiptRef"],
        "sourceEvidence": [{"path": "minicode/auth.py", "startLine": 1, "endLine": 3,
                            "sourceDigest": "sha256:" + "a" * 64}],
        "reason": "The observed public API requires a relative scope; assertions are unchanged.",
    }
    ref = _correction_ref(correction)
    documents[ref] = correction
    documents["artifact_assignment_reviewer"] = {
        "resultType": "AssignmentArtifact.v1", "stage": "reviewer", "assignment": assignment.to_dict(),
    }
    coverage["probes"] = [new]
    for row in coverage["criteria"]:
        row["probeIds"] = [new["probeId"]]
    coverage_ref = "artifact_review_coverage_" + assignment.assignment_id
    documents[coverage_ref] = coverage
    review["coverageArtifactRef"] = coverage_ref
    evidence = template["evidence"]
    checks = (SimpleNamespace(check_id=evidence["checkId"], kind=evidence["kind"], argv=tuple(evidence["argv"])),)
    return assignment, review, documents, correction, checks


def _parse(assignment, documents, correction, checks):
    return contracts.parse_review_probe_correction(assignment, correction,
                                                  artifact_documents=documents, required_checks=checks)


def _audit(assignment, review, documents, checks, **extra):
    return audit.validate_review_coverage(
        review=review, assignment=assignment, documents=documents,
        role_artifact_ids=extra.pop("role_artifact_ids", set()),
        prior_reviewer_ids={assignment.assignment_id}, requested_checks=checks,
        patch_digest=review["patchDigest"], **extra,
    )


@pytest.mark.parametrize("kind", ["exception", "assertion"])
def test_explicit_fixture_correction_preserves_every_classified_failure(kind):
    assignment, review, documents, correction, checks = _fixture()
    _case_report(documents[correction["oldReceiptRefs"][0]], kind)
    before = copy.deepcopy(documents)
    assert _parse(assignment, documents, correction, checks) == correction
    assert _audit(assignment, review, documents, checks) is None
    assert documents == before
    assert documents[correction["oldReceiptRefs"][0]]["status"] == "failed"
    no_authority = {ref: doc for ref, doc in documents.items() if ref != _correction_ref(correction)}
    with pytest.raises(audit.SchedulerAuditValidationError, match="active probe"):
        _audit(assignment, review, no_authority, checks)


@pytest.mark.parametrize("kind", ["unknown", None, "unclassified", "inconclusive"])
def test_any_previous_passed_or_unclassified_attempt_cannot_be_retired(kind):
    assignment, _review, documents, correction, checks = _fixture()
    receipt = documents[correction["oldReceiptRefs"][0]]
    if kind == "unclassified":
        receipt["testReport"].pop("caseReport")
    elif kind == "inconclusive":
        receipt.update(status="inconclusive", reason="unclassified setup failure")
    else:
        _case_report(receipt, kind)
    with pytest.raises(contracts.ContractValidationError):
        _parse(assignment, documents, correction, checks)


@pytest.mark.parametrize("corruption", ["omitted_history", "missing_old_source", "stale_replacement",
                                       "wrong_command", "wrong_owner", "no_anchor", "scope", "case_digest"])
def test_correction_cannot_rebind_history_command_owner_or_host_anchors(corruption):
    assignment, _review, documents, correction, checks = _fixture()
    old = documents[correction["oldReceiptRefs"][0]]
    new = documents[correction["newReceiptRef"]]
    if corruption == "omitted_history":
        another = copy.deepcopy(old)
        another["sequence"] = 3
        documents[_receipt_ref(another)] = another
    elif corruption == "missing_old_source":
        documents.pop(correction["oldSourceRef"])
    elif corruption == "stale_replacement":
        new["patchDigest"] = "sha256:" + "f" * 64
    elif corruption == "wrong_command":
        new["evidence"]["argv"].append("--unapproved")
        new["evidence"]["commandDigest"] = "sha256:" + hashlib.sha256("\0".join(new["evidence"]["argv"]).encode()).hexdigest()
    elif corruption == "wrong_owner":
        correction["assignmentId"] = "asg_other_owner"
    elif corruption == "no_anchor":
        correction["sourceEvidence"] = []
    elif corruption == "scope":
        correction["sourceEvidence"][0]["path"] = ".env"
    else:
        old["testReport"]["caseReport"]["digest"] = "sha256:" + "c" * 64
    with pytest.raises(contracts.ContractValidationError):
        _parse(assignment, documents, correction, checks)


def test_corrected_probe_with_real_assertion_failure_stays_active_and_blocks_approval():
    assignment, review, documents, correction, checks = _fixture(replacement_kind="assertion")
    assert _parse(assignment, documents, correction, checks) == correction
    with pytest.raises(audit.SchedulerAuditValidationError, match="coverage"):
        _audit(assignment, review, documents, checks)
    review["verdict"] = "inconclusive"
    review["criteria"][0]["status"] = "failed"
    assert _audit(assignment, review, documents, checks) is None


def test_correction_is_not_authorized_by_a_role_result_slot():
    assignment, review, documents, correction, checks = _fixture()
    with pytest.raises(audit.SchedulerAuditValidationError, match="worker result"):
        _audit(assignment, review, documents, checks, role_artifact_ids={_correction_ref(correction)})


def test_correcting_one_setup_cannot_hide_another_genuine_requirement_failure():
    assignment, review, documents, correction, checks = _fixture()
    source = documents[correction["newSourceRef"]]
    failing = ReviewerProbe.create(criterion_ids=source["criterionIds"], scenario="Another independent requirement.",
                                    code=source["code"]).to_dict()
    source_ref = review_probe_source_artifact_id(failing["probeId"])
    receipt = copy.deepcopy(documents[correction["newReceiptRef"]])
    receipt.update(probeId=failing["probeId"], sourceRef=source_ref, sequence=3)
    _case_report(receipt, "assertion")
    documents.update({source_ref: failing, _receipt_ref(receipt): receipt})
    assert _parse(assignment, documents, correction, checks) == correction
    with pytest.raises(audit.SchedulerAuditValidationError, match="active probe"):
        _audit(assignment, review, documents, checks)


def test_repair_refs_retain_old_failure_and_append_correction_receipt():
    assignment, _review, documents, correction, checks = _fixture(replacement_kind="assertion")
    refs = audit._review_probe_repair_refs(documents, assignment_id=assignment.assignment_id,
                                          patch_digest=correction["patchDigest"], requested_checks=checks)
    assert correction["oldSourceRef"] in refs and correction["oldReceiptRefs"][0] in refs
    assert correction["newSourceRef"] in refs and correction["newReceiptRef"] in refs
    assert refs[-1] == _correction_ref(correction)


def test_old_attempts_from_previous_executor_require_their_real_assignment():
    assignment, _review, documents, correction, checks = _fixture()
    prior = replace(assignment, assignment_id="asg_prior_executor", role=contracts.WorkerRole.EXECUTOR,
                    mode="implement", expected_result_type="ExecutionResult.v1")
    old_ref = correction["oldReceiptRefs"][0]
    receipt = documents.pop(old_ref)
    receipt["assignmentId"] = prior.assignment_id
    new_ref = _receipt_ref(receipt)
    documents[new_ref] = receipt
    correction["oldReceiptRefs"] = [new_ref]
    with pytest.raises(contracts.ContractValidationError, match="owner"):
        _parse(assignment, documents, correction, checks)
    # Only the real owner identity is reused to validate the historical receipt;
    # no Executor is granted authority to publish a correction or approval.
    documents["artifact_prior_assignment"] = {
        "resultType": "AssignmentArtifact.v1", "stage": "executor", "assignment": prior.to_dict(),
    }
    assert _parse(assignment, documents, correction, checks) == correction


def test_prior_correction_remains_auditable_at_the_next_reviewer_revision():
    assignment, review, documents, correction, checks = _fixture()
    current = replace(assignment, assignment_id="asg_next_reviewer")
    documents["artifact_next_assignment"] = {
        "resultType": "AssignmentArtifact.v1", "stage": "reviewer", "assignment": current.to_dict(),
    }
    review["assignmentId"] = current.assignment_id
    review["coverageArtifactRef"] = "artifact_review_coverage_" + current.assignment_id
    coverage = copy.deepcopy(documents["artifact_review_coverage_" + assignment.assignment_id])
    coverage["assignmentId"] = current.assignment_id
    receipt = copy.deepcopy(documents[correction["newReceiptRef"]])
    receipt.update(assignmentId=current.assignment_id, sequence=3)
    receipt_ref = _receipt_ref(receipt)
    documents[receipt_ref] = receipt
    coverage["probes"][0]["receiptRef"] = receipt_ref
    documents[review["coverageArtifactRef"]] = coverage
    assert audit.validate_review_coverage(
        review=review, assignment=current, documents=documents, role_artifact_ids=set(),
        prior_reviewer_ids={assignment.assignment_id, current.assignment_id}, requested_checks=checks,
        patch_digest=review["patchDigest"],
    ) is None
    documents.pop(correction["oldReceiptRefs"][0])
    with pytest.raises(audit.SchedulerAuditValidationError, match="correction evidence"):
        audit.validate_review_coverage(
            review=review, assignment=current, documents=documents, role_artifact_ids=set(),
            prior_reviewer_ids={assignment.assignment_id, current.assignment_id}, requested_checks=checks,
            patch_digest=review["patchDigest"],
        )


def test_correction_chain_replays_in_a_real_durable_workflow_archive(tmp_path, monkeypatch):
    from test_review_coverage_audit import _marked_archive, _replay

    archived, documents, assignment, review, old_coverage = _marked_archive(tmp_path, monkeypatch)
    original_probe = old_coverage["probes"][0]
    evidence = documents[original_probe["receiptRef"]]["evidence"]
    documents.pop(original_probe["sourceRef"])
    documents.pop(original_probe["receiptRef"])
    _assignment, _review, extra, correction, _checks = _fixture(
        assignment=assignment, review=review, evidence=evidence,
    )
    extra.pop("artifact_assignment_reviewer")  # retain the actual archived Assignment wrapper
    documents.update(extra)
    replay = _replay(archived, documents)
    assert replay.trace["committed"]
    assert any(item.artifact_id == correction["oldReceiptRefs"][0] for item in replay.artifacts)
    assert any(item.artifact_id == _correction_ref(correction) for item in replay.artifacts)
    documents.pop(_correction_ref(correction))
    with pytest.raises(audit.SchedulerAuditValidationError, match="active probe"):
        _replay(archived, documents)
