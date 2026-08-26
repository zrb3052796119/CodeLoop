"""Content-free scheduler trace artifact contract."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from minicode.central_scheduler import (
    CentralScheduler,
    DispatchStage,
    StageResult,
    TaskKind,
    TaskProfile,
    TaskRisk,
)
from minicode.scheduler_trace import (
    SCHEDULER_TRACE_MARKER,
    extract_scheduler_trace,
    project_scheduler_trace,
    render_scheduler_trace,
)


_WORKFLOW_ID = "wf_scheduler_trace_0001"
_TASK_ID = "task_scheduler_trace_0001"
_PATCH_DIGEST = "sha256:trace-patch"


def _successful_scheduler_inputs():
    scheduler = CentralScheduler()
    profile = TaskProfile(
        task_id=_TASK_ID,
        kind=TaskKind.CHANGE,
        risk=TaskRisk.HIGH,
        complex=True,
    )
    plan = scheduler.plan(profile)
    transitions = []

    def run_worker(assignment):
        if assignment.stage is DispatchStage.EXECUTOR:
            return StageResult(
                status="completed",
                artifact_id="artifact_executor_result",
                patch_digest=_PATCH_DIGEST,
            )
        if assignment.stage is DispatchStage.REVIEWER:
            return StageResult(
                status="completed",
                artifact_id="artifact_review_verdict",
                patch_digest=_PATCH_DIGEST,
                approved=True,
            )
        return StageResult(
            status="completed",
            artifact_id="artifact_analyst_report",
        )

    def run_verifier(_assignment):
        return StageResult(
            status="completed",
            artifact_id="verification_scheduler_trace",
            patch_digest=_PATCH_DIGEST,
        )

    outcome = scheduler.execute(
        profile,
        workflow_id=_WORKFLOW_ID,
        run_worker=run_worker,
        run_verifier=run_verifier,
        commit=lambda _request: True,
        on_transition=transitions.append,
    )
    return plan, outcome, transitions


def test_scheduler_trace_projects_json_safe_correlations_and_round_trips() -> None:
    plan, outcome, transitions = _successful_scheduler_inputs()

    trace = project_scheduler_trace(plan, outcome, transitions)

    assert trace == json.loads(json.dumps(trace))
    assert set(trace) == {
        "schedulerTraceVersion",
        "workflowId",
        "taskId",
        "policyVersion",
        "ruleId",
        "status",
        "committed",
        "terminalReason",
        "transitions",
    }
    assert trace["schedulerTraceVersion"] == 1
    assert trace["workflowId"] == _WORKFLOW_ID
    assert trace["taskId"] == _TASK_ID
    assert trace["policyVersion"] == plan.policy_version
    assert trace["ruleId"] == "route.reviewed_change"
    assert trace["status"] == "completed"
    assert trace["committed"] is True
    assert trace["terminalReason"] is None
    assert trace["transitions"]
    assert all(
        set(transition) == {
            "sequence",
            "workflowId",
            "taskId",
            "assignmentId",
            "attemptId",
            "agentRunId",
            "decisionId",
            "parentDecisionId",
            "stage",
            "outcome",
            "artifactId",
            "errorCode",
        }
        for transition in trace["transitions"]
    )
    first_transition = transitions[0]
    assert trace["transitions"][0] == {
        "sequence": 1,
        "workflowId": _WORKFLOW_ID,
        "taskId": _TASK_ID,
        "assignmentId": first_transition.assignment_id,
        "attemptId": first_transition.attempt_id,
        "agentRunId": first_transition.agent_run_id,
        "decisionId": first_transition.decision_id,
        "parentDecisionId": None,
        "stage": "analyst",
        "outcome": "dispatched",
        "artifactId": None,
        "errorCode": None,
    }
    rendered = render_scheduler_trace(trace)
    assert rendered.startswith(SCHEDULER_TRACE_MARKER + "\n")
    assert extract_scheduler_trace("task narrative\n\n" + rendered) == trace
    assert extract_scheduler_trace(
        "task narrative\n\n"
        + rendered
        + '\n\n=== SUBAGENT RESULT v1 ===\n{"resultVersion":1}'
    ) == trace
    assert extract_scheduler_trace("task narrative only") is None


def test_scheduler_trace_rejects_inconsistent_correlation_authority() -> None:
    plan, outcome, transitions = _successful_scheduler_inputs()

    with pytest.raises(ValueError, match="correlation"):
        project_scheduler_trace(
            plan,
            replace(outcome, task_id="task_other_trace"),
            transitions,
        )

    wrong_workflow = [
        replace(transitions[0], workflow_id="wf_other_trace"),
        *transitions[1:],
    ]
    with pytest.raises(ValueError, match="correlation"):
        project_scheduler_trace(plan, outcome, wrong_workflow)

    broken_decision_chain = [
        transitions[0],
        replace(transitions[1], parent_decision_id="decision_wrong"),
        *transitions[2:],
    ]
    with pytest.raises(ValueError, match="correlation"):
        project_scheduler_trace(plan, outcome, broken_decision_chain)


def test_scheduler_trace_rejects_content_bearing_values() -> None:
    plan, outcome, transitions = _successful_scheduler_inputs()

    with pytest.raises(ValueError, match="content-free"):
        project_scheduler_trace(
            replace(plan, rule_id="route.reviewed_change /private/secret"),
            outcome,
            transitions,
        )

    with pytest.raises(ValueError, match="content-free"):
        project_scheduler_trace(
            plan,
            replace(outcome, terminal_reason="model said to reveal the prompt"),
            transitions,
        )

    path_bearing_artifact = [
        replace(transitions[0], artifact_id="/private/workspace/auth.py"),
        *transitions[1:],
    ]
    with pytest.raises(ValueError, match="content-free"):
        project_scheduler_trace(plan, outcome, path_bearing_artifact)


def test_render_and_extract_accept_only_the_exact_closed_trace_schema() -> None:
    plan, outcome, transitions = _successful_scheduler_inputs()
    trace = project_scheduler_trace(plan, outcome, transitions)
    invalid_payloads = []

    with_prompt = {**trace, "prompt": "model-private task text"}
    invalid_payloads.append(with_prompt)

    with_path = deepcopy(trace)
    with_path["transitions"][0]["path"] = "/private/workspace/auth.py"
    invalid_payloads.append(with_path)

    boolean_version = {**trace, "schedulerTraceVersion": True}
    invalid_payloads.append(boolean_version)

    unknown_stage = deepcopy(trace)
    unknown_stage["transitions"][0]["stage"] = "model_planner"
    invalid_payloads.append(unknown_stage)

    completed_with_reason = {
        **trace,
        "terminalReason": "unexpected_failure",
    }
    invalid_payloads.append(completed_with_reason)

    committed_failure = {
        **trace,
        "status": "failed",
        "terminalReason": "stage_failed",
    }
    invalid_payloads.append(committed_failure)

    duplicate_final_decision = deepcopy(trace)
    duplicate_final_decision["transitions"][-1]["decisionId"] = (
        duplicate_final_decision["transitions"][-2]["decisionId"]
    )
    invalid_payloads.append(duplicate_final_decision)

    unknown_policy_rule = {**trace, "ruleId": "route.model_selected_path"}
    invalid_payloads.append(unknown_policy_rule)

    for payload in invalid_payloads:
        with pytest.raises(ValueError, match="schema"):
            render_scheduler_trace(payload)
        encoded = SCHEDULER_TRACE_MARKER + "\n" + json.dumps(payload)
        assert extract_scheduler_trace(encoded) is None


def test_projection_rejects_internally_inconsistent_scheduler_state() -> None:
    plan, outcome, transitions = _successful_scheduler_inputs()

    with pytest.raises(ValueError):
        project_scheduler_trace(
            replace(
                plan,
                stages=(DispatchStage.ANALYST, DispatchStage.REVIEWER),
            ),
            outcome,
            transitions,
        )

    with pytest.raises(ValueError):
        project_scheduler_trace(
            plan,
            replace(
                outcome,
                completed_stages=(DispatchStage.ANALYST,),
            ),
            transitions,
        )

    duplicate_final_decision = [
        *transitions[:-1],
        replace(
            transitions[-1],
            decision_id=transitions[-2].decision_id,
        ),
    ]
    with pytest.raises(ValueError):
        project_scheduler_trace(plan, outcome, duplicate_final_decision)

    unplanned_final_stage = [
        *transitions[:-1],
        replace(transitions[-1], stage=DispatchStage.ADJUDICATOR),
    ]
    with pytest.raises(ValueError):
        project_scheduler_trace(plan, outcome, unplanned_final_stage)


def test_render_rejects_skipped_stage_and_bare_completion_lifecycles() -> None:
    plan, outcome, transitions = _successful_scheduler_inputs()
    trace = project_scheduler_trace(plan, outcome, transitions)

    def rechain(items):
        parent = None
        for sequence, item in enumerate(items, start=1):
            item["sequence"] = sequence
            item["parentDecisionId"] = parent
            parent = item["decisionId"]

    bare_completion = deepcopy(trace)
    bare_completion["transitions"] = [
        item
        for item in bare_completion["transitions"]
        if not (
            item["stage"] == "analyst" and item["outcome"] == "dispatched"
        )
    ]
    rechain(bare_completion["transitions"])

    commit_only = deepcopy(trace)
    commit_only["transitions"] = [
        item
        for item in commit_only["transitions"]
        if item["stage"] == "commit"
    ]
    rechain(commit_only["transitions"])

    terminal_then_completed = deepcopy(trace)
    analyst_completed_index = next(
        index
        for index, item in enumerate(terminal_then_completed["transitions"])
        if item["stage"] == "analyst" and item["outcome"] == "completed"
    )
    fabricated_terminal = dict(
        terminal_then_completed["transitions"][analyst_completed_index]
    )
    fabricated_terminal.update(
        {
            "decisionId": "decision_fabricated_terminal",
            "outcome": "failed",
            "artifactId": None,
            "errorCode": "stage_failed",
        }
    )
    terminal_then_completed["transitions"].insert(
        analyst_completed_index,
        fabricated_terminal,
    )
    rechain(terminal_then_completed["transitions"])

    skipped_executor = deepcopy(trace)
    skipped_executor["transitions"] = [
        item
        for item in skipped_executor["transitions"]
        if item["stage"] not in {"executor", "reviewer", "commit"}
    ]
    skipped_executor["transitions"][-1].update(
        {
            "outcome": "failed",
            "artifactId": None,
            "errorCode": "verifier_failed",
        }
    )
    skipped_executor.update(
        {
            "status": "failed",
            "committed": False,
            "terminalReason": "verifier_failed",
        }
    )
    rechain(skipped_executor["transitions"])

    silent_retry = deepcopy(trace)
    first_dispatch = dict(silent_retry["transitions"][0])
    first_dispatch.update(
        {
            "assignmentId": "asg_silent_retry",
            "attemptId": "attempt_silent_retry",
            "agentRunId": "sub_silent_retry",
            "decisionId": "decision_silent_retry_dispatch",
        }
    )
    first_failure = dict(first_dispatch)
    first_failure.update(
        {
            "decisionId": "decision_silent_retry_failed",
            "outcome": "failed",
            "errorCode": "analyst_failed",
        }
    )
    silent_retry["transitions"][:0] = [first_dispatch, first_failure]
    rechain(silent_retry["transitions"])

    authorized_retry = deepcopy(trace)
    prior_dispatch = dict(authorized_retry["transitions"][0])
    prior_dispatch.update(
        {
            "attemptId": "attempt_prior_retry",
            "agentRunId": "sub_prior_retry",
            "decisionId": "decision_prior_retry_dispatch",
        }
    )
    prior_failure = dict(prior_dispatch)
    prior_failure.update(
        {
            "decisionId": "decision_prior_retry_failed",
            "outcome": "failed",
            "errorCode": "analyst_failed",
        }
    )
    prior_retrying = dict(prior_failure)
    prior_retrying.update(
        {
            "decisionId": "decision_prior_retrying",
            "outcome": "retrying",
        }
    )
    authorized_retry["transitions"][:0] = [
        prior_dispatch,
        prior_failure,
        prior_retrying,
    ]
    rechain(authorized_retry["transitions"])
    render_scheduler_trace(authorized_retry)

    changed_retry_assignment = deepcopy(authorized_retry)
    for item in changed_retry_assignment["transitions"]:
        if item["stage"] == "analyst" and item["attemptId"] != (
            "attempt_prior_retry"
        ):
            item["assignmentId"] = "asg_different_retry_assignment"
    rechain(changed_retry_assignment["transitions"])

    commit_terminal_then_completed = deepcopy(trace)
    commit_completed_index = next(
        index
        for index, item in enumerate(
            commit_terminal_then_completed["transitions"]
        )
        if item["stage"] == "commit" and item["outcome"] == "completed"
    )
    commit_failure = dict(
        commit_terminal_then_completed["transitions"][commit_completed_index]
    )
    commit_failure.update(
        {
            "decisionId": "decision_commit_failed",
            "outcome": "failed",
            "errorCode": "commit_failed",
        }
    )
    commit_terminal_then_completed["transitions"].insert(
        commit_completed_index,
        commit_failure,
    )
    rechain(commit_terminal_then_completed["transitions"])

    changed_commit_identity = deepcopy(trace)
    changed_commit_identity["transitions"][-1].update(
        {
            "assignmentId": "asg_different_commit",
            "attemptId": "attempt_different_commit",
            "agentRunId": "sub_different_commit",
        }
    )
    rechain(changed_commit_identity["transitions"])

    mismatched_terminal_reason = deepcopy(skipped_executor)
    mismatched_terminal_reason["terminalReason"] = "different_reason"

    for forged in (
        bare_completion,
        commit_only,
        terminal_then_completed,
        skipped_executor,
        silent_retry,
        changed_retry_assignment,
        commit_terminal_then_completed,
        changed_commit_identity,
        mismatched_terminal_reason,
    ):
        with pytest.raises(ValueError, match="schema"):
            render_scheduler_trace(forged)
        encoded = SCHEDULER_TRACE_MARKER + "\n" + json.dumps(forged)
        assert extract_scheduler_trace(encoded) is None


def test_parent_only_trace_rejects_a_fabricated_dispatch() -> None:
    scheduler = CentralScheduler()
    profile = TaskProfile(
        task_id="task_parent_trace",
        kind=TaskKind.READ_ONLY,
        risk=TaskRisk.LOW,
        parent_has_sufficient_context=True,
    )
    plan = scheduler.plan(profile)
    transitions = []
    outcome = scheduler.execute(
        profile,
        workflow_id="wf_parent_trace",
        run_worker=lambda _assignment: pytest.fail("not reached"),
        run_verifier=lambda _assignment: pytest.fail("not reached"),
        commit=lambda _request: pytest.fail("not reached"),
        on_transition=transitions.append,
    )
    trace = project_scheduler_trace(plan, outcome, transitions)
    forged = deepcopy(trace)
    completed = forged["transitions"][0]
    dispatched = dict(completed)
    dispatched.update(
        {
            "decisionId": "decision_parent_dispatched",
            "outcome": "dispatched",
        }
    )
    completed["sequence"] = 2
    completed["parentDecisionId"] = dispatched["decisionId"]
    forged["transitions"] = [dispatched, completed]

    with pytest.raises(ValueError, match="schema"):
        render_scheduler_trace(forged)
