"""Replay the real V15 failure boundary; child spend cannot erase parent hand-back."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import estimate_message_tokens
from minicode.subagent_mailbox import SubagentMailbox
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.types import AgentStep, ModelAdapter, ModelUsage
from minicode.workflow_runner import run_workflow
from test_workflow_v2_structured_routing import _request, _write_executor_declaration


@pytest.mark.parametrize("structured_runner", [True, False])
def test_v15_recorded_spend_keeps_the_authorized_parent_handoff(tmp_path, structured_runner):
    recorded = json.loads((Path(__file__).parent / "fixtures/parent_handoff_v15.json").read_text())
    task_calls = [(i, m) for i, m in enumerate(recorded)
                  if m.get("role") == "assistant_tool_call" and m.get("toolName") == "task"]
    index, dispatch = task_calls[-1]
    receipt = recorded[index + 1]["content"]
    # Frozen run: before call22 used445866, parent response spent12222;
    # child calls23/24 spent14281/15733, leaving11898 against12734 closing input.
    assert sum(estimate_message_tokens(m) for m in recorded[:-1]) == 12734
    budget = AgentTurnBudget(max_total_tokens=500000, max_model_calls=40)
    budget.record_model_call(input_tokens=445866)
    (tmp_path / "allowed.txt").write_text("before")
    child_admissions = []
    floors = []

    def spend(context):
        floors.append(context._logical_agent_preserve_total_tokens)
        for input_tokens, output_tokens in ((14029, 252), (14897, 836)):
            try:
                reservation = context._agent_budget.reserve_model_call(input_tokens + output_tokens)
            except AgentBudgetExceeded:
                break
            context._agent_budget.record_model_call(input_tokens=input_tokens,
                output_tokens=output_tokens, reservation=reservation)
            child_admissions.append(input_tokens + output_tokens)

    def worker(inputs, context):
        spend(context)
        _write_executor_declaration(inputs, context, status="blocked")
        return ToolResult(ok=True, output="recorded blocked declaration delivered")

    def task(_inputs, context):
        if structured_runner:
            result = run_workflow({"description": "V15 budget replay", "prompt": "Preserve the candidate.",
                                   "agent_type": "workflow", "workflow_request": _request(risk="low", complex=False)},
                                  context, depth=0, subagent_id="sub_dddddddddddddddddddddddddddddddd", run_subagent=worker)
            assert not result.ok
            assert "executor_blocked" in result.output, result.output
        else:
            # A generic tool carrying workflow-looking input has no parsed
            # runner/host capability. Its original parent floor must survive.
            from minicode.workflow_runner import _WorkerCallBudget
            spend(replace(context, _agent_budget=_WorkerCallBudget(
                context._agent_budget, "generic-child", max_calls=2, preserve_calls=1,
                preserve_tokens=context._logical_agent_preserve_total_tokens,
            )))
        assert (tmp_path / "allowed.txt").read_text() == "before"
        # Intentionally drop the private current result. Old receipt text is
        # never authority to claim a current commit or retained candidate.
        return ToolResult(ok=False, output=receipt)

    class Parent(ModelAdapter):
        calls = 0
        def next(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return AgentStep(type="tool_calls", content=dispatch.get("content", ""),
                    reasoningContent=dispatch.get("reasoningContent"),
                    calls=[{"id": dispatch["toolUseId"], "toolName": "task", "input": dispatch["input"]}],
                    usage=ModelUsage(input_tokens=10691, output_tokens=1531, source="provider"))
            assert self.calls == 2
            assert any(m.get("role") == "tool_result" and m.get("toolUseId") == dispatch["toolUseId"] and receipt in m.get("content", "") for m in messages)
            return AgentStep(type="assistant", kind="final", content="Incomplete: checks failed; no writeback.",
                usage=ModelUsage(input_tokens=12734, output_tokens=100, source="provider"))

    model = Parent()
    messages = run_agent_turn(model=model, tools=ToolRegistry([ToolDefinition(name="task", description="workflow",
            input_schema={"type": "object"}, validator=lambda x: x, run=task)]),
        messages=recorded[:index], cwd=str(tmp_path), runtime={"model": "fake"},
        agent_budget=budget, subagent_mailbox=SubagentMailbox(), max_steps=5,
        enable_work_chain=False, enable_implementation_phases=False, enable_bounded_completion=True)
    if structured_runner:
        assert model.calls == 1, "the claimed host handoff needs no additional parent model summary"
        assert messages[-1]["_workflow_delivery_receipt_summary"] is True
        delivery = messages[-1]["_workflow_delivery"]
        assert delivery["delivery"] == "partial"
        assert delivery["overallGoal"] == "not_assessed"
        assert len(delivery["workflows"]) == 1
        workflow = delivery["workflows"][0]
        assert workflow["commit"] == workflow["review"] == "unknown"
        assert workflow["candidateRef"] is None
        assert workflow["files"] == workflow["verification"] == []
        assert "commit state is unconfirmed" in messages[-1]["content"]
        assert "Candidate retained" not in messages[-1]["content"]
        assert child_admissions == [14281, 15733]
        assert floors == [(500000 - 445866 - 12222) * 15 // 100]
        assert budget.snapshot().used_total_tokens == 488102
    else:
        assert model.calls == 2, "generic tool calls still reserve the parent model hand-back"
        assert messages[-1]["content"] == (
            "Partial result: turn_budget_exceeded: token budget exhausted. "
            "Overall task completion is not confirmed.\n\n"
            "Incomplete: checks failed; no writeback."
        )
        assert not messages[-1].get("_workflow_delivery_receipt_summary")
        assert child_admissions == [14281], "retire optional child work within the same total allowance"
        assert floors[0] > 12734
        assert budget.snapshot().used_total_tokens == 485203
    assert messages[-1]["_partial"] is True
    # The historical prefix records usage only; this fixture reserves three
    # actual requests, allocated differently between parent and child.
    assert budget.snapshot().used_model_calls == model.calls + len(child_admissions) == 3
    assert budget.snapshot().reserved_total_tokens == 0
    assert budget.snapshot().used_total_tokens <= 500000


def test_parallel_sibling_floor_is_preserved_once_and_reservations_release():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from minicode.workflow_runner import _WorkerCallBudget

    budget = AgentTurnBudget(max_total_tokens=100000, max_model_calls=10)
    workers = [_WorkerCallBudget(budget, f"child-{i}", max_calls=3,
               preserve_calls=1, preserve_tokens=20000) for i in range(2)]
    barrier = Barrier(2)

    def reserve(worker):
        barrier.wait(timeout=5)
        return worker.reserve_model_call(35000)

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(reserve, workers))
    assert budget.snapshot().reserved_total_tokens == 70000
    for worker, reservation in zip(workers, reservations):
        worker.record_model_call(input_tokens=35000, reservation=reservation)
    parent = budget.reserve_model_call(20000)
    budget.fail_model_call(parent)
    assert budget.snapshot().reserved_total_tokens == 0
    # A cancelled child has no persistent preservation lease to release.
    child = workers[0].reserve_model_call(1000)
    workers[0].fail_model_call(child)
    assert budget.snapshot().reserved_total_tokens == 0


def test_nested_parent_floor_adds_once_per_ancestor_and_does_not_raise_total_limit():
    import pytest
    from minicode.workflow_runner import _WorkerCallBudget

    budget = AgentTurnBudget(max_total_tokens=100000, max_model_calls=10)
    parent = _WorkerCallBudget(budget, "parent", max_calls=7,
                              preserve_calls=1, preserve_tokens=20000)
    child = _WorkerCallBudget(parent, "child", max_calls=5,
                             preserve_calls=1, preserve_tokens=10000)
    with pytest.raises(AgentBudgetExceeded):
        child.reserve_model_call(70001)
    reservation = child.reserve_model_call(70000)
    child.record_model_call(input_tokens=70000, reservation=reservation)
    reservation = parent.reserve_model_call(10000)
    parent.record_model_call(input_tokens=10000, reservation=reservation)
    reservation = budget.reserve_model_call(20000)
    budget.record_model_call(input_tokens=20000, reservation=reservation)
    assert budget.snapshot().used_total_tokens == 100000
    assert budget.snapshot().reserved_total_tokens == 0


def test_real_workflow_repair_keeps_the_original_parent_handoff_floor(tmp_path, monkeypatch):
    import subprocess
    import sys
    import minicode.workflow_runner as runner_module
    from minicode.deterministic_verifier import DeterministicVerifier
    from minicode.permissions import PermissionManager
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a: None)
    from minicode.tooling import ToolContext

    (tmp_path / "allowed.txt").write_text("before")
    (tmp_path / "test_acceptance.py").write_text(
        "from pathlib import Path\ndef test_value():\n    assert Path('allowed.txt').read_text() == 'after'\n")
    budget = AgentTurnBudget(max_total_tokens=100000, max_model_calls=40)
    budget.record_model_call(input_tokens=50000)
    floors = []
    candidate_values = []

    def command_runner(_argv, **kwargs):
        return subprocess.run([sys.executable, "-m", "pytest", "-q"], **kwargs)

    monkeypatch.setattr(runner_module, "_deterministic_verifier_for_context",
        lambda *_a, **_k: DeterministicVerifier(command_runner=command_runner))

    def worker(inputs, context):
        floors.append(context._logical_agent_preserve_total_tokens)
        target = Path(context.cwd, "allowed.txt")
        candidate_values.append(target.read_text())
        reservation = context._agent_budget.reserve_model_call(3000)
        context._agent_budget.record_model_call(input_tokens=3000, reservation=reservation)
        if len(floors) == 1:
            target.write_text("almost")
            _write_executor_declaration(inputs, context)
        else:
            _write_executor_declaration(inputs, context, status="blocked")
        return ToolResult(ok=True, output="declaration delivered")

    result = run_workflow({"description": "repair floor", "prompt": "Repair the value.",
                           "agent_type": "workflow", "workflow_request": _request(risk="low", complex=False)},
        ToolContext(cwd=str(tmp_path), _runtime={"model": "fake"}, _agent_budget=budget,
            _logical_agent_preserve_total_tokens=20000, _subagent_mailbox=SubagentMailbox()),
        depth=0, subagent_id="sub_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", run_subagent=worker)
    assert not result.ok
    assert "implementation_repair_requested" in result.output, result.output
    assert candidate_values == ["before", "almost"], "actual verifier rollback restores the repair candidate"
    assert floors == [20000, 20000], "same parent's floor cannot shrink or be counted twice during repair"
    assert (tmp_path / "allowed.txt").read_text() == "before"
    assert budget.snapshot().reserved_total_tokens == 0


def test_mixed_batch_prices_other_outputs_and_actual_finished_serial_results():
    from minicode.agent_loop import _parent_handoff_token_floor
    from minicode.tooling import _LARGE_OUTPUT_THRESHOLD
    from minicode.workflow_finalization import _WORKFLOW_OUTPUT_MAX_CHARS
    from minicode.workflow_runner import _EXECUTOR_OBSERVATION_MAX_CHARS

    workflow = {"id": "wf", "toolName": "task", "input": {"agent_type": "workflow"}}
    other = {"id": "read", "toolName": "read_file", "input": {"path": "source.py"}}
    single = _parent_handoff_token_floor([], [workflow])
    mixed = _parent_handoff_token_floor([], [other, workflow])
    actual = _parent_handoff_token_floor([], [other, workflow],
        completed_results=[(other, ToolResult(ok=True, output="known small result"))])
    assert mixed - single >= _LARGE_OUTPUT_THRESHOLD * 2 // 3
    assert single < actual < mixed
    # The declared allowance is derived from the actual formatter caps,
    # including the newer diagnostics and workspace prefix.
    assert single >= (_WORKFLOW_OUTPUT_MAX_CHARS + _EXECUTOR_OBSERVATION_MAX_CHARS + 512) * 2 // 3 + 2048
