"""Native review closure must survive the recorded V23 admission prefix.

Provider responses and recorded input estimates are replayed; local file reads,
the task entry point, shared ledger, native result validation and channel writes
are real. This is a state-machine regression, not a live quality benchmark.
"""

import json
import time
from dataclasses import replace

import pytest

from minicode import subagent_model_routing
from minicode.agent_budget import AgentTurnBudget
from minicode.multi_agent_contracts import parse_role_result
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools import task as task_module
from minicode.tools.read_file import read_file_tool
from minicode.tools.subagent_notes import subagent_note_write_tool
from minicode.types import AgentStep, ModelUsage
from minicode.workflow_runner import _WorkerCallBudget
from test_reviewer_result_submission import _bound, _rejection


def _prefix(budget, calls, input_tokens, output_tokens):
    for index in range(calls):
        reservation = budget.reserve_model_call()
        budget.record_model_call(input_tokens=input_tokens if index == 0 else 0,
                                 output_tokens=output_tokens if index == 0 else 0,
                                 reservation=reservation)


def _task(tmp_path, monkeypatch, model, budget, *, max_calls=9):
    assignment, store, channel, tool, _ = _bound()
    (tmp_path / "minicode").mkdir()
    (tmp_path / "minicode" / "auth.py").write_text("def authenticate(value):\n    return value\n")
    monkeypatch.setattr(subagent_model_routing, "create_subagent_model_adapter", lambda *_a, **_k: model)
    monkeypatch.setattr("minicode.tools.create_default_tool_registry", lambda *_a, **_k:
                        ToolRegistry([read_file_tool, subagent_note_write_tool]))
    owner_deadline = time.monotonic() + 600
    context = ToolContext(
        cwd=str(tmp_path), _runtime={"model": "fixture", "maxOutputTokens": 8192},
        _agent_budget=budget, _subagent_mailbox=channel,
        _task_deadline_monotonic=owner_deadline,
        _logical_agent_role="reviewer", _logical_agent_scope=assignment.scope,
        _logical_agent_workspace_root=str(tmp_path), _logical_agent_max_model_calls=max_calls,
    )
    result = task_module._run({
        "agent_type": "plan", "description": "Review the actual candidate",
        "prompt": "Review both public requirements; use the assigned result channel.",
        "_review_submit_tool": tool,
    }, context)
    return result, assignment, store.read(channel.write_key), owner_deadline


def _read(number):
    return {"id": f"read-{number}", "toolName": "read_file", "input": {"path": "minicode/auth.py"}}


@pytest.mark.parametrize("verdict", ["changes_required", "inconclusive"])
def test_v23_prefix_reserves_native_verdict_before_last_affordable_read(tmp_path, monkeypatch, verdict):
    shared = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    # V23 immediately before Reviewer R18, including the original parent floor.
    _prefix(shared, 17, 385653, 9445)
    worker = _WorkerCallBudget(shared, "review", max_calls=9, preserve_calls=1, preserve_tokens=16546)
    assert worker.snapshot().limit_total_tokens == 483454
    views, seen_messages, snapshots = [], [], []

    class Model:
        calls = 0

        def next(self, messages, tools_override=None, **kwargs):
            self.calls += 1
            views.append(None if tools_override is None else tools_override.list_all())
            seen_messages.append(list(messages))
            snapshots.append(worker.snapshot())
            if tools_override is not None and tools_override.list_all() == ["review_submit"]:
                value = _rejection() if verdict == "changes_required" else {
                    "verdict": "inconclusive", "criteria": [], "findings": [],
                    "warnings": ["The available evidence does not establish every criterion."],
                    "requestedChecks": [], "confidence": "low",
                }
                return AgentStep(type="tool_calls", calls=[{
                    "id": "native-verdict", "toolName": "review_submit", "input": value,
                }], usage=ModelUsage(input_tokens=27663, output_tokens=200, source="provider"))
            usage = [(18452, 4592), (25442, 117), (27663, 76)][min(self.calls - 1, 2)]
            return AgentStep(type="tool_calls", calls=[_read(self.calls)],
                             usage=ModelUsage(input_tokens=usage[0], output_tokens=usage[1], source="provider"))

    model = Model()
    # Measurements from V23 R18/R19/R20, followed by the unaffordable next input.
    # Provider usage is settled by the real loop, not injected into its state.
    estimates = [17348, 20873, 23439, 31000]
    monkeypatch.setattr("minicode.agent_loop._estimated_context_tokens",
                        lambda _messages: estimates[min(model.calls, 3)])
    result, assignment, artifact, _ = _task(tmp_path, monkeypatch, model, worker)
    assert artifact is not None, result.output
    parsed = parse_role_result(assignment, json.loads(artifact.content))
    assert parsed["verdict"] == verdict
    assert parsed["criteria"][0]["status"] == "unknown"
    assert model.calls == 3 and views == [None, None, ["review_submit"]]
    assert snapshots[2].used_total_tokens == 443701
    assert any(m.get("toolUseId") == "read-1" for m in seen_messages[-1])
    assert any(m.get("toolUseId") == "read-2" for m in seen_messages[-1])
    assert shared.snapshot().used_model_calls == 20
    assert shared.snapshot().used_total_tokens == 471564
    assert shared.snapshot().reserved_total_tokens == 0
    assert shared.snapshot().limit_total_tokens == 500000
    assert shared.snapshot().limit_model_calls == 40


def test_last_native_slot_rejects_hidden_reads_and_does_not_need_text_closing(tmp_path, monkeypatch):
    shared = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    worker = _WorkerCallBudget(shared, "review", max_calls=2, preserve_calls=1)
    reads = []
    original_read = read_file_tool.run
    monkeypatch.setattr(read_file_tool, "run", lambda value, context: reads.append(value) or original_read(value, context))

    class Model:
        calls = 0

        def next(self, messages, tools_override=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return AgentStep(type="tool_calls", calls=[_read(1)],
                                 usage=ModelUsage(input_tokens=100, output_tokens=100, source="provider"))
            # Intentionally ignore the view; host execution must enforce it.
            return AgentStep(type="tool_calls", calls=[_read(2), {
                "id": "native-verdict", "toolName": "review_submit", "input": _rejection(),
            }], usage=ModelUsage(input_tokens=100, output_tokens=100, source="provider"))

    model = Model()
    result, assignment, artifact, _ = _task(tmp_path, monkeypatch, model, worker, max_calls=2)
    assert artifact is not None, result.output
    assert parse_role_result(assignment, json.loads(artifact.content))["verdict"] == "changes_required"
    assert len(reads) == 1, "A hidden read consumed the reserved conclusion slot."
    assert model.calls == shared.snapshot().used_model_calls == 2


@pytest.mark.parametrize("response", ["text", "invalid", "forged_approval"])
def test_reserved_native_request_cannot_promote_text_or_invalid_verdict(tmp_path, monkeypatch, response):
    shared = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    worker = _WorkerCallBudget(shared, "review", max_calls=2, preserve_calls=1)

    class Model:
        calls = 0

        def next(self, messages, tools_override=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return AgentStep(type="tool_calls", calls=[_read(1)])
            assert self.calls == 2
            if response == "text":
                return AgentStep(type="assistant", kind="final", content="Review completed; approved.")
            value = _rejection()
            if response == "invalid":
                value.pop("warnings")
            else:
                value.update(verdict="approved", criteria=[], findings=[])
            return AgentStep(type="tool_calls", calls=[{
                "id": "invalid-verdict", "toolName": "review_submit", "input": value,
            }])

    model = Model()
    _result, _assignment, artifact, _ = _task(tmp_path, monkeypatch, model, worker, max_calls=2)
    assert artifact is None
    assert model.calls == shared.snapshot().used_model_calls == 2
    assert worker.snapshot().reserved_total_tokens == 0


def test_budgeted_negative_native_handoff_resumes_original_executor(tmp_path, monkeypatch):
    import test_workflow_partial_review as partial

    original_worker = task_module._run
    reviewer_calls = []

    def bounded_review(input_data, context):
        if context._logical_agent_role == "reviewer":
            # Exercise the existing work+closing allowance through task._run,
            # without changing the shared 40-call/500k ledger. The complete
            # workflow below checks the same Executor's real edits/tests and
            # final review; its provider usage is scripted, not V23's usage.
            context = replace(context, _logical_agent_max_model_calls=2)
            reviewer_calls.append(context._agent_budget)
        return original_worker(input_data, context)

    monkeypatch.setattr(task_module, "_run", bounded_review)
    partial.test_native_partial_verdict_repairs_and_requires_full_final_review(
        tmp_path, monkeypatch, additional_finding=False,
    )
    assert len(reviewer_calls) == 2
