"""Resilience contracts for sealed workflow finalization."""

from __future__ import annotations

import time
from dataclasses import fields

import pytest

from minicode import workflow_finalization
from minicode.central_scheduler import (
    CentralScheduler,
    TaskKind,
    TaskProfile,
    TaskRisk,
)
from minicode.tooling import ToolResult
from minicode.turn_cancellation import TurnCancellationRequested


def _read_only_snapshot() -> workflow_finalization._WorkflowFinalizationSnapshot:
    profile = TaskProfile(
        task_id="task_finalization_resilience",
        kind=TaskKind.READ_ONLY,
        risk=TaskRisk.LOW,
        parent_has_sufficient_context=True,
        scope_clear=True,
    )
    scheduler = CentralScheduler()
    plan = scheduler.plan(profile)
    transitions = []

    def forbidden_execution_capability(*_args, **_kwargs):
        raise AssertionError("the parent-only route cannot execute a phase port")

    outcome = scheduler.execute(
        profile,
        workflow_id="wf_finalization_resilience",
        run_worker=forbidden_execution_capability,
        run_verifier=forbidden_execution_capability,
        commit=forbidden_execution_capability,
        on_transition=transitions.append,
    )
    capture_kwargs = {
        "description": "finalization resilience",
        "subagent_id": "sub_" + "a" * 32,
        "started_at": time.time(),
        "contract_mode": "legacy",
        "profile": profile,
        "dispatch_plan": plan,
        "scheduler_outcome": outcome,
        "transitions": transitions,
        "artifacts": [],
        "explore_results": [],
        "plan_result": None,
        "execute_result": None,
        "review_result": None,
        "analyst_report": None,
        "execution_artifact": None,
        "verification_result": None,
        "typed_review": {
            "reviewVersion": 1,
            "verdict": "not_applicable",
            "blockingFindings": [],
            "warnings": [],
        },
        "approval_request_id": None,
        "commit_ready_status": "not_applicable",
        "commit_error_text": None,
        "production_audit_required": False,
        "initial_finalization_status": "completed",
    }
    return workflow_finalization._WorkflowFinalizationSnapshot.capture(**capture_kwargs)


def _direct_snapshot_values() -> dict[str, object]:
    snapshot = _read_only_snapshot()
    return {field.name: getattr(snapshot, field.name) for field in fields(snapshot)}


def test_uncommitted_audit_persistence_cancellation_propagates() -> None:
    cancellation = TurnCancellationRequested("turn_" + "b" * 32)

    def cancel_during_audit(_audit: object) -> object:
        raise cancellation

    with pytest.raises(TurnCancellationRequested) as caught:
        workflow_finalization.finalize_workflow(
            _read_only_snapshot(),
            workflow_finalization._WorkflowFinalizationPorts(
                checkpoint=lambda: None,
                persist_audit=cancel_during_audit,
                observe=lambda _observation: None,
            ),
        )

    assert caught.value is cancellation


def test_phase_result_snapshot_rejects_tool_result_subclasses() -> None:
    class DerivedToolResult(ToolResult):
        pass

    with pytest.raises(TypeError):
        workflow_finalization._PhaseResultSnapshot.from_result(
            DerivedToolResult(ok=True, output="subclass output")
        )


def test_direct_snapshot_rejects_invalid_initial_finalization_status() -> None:
    values = _direct_snapshot_values()
    values["initial_finalization_status"] = "execution_still_active"

    with pytest.raises(ValueError):
        workflow_finalization._WorkflowFinalizationSnapshot(**values)


def test_direct_snapshot_rejects_mutable_commit_error() -> None:
    values = _direct_snapshot_values()
    values["commit_error_text"] = RuntimeError("mutable execution error")

    with pytest.raises(TypeError):
        workflow_finalization._WorkflowFinalizationSnapshot(**values)
