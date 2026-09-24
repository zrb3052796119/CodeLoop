"""Implementation repair is an explicit, bounded trace transition."""

from copy import deepcopy
from dataclasses import replace

import pytest

from minicode.central_scheduler import DispatchStage
from minicode.scheduler_trace import (
    extract_scheduler_trace,
    project_scheduler_trace,
    render_scheduler_trace,
)
from test_scheduler_trace import _successful_scheduler_inputs


def repair_inputs(failed_stage="verifier", repairs=1):
    plan, outcome, original = _successful_scheduler_inputs()
    executor_start = next(
        index for index, item in enumerate(original)
        if item.stage is DispatchStage.EXECUTOR
    )
    failure_index = next(
        index for index, item in enumerate(original)
        if item.stage.value == failed_stage and item.outcome == "completed"
    )
    transitions = list(original[:executor_start])
    for revision in range(repairs + 1):
        candidate = [
            replace(
                item,
                assignment_id=f"{item.assignment_id}_r{revision}",
                attempt_id=f"{item.attempt_id}_r{revision}",
                agent_run_id=f"{item.agent_run_id}_r{revision}",
                artifact_id=f"{item.artifact_id}_r{revision}" if item.artifact_id else None,
            )
            for item in original[executor_start:]
        ]
        if revision < repairs:
            candidate = candidate[:failure_index - executor_start + 1]
            candidate[-1] = replace(
                candidate[-1], outcome="failed", error_code="implementation_check_failed"
            )
            candidate.append(replace(
                candidate[-1], outcome="repairing", error_code="implementation_repair_requested"
            ))
        transitions.extend(candidate)
    previous = None
    for index, item in enumerate(transitions, 1):
        decision_id = f"decision_repair_trace_{index}"
        transitions[index - 1] = replace(
            item, sequence=index, decision_id=decision_id, parent_decision_id=previous
        )
        previous = decision_id
    return plan, outcome, transitions


@pytest.mark.parametrize("failed_stage", ["verifier", "reviewer"])
@pytest.mark.parametrize("repairs", [1, 2])
def test_repair_trace_round_trips_with_fresh_execution_gates(failed_stage, repairs):
    plan, outcome, transitions = repair_inputs(failed_stage, repairs)
    trace = project_scheduler_trace(plan, outcome, transitions)
    assert extract_scheduler_trace(render_scheduler_trace(trace)) == trace
    assert sum(item["outcome"] == "repairing" for item in trace["transitions"]) == repairs


@pytest.mark.parametrize("forgery", [
    "missing_marker", "different_failure_artifact", "wrong_repair_cause",
    "successful_failure", "reused_executor", "reused_verifier", "skip_verifier",
])
def test_repair_trace_rejects_missing_or_stale_authority(forgery):
    plan, outcome, transitions = repair_inputs()
    trace = project_scheduler_trace(plan, outcome, transitions)
    forged = deepcopy(trace)
    items = forged["transitions"]
    repair_at = next(index for index, item in enumerate(items) if item["outcome"] == "repairing")
    if forgery == "missing_marker":
        items.pop(repair_at)
    elif forgery == "different_failure_artifact":
        items[repair_at]["artifactId"] = "artifact_unrelated"
    elif forgery == "wrong_repair_cause":
        items[repair_at]["errorCode"] = "stage_result_invalid"
    elif forgery == "successful_failure":
        items[repair_at - 1].update(outcome="completed", errorCode=None)
    elif forgery in {"reused_executor", "reused_verifier"}:
        stage = forgery.removeprefix("reused_")
        first = next(item for item in items if item["stage"] == stage)
        for item in items[repair_at + 1:]:
            if item["stage"] == stage:
                for key in ("assignmentId", "attemptId", "agentRunId"):
                    item[key] = first[key]
    else:
        items[:] = [item for index, item in enumerate(items) if not (
            index > repair_at and item["stage"] == "verifier"
        )]
    previous = None
    for index, item in enumerate(items, 1):
        item.update(sequence=index, parentDecisionId=previous)
        previous = item["decisionId"]
    with pytest.raises(ValueError):
        render_scheduler_trace(forged)


def test_repair_trace_rejects_more_than_two_repairs():
    with pytest.raises(ValueError):
        project_scheduler_trace(*repair_inputs(repairs=3))
