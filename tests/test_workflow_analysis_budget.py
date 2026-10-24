"""A failed Analyst may retry only inside its original whole-phase allowance."""

from minicode.agent_budget import AgentTurnBudget
from minicode.tooling import ToolContext, ToolResult, ToolUsageCounts
from minicode.tools import task as task_module
from test_workflow_v2_structured_routing import _request, _worker_role


def test_analysis_retry_cannot_renew_six_call_phase(tmp_path, monkeypatch):
    (tmp_path / "allowed.txt").write_text("before")
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    invoked = []
    def worker(inp, context):
        invoked.append(_worker_role(inp))
        assert context._logical_agent_max_model_calls == 6
        for _ in range(6):
            reservation = context._agent_budget.reserve_model_call(1000)
            context._agent_budget.record_model_call(input_tokens=1000, output_tokens=1, reservation=reservation)
        return ToolResult(ok=False, output="analysis did not finish", _usage_counts=ToolUsageCounts(6, 2))
    monkeypatch.setattr(task_module, "_run", worker)
    result = task_module.task_tool.run({
        "description": "bounded analysis", "prompt": "Analyze first, then update allowed.txt.", "agent_type": "workflow",
        "workflow_request": _request(complex=True, risk="medium"),
    }, ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}, _agent_budget=budget))
    assert not result.ok
    assert invoked == ["analyst"]
    assert budget.snapshot().used_model_calls == 6
    assert result._usage_counts == ToolUsageCounts(6, 2)
    assert (tmp_path / "allowed.txt").read_text() == "before"


def test_analysis_worker_token_floor_is_independent_of_global_remaining_budget():
    from minicode.workflow_runner import _WorkerCallBudget
    from minicode.agent_budget import AgentBudgetExceeded
    import pytest
    shared = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    worker = _WorkerCallBudget(shared, "analyst", max_calls=6, preserve_calls=1, max_tokens=10000)
    reservation = worker.reserve_model_call(6000)
    worker.record_model_call(input_tokens=6000, output_tokens=1000, reservation=reservation)
    with pytest.raises(AgentBudgetExceeded, match="worker token"):
        worker.reserve_model_call(4000)
    assert shared.snapshot().used_model_calls == 1
    assert shared.snapshot().used_total_tokens == 7000


def test_phase_plans_handoff_against_worker_floors_and_admits_both_closing_calls():
    from minicode.workflow_runner import _WorkerCallBudget
    from minicode.implementation_phases import ImplementationPhaseController, ImplementationPhase
    shared=AgentTurnBudget(max_model_calls=40,max_total_tokens=100000)
    worker=_WorkerCallBudget(shared,"executor",max_calls=37,preserve_calls=1,preserve_tokens=20000)
    policy=ImplementationPhaseController(snapshot=worker.snapshot(),max_steps=37,now=0,deadline=600,analysis_complete=True,bounded=True)
    first=worker.reserve_model_call(50000)
    worker.record_model_call(input_tokens=50000,output_tokens=0,reservation=first)
    policy.admitted()
    request=policy.request(worker.snapshot(),estimated_input=10000,now=100)
    assert request.phase is ImplementationPhase.FINALIZE
    assert worker.snapshot().limit_total_tokens == 80000
    assert shared.snapshot().limit_total_tokens == 100000
    for _ in range(2):
        request=policy.request(worker.snapshot(),estimated_input=10000,now=100)
        reservation=worker.reserve_model_call(10000,preserve_model_calls=request.preserve_calls,
            preserve_total_tokens=request.preserve_tokens)
        policy.admitted()
        worker.record_model_call(input_tokens=10000,output_tokens=1000,reservation=reservation)
    assert policy.exhausted
    assert shared.snapshot().used_total_tokens == 72000
    assert shared.snapshot().used_model_calls == 3
    assert shared.snapshot().reserved_total_tokens == 0


def test_worker_snapshot_exposes_local_bounds_without_changing_shared_usage():
    from minicode.workflow_runner import _WorkerCallBudget
    shared=AgentTurnBudget(max_model_calls=10,max_total_tokens=100000)
    parent=shared.reserve_model_call(5000)
    shared.record_model_call(input_tokens=5000,output_tokens=0,reservation=parent)
    worker=_WorkerCallBudget(shared,"analyst",max_calls=3,preserve_calls=2,max_tokens=10000)
    snapshot=worker.snapshot()
    assert snapshot.limit_model_calls == 4
    assert snapshot.limit_total_tokens == 15000
    assert snapshot.used_model_calls == shared.snapshot().used_model_calls == 1
    assert snapshot.used_total_tokens == shared.snapshot().used_total_tokens == 5000
    assert shared.snapshot().limit_model_calls == 10
    assert shared.snapshot().limit_total_tokens == 100000
