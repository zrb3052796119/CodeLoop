"""Executable test bindings cannot certify semantic requirement coverage."""

from minicode.central_scheduler import CentralScheduler, DispatchStage
from minicode.multi_agent_contracts import parse_workflow_request

from test_workflow_v2_structured_routing import _request


def test_green_check_mapping_does_not_remove_completion_review():
    raw = _request(risk="low", complex=False)
    raw["criteria"].append({
        "id": "ac2", "kind": "behavior", "text": "Reject invalid input without changing state.",
    })
    raw["verificationChecks"][0]["criterionIds"].append("ac2")
    profile = parse_workflow_request(raw, legacy_task_id="task_coverage").task_profile
    plan = CentralScheduler().plan(profile)
    assert plan.stages == (
        DispatchStage.EXECUTOR, DispatchStage.VERIFIER, DispatchStage.REVIEWER,
    ), "A declared check association is not evidence that both requirements are tested"


def test_previous_scoped_policy_is_replayed_without_reinterpretation():
    request = parse_workflow_request(_request(risk="low"), legacy_task_id="task_coverage")
    historical = CentralScheduler().plan(request.task_profile_for_policy(3), policy_version=3)
    assert historical.stages == (DispatchStage.EXECUTOR, DispatchStage.VERIFIER)
