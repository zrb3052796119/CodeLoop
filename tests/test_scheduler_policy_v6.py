"""V6 pays for analysis only when explicitly needed or after a real failure."""

from copy import deepcopy
from dataclasses import replace

import pytest

from minicode.central_scheduler import (
    CentralScheduler, DispatchStage, SchedulerExecutionPolicy, StageResult,
    TaskKind, TaskProfile, TaskRisk,
)
from minicode.scheduler_trace import project_scheduler_trace, render_scheduler_trace
from minicode.workflow_review_policy import requests_analysis, review_routing_decision, validate_review_routing_decision


def _profile(**overrides):
    return replace(TaskProfile(
        task_id="task_policy_v6", kind=TaskKind.CHANGE, risk=TaskRisk.MEDIUM,
        complex=True, scope_clear=True, acceptance_executable=True,
        independent_review_required=False,
    ), **overrides)


@pytest.mark.parametrize("analysis,review,stages", [
    (False, False, ("executor", "verifier")),
    (True, False, ("analyst", "executor", "verifier")),
    (False, True, ("executor", "verifier", "reviewer")),
    (True, True, ("analyst", "executor", "verifier", "reviewer")),
])
def test_complete_requests_route_by_explicit_needs_not_complexity(analysis, review, stages):
    plan = CentralScheduler().plan(_profile(analysis_requested=analysis, independent_review_required=review))
    assert plan.policy_version == 6
    assert tuple(stage.value for stage in plan.stages) == stages


@pytest.mark.parametrize("version", [2, 3, 4, 5])
def test_historical_policy_ignores_new_analysis_signal(version):
    profile = _profile(risk=TaskRisk.LOW, complex=False)
    scheduler = CentralScheduler()
    before = scheduler.plan(profile, policy_version=version)
    assert scheduler.plan(replace(profile, analysis_requested=True), policy_version=version) == before
    expected = ("executor", "verifier", "reviewer") if version == 4 else ("executor", "verifier")
    assert tuple(stage.value for stage in before.stages) == expected


@pytest.mark.parametrize("risk", [TaskRisk.HIGH, TaskRisk.CRITICAL])
def test_elevated_authority_keeps_human_analysis_and_review(risk):
    plan = CentralScheduler().plan(_profile(risk=risk, human_confirmation_required=True))
    assert plan.requires_human
    assert plan.stages == tuple(DispatchStage(stage) for stage in ("analyst", "human", "executor", "verifier", "reviewer"))


@pytest.mark.parametrize("text", ["needs_analysis: true", "Please analyze first.", "Investigate before editing.", "请先分析再修改", "修改前进行调查"])
def test_explicit_analysis_directives(text):
    assert requests_analysis(text)


@pytest.mark.parametrize("text", ["分析失败原因", "The analysis failed.", "needs_analysis: false", "Fix the analyzer error.", "Review this change.", "Do not analyze first.", "不要先分析"])
def test_ordinary_analysis_mentions_do_not_add_a_role(text):
    assert not requests_analysis(text)


def _repair_trace(*, error="verification_failed", analysis_status="completed", repairs=1, analysis_verdict=None):
    scheduler = CentralScheduler()
    profile = _profile()
    visits = []
    transitions = []
    executions = 0

    def worker(assignment):
        nonlocal executions
        visits.append(assignment)
        if assignment.stage is DispatchStage.EXECUTOR:
            executions += 1
            return StageResult(status="completed", artifact_id=f"artifact_execution_{executions}", patch_digest=f"patch_{executions}")
        if assignment.stage is DispatchStage.ADJUDICATOR:
            return StageResult(status="completed", artifact_id="artifact_adjudication",
                               conflict_verdict={"decision": analysis_verdict, "conditions": []})
        assert assignment.stage is DispatchStage.ANALYST
        if analysis_verdict is not None:
            return StageResult(status="blocked", artifact_id="artifact_analysis",
                               error_code="analyst_reclassification_required", escalate_conflict={},
                               conflict_artifact_id="artifact_conflict")
        return StageResult(status=analysis_status, artifact_id="artifact_analysis",
                           error_code=None if analysis_status == "completed" else "analyst_result_blocked")

    def verify(assignment):
        visits.append(assignment)
        failed = executions <= repairs
        return StageResult(status="failed" if failed else "completed",
            artifact_id=f"artifact_verification_{executions}", patch_digest=f"patch_{executions}",
            repairable=failed, error_code=error if failed else None)

    outcome = scheduler.execute(profile, workflow_id="wf_policy_v6", run_worker=worker,
        run_verifier=verify, commit=lambda _: True, on_transition=transitions.append,
        policy=SchedulerExecutionPolicy(implementation_max_repairs=2))
    trace = project_scheduler_trace(scheduler.plan(profile), outcome, transitions)
    return outcome, visits, trace


def test_real_verification_failure_gets_one_analysis_then_original_execution_flow():
    outcome, visits, trace = _repair_trace(repairs=2)
    assert outcome.committed
    assert [item.stage.value for item in visits] == ["executor", "verifier", "analyst", "executor", "verifier", "executor", "verifier"]
    analysis = next(item for item in visits if item.stage is DispatchStage.ANALYST)
    assert analysis.repair_source_artifact_id == "artifact_verification_1"
    assert analysis.input_artifact_ids == ("artifact_execution_1", "artifact_verification_1")
    assert visits[3].repair_source_artifact_id == "artifact_verification_1"
    assert outcome.completed_stages == (DispatchStage.EXECUTOR, DispatchStage.VERIFIER)
    assert render_scheduler_trace(trace)


def test_failed_diagnostic_preserves_an_auditable_terminal_trace():
    outcome, visits, trace = _repair_trace(analysis_status="blocked")
    assert not outcome.committed
    assert outcome.failure_stage is DispatchStage.ANALYST
    assert visits[-1].stage is DispatchStage.ANALYST
    assert render_scheduler_trace(trace)


@pytest.mark.parametrize("decision", ["select_b", "select_a", "insufficient"])
def test_diagnostic_analysis_preserves_existing_conflict_escalation(decision):
    outcome, visits, trace = _repair_trace(analysis_verdict=decision)
    assert outcome.committed is (decision == "select_b")
    assert [item.stage.value for item in visits][:4] == ["executor", "verifier", "analyst", "adjudicator"]
    if decision == "select_b":
        assert visits[4].stage is DispatchStage.EXECUTOR
        assert visits[4].repair_source_artifact_id == "artifact_verification_1"
    else:
        assert outcome.failure_stage is DispatchStage.ADJUDICATOR
    assert render_scheduler_trace(trace)


@pytest.mark.parametrize("error", ["executor_declaration_invalid", "executor_budget_insufficient", "transport_failed", "acceptance_evidence_incomplete"])
def test_protocol_failures_never_buy_diagnostic_analysis(error):
    _, visits, _ = _repair_trace(error=error)
    assert all(item.stage is not DispatchStage.ANALYST for item in visits)


def test_failure_analysis_trace_rejects_reinterpreted_old_policy_or_fake_failure():
    _, _, trace = _repair_trace()
    for field, value in (("policyVersion", 5), ("failure", "executor_declaration_invalid")):
        forged = deepcopy(trace)
        if field == "failure":
            next(item for item in forged["transitions"] if item["outcome"] == "failed")["errorCode"] = value
        else:
            forged[field] = value
        with pytest.raises(ValueError):
            render_scheduler_trace(forged)


def test_review_decision_v5_replays_but_v6_accepts_complete_multi_requirement_change():
    from minicode.multi_agent_contracts import parse_workflow_request
    from test_workflow_v2_structured_routing import _request

    raw = _request(risk="low", complex=True)
    raw["criteria"].append({"id": "ac2", "kind": "behavior", "text": "Also preserve compatibility."})
    raw["verificationChecks"][0]["criterionIds"].append("ac2")
    request = parse_workflow_request(raw, legacy_task_id="task_policy")
    args = dict(request=request, profile=request.task_profile, workflow_id="wf_policy",
        request_digest="sha256:" + "a" * 64, objective_digest="sha256:" + "b" * 64,
        base_digest="sha256:" + "c" * 64, user_request_digest=None,
        user_review_requested=False, delegation_review_requested=False, host_scope_reasons=())
    old = review_routing_decision(**args, policy_version=5)
    current = review_routing_decision(**args, analysis_requested=True)
    assert old["independentReviewRequired"] is True
    assert current["independentReviewRequired"] is False
    assert current["reasons"] == ["explicit_analysis_requested"]
    assert set(old) == set(current)
    for document in (old, current):
        assert validate_review_routing_decision(document, **{key: args[key] for key in (
            "request", "profile", "workflow_id", "request_digest", "objective_digest", "base_digest",
        )}) is document["independentReviewRequired"]
