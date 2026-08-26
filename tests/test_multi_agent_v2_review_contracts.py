"""Closed verification, review, and conflict hand-off contracts."""

from __future__ import annotations

import pytest

from minicode.multi_agent_contracts import (
    ContractValidationError,
    parse_assignment,
    parse_conflict_package,
    parse_role_result,
    parse_verification_result,
)


PATCH_DIGEST = "sha256:" + "a" * 64


def _assignment(role: str, **overrides):
    modes = {
        "reviewer": "change_review",
        "adjudicator": "resolve_conflict",
    }
    result_types = {
        "reviewer": "ReviewVerdict.v1",
        "adjudicator": "AdjudicationVerdict.v1",
    }
    value = {
        "assignmentVersion": 1,
        "workflowId": "wf_12345678",
        "taskId": "task_12345678",
        "assignmentId": "asg_12345678",
        "role": role,
        "mode": modes[role],
        "objective": "Judge the frozen artifact against the criteria.",
        "scope": {
            "readRoots": ["."],
            "writePaths": ["minicode/auth.py"],
            "forbiddenPaths": [".env"],
        },
        "constraints": [],
        "acceptanceCriteria": [
            {"id": "ac1", "kind": "behavior", "text": "Login still works."},
            {"id": "ac2", "kind": "safety", "text": "Secrets stay hidden."},
        ],
        "inputRefs": [
            "artifact_execution",
            "artifact_verification",
            "artifact_test_log",
            "artifact_diff",
        ],
        "risk": "high",
        "budget": {
            "deadlineAt": "2026-08-26T12:00:00Z",
            "maxModelCalls": 4,
            "maxInputTokens": 100000,
            "maxOutputTokens": 8000,
        },
        "expectedResultType": result_types[role],
    }
    value.update(overrides)
    return value


def _verification(**overrides):
    value = {
        "verificationVersion": 1,
        "verificationId": "verification_12345678",
        "workflowId": "wf_12345678",
        "taskId": "task_12345678",
        "executionAssignmentId": "asg_executor1",
        "patchDigest": PATCH_DIGEST,
        "status": "passed",
        "checks": [
            {
                "checkId": "check_tests",
                "kind": "tests",
                "status": "passed",
                "exitCode": 0,
                "durationMs": 25,
                "artifactRef": "artifact_test_log",
            }
        ],
    }
    value.update(overrides)
    return value


def test_verification_result_is_deterministic_and_patch_bound() -> None:
    result = parse_verification_result(
        _verification(),
        workflow_id="wf_12345678",
        task_id="task_12345678",
        execution_assignment_id="asg_executor1",
        patch_digest=PATCH_DIGEST,
        required_check_ids=("check_tests",),
    )

    assert result["resultType"] == "VerificationResult.v1"
    assert result["status"] == "passed"

    with pytest.raises(ContractValidationError, match="patchDigest"):
        parse_verification_result(
            _verification(patchDigest="sha256:" + "b" * 64),
            workflow_id="wf_12345678",
            task_id="task_12345678",
            execution_assignment_id="asg_executor1",
            patch_digest=PATCH_DIGEST,
            required_check_ids=("check_tests",),
        )


def test_verification_status_must_match_required_check_evidence() -> None:
    failed_check = {
        **_verification()["checks"][0],
        "status": "failed",
        "exitCode": 1,
    }

    with pytest.raises(ContractValidationError, match="aggregate status"):
        parse_verification_result(
            _verification(checks=[failed_check]),
            workflow_id="wf_12345678",
            task_id="task_12345678",
            execution_assignment_id="asg_executor1",
            patch_digest=PATCH_DIGEST,
            required_check_ids=("check_tests",),
        )


def _review(**overrides):
    value = {
        "resultVersion": 1,
        "assignmentId": "asg_12345678",
        "patchDigest": PATCH_DIGEST,
        "verdict": "approved",
        "criteria": [
            {
                "criterionId": "ac1",
                "status": "satisfied",
                "evidenceRefs": ["artifact_test_log"],
            },
            {
                "criterionId": "ac2",
                "status": "satisfied",
                "evidenceRefs": ["artifact_diff"],
            },
        ],
        "blockingFindings": [],
        "warnings": [],
        "requestedChecks": [],
        "confidence": "high",
    }
    value.update(overrides)
    return value


def test_reviewer_approval_accounts_for_every_criterion_and_patch() -> None:
    assignment = parse_assignment(_assignment("reviewer"))

    result = parse_role_result(assignment, _review())

    assert result["resultType"] == "ReviewVerdict.v1"
    assert result["verdict"] == "approved"

    incomplete = _review(criteria=_review()["criteria"][:1])
    with pytest.raises(ContractValidationError, match="acceptance criteria"):
        parse_role_result(assignment, incomplete)


def test_reviewer_cannot_approve_unknown_criteria_or_blocking_findings() -> None:
    assignment = parse_assignment(_assignment("reviewer"))
    unknown = [
        {**item, "status": "unknown" if item["criterionId"] == "ac2" else "satisfied"}
        for item in _review()["criteria"]
    ]

    with pytest.raises(ContractValidationError, match="approved verdict"):
        parse_role_result(assignment, _review(criteria=unknown))
    with pytest.raises(ContractValidationError, match="approved verdict"):
        parse_role_result(
            assignment,
            _review(
                blockingFindings=[
                    {
                        "id": "finding_auth",
                        "severity": "high",
                        "statement": "Authorization regressed.",
                        "evidenceRefs": ["artifact_diff"],
                    }
                ]
            ),
        )


def _conflict(**overrides):
    value = {
        "conflictVersion": 1,
        "conflictId": "conflict_12345678",
        "workflowId": "wf_12345678",
        "taskId": "task_12345678",
        "kind": "fact",
        "risk": "low",
        "question": {
            "questionId": "question_auth",
            "prompt": "Which call path is active?",
            "choices": ["A", "B"],
        },
        "claims": [
            {
                "claimId": "claim_a",
                "choice": "A",
                "statement": "Path A is active.",
                "evidenceRefs": ["artifact_a"],
            },
            {
                "claimId": "claim_b",
                "choice": "B",
                "statement": "Path B is active.",
                "evidenceRefs": ["artifact_b"],
            },
        ],
        "evidenceDigest": "sha256:" + "c" * 64,
        "voteEligible": True,
    }
    value.update(overrides)
    return value


def test_conflict_package_restricts_voting_to_low_risk_finite_questions() -> None:
    package = parse_conflict_package(
        _conflict(),
        workflow_id="wf_12345678",
        task_id="task_12345678",
    )

    assert package["voteEligible"] is True

    with pytest.raises(ContractValidationError, match="voting"):
        parse_conflict_package(
            _conflict(risk="high"),
            workflow_id="wf_12345678",
            task_id="task_12345678",
        )


def test_adjudicator_verdict_is_bound_to_the_frozen_conflict() -> None:
    package = parse_conflict_package(
        _conflict(voteEligible=False),
        workflow_id="wf_12345678",
        task_id="task_12345678",
    )
    assignment_value = _assignment("adjudicator")
    assignment_value["inputRefs"] = ["conflict_12345678"]
    assignment = parse_assignment(assignment_value)
    verdict = {
        "resultVersion": 1,
        "assignmentId": "asg_12345678",
        "conflictId": "conflict_12345678",
        "decision": "select_a",
        "resolvedClaims": [
            {
                "claimId": "claim_a",
                "resolution": "Accepted from immutable source evidence.",
                "evidenceRefs": ["artifact_a"],
            }
        ],
        "conditions": [],
        "confidence": "high",
    }

    result = parse_role_result(
        assignment,
        verdict,
        conflict_package=package,
    )

    assert result["resultType"] == "AdjudicationVerdict.v1"
    assert result["decision"] == "select_a"

    with pytest.raises(ContractValidationError, match="conflictId"):
        parse_role_result(
            assignment,
            {**verdict, "conflictId": "conflict_different"},
            conflict_package=package,
        )
