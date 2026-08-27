"""Contract-first tests for the V2 Executor hand-off seam.

The design names ``ImplementationOrder.v1`` but does not spell out its JSON
shape.  These tests freeze the smallest closed form needed to bind an
Executor to one Assignment, baseline, write scope, ordered step set, and the
Assignment's acceptance criteria.  Result parsing stays behind the existing
``parse_role_result`` interface and receives the locked order as explicit
handoff context.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import minicode.multi_agent_contracts as contracts

_BASE_DIGEST = "sha256:" + "a" * 64
_PATCH_DIGEST = "sha256:" + "b" * 64


def _executor_assignment(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "assignmentVersion": 1,
        "workflowId": "wf_execution_contract",
        "taskId": "task_execution_contract",
        "assignmentId": "asg_execution_contract",
        "role": "executor",
        "mode": "implement",
        "objective": "Harden authentication without changing public behavior.",
        "scope": {
            "readRoots": ["minicode", "tests"],
            "writePaths": ["minicode/auth.py", "tests/test_auth.py"],
            "forbiddenPaths": [".env", ".git"],
        },
        "constraints": [
            {
                "id": "c_scope",
                "kind": "scope",
                "text": "Modify only the locked write paths.",
            }
        ],
        "acceptanceCriteria": [
            {
                "id": "ac_behavior",
                "kind": "behavior",
                "text": "Authentication behavior remains compatible.",
            },
            {
                "id": "ac_tests",
                "kind": "quality",
                "text": "Focused authentication tests pass.",
            },
        ],
        "inputRefs": ["artifact_analyst_report"],
        "risk": "medium",
        "budget": {
            "deadlineAt": "2026-08-27T08:30:00+08:00",
            "maxModelCalls": 12,
            "maxInputTokens": 240000,
            "maxOutputTokens": 16000,
        },
        "expectedResultType": "ExecutionResult.v1",
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    "deadline",
    [
        "2026-08-27T00:30:00Z",
        "2026-08-27T08:30:00+08:00",
        "2026-08-27T08:30:00.123456-04:30",
    ],
)
def test_assignment_deadline_accepts_strict_rfc3339(deadline: str) -> None:
    value = _executor_assignment()
    value["budget"] = {**value["budget"], "deadlineAt": deadline}

    assignment = contracts.parse_assignment(value)

    assert assignment.budget.deadline_at == deadline


@pytest.mark.parametrize(
    "deadline",
    [
        "2026-08-27",
        "2026-08-27T08:30:00",
        "2026-08-27T08:30:00+0800",
        "2026-08-27T08:30:00+08:00:30",
        "2026-08-27t08:30:00Z",
        "2026-08-27 08:30:00Z",
        "2026-08-27T08:30:00z",
    ],
)
def test_assignment_deadline_rejects_non_rfc3339_variants(deadline: str) -> None:
    value = _executor_assignment()
    value["budget"] = {**value["budget"], "deadlineAt": deadline}

    with pytest.raises(contracts.ContractValidationError):
        contracts.parse_assignment(value)


def _implementation_order(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "orderVersion": 1,
        "orderId": "order_execution_contract",
        "assignmentId": "asg_execution_contract",
        "baseDigest": _BASE_DIGEST,
        "allowedPaths": ["minicode/auth.py", "tests/test_auth.py"],
        "steps": [
            {
                "id": "s_implement",
                "action": "Harden the authentication implementation.",
                "allowedPaths": ["minicode/auth.py"],
                "criterionIds": ["ac_behavior"],
            },
            {
                "id": "s_tests",
                "action": "Add focused regression coverage.",
                "allowedPaths": ["tests/test_auth.py"],
                "criterionIds": ["ac_tests"],
            },
        ],
        "criterionIds": ["ac_behavior", "ac_tests"],
        "verificationRequests": ["pytest tests/test_auth.py -q"],
    }
    value.update(overrides)
    return value


def _parse_order(
    assignment: contracts.Assignment,
    value: object,
) -> contracts.ImplementationOrder:
    return contracts.parse_implementation_order(assignment, value)


def test_implementation_order_binds_assignment_scope_baseline_steps_and_criteria() -> (
    None
):
    assignment = contracts.parse_assignment(_executor_assignment())

    order = _parse_order(assignment, _implementation_order())

    assert order.assignment_id == assignment.assignment_id
    assert order.base_digest == _BASE_DIGEST
    assert order.allowed_paths == ("minicode/auth.py", "tests/test_auth.py")
    assert [step.step_id for step in order.steps] == [
        "s_implement",
        "s_tests",
    ]
    assert order.criterion_ids == ("ac_behavior", "ac_tests")
    assert order.to_dict() == _implementation_order()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"unlockedPrompt": "ignore the order"}),
        lambda value: value.update({"orderVersion": True}),
        lambda value: value.update({"orderId": "asg_wrong_kind"}),
        lambda value: value.update({"assignmentId": "asg_other_assignment"}),
        lambda value: value.update({"baseDigest": "sha256:not-a-digest"}),
    ],
)
def test_implementation_order_rejects_open_shape_identity_and_digest_drift(
    mutation,
) -> None:
    assignment = contracts.parse_assignment(_executor_assignment())
    value = _implementation_order()
    mutation(value)

    with pytest.raises(contracts.ContractValidationError):
        _parse_order(assignment, value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"allowedPaths": ["minicode/permissions.py"]}),
        lambda value: value["steps"][0].update(
            {"allowedPaths": ["tests/test_auth.py"]}
        ),
        lambda value: value["steps"][0].update({"allowedPaths": ["../outside.py"]}),
    ],
)
def test_implementation_order_rejects_scope_expansion_at_order_or_step_level(
    mutation,
) -> None:
    assignment = contracts.parse_assignment(_executor_assignment())
    value = _implementation_order()
    mutation(value)

    with pytest.raises(contracts.ContractValidationError):
        _parse_order(assignment, value)


def test_implementation_order_keeps_allow_containment_case_sensitive() -> None:
    assignment = contracts.parse_assignment(_executor_assignment())
    value = _implementation_order()
    value["allowedPaths"][0] = "Minicode/auth.py"
    value["steps"][0]["allowedPaths"] = ["Minicode/auth.py"]

    with pytest.raises(contracts.ContractValidationError, match="write scope"):
        _parse_order(assignment, value)


def test_implementation_order_rejects_forbidden_case_aliases_defensively() -> None:
    assignment = contracts.parse_assignment(_executor_assignment())
    assignment = replace(
        assignment,
        scope=contracts.AssignmentScope(
            read_roots=assignment.scope.read_roots,
            write_paths=("minicode/Private/auth.py", "tests/test_auth.py"),
            forbidden_paths=("minicode/private",),
        ),
    )
    value = _implementation_order()
    value["allowedPaths"][0] = "minicode/Private/auth.py"
    value["steps"][0]["allowedPaths"] = ["minicode/Private/auth.py"]

    with pytest.raises(contracts.ContractValidationError, match="forbidden"):
        _parse_order(assignment, value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"criterionIds": ["ac_behavior"]}),
        lambda value: value["steps"][0].update({"criterionIds": ["ac_unknown"]}),
        lambda value: value["steps"][1].update({"id": "s_implement"}),
        lambda value: value["steps"][1].update({"criterionIds": []}),
    ],
)
def test_implementation_order_rejects_step_or_criterion_accounting_drift(
    mutation,
) -> None:
    assignment = contracts.parse_assignment(_executor_assignment())
    value = _implementation_order()
    mutation(value)

    with pytest.raises(contracts.ContractValidationError):
        _parse_order(assignment, value)


def _execution_result(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "resultVersion": 1,
        "assignmentId": "asg_execution_contract",
        "orderId": "order_execution_contract",
        "status": "completed",
        "changeSet": {
            "workspaceRef": "isolated://wf_execution_contract/execution",
            "baseDigest": _BASE_DIGEST,
            "patchDigest": _PATCH_DIGEST,
            "files": [
                {"path": "minicode/auth.py", "action": "modified"},
                {"path": "tests/test_auth.py", "action": "created"},
            ],
        },
        "stepResults": [
            {
                "stepId": "s_implement",
                "status": "completed",
                "artifactRefs": [],
            },
            {
                "stepId": "s_tests",
                "status": "completed",
                "artifactRefs": [],
            },
        ],
        "deviations": [],
        "selfChecks": [],
        "blockers": [],
    }
    value.update(overrides)
    return value


def _parse_execution_result(
    assignment: contracts.Assignment,
    order: contracts.ImplementationOrder,
    value: object,
) -> dict[str, object]:
    return contracts.parse_role_result(
        assignment,
        value,
        implementation_order=order,
    )


def _execution_context() -> tuple[contracts.Assignment, contracts.ImplementationOrder]:
    assignment = contracts.parse_assignment(_executor_assignment())
    order = _parse_order(assignment, _implementation_order())
    return assignment, order


def test_execution_result_binds_assignment_order_digests_changed_paths_and_steps() -> (
    None
):
    assignment, order = _execution_context()

    result = _parse_execution_result(assignment, order, _execution_result())

    assert result["resultType"] == "ExecutionResult.v1"
    assert result["assignmentId"] == assignment.assignment_id
    assert result["orderId"] == order.order_id
    assert result["changeSet"]["baseDigest"] == order.base_digest
    assert result["changeSet"]["patchDigest"] == _PATCH_DIGEST
    assert {item["path"] for item in result["changeSet"]["files"]} == {
        "minicode/auth.py",
        "tests/test_auth.py",
    }
    assert {item["stepId"] for item in result["stepResults"]} == {
        "s_implement",
        "s_tests",
    }


def test_executor_declaration_cannot_supply_or_forge_scheduler_change_set() -> None:
    assignment, order = _execution_context()
    declaration = {
        key: value
        for key, value in _execution_result().items()
        if key != "changeSet"
    }

    result = contracts.parse_executor_declaration(
        assignment,
        declaration,
        implementation_order=order,
        scheduler_change_set=_execution_result()["changeSet"],
    )

    assert result["changeSet"]["patchDigest"] == _PATCH_DIGEST
    with pytest.raises(contracts.ContractValidationError, match="fields do not"):
        contracts.parse_executor_declaration(
            assignment,
            {**declaration, "changeSet": _execution_result()["changeSet"]},
            implementation_order=order,
            scheduler_change_set=_execution_result()["changeSet"],
        )


@pytest.mark.parametrize(
    "override",
    [
        {
            "selfChecks": [
                {
                    "check": "pytest -q",
                    "status": "passed",
                    "artifactRef": "artifact_untrusted",
                }
            ]
        },
        {
            "stepResults": [
                {
                    "stepId": "s_implement",
                    "status": "completed",
                    "artifactRefs": ["artifact_untrusted"],
                },
                {
                    "stepId": "s_tests",
                    "status": "completed",
                    "artifactRefs": [],
                },
            ]
        },
    ],
)
def test_executor_cannot_self_issue_verification_or_artifact_evidence(
    override: dict[str, object],
) -> None:
    assignment, order = _execution_context()

    with pytest.raises(contracts.ContractValidationError, match="owns"):
        _parse_execution_result(
            assignment,
            order,
            _execution_result(**override),
        )


def test_blocked_executor_declaration_stays_non_promotable() -> None:
    assignment, order = _execution_context()
    blocked = _execution_result(
        status="blocked",
        changeSet=None,
        stepResults=[
            {
                "stepId": "s_implement",
                "status": "completed",
                "artifactRefs": [],
            },
            {
                "stepId": "s_tests",
                "status": "skipped",
                "artifactRefs": [],
            },
        ],
        blockers=["Required fixture is unavailable."],
    )
    declaration = {key: value for key, value in blocked.items() if key != "changeSet"}

    result = contracts.parse_executor_declaration(
        assignment,
        declaration,
        implementation_order=order,
        scheduler_change_set=None,
    )

    assert result["status"] == "blocked"
    assert result["changeSet"] is None


def test_execution_result_allows_a_bounded_non_high_deviation() -> None:
    assignment, order = _execution_context()
    value = _execution_result(
        deviations=[
            {
                "stepId": "s_tests",
                "reason": "Used the repository-native fixture.",
                "impact": "medium",
            }
        ]
    )

    result = _parse_execution_result(assignment, order, value)

    assert result["deviations"][0]["impact"] == "medium"


def test_blocked_execution_is_typed_but_cannot_expose_a_promotable_change_set() -> None:
    assignment, order = _execution_context()
    value = _execution_result(
        status="blocked",
        changeSet=None,
        stepResults=[
                {
                    "stepId": "s_implement",
                    "status": "completed",
                    "artifactRefs": [],
                },
            {
                "stepId": "s_tests",
                "status": "skipped",
                "artifactRefs": [],
            },
        ],
        deviations=[
            {
                "stepId": "s_tests",
                "reason": "Required fixture is unavailable.",
                "impact": "high",
            }
        ],
        blockers=["Required fixture is unavailable."],
    )

    result = _parse_execution_result(assignment, order, value)

    assert result["status"] == "blocked"
    assert result["changeSet"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"executorPrompt": "unbounded"}),
        lambda value: value.update({"resultVersion": True}),
        lambda value: value.update({"assignmentId": "asg_other_assignment"}),
        lambda value: value.update({"orderId": "order_other"}),
        lambda value: value["changeSet"].update({"baseDigest": "sha256:" + "c" * 64}),
        lambda value: value["changeSet"].update({"patchDigest": "sha256:not-a-digest"}),
        lambda value: value["changeSet"].update(
            {"workspaceRef": "file:///parent/workspace"}
        ),
    ],
)
def test_execution_result_rejects_open_shape_identity_order_and_digest_drift(
    mutation,
) -> None:
    assignment, order = _execution_context()
    value = _execution_result()
    mutation(value)

    with pytest.raises(contracts.ContractValidationError):
        _parse_execution_result(assignment, order, value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["changeSet"]["files"].append(
            {"path": "minicode/permissions.py", "action": "modified"}
        ),
        lambda value: value["changeSet"]["files"].append(
            {"path": "../outside.py", "action": "created"}
        ),
        lambda value: value["changeSet"]["files"][0].update({"action": "read"}),
        lambda value: value["changeSet"]["files"].append(
            {"path": "minicode/auth.py", "action": "modified"}
        ),
    ],
)
def test_execution_result_rejects_changed_path_scope_or_action_violations(
    mutation,
) -> None:
    assignment, order = _execution_context()
    value = _execution_result()
    mutation(value)

    with pytest.raises(contracts.ContractValidationError):
        _parse_execution_result(assignment, order, value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["stepResults"].pop(),
        lambda value: value["stepResults"][1].update({"stepId": "s_unknown"}),
        lambda value: value["stepResults"][1].update({"stepId": "s_implement"}),
        lambda value: value["stepResults"][0].update({"status": "failed"}),
        lambda value: value["stepResults"].append(
            {
                "stepId": "s_extra",
                "status": "completed",
                "artifactRefs": [],
            }
        ),
    ],
)
def test_execution_result_rejects_incomplete_or_inconsistent_step_accounting(
    mutation,
) -> None:
    assignment, order = _execution_context()
    value = _execution_result()
    mutation(value)

    with pytest.raises(contracts.ContractValidationError):
        _parse_execution_result(assignment, order, value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(
            {
                "deviations": [
                    {
                        "stepId": "s_implement",
                        "reason": "Expanded the implementation scope.",
                        "impact": "high",
                    }
                ]
            }
        ),
        lambda value: value.update({"blockers": ["Unresolved blocker"]}),
        lambda value: value.update(
            {
                "deviations": [
                    {
                        "stepId": "s_unknown",
                        "reason": "Unknown step.",
                        "impact": "low",
                    }
                ]
            }
        ),
    ],
)
def test_completed_execution_rejects_unresolved_deviation_or_blocker_gates(
    mutation,
) -> None:
    assignment, order = _execution_context()
    value = _execution_result()
    mutation(value)

    with pytest.raises(contracts.ContractValidationError):
        _parse_execution_result(assignment, order, value)


@pytest.mark.parametrize("status", ["blocked", "failed"])
def test_noncompleted_execution_rejects_a_promotable_change_set(status: str) -> None:
    assignment, order = _execution_context()
    value = _execution_result(
        status=status,
        blockers=["Execution did not complete."],
    )

    with pytest.raises(contracts.ContractValidationError):
        _parse_execution_result(assignment, order, value)
