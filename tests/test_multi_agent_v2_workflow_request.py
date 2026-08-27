"""Contract-first tests for the optional structured workflow request.

``WorkflowRequest.v1`` is the task-tool wire input that lets the Scheduler
classify a workflow from bounded facts instead of prose.  The wire profile
does not contain the derived routing claims ``scopeClear`` or
``acceptanceExecutable``: those are computed from the closed scope and
verification checks so an input cannot claim the low-risk shortcut without
the evidence needed to justify it.

Each ``VerificationCheck`` carries a direct argv vector and criterion IDs.
This is the smallest closed form that lets deterministic verification bind a
check back to acceptance criteria without accepting a shell command string.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

import minicode.multi_agent_contracts as contracts
from minicode.agent_budget import AgentTurnBudget
from minicode.central_scheduler import (
    CentralScheduler,
    DispatchStage,
    StageAssignment,
    TaskKind,
    TaskProfile,
    TaskRisk,
)
from minicode.scheduler_permissions import MANDATORY_SCHEDULER_FORBIDDEN_PATHS
from minicode.tooling import ToolContext
from minicode.tools.task import task_tool
from minicode.workflow_runner import _build_worker_assignment

_TASK_ID = "task_workflow_request"


def _workflow_request(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "requestVersion": 1,
        "profile": {
            "taskId": _TASK_ID,
            "kind": "change",
            "risk": "low",
            "complex": False,
            "parentHasSufficientContext": True,
        },
        "scope": {
            "readRoots": ["minicode", "tests"],
            "writePaths": ["minicode/auth.py", "tests/test_auth.py"],
            "forbiddenPaths": [".env", ".git"],
        },
        "criteria": [
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
        "verificationChecks": [
            {
                "checkId": "check_auth_tests",
                "kind": "tests",
                "argv": ["pytest", "-q", "tests/test_auth.py"],
                "criterionIds": ["ac_behavior", "ac_tests"],
            }
        ],
    }
    value.update(overrides)
    return value


def _parse_request(
    value: object | None,
    *,
    legacy_task_id: str = _TASK_ID,
) -> contracts.WorkflowRequest:
    return contracts.parse_workflow_request(
        value,
        legacy_task_id=legacy_task_id,
    )


def test_task_workflow_exposes_and_preserves_the_optional_structured_input() -> None:
    request = _workflow_request()

    schema = task_tool.input_schema["properties"]["workflow_request"]
    validated = task_tool.validator(
        {
            "description": "run scoped workflow",
            "prompt": "Apply the bounded authentication change.",
            "agent_type": "workflow",
            "workflow_request": request,
        }
    )

    assert schema["type"] == "object"
    assert validated["workflow_request"] is not None


def test_structured_request_conservatively_derives_risk_and_complexity() -> None:
    raw = _workflow_request()

    request = _parse_request(raw)

    projected = request.to_dict()
    assert projected["profile"] == raw["profile"]
    assert projected["criteria"] == raw["criteria"]
    assert projected["verificationChecks"] == raw["verificationChecks"]
    assert set(MANDATORY_SCHEDULER_FORBIDDEN_PATHS).issubset(
        projected["scope"]["forbiddenPaths"]
    )
    assert request.task_profile == TaskProfile(
        task_id=_TASK_ID,
        kind=TaskKind.CHANGE,
        risk=TaskRisk.HIGH,
        complex=True,
        parent_has_sufficient_context=False,
        scope_clear=True,
        acceptance_executable=False,
        human_confirmation_required=True,
    )
    assert request.scope.write_paths == (
        "minicode/auth.py",
        "tests/test_auth.py",
    )
    assert [item.criterion_id for item in request.criteria] == [
        "ac_behavior",
        "ac_tests",
    ]
    assert request.verification_checks[0].argv == (
        "pytest",
        "-q",
        "tests/test_auth.py",
    )
    assert CentralScheduler().plan(request.task_profile).rule_id == (
        "route.high_risk_human_gate"
    )


def test_assignment_budget_projects_the_enforced_shared_limits() -> None:
    request = _parse_request(_workflow_request())
    budget = AgentTurnBudget(max_model_calls=5, max_total_tokens=1_000)
    budget.reserve_model_call(estimated_input_tokens=200)
    budget.reserve_model_call(estimated_input_tokens=100)
    deadline_monotonic = time.monotonic() + 30

    assignment = _build_worker_assignment(
        request,
        StageAssignment(
            workflow_id="wf_budget_projection",
            task_id=_TASK_ID,
            assignment_id="asg_budget_projection",
            attempt_id="attempt_budget_projection",
            attempt=1,
            agent_run_id="sub_" + "a" * 32,
            stage=DispatchStage.ANALYST,
            rule_id="route.reviewed_change",
        ),
        context=ToolContext(
            _task_deadline_monotonic=deadline_monotonic,
            _task_timeout_seconds=30,
        ),
        agent_budget=budget,
        role="analyst",
        mode="prepare_change",
        objective="Analyze the bounded request.",
    )

    deadline = datetime.fromisoformat(
        assignment.budget.deadline_at.replace("Z", "+00:00")
    )
    remaining_wall_time = (deadline - datetime.now(timezone.utc)).total_seconds()
    assert 27 <= remaining_wall_time <= 30
    assert assignment.budget.max_model_calls == 3
    assert assignment.budget.max_input_tokens == 700
    assert assignment.budget.max_output_tokens == 700


def test_public_check_claim_cannot_skip_semantic_review_even_for_text() -> None:
    raw = _workflow_request()
    raw["scope"] = {
        "readRoots": ["."],
        "writePaths": ["notes.txt"],
        "forbiddenPaths": [".env", ".git"],
    }

    request = _parse_request(raw)

    assert request.task_profile.risk is TaskRisk.LOW
    assert request.task_profile.complex is False
    assert request.task_profile.parent_has_sufficient_context is False
    assert CentralScheduler().plan(request.task_profile).rule_id == (
        "route.reviewed_change"
    )


def test_missing_request_maps_to_the_conservative_legacy_full_review_route() -> None:
    request = _parse_request(None, legacy_task_id="task_legacy_workflow")

    assert request.task_profile == TaskProfile(
        task_id="task_legacy_workflow",
        kind=TaskKind.CHANGE,
        risk=TaskRisk.HIGH,
        complex=True,
        parent_has_sufficient_context=False,
        scope_clear=False,
        acceptance_executable=False,
    )
    plan = CentralScheduler().plan(request.task_profile)
    assert plan.stages == (
        DispatchStage.ANALYST,
        DispatchStage.EXECUTOR,
        DispatchStage.VERIFIER,
        DispatchStage.REVIEWER,
    )
    assert plan.rule_id == "route.reviewed_change"
    assert plan.requires_human is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"route": "executor_only"}),
        lambda value: value.update({"requestVersion": True}),
        lambda value: value["profile"].update({"scopeClear": True}),
        lambda value: value["profile"].update({"acceptanceExecutable": True}),
        lambda value: value["profile"].update({"taskId": "task"}),
        lambda value: value["profile"].update({"kind": "write"}),
        lambda value: value["profile"].update({"risk": "urgent"}),
        lambda value: value["profile"].update({"complex": "false"}),
        lambda value: value["profile"].update({"parentHasSufficientContext": 1}),
    ],
)
def test_request_and_profile_are_closed_and_routing_types_are_strict(
    mutation,
) -> None:
    value = _workflow_request()
    mutation(value)

    with pytest.raises(contracts.ContractValidationError):
        _parse_request(value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["scope"].update({"followSymlinks": True}),
        lambda value: value["scope"].update({"writePaths": ["../outside.py"]}),
        lambda value: value["scope"].update({"writePaths": [".git/config"]}),
        lambda value: value["scope"].update({"readRoots": [".env"]}),
        lambda value: value["scope"].update({"forbiddenPaths": ["minicode/auth.py"]}),
        lambda value: value["scope"].update({"readRoots": ["minicode", "minicode"]}),
    ],
)
def test_workflow_scope_is_closed_normalized_and_non_overlapping(mutation) -> None:
    value = _workflow_request()
    mutation(value)

    with pytest.raises(contracts.ContractValidationError):
        _parse_request(value)


@pytest.mark.parametrize(
    "scope",
    [
        {
            "readRoots": ["minicode", "tests"],
            "writePaths": ["Minicode/auth.py"],
            "forbiddenPaths": ["minicode"],
        },
        {
            "readRoots": ["minicode", "tests"],
            "writePaths": ["Secrets/token.py"],
            "forbiddenPaths": [],
        },
        {
            "readRoots": ["Secrets"],
            "writePaths": ["minicode/auth.py"],
            "forbiddenPaths": [],
        },
    ],
)
def test_workflow_scope_rejects_declared_and_mandatory_forbidden_case_aliases(
    scope: dict[str, list[str]],
) -> None:
    value = _workflow_request()
    value["scope"] = scope

    with pytest.raises(contracts.ContractValidationError, match="forbidden"):
        _parse_request(value)


def test_workflow_scope_cannot_remove_scheduler_mandatory_forbidden_paths() -> None:
    value = _workflow_request()
    value["scope"] = {**value["scope"], "forbiddenPaths": []}

    request = _parse_request(value)

    assert set(MANDATORY_SCHEDULER_FORBIDDEN_PATHS).issubset(
        request.scope.forbidden_paths
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["criteria"][0].update({"priority": "must"}),
        lambda value: value["criteria"][1].update({"id": "ac_behavior"}),
        lambda value: value["criteria"][0].update({"kind": "opinion"}),
        lambda value: value.update({"criteria": []}),
    ],
)
def test_acceptance_criteria_are_closed_nonempty_and_uniquely_identified(
    mutation,
) -> None:
    value = _workflow_request()
    mutation(value)

    with pytest.raises(contracts.ContractValidationError):
        _parse_request(value)


def test_workflow_criteria_and_check_links_fit_derived_artifacts() -> None:
    too_many_criteria = _workflow_request()
    too_many_criteria["criteria"] = [
        {
            "id": f"ac_{index}",
            "kind": "behavior",
            "text": f"Criterion {index} is satisfied.",
        }
        for index in range(41)
    ]
    too_many_criteria["verificationChecks"][0]["criterionIds"] = [
        item["id"] for item in too_many_criteria["criteria"]
    ]
    with pytest.raises(contracts.ContractValidationError, match="artifact budget"):
        _parse_request(too_many_criteria)

    too_many_links = _workflow_request()
    too_many_links["criteria"] = [
        {
            "id": f"ac_{index}",
            "kind": "behavior",
            "text": f"Criterion {index} is satisfied.",
        }
        for index in range(3)
    ]
    too_many_links["verificationChecks"] = [
        {
            "checkId": f"check_{index}",
            "kind": "tests",
            "argv": ["pytest", "-q", f"tests/test_{index}.py"],
            "criterionIds": [item["id"] for item in too_many_links["criteria"]],
        }
        for index in range(27)
    ]
    with pytest.raises(contracts.ContractValidationError, match="artifact budget"):
        _parse_request(too_many_links)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["verificationChecks"][0].update({"timeoutSeconds": 30}),
        lambda value: value["verificationChecks"][0].update(
            {"argv": "pytest -q tests/test_auth.py"}
        ),
        lambda value: value["verificationChecks"][0].update({"argv": []}),
        lambda value: value["verificationChecks"][0].update({"argv": ["", "-q"]}),
        lambda value: value["verificationChecks"][0].update(
            {"argv": ["pytest", "bad\x00argument"]}
        ),
        lambda value: value["verificationChecks"][0].update(
            {"argv": ["pytest", "/tmp/outside_test.py"]}
        ),
        lambda value: value["verificationChecks"][0].update(
            {"argv": ["pytest", "../outside_test.py"]}
        ),
        lambda value: value["verificationChecks"][0].update(
            {"argv": ["pytest", "@/tmp/pytest.args"]}
        ),
        lambda value: value["verificationChecks"][0].update(
            {"argv": ["pytest", r"C:\tmp\outside_test.py"]}
        ),
        lambda value: value["verificationChecks"][0].update(
            {"argv": ["pytest", "--collect-only"]}
        ),
        lambda value: value["verificationChecks"][0].update(
            {"argv": ["cargo", "test", "--no-run"]}
        ),
        lambda value: value["verificationChecks"][0].update(
            {"argv": ["npm", "run", "test", "--if-present"]}
        ),
        lambda value: value["verificationChecks"][0].update(
            {"argv": ["make", "test", "-C/tmp"]}
        ),
        lambda value: value["verificationChecks"][0].update(
            {"argv": ["pytest", "-c/tmp/pytest.ini"]}
        ),
        lambda value: value["verificationChecks"][0].update(
            {"argv": ["pytest", "--override-ini=pythonpath=/tmp"]}
        ),
        lambda value: value["verificationChecks"][0].update(
            {"argv": ["go", "test", "-run=nomatchunlikely"]}
        ),
        lambda value: value["verificationChecks"][0].update(
            {"argv": ["dotnet", "test", "--list-tests"]}
        ),
        lambda value: value["verificationChecks"][0].update(
            {"argv": ["npx", "jest", "--listTests"]}
        ),
        lambda value: value["verificationChecks"][0].update(
            {"argv": ["ruff", "check", "--show-files"]}
        ),
        lambda value: value["verificationChecks"][0].update({"kind": "command"}),
        lambda value: value["verificationChecks"][0].update(
            {"criterionIds": ["ac_unknown"]}
        ),
        lambda value: value["verificationChecks"].append(
            {
                **value["verificationChecks"][0],
                "argv": ["pytest", "-q"],
            }
        ),
        lambda value: value["verificationChecks"].append(
            {
                **value["verificationChecks"][0],
                "checkId": "check_duplicate_argv",
            }
        ),
    ],
)
def test_verification_checks_use_closed_unique_direct_argv_contracts(
    mutation,
) -> None:
    value = _workflow_request()
    mutation(value)

    with pytest.raises(contracts.ContractValidationError):
        _parse_request(value)


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "scope": {
                "readRoots": ["minicode", "tests"],
                "writePaths": [],
                "forbiddenPaths": [".env", ".git"],
            }
        },
        {"verificationChecks": []},
        {
            "verificationChecks": [
                {
                    "checkId": "check_tests_only",
                    "kind": "tests",
                    "argv": ["pytest", "-q", "tests/test_auth.py"],
                    "criterionIds": ["ac_tests"],
                }
            ]
        },
    ],
)
def test_change_request_requires_write_scope_and_complete_executable_acceptance(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(contracts.ContractValidationError):
        _parse_request(_workflow_request(**overrides))


@pytest.mark.parametrize("kind", ["change", "read_only"])
def test_critical_request_always_routes_to_a_human_gate(kind: str) -> None:
    value = _workflow_request()
    value["profile"] = {
        **value["profile"],
        "kind": kind,
        "risk": "critical",
    }
    if kind == "read_only":
        value["scope"] = {
            **value["scope"],
            "writePaths": [],
        }

    request = _parse_request(value)
    plan = CentralScheduler().plan(request.task_profile)

    assert DispatchStage.HUMAN in plan.stages
    assert plan.requires_human is True


def test_structured_workflow_request_is_rejected_for_non_workflow_agents() -> None:
    with pytest.raises(ValueError, match="workflow"):
        task_tool.validator(
            {
                "description": "run direct worker",
                "prompt": "Apply the bounded authentication change.",
                "agent_type": "general",
                "workflow_request": _workflow_request(),
            }
        )


def test_structured_request_rejects_legacy_parallel_exploration_bypass() -> None:
    with pytest.raises(ValueError, match="central scheduling"):
        task_tool.validator(
            {
                "description": "run structured workflow",
                "prompt": "Apply the bounded authentication change.",
                "agent_type": "workflow",
                "parallel_explore": True,
                "workflow_request": _workflow_request(),
            }
        )
