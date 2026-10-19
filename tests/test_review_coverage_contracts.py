"""Marked review approval requires independently bound host coverage evidence."""

from dataclasses import replace
import copy
import hashlib
import json
import sys

import pytest

from minicode.deterministic_verifier import DeterministicVerifier
from minicode import multi_agent_contracts as contracts
from minicode.multi_agent_contracts import (
    AssignmentConstraint, ContractValidationError, parse_role_result,
)
from test_reviewer_result_submission import _approval, _bound, _execute


def _marked_review():
    assignment, store, channel, tool, _ = _bound()
    assert _execute(tool, channel, _approval()).ok
    document = json.loads(store.read(channel.write_key).content)
    assignment = replace(assignment, constraints=(*assignment.constraints, AssignmentConstraint(
        constraint_id="c_scheduler_review_coverage_v1", kind="policy",
        text="Approval requires host-bound independent review coverage.",
    )))
    return assignment, document


def test_marked_approval_cannot_use_legacy_note_without_host_coverage():
    assignment, document = _marked_review()
    with pytest.raises(ContractValidationError, match="coverage"):
        parse_role_result(assignment, document)


def _coverage(assignment, review, *, evidence=None):
    """Closed contract fixture; real independent execution is tested separately."""
    from minicode.reviewer_verification import ReviewerProbe

    code = "def test_independent_behavior():\n    assert 1 + 1 == 2\n"
    criterion_ids = sorted(item.criterion_id for item in assignment.acceptance_criteria)
    source = ReviewerProbe.create(criterion_ids=criterion_ids,
                                  scenario="Check a public failure boundary.", code=code).to_dict()
    probe_id = source["probeId"]
    source_ref = "artifact_review_probe_source_" + hashlib.sha256(probe_id.encode()).hexdigest()[:32]
    material = "\0".join((assignment.workflow_id, assignment.assignment_id, probe_id, "1"))
    receipt_ref = "artifact_review_probe_run_" + hashlib.sha256(material.encode()).hexdigest()[:32]
    argv = [sys.executable, "-m", "pytest", "-q", "tests/test_auth.py"]
    if evidence is None:
        evidence = {
            "evidenceVersion": 1, "checkId": "public", "kind": "tests", "argv": argv,
            "resolvedExecutable": sys.executable,
            "commandDigest": "sha256:" + hashlib.sha256("\0".join(argv).encode()).hexdigest(),
            "status": "passed", "exitCode": 0, "durationMs": 1, "failureReason": None,
            "stdout": DeterministicVerifier._stream_evidence(b"1 passed\n"),
            "stderr": DeterministicVerifier._stream_evidence(b""),
        }
    documents = {
        source_ref: source,
        receipt_ref: {"probeVersion": 1, "resultType": "ReviewProbeExecution.v1",
                      "workflowId": assignment.workflow_id, "taskId": assignment.task_id,
                      "assignmentId": assignment.assignment_id, "patchDigest": review["patchDigest"],
                      "probeId": probe_id, "sourceRef": source_ref, "sequence": 1,
                      "status": "passed", "reason": None, "evidence": copy.deepcopy(evidence),
                      "testReport": {"collected": 1, "passed": 1, "failed": 0, "errors": 0,
                                     "skipped": 0, "reportDigest": "sha256:" + "e" * 64}},
    }
    coverage = {
        "coverageVersion": 1, "resultType": "ReviewCoverageEvidence.v1", "workflowId": assignment.workflow_id,
        "taskId": assignment.task_id, "assignmentId": assignment.assignment_id, "patchDigest": review["patchDigest"],
        "criteria": [{"criterionId": criterion_id, "rationale": "The independent assertion checks this public boundary.",
                      "probeIds": [probe_id], "sourceEvidence": []} for criterion_id in criterion_ids],
        "probes": [{"probeId": probe_id, "sourceRef": source_ref, "receiptRef": receipt_ref}],
    }
    return coverage, documents


def test_bound_probe_approval_is_canonical_and_does_not_mutate_observations():
    assignment, review = _marked_review()
    review["coverageArtifactRef"] = "artifact_review_coverage_" + assignment.assignment_id
    parsed_review = parse_role_result(assignment, review)
    coverage, documents = _coverage(assignment, parsed_review)
    before = copy.deepcopy((coverage, documents))
    assert contracts.parse_review_coverage_evidence(assignment, coverage, review=parsed_review, artifact_documents=documents) == coverage
    assert (coverage, documents) == before


def _coverage_with_execution_report():
    assignment, review = _marked_review()
    coverage, documents = _coverage(assignment, review)
    receipt = documents[coverage["probes"][0]["receiptRef"]]
    report = receipt["testReport"]
    node_id = "tests/test_auth.py::test_independent_behavior"
    cases = [{"nodeId": node_id, "outcome": "passed", "failureKind": None}]
    classification = {"junitDigest": report["reportDigest"], "cases": cases}
    report["caseReport"] = {"cases": cases, "digest": _report_digest(classification)}
    execution = {"version": 1, "junitDigest": report["reportDigest"], "cases": [{
        "nodeId": node_id, "complete": True, "limitations": [], "assertions": [], "calls": []}]}
    report["executionReport"] = {**execution, "digest": _report_digest(execution)}
    return assignment, review, coverage, documents, report


def _report_digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def test_bound_execution_report_retains_closed_contract_and_original_observations():
    assignment, review, coverage, documents, _report = _coverage_with_execution_report()
    before = copy.deepcopy((coverage, documents))
    assert contracts.parse_review_coverage_evidence(assignment, coverage, review=review,
        artifact_documents=documents) == coverage
    assert (coverage, documents) == before


@pytest.mark.parametrize("corruption", ["missing_case_report", "foreign_junit", "execution_digest",
    "foreign_case", "case_digest", "extra_report_field", "extra_execution_field"])
def test_execution_report_requires_original_junit_case_binding_and_exact_fields(corruption):
    assignment, review, coverage, documents, report = _coverage_with_execution_report()
    execution = report["executionReport"]
    if corruption == "missing_case_report":
        report.pop("caseReport")
    elif corruption == "foreign_junit":
        execution["junitDigest"] = "sha256:" + "f" * 64
        execution["digest"] = _report_digest({key: value for key, value in execution.items() if key != "digest"})
    elif corruption == "execution_digest":
        execution["digest"] = "sha256:" + "f" * 64
    elif corruption == "foreign_case":
        execution["cases"][0]["nodeId"] = "tests/other.py::test_unrelated"
        execution["digest"] = _report_digest({key: value for key, value in execution.items() if key != "digest"})
    elif corruption == "case_digest":
        report["caseReport"]["digest"] = "sha256:" + "f" * 64
    elif corruption == "extra_report_field":
        report["extraAuthority"] = True
    else:
        execution["extraAuthority"] = True
    with pytest.raises(ContractValidationError):
        contracts.parse_review_coverage_evidence(assignment, coverage, review=review, artifact_documents=documents)


@pytest.mark.parametrize("corruption", [
    "missing_probe", "failed_probe", "inconclusive_probe", "wrong_patch", "wrong_owner",
    "source_changed", "source_namespace", "command_digest", "relative_executable", "bool_exit",
    "missing_criterion", "duplicate_criterion", "no_rationale", "no_criterion_evidence", "wrong_probe_criterion",
])
def test_coverage_cannot_manufacture_or_rebind_passing_evidence(corruption):
    assignment, review = _marked_review()
    coverage, documents = _coverage(assignment, review)
    probe = coverage["probes"][0]
    source, receipt = documents[probe["sourceRef"]], documents[probe["receiptRef"]]
    if corruption == "missing_probe":
        coverage["probes"] = []
    elif corruption == "failed_probe":
        receipt["status"] = "failed"
        receipt["evidence"].update(status="failed", exitCode=1)
    elif corruption == "inconclusive_probe":
        receipt["status"] = "inconclusive"
        receipt["reason"] = "timeout"
        receipt["evidence"].update(status="inconclusive", exitCode=None, failureReason="timeout")
    elif corruption == "wrong_patch":
        receipt["patchDigest"] = "sha256:" + "c" * 64
    elif corruption == "wrong_owner":
        receipt["assignmentId"] = "asg_other_reviewer"
    elif corruption == "source_changed":
        source["code"] += "# changed after execution\n"
    elif corruption == "source_namespace":
        documents["artifact_worker_result"] = source
        probe["sourceRef"] = receipt["sourceRef"] = "artifact_worker_result"
    elif corruption == "command_digest":
        receipt["evidence"]["argv"] += ["--new-unapproved-argument"]
    elif corruption == "relative_executable":
        receipt["evidence"]["resolvedExecutable"] = "python"
    elif corruption == "bool_exit":
        receipt["evidence"]["exitCode"] = False
    elif corruption == "missing_criterion":
        coverage["criteria"].pop()
    elif corruption == "duplicate_criterion":
        coverage["criteria"][1] = copy.deepcopy(coverage["criteria"][0])
    elif corruption == "no_rationale":
        coverage["criteria"][0]["rationale"] = " "
    elif corruption == "no_criterion_evidence":
        coverage["criteria"][0]["probeIds"] = []
    elif corruption == "wrong_probe_criterion":
        source["criterionIds"] = [coverage["criteria"][1]["criterionId"]]
    with pytest.raises(ContractValidationError):
        contracts.parse_review_coverage_evidence(assignment, coverage, review=review, artifact_documents=documents)


@pytest.mark.parametrize("path,start,end", [(".env", 1, 1), ("elsewhere.py", 1, 1),
                                           ("minicode/auth.py", True, 1), ("minicode/auth.py", 2, 1)])
def test_source_anchor_cannot_escape_scope_or_invent_line_range(path, start, end):
    assignment, review = _marked_review()
    coverage, documents = _coverage(assignment, review)
    coverage["criteria"][0].update(probeIds=[], sourceEvidence=[{
        "path": path, "startLine": start, "endLine": end, "sourceDigest": "sha256:" + "d" * 64,
    }])
    with pytest.raises(ContractValidationError):
        contracts.parse_review_coverage_evidence(assignment, coverage, review=review, artifact_documents=documents)


def test_negative_review_can_deliver_real_failure_without_claiming_coverage():
    assignment, review = _marked_review()
    review["verdict"] = "inconclusive"
    review["criteria"][0]["status"] = "failed"
    assert "coverageArtifactRef" not in parse_role_result(assignment, review)
    coverage, documents = _coverage(assignment, review)
    receipt = documents[coverage["probes"][0]["receiptRef"]]
    receipt["status"] = "failed"
    receipt["evidence"].update(status="failed", exitCode=1)
    receipt["testReport"].update(passed=0, failed=1)
    assert contracts.parse_review_coverage_evidence(assignment, coverage, review=review, artifact_documents=documents) == coverage


@pytest.mark.parametrize("report", [
    None,
    {"collected": 1, "passed": 0, "failed": 0, "errors": 0, "skipped": 1, "reportDigest": "sha256:" + "e" * 64},
    {"collected": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "reportDigest": "sha256:" + "e" * 64},
    {"collected": 1, "passed": True, "failed": 0, "errors": 0, "skipped": 0, "reportDigest": "sha256:" + "e" * 64},
    {"collected": 2, "passed": 1, "failed": 0, "errors": 0, "skipped": 0, "reportDigest": "sha256:" + "e" * 64},
])
def test_stdout_pass_cannot_replace_complete_host_test_report(report):
    assignment, review = _marked_review()
    coverage, documents = _coverage(assignment, review)
    receipt = documents[coverage["probes"][0]["receiptRef"]]
    assert receipt["evidence"]["stdout"]["preview"] == "1 passed\n"
    receipt["testReport"] = report
    with pytest.raises(ContractValidationError):
        contracts.parse_review_coverage_evidence(assignment, coverage, review=review, artifact_documents=documents)


def test_inconclusive_host_keeps_true_raw_pass_and_machine_skip_report():
    assignment, review = _marked_review()
    review["verdict"] = "inconclusive"
    coverage, documents = _coverage(assignment, review)
    receipt = documents[coverage["probes"][0]["receiptRef"]]
    receipt.update(status="inconclusive", reason="pytest reported only skipped tests")
    receipt["testReport"].update(passed=0, skipped=1)
    assert receipt["evidence"]["status"] == "passed" and receipt["evidence"]["exitCode"] == 0
    assert contracts.parse_review_coverage_evidence(assignment, coverage, review=review, artifact_documents=documents) == coverage
