"""Host usage survives failures and projection without trusting display text."""

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path

import pytest

from minicode import subagent_worker, workflow_finalization, workflow_runner
from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.subagent_mailbox import SubagentMailbox
from minicode.subagent_supervisor import SubagentProcessOutcome
from minicode.task_deadline import TaskDeadlineExceeded
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext, ToolResult, ToolUsageCounts
from minicode.tools import task as task_module
from test_subagent_subprocess import _TYPED_REPORT, _run_worker_in_process
from test_workflow_finalization_resilience import _read_only_snapshot


def test_workflow_counts_ignore_forged_or_changed_display_and_keep_failures():
    results = [
        ToolResult(ok=True, output="Model turns: 999 (tool calls: 999)"),
        ToolResult(ok=False, output="provider stopped", _usage_counts=ToolUsageCounts(2, 3)),
        ToolResult(ok=True, output="new header format", _usage_counts=ToolUsageCounts(4, 5)),
    ]
    assert workflow_runner._workflow_result_counts(results) == (6, 8)
    snapshots = tuple(workflow_finalization._PhaseResultSnapshot.from_result(item)
                      for item in results)
    results[1]._usage_counts = ToolUsageCounts(100, 100)
    assert workflow_finalization._workflow_result_counts(snapshots) == (6, 8)
    assert snapshots[1].to_result()._usage_counts == ToolUsageCounts(2, 3)
    with pytest.raises(FrozenInstanceError):
        snapshots[1].usage_counts.model_turns = 100


@pytest.mark.parametrize("invalid", [-1, True, 1.5, "2", None])
def test_usage_counts_reject_malformed_host_values(invalid):
    with pytest.raises(ValueError):
        ToolUsageCounts(model_turns=invalid)
    with pytest.raises(ValueError):
        ToolUsageCounts(tool_calls=invalid)


def test_final_result_and_projection_failure_keep_detached_usage():
    phase = workflow_finalization._PhaseResultSnapshot.from_result(
        ToolResult(ok=False, output="Model turns: 999 (tool calls: 999)",
                   _usage_counts=ToolUsageCounts(2, 3)))
    snapshot = replace(_read_only_snapshot(), explore_results=(phase,))
    ports = workflow_finalization._WorkflowFinalizationPorts(
        checkpoint=lambda: None, persist_audit=None, observe=lambda _event: None)
    result = workflow_finalization.finalize_workflow(snapshot, ports)
    assert result._usage_counts == ToolUsageCounts(2, 3)
    fallback = workflow_finalization._fallback_tool_result(
        snapshot, workflow_finalization._ProjectionState(finalization_status="projection_failed"))
    assert fallback._usage_counts == ToolUsageCounts(2, 3)


def test_host_attempt_aggregate_survives_snapshot_projection_fallback():
    snapshot = replace(_read_only_snapshot(), usage_counts=ToolUsageCounts(9, 12))
    observed = []
    ports = workflow_finalization._WorkflowFinalizationPorts(
        checkpoint=lambda: None, persist_audit=None, observe=observed.append)
    result = workflow_finalization.finalize_workflow(snapshot, ports)
    assert result._usage_counts == ToolUsageCounts(9, 12)
    assert [(item.model_turns, item.tool_calls) for item in observed] == [(9, 12)]
    fallback = workflow_finalization._fallback_tool_result(
        snapshot, workflow_finalization._ProjectionState(finalization_status="projection_failed"))
    assert fallback._usage_counts == ToolUsageCounts(9, 12)


def test_workflow_usage_includes_failed_analysis_attempt_before_success(tmp_path, monkeypatch):
    from minicode.deterministic_verifier import DeterministicVerifier
    from test_workflow_v2_structured_routing import (
        _request, _verification_result, _worker_role, _write_analyst_result,
        _write_executor_declaration,
    )

    Path(tmp_path, "allowed.txt").write_text("before")
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    roles, observations = [], []

    class Sink:
        def emit(self, event_type, *, step=None, payload=None):
            if event_type == "subagent.completed" and payload.get("agentType") == "workflow":
                observations.append(payload)

    def worker(data, context):
        role = _worker_role(data)
        roles.append(role)
        first_failure = role == "analyst" and len(roles) == 1
        counts = ToolUsageCounts(2 if first_failure or role == "executor" else 1,
                                 3 if first_failure else 4 if role == "analyst" else 5)
        for _ in range(counts.model_turns):
            reservation = context._agent_budget.reserve_model_call(10)
            context._agent_budget.record_model_call(input_tokens=10, output_tokens=1, reservation=reservation)
        if not first_failure:
            if role == "analyst":
                _write_analyst_result(data, context, prepare_change=True)
            else:
                Path(context.cwd, "allowed.txt").write_text("after")
                _write_executor_declaration(data, context)
        return ToolResult(ok=not first_failure, output="Model turns: 999 (tool calls: 999)",
                          _usage_counts=counts)

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", lambda _self, **kwargs: _verification_result(kwargs))
    result = task_module.task_tool.run(
        {"agent_type": "workflow", "description": "count all attempts",
         "prompt": "Analyze first, then update allowed.txt.", "workflow_request": _request()},
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}, _agent_budget=budget,
                    _workflow_commit_audit_waived=True, _event_sink=Sink()),
    )
    assert result.ok, result.output
    assert roles == ["analyst", "analyst", "executor"]
    assert result._usage_counts == ToolUsageCounts(5, 12)
    assert budget.snapshot().used_model_calls == 5
    assert [(item["modelTurns"], item["toolCalls"]) for item in observations] == [(5, 12)]


@pytest.mark.parametrize("ending", ["completed", "failed", "deadline", "budget", "error"])
def test_in_process_result_counts_survive_each_return_path(tmp_path, monkeypatch, ending):
    def turn(**kwargs):
        sink = kwargs["event_sink"]
        for _ in range(2):
            sink.emit("model.started")
        for _ in range(3):
            sink.emit("tool.started")
        if ending == "deadline":
            raise TaskDeadlineExceeded(1)
        if ending == "budget":
            raise AgentBudgetExceeded("finished", kwargs["agent_budget"].snapshot())
        if ending == "error":
            raise RuntimeError("provider failure")
        if ending == "completed":
            kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
            kwargs["subagent_mailbox"].write("usage_result", json.dumps(_TYPED_REPORT), author="child")
        return [{"role": "assistant", "content": "Model turns: 999 (tool calls: 999)"}]

    monkeypatch.setattr(task_module, "run_agent_turn", turn)
    result = task_module.task_tool.run(
        {"agent_type": "explore", "description": "record child usage", "prompt": "Inspect the code.",
         "_result_key": "usage_result"},
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}, _subagent_mailbox=SubagentMailbox()))
    assert result._usage_counts == ToolUsageCounts(2, 3)
    assert result.ok is (ending == "completed")
    if result.ok:
        assert "  Model turns: 2 (tool calls: 3)\n" in result.output


@pytest.mark.parametrize("status", ["completed", "failed", "deadline_exceeded", "budget_exceeded", "error"])
def test_subprocess_terminal_counts_are_received_without_parsing_prose(tmp_path, monkeypatch, status):
    def supervisor(_request, **_kwargs):
        return SubagentProcessOutcome(status=status, result={
            "version": 1, "status": status, "model_turns": 2, "tool_calls": 3,
            "final_message": "Model turns: 999 (tool calls: 999)",
        }, error="worker failed" if status != "completed" else None, duration_ms=1, pid=None)

    monkeypatch.setattr(task_module, "run_subagent_process", supervisor)
    result = task_module.task_tool.run(
        {"agent_type": "general", "description": "record child usage", "prompt": "Change the code."},
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}, _agent_budget=AgentTurnBudget()))
    assert result._usage_counts == ToolUsageCounts(2, 3)
    assert result.ok is (status == "completed")
    if result.ok:
        assert "  Model turns: 2 (tool calls: 3)\n" in result.output


@pytest.mark.parametrize("status,tool_calls", [("timed_out", 3), ("result_invalid", 3),
                                              ("completed", True), ("completed", -1)])
def test_untrusted_worker_counts_never_enter_host_usage(tmp_path, monkeypatch, status, tool_calls):
    monkeypatch.setattr(task_module, "run_subagent_process", lambda *_a, **_k:
        SubagentProcessOutcome(status=status, result={
            "version": 1, "status": status, "model_turns": 2, "tool_calls": tool_calls,
        }, error="untrusted", duration_ms=1, pid=None))
    result = task_module.task_tool.run(
        {"agent_type": "general", "description": "record child usage", "prompt": "Change the code."},
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}, _agent_budget=AgentTurnBudget()))
    assert result._usage_counts == ToolUsageCounts()


@pytest.mark.parametrize("ending", ["deadline", "budget", "error"])
def test_worker_serializes_observed_counts_on_failures(tmp_path, monkeypatch, ending):
    def turn(**kwargs):
        kwargs["event_sink"].emit("model.started")
        kwargs["event_sink"].emit("tool.started")
        if ending == "deadline":
            raise TaskDeadlineExceeded(1)
        if ending == "budget":
            raise AgentBudgetExceeded("finished", kwargs["agent_budget"].snapshot())
        raise RuntimeError("provider failure")

    monkeypatch.setattr(subagent_worker, "run_agent_turn", turn)
    code, payload = _run_worker_in_process(tmp_path)
    assert code == 1
    assert (payload["model_turns"], payload["tool_calls"]) == (1, 1)
