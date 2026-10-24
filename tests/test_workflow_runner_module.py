"""Architectural boundary tests for the centrally controlled workflow runner."""

from __future__ import annotations

import ast
import inspect
import json
import time
from pathlib import Path

import pytest


def _imported_modules(source_path: Path) -> set[str | None]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    return imported


def _imported_names(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }


def test_workflow_finalization_cannot_import_execution_capabilities() -> None:
    from minicode import workflow_finalization

    source_path = Path(workflow_finalization.__file__)
    forbidden_modules = {
        "importlib",
        "minicode.agent_loop",
        "minicode.deterministic_verifier",
        "minicode.tools.task",
        "minicode.workflow_runner",
        "minicode.workflow_workspace",
        "os",
        "shutil",
        "subprocess",
        "tempfile",
    }
    forbidden_names = {
        "AssignmentArtifactChannel",
        "CentralScheduler",
        "CommitRequest",
        "DeterministicVerifier",
        "FrozenWorkflowChange",
        "SchedulerArtifactStore",
        "StageAssignment",
        "StageResult",
        "WorkflowWorkspaceTransaction",
    }

    assert _imported_modules(source_path).isdisjoint(forbidden_modules)
    assert _imported_names(source_path).isdisjoint(forbidden_names)


def test_workflow_finalization_uses_a_frozen_snapshot_and_narrow_ports() -> None:
    from minicode import workflow_finalization

    parameters = inspect.signature(workflow_finalization.finalize_workflow).parameters
    assert tuple(parameters) == ("snapshot", "ports", "evidence_note")
    assert parameters["evidence_note"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["evidence_note"].default == ""

    snapshot_type = workflow_finalization._WorkflowFinalizationSnapshot
    ports_type = workflow_finalization._WorkflowFinalizationPorts
    phase_type = workflow_finalization._PhaseResultSnapshot
    assert snapshot_type.__dataclass_params__.frozen is True
    assert ports_type.__dataclass_params__.frozen is True
    assert phase_type.__dataclass_params__.frozen is True
    assert set(ports_type.__dataclass_fields__) == {
        "checkpoint",
        "persist_audit",
        "observe",
    }
    forbidden_snapshot_fields = {
        "context",
        "run_subagent",
        "transaction",
        "frozen_workspace_change",
        "verifier",
        "human_approval_provider",
        "prepare_commit",
        "commit",
        "promotion",
        "artifact_store",
        "scheduler_artifact_authority",
    }
    assert set(snapshot_type.__dataclass_fields__).isdisjoint(forbidden_snapshot_fields)


def test_finalization_snapshot_detaches_mutable_authority_inputs() -> None:
    from minicode import workflow_finalization
    from minicode.central_scheduler import (
        CentralScheduler,
        TaskKind,
        TaskProfile,
        TaskRisk,
        WorkflowExecutionOutcome,
    )
    from minicode.tooling import ToolResult

    profile = TaskProfile(
        task_id="task_snapshot",
        kind=TaskKind.CHANGE,
        risk=TaskRisk.LOW,
        scope_clear=True,
        acceptance_executable=True,
    )
    plan = CentralScheduler().plan(profile)
    outcome = WorkflowExecutionOutcome(
        workflow_id="wf_snapshot",
        task_id=profile.task_id,
        status="failed",
        completed_stages=(),
        committed=False,
        terminal_reason="capture_test",
    )
    explore_results = [ToolResult(ok=True, output="explore-before")]
    plan_result = ToolResult(ok=True, output="plan-before")
    analyst_report = {"nested": ["analyst-before"]}
    execution_artifact = {"nested": ["execution-before"]}
    verification_result = {"nested": ["verification-before"]}
    typed_review = {"nested": ["review-before"]}

    snapshot = workflow_finalization._WorkflowFinalizationSnapshot.capture(
        description="detached snapshot",
        subagent_id="sub_snapshot",
        started_at=1.0,
        contract_mode="v2",
        profile=profile,
        dispatch_plan=plan,
        scheduler_outcome=outcome,
        transitions=[],
        artifacts=[],
        explore_results=explore_results,
        plan_result=plan_result,
        execute_result=None,
        review_result=None,
        analyst_report=analyst_report,
        execution_artifact=execution_artifact,
        verification_result=verification_result,
        typed_review=typed_review,
        approval_request_id=None,
        commit_ready_status="not_applicable",
        commit_error_text=None,
        production_audit_required=False,
        initial_finalization_status="completed",
    )

    explore_results.append(ToolResult(ok=False, output="late result"))
    explore_results[0].ok = False
    explore_results[0].output = "explore-after"
    plan_result.ok = False
    plan_result.output = "plan-after"
    analyst_report["nested"].append("analyst-after")
    execution_artifact["nested"].append("execution-after")
    verification_result["nested"].append("verification-after")
    typed_review["nested"].append("review-after")

    assert snapshot.explore_results == (
        workflow_finalization._PhaseResultSnapshot(
            ok=True,
            output="explore-before",
        ),
    )
    assert snapshot.plan_result == workflow_finalization._PhaseResultSnapshot(
        ok=True,
        output="plan-before",
    )
    assert json.loads(snapshot.analyst_report_json) == {"nested": ["analyst-before"]}
    assert json.loads(snapshot.execution_artifact_json) == {
        "nested": ["execution-before"]
    }
    assert json.loads(snapshot.verification_result_json) == {
        "nested": ["verification-before"]
    }
    assert json.loads(snapshot.typed_review_json) == {"nested": ["review-before"]}
    detached_result = snapshot.plan_result.to_result()
    detached_result.output = "detached mutation"
    assert snapshot.plan_result.output == "plan-before"


def test_finalize_workflow_runs_with_finalization_only_ports() -> None:
    from minicode import workflow_finalization
    from minicode.central_scheduler import (
        CentralScheduler,
        TaskKind,
        TaskProfile,
        TaskRisk,
    )

    transitions = []
    profile = TaskProfile(
        task_id="task_finalizer_only",
        kind=TaskKind.READ_ONLY,
        risk=TaskRisk.LOW,
        parent_has_sufficient_context=True,
        scope_clear=True,
    )
    scheduler = CentralScheduler()
    plan = scheduler.plan(profile)

    def forbidden_execution_capability(*_args, **_kwargs):
        raise AssertionError("the parent-only route must not use execution ports")

    outcome = scheduler.execute(
        profile,
        workflow_id="wf_finalizer_only",
        run_worker=forbidden_execution_capability,
        run_verifier=forbidden_execution_capability,
        commit=forbidden_execution_capability,
        on_transition=transitions.append,
    )
    snapshot = workflow_finalization._WorkflowFinalizationSnapshot.capture(
        description="finalizer-only",
        subagent_id="sub_" + "f" * 32,
        started_at=time.time(),
        contract_mode="legacy",
        profile=profile,
        dispatch_plan=plan,
        scheduler_outcome=outcome,
        transitions=transitions,
        artifacts=[],
        explore_results=[],
        plan_result=None,
        execute_result=None,
        review_result=None,
        analyst_report=None,
        execution_artifact=None,
        verification_result=None,
        typed_review={
            "reviewVersion": 1,
            "verdict": "not_applicable",
            "blockingFindings": [],
            "warnings": [],
        },
        approval_request_id=None,
        commit_ready_status="not_applicable",
        commit_error_text=None,
        production_audit_required=False,
        initial_finalization_status="completed",
    )
    audits = []
    observations = []
    result = workflow_finalization.finalize_workflow(
        snapshot,
        workflow_finalization._WorkflowFinalizationPorts(
            checkpoint=lambda: None,
            persist_audit=lambda audit: audits.append(audit) is None,
            observe=observations.append,
        ),
    )

    assert result.ok is True
    assert result._must_deliver is False
    assert "=== WORKFLOW FINALIZATION v1 ===" in result.output
    assert len(result.output) <= workflow_finalization._WORKFLOW_OUTPUT_MAX_CHARS
    assert len(audits) == 1
    assert len(observations) == 1


def test_workflow_runner_exposes_one_complete_run_seam() -> None:
    from minicode import workflow_runner

    assert workflow_runner.__all__ == ("run_workflow",)
    parameters = inspect.signature(workflow_runner.run_workflow).parameters
    assert tuple(parameters) == (
        "input_data",
        "context",
        "depth",
        "subagent_id",
        "run_subagent",
    )
    assert parameters["depth"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["subagent_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["run_subagent"].kind is inspect.Parameter.KEYWORD_ONLY


def test_workflow_session_has_one_mutable_authority_state() -> None:
    from minicode import workflow_runner

    assert set(workflow_runner._WorkflowRunSession.__slots__) == {
        "_input_data",
        "_context",
        "_depth",
        "_subagent_id",
        "_workflow_journal",
        "_transaction",
        "_run_subagent",
        "_ran",
        "_state",
        "_executor_session",
        "_continuation_used_calls",
        "_continuation_candidates",
        "_verification_outputs",
        "_usage_counts",
        "_usage_lock",
    }
    state_type = workflow_runner._WorkflowExecutionState
    assert state_type.__dataclass_params__.frozen is False
    assert "__dict__" not in state_type.__slots__


def test_workflow_session_seal_revokes_every_execution_capability(tmp_path) -> None:
    from minicode import workflow_runner
    from minicode.executor_session import ExecutorSession

    session = workflow_runner._WorkflowRunSession.__new__(
        workflow_runner._WorkflowRunSession
    )
    session._state = object()
    session._context = object()
    session._workflow_journal = object()
    session._transaction = object()
    session._run_subagent = object()
    continuation = ExecutorSession(owner=session, workspace_root=str(tmp_path))
    session._executor_session = continuation
    session._verification_outputs = None

    session._seal_execution_capabilities()

    assert session._state is None
    assert session._context is None
    assert session._workflow_journal is None
    assert session._transaction is None
    assert session._run_subagent is None
    assert session._executor_session is None
    with pytest.raises(ValueError, match="closed"):
        continuation.begin(owner=session, context=object())
    with pytest.raises(RuntimeError, match="authority is sealed"):
        session._execution_state()


def test_finalization_port_restores_the_exact_typed_cancellation() -> None:
    from minicode import workflow_runner
    from minicode.conflict_port_runner import ConflictPortCancelled
    from minicode.turn_cancellation import TurnCancellationRequested

    cancellation = TurnCancellationRequested("turn_" + "a" * 32)

    class CancellingRunner:
        def run(self, *_args, **_kwargs):
            raise ConflictPortCancelled(cause=cancellation)

    with pytest.raises(TurnCancellationRequested) as caught:
        workflow_runner._run_finalization_port(
            CancellingRunner(),
            lambda: None,
            port_name="workflow-final-audit",
            operation_id="wf_test:audit",
            context=object(),
            committed=False,
        )

    assert caught.value is cancellation


def test_workflow_runner_does_not_reverse_import_task_tool() -> None:
    from minicode import workflow_runner

    source_path = Path(workflow_runner.__file__)
    assert "minicode.tools.task" not in _imported_modules(source_path)


def test_task_tool_does_not_own_workflow_runtime_internals() -> None:
    import minicode.tools.task as task_module

    forbidden = {
        "minicode.central_scheduler",
        "minicode.deterministic_verifier",
        "minicode.scheduler_artifacts",
        "minicode.scheduler_audit",
        "minicode.scheduler_trace",
        "minicode.workflow_workspace",
    }
    assert _imported_modules(Path(task_module.__file__)).isdisjoint(forbidden)


def test_task_binds_the_current_child_runner_at_workflow_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import minicode.tools.task as task_module
    from minicode.tooling import ToolContext, ToolResult

    captured: dict[str, object] = {}

    def child_runner(_input_data: dict, _context: object) -> ToolResult:
        raise AssertionError("the capture runner must not dispatch a child")

    def capture_workflow(
        input_data: dict,
        context: object,
        *,
        depth: int,
        subagent_id: str,
        run_subagent: object,
    ) -> ToolResult:
        captured.update(
            input_data=input_data,
            context=context,
            depth=depth,
            subagent_id=subagent_id,
            run_subagent=run_subagent,
        )
        return ToolResult(ok=True, output="captured")

    monkeypatch.setattr(task_module, "_run", child_runner)
    monkeypatch.setattr(task_module, "run_workflow", capture_workflow)

    result = task_module.task_tool.run(
        {
            "description": "capture workflow",
            "prompt": "Capture the runner handoff.",
            "agent_type": "workflow",
        },
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}),
    )

    assert result.ok is True
    assert captured["run_subagent"] is child_runner
    assert captured["depth"] == 0
    assert str(captured["subagent_id"]).startswith("sub_")
