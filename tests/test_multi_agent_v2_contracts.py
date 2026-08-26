from __future__ import annotations

import pytest

from minicode.multi_agent_contracts import (
    ContractValidationError,
    WorkerRole,
    parse_assignment,
    parse_role_result,
)


def _assignment(**overrides):
    value = {
        "assignmentVersion": 1,
        "workflowId": "wf_12345678",
        "taskId": "task_12345678",
        "assignmentId": "asg_12345678",
        "role": "analyst",
        "mode": "investigate",
        "objective": "Trace the authentication call path.",
        "scope": {
            "readRoots": ["minicode"],
            "writePaths": [],
            "forbiddenPaths": [".env"],
        },
        "constraints": [
            {"id": "c1", "kind": "must_not_modify", "text": "Read only."}
        ],
        "acceptanceCriteria": [
            {"id": "ac1", "kind": "fact", "text": "Identify the call path."}
        ],
        "inputRefs": ["artifact_1"],
        "risk": "medium",
        "budget": {
            "deadlineAt": "2026-08-26T12:00:00Z",
            "maxModelCalls": 8,
            "maxInputTokens": 200000,
            "maxOutputTokens": 12000,
        },
        "expectedResultType": "AnalystReport.v1",
    }
    value.update(overrides)
    return value


def test_assignment_contract_binds_role_scope_and_budget() -> None:
    assignment = parse_assignment(_assignment())

    assert assignment.role is WorkerRole.ANALYST
    assert assignment.mode == "investigate"
    assert assignment.scope.write_paths == ()
    assert assignment.budget.max_model_calls == 8
    assert assignment.to_dict() == _assignment()


def test_assignment_contract_rejects_unknown_fields_and_forbidden_write_scope() -> None:
    with pytest.raises(ContractValidationError, match="assignment fields"):
        parse_assignment({**_assignment(), "agentPrompt": "unbounded"})

    invalid_scope = _assignment()
    invalid_scope["scope"] = {
        **invalid_scope["scope"],
        "writePaths": ["minicode/agent_loop.py"],
        "forbiddenPaths": ["minicode/agent_loop.py"],
    }
    with pytest.raises(ContractValidationError, match="forbidden paths"):
        parse_assignment(invalid_scope)


def test_assignment_contract_rejects_a_field_valid_but_oversized_envelope() -> None:
    value = _assignment()
    value["constraints"] = [
        {
            "id": f"c{index}",
            "kind": "scope",
            "text": f"{index}" + "x" * 3_999,
        }
        for index in range(5)
    ]

    with pytest.raises(ContractValidationError, match="byte contract limit"):
        parse_assignment(value)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("assignmentVersion", True, "version"),
        ("workflowId", "task_12345678", "workflowId"),
        ("taskId", "wf_12345678", "taskId"),
        ("assignmentId", "task_12345678", "assignmentId"),
    ],
)
def test_assignment_contract_rejects_spoofed_version_and_id_kinds(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ContractValidationError, match=message):
        parse_assignment(_assignment(**{field: value}))


@pytest.mark.parametrize(
    "deadline",
    ["2026-08-26", "2026-08-26T12:00:00"],
)
def test_assignment_deadline_requires_timezone_aware_rfc3339(deadline: str) -> None:
    value = _assignment()
    value["budget"] = {**value["budget"], "deadlineAt": deadline}

    with pytest.raises(ContractValidationError, match="timezone-aware"):
        parse_assignment(value)


def _analyst_report(**overrides):
    value = {
        "resultVersion": 1,
        "assignmentId": "asg_12345678",
        "status": "completed",
        "findings": [
            {
                "claimId": "claim_auth_path",
                "statement": "login delegates to authenticate.",
                "evidence": [
                    {
                        "path": "minicode/auth.py",
                        "lineStart": 10,
                        "lineEnd": 12,
                    }
                ],
                "confidence": "high",
            }
        ],
        "unknowns": [],
        "risks": [],
        "implementationOrderCandidate": {
            "required": False,
            "steps": [],
            "verificationRequests": [],
        },
        "ballot": None,
    }
    value.update(overrides)
    return value


def test_analyst_result_requires_evidence_and_matching_assignment() -> None:
    assignment = parse_assignment(_assignment())

    report = parse_role_result(assignment, _analyst_report())

    assert report["resultType"] == "AnalystReport.v1"
    assert report["findings"][0]["evidence"][0]["lineStart"] == 10

    with pytest.raises(ContractValidationError, match="assignmentId"):
        parse_role_result(
            assignment,
            _analyst_report(assignmentId="asg_different"),
        )

    missing_evidence = _analyst_report()
    missing_evidence["findings"][0]["evidence"] = []
    with pytest.raises(ContractValidationError, match="evidence"):
        parse_role_result(assignment, missing_evidence)


def test_analyst_evidence_count_reserves_the_workflow_audit_budget() -> None:
    assignment = parse_assignment(_assignment())

    def report_with_evidence(count: int) -> dict:
        report = _analyst_report()
        report["findings"] = [
            {
                "claimId": f"claim_{group}",
                "statement": f"Evidence group {group} is bounded.",
                "evidence": [
                    {
                        "path": "minicode/auth.py",
                        "lineStart": offset + 1,
                        "lineEnd": offset + 1,
                    }
                    for offset in range(
                        group * 20,
                        min(count, (group + 1) * 20),
                    )
                ],
                "confidence": "high",
            }
            for group in range((count + 19) // 20)
        ]
        return report

    assert len(
        parse_role_result(assignment, report_with_evidence(64))["findings"]
    ) == 4
    with pytest.raises(ContractValidationError, match="snapshot budget"):
        parse_role_result(assignment, report_with_evidence(65))

    oversized = _analyst_report()
    oversized["findings"] = [
        {
            "claimId": f"claim_large_{index}",
            "statement": "x" * 1_800,
            "evidence": [
                {
                    "path": "minicode/auth.py",
                    "lineStart": index + 1,
                    "lineEnd": index + 1,
                }
            ],
            "confidence": "high",
        }
        for index in range(9)
    ]
    with pytest.raises(ContractValidationError, match="16000-byte"):
        parse_role_result(assignment, oversized)


def test_prepare_change_plan_is_bounded_by_scheduler_owned_target_scope() -> None:
    value = _assignment(
        mode="prepare_change",
        expectedResultType="AnalystReport.v1",
    )
    value["scope"] = {
        **value["scope"],
        "writePaths": ["minicode/auth.py"],
    }
    assignment = parse_assignment(value)
    report = _analyst_report(
        implementationOrderCandidate={
            "required": True,
            "steps": [
                {
                    "id": "s1",
                    "action": "Harden authentication.",
                    "allowedPaths": ["minicode/auth.py"],
                }
            ],
            "verificationRequests": ["pytest tests/test_auth.py -q"],
        }
    )

    assert parse_role_result(assignment, report)[
        "implementationOrderCandidate"
    ]["steps"][0]["allowedPaths"] == ["minicode/auth.py"]

    report["implementationOrderCandidate"]["steps"][0]["allowedPaths"] = [
        "minicode/permissions.py"
    ]
    with pytest.raises(ContractValidationError, match="target write scope"):
        parse_role_result(assignment, report)

    report["implementationOrderCandidate"]["steps"][0]["allowedPaths"] = []
    with pytest.raises(ContractValidationError, match="target write paths"):
        parse_role_result(assignment, report)


def test_assess_claim_uses_a_mode_discriminated_sealed_ballot() -> None:
    value = _assignment(mode="assess_claim")
    assignment = parse_assignment(value)
    report = _analyst_report(
        ballot={
            "questionId": "question_auth",
            "choice": "A",
            "evidenceRefs": ["claim_auth_path"],
            "confidence": "high",
        }
    )
    report.pop("implementationOrderCandidate")

    parsed = parse_role_result(assignment, report)

    assert parsed["ballot"]["choice"] == "A"
    assert parsed["implementationOrderCandidate"] is None
