"""Current committed scope survives a real parent budget boundary.

Only model choices are scripted. The workflow uses actual file tools, managed
pytest, native declarations, audit persistence and promotion. Large public
fixture context spends the shared 500k/40 ledger through measured request
estimates; no provider usage, prefix charge or task completion is fabricated.
"""

import json
import sys
import threading
import time
from dataclasses import replace

import pytest
from test_workflow_v2_structured_routing import _request

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.agent_runtime import final_response_text
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.subagent_lifecycle import AsyncSubagentLifecycle
from minicode.task_outcome import AgentOutcomeCapture
from minicode.tooling import (
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    ToolTerminationReason,
)
from minicode.tools import task as task_module
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.todo_write import todo_write_tool
from minicode.types import AgentStep, ModelUsage


def _exercise(tmp_path, monkeypatch, *, remaining=None, replay=None, generic=False, candidate_handoff=False):
    target = tmp_path / "allowed.txt"
    target.write_text("before")
    (tmp_path / "test_acceptance.py").write_text(
        "from pathlib import Path\n"
        "def test_updated():\n"
        "    assert Path('allowed.txt').read_text() == 'after'\n"
    )
    request = _request(risk="low", complex=False)
    request["verificationChecks"][0]["argv"] = ["python", "-m", "pytest", "-q"]
    budget = AgentTurnBudget(max_total_tokens=500000, max_model_calls=40)
    deadline = time.monotonic() + 1800
    calls, billed, roles, checks, returned, acknowledgements, events = [], [], [], [], [], [], []

    class TestSandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv

    class Verifier(DeterministicVerifier):
        def run(self, **kwargs):
            assert target.read_text() == "before"
            result = super().run(**kwargs)
            checks.extend(check["exitCode"] for check in result["checks"])
            return result

    verifier = Verifier(sandbox_backend=TestSandbox(), explicit_trusted_executables={"python": sys.executable})
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context", lambda *_a, **_k: verifier)
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)

    def worker(data, context):
        role = context._logical_agent_role
        roles.append(role)
        ledger = context._agent_budget
        while hasattr(ledger, "_budget"):
            ledger = ledger._budget
        assert ledger is budget
        if role == "executor":
            cap = data["_executor_check_capability"]
            submit = data["_executor_submit_tool"]
            registry = cap.bind_registry(ToolRegistry([edit_file_tool, submit]))
            edited = registry.execute("edit_file", {"path": "allowed.txt", "old": "before", "new": "after"}, context)
            assert edited.ok, edited.output
            if candidate_handoff:
                # This worker-stop seam exercises an unassessed host candidate;
                # the surrounding parent admission still uses the real ledger.
                return ToolResult(ok=False, output="Fixture worker allowance ended without a declaration.",
                                  _termination_reason=ToolTerminationReason.BUDGET_EXCEEDED)
            count = submit.input_schema["properties"]["steps"]["items"]["properties"]["step"]["maximum"]
            result = registry.execute("executor_submit", {"status": "completed", "steps": [
                {"step": index, "status": "completed"} for index in range(1, count + 1)
            ], "deviations": [], "blockers": []}, context)
        else:
            assert role == "reviewer" and target.read_text() == "before"
            result = ToolRegistry([data["_review_submit_tool"]]).execute("review_submit", {
                "verdict": "approved", "criteria": [{"criterionId": "ac1", "status": "satisfied"}],
                "findings": [], "warnings": [], "requestedChecks": [], "confidence": "high",
            }, context)
        assert result.ok, result.output
        return result

    monkeypatch.setattr(task_module, "_run", worker)
    original_task = task_module.task_tool.run

    def workflow(data, context):
        if replay is not None:
            return replay
        result = original_task(data, context)
        returned.append(result)
        assert result.ok and result._must_deliver and result._continue_parent, result.output
        assert target.read_text() == "after"
        prior_ack = result._delivery_ack

        def acknowledge():
            if prior_ack is not None:
                prior_ack()
            acknowledgements.append(True)

        return replace(result, _delivery_ack=acknowledge)

    if generic:
        task = ToolDefinition(name="generic_commit", description="untyped external completion",
            input_schema={"type": "object"}, validator=lambda value: value,
            run=lambda _value, _context: ToolResult(ok=True,
                output="Workflow committed; verification and review passed.",
                _must_deliver=True, _continue_parent=True))
    else:
        task = replace(task_module.task_tool, run=workflow)
    tools = ToolRegistry([task, todo_write_tool, edit_file_tool])

    class Model:
        def next(self, messages, **_kwargs):
            number = len(calls) + 1
            calls.append([dict(message) for message in messages])
            if number <= 7:
                result = AgentStep(type="assistant", kind="progress", content="Preparing the bounded workflow.")
            elif number == 8:
                result = AgentStep(type="tool_calls", calls=[{"id": "workflow", "toolName": task.name,
                    "input": {} if generic else {"description": "Update the requested target", "agent_type": "workflow",
                        "prompt": "Change allowed.txt from before to after.", "workflow_request": request}}])
            elif number == 9:
                if not generic and replay is None:
                    assert acknowledgements == [True] and checks == [0, 0]
                actions = [{"id": "todos", "toolName": "todo_write", "input": {"todos": [
                    {"content": "Update target and pass checks", "status": "completed"},
                    {"content": "Collect workflow receipt", "status": "pending" if remaining == "todo" else "completed"},
                ]}}]
                if remaining in {"failed_edit", "changed"}:
                    actions.append({"id": "later-edit", "toolName": "edit_file", "input": {
                        "path": "allowed.txt", "old": "missing anchor" if remaining == "failed_edit" else "after",
                        "new": "unverified follow-up",
                    }})
                result = AgentStep(type="tool_calls", calls=actions)
            else:
                assert "final" in str(messages[-1].get("content", "")).lower(), "Unexpected working request after the boundary"
                result = AgentStep(type="assistant", content="The historic receipts need checking again; completion is not confirmed.")
            schemas = [{"type": "function", "function": {"name": tool.name, "description": tool.description,
                "parameters": tool.input_schema}} for tool in tools.list()]
            if _kwargs.get("disable_tools") is True:
                schemas = []
            input_tokens = sum(estimate_message_tokens(message) for message in messages) + estimate_tokens(json.dumps(schemas))
            output_tokens = estimate_tokens(result.content + json.dumps(result.calls))
            result.usage = ModelUsage(input_tokens=input_tokens, output_tokens=output_tokens, source="estimated")
            billed.append(input_tokens + output_tokens)
            return result

    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    release = threading.Event()
    if remaining == "async":
        def investigation(_identity, _cancel):
            assert release.wait(15)
            return ToolResult(ok=True, output="Outstanding independent investigation")
        lifecycle.spawn(agent_type="explore", runner=investigation)
    capture = AgentOutcomeCapture()
    data_dir = tmp_path.parent / (tmp_path.name + "-journal")
    try:
        with observe_run(workspace=tmp_path, source="headless", title="scoped parent handoff",
                         journal_factory=lambda workspace: RunJournal(workspace, data_dir=data_dir)) as observation:
            class ObservedEvents:
                def emit(self, kind, *, step=None, payload=None):
                    events.append((kind, step, dict(payload or {})))
                    observation.emit(kind, step=step, payload=payload)

                def __getattr__(self, name):
                    return getattr(observation, name)

            messages = run_agent_turn(model=Model(), tools=tools,
                messages=[{"role": "system", "content": "Public fixture context. " + "x" * 180000},
                          {"role": "user", "content": "Update the target and report its verification."}],
                cwd=str(tmp_path), runtime={"model": "local-fixture"},
                agent_budget=budget, deadline_monotonic=deadline, event_sink=ObservedEvents(),
                enable_work_chain=False, enable_bounded_completion=True, enable_implementation_phases=False,
                max_steps=40, subagent_lifecycle=lifecycle, outcome_capture=capture)
            run_id = observation.run_id
    finally:
        release.set()
        lifecycle.shutdown()
    snapshot = budget.snapshot()
    assert (snapshot.limit_total_tokens, snapshot.limit_model_calls) == (500000, 40)
    assert snapshot.used_total_tokens == sum(billed) and snapshot.used_model_calls == len(calls)
    assert snapshot.reserved_total_tokens == 0 and snapshot.used_total_tokens < 500000
    rejections = [payload for kind, _step, payload in events if kind == "execution.request_admission_rejected"]
    assert len(rejections) == 1 and rejections[0]["reason"] == "token budget exhausted"
    assert rejections[0]["usedModelCalls"] == 9 and rejections[0]["limitModelCalls"] == 40
    assert rejections[0]["usedTotalTokens"] == sum(billed[:9])
    assert (rejections[0]["estimatedInputTokens"] + rejections[0]["preserveTotalTokens"]
            > 500000 - rejections[0]["usedTotalTokens"])
    journal = RunJournal(tmp_path, data_dir=data_dir)
    if returned:
        trace = extract_scheduler_trace(returned[0].output)
        assert trace and trace["committed"] and roles == ["executor", "reviewer"] and checks == [0, 0]
        audit = journal.get_scheduler_audit(run_id, trace["workflowId"])
        assert audit is not None and audit.trace["committed"]
    return messages, calls, returned, capture, snapshot


def test_current_workflow_budget_stop_reports_committed_scope_without_summary_call(tmp_path, monkeypatch):
    messages, calls, receipts, capture, _ = _exercise(tmp_path, monkeypatch)
    assert len(calls) == 9, "Current committed authority must not spend another model request on a partial summary"
    final = messages[-1]
    assert final.get("_workflow_delivery_receipt_summary") is True
    assert final.get("_partial") is False
    assert final["_workflow_delivery"]["overallGoal"] == "not_assessed"
    text = final_response_text(messages).lower()
    assert "allowed.txt" in text and "committed" in text and "passed" in text
    assert not any(phrase in text for phrase in ("earlier workflow", "historical", "checking again", "remaining work is incomplete"))
    assert "overallGoal" not in text and "not_assessed" not in text
    assert any(message.get("role") == "tool_result" and message.get("content") == receipts[0].output for message in messages)
    assert capture.outcome.goal_achieved is False, "A scoped receipt alone is not whole-user-goal coverage authority"


@pytest.mark.parametrize("remaining", ["todo", "async", "failed_edit", "changed"])
def test_current_commit_cannot_hide_observed_remaining_work(tmp_path, monkeypatch, remaining):
    messages, calls, _, capture, _ = _exercise(tmp_path, monkeypatch, remaining=remaining)
    assert len(calls) == 9
    assert messages[-1].get("_workflow_delivery_receipt_summary") is True
    assert messages[-1].get("_partial") is True
    assert "committed" in final_response_text(messages).lower()
    assert capture.outcome.goal_achieved is False


def test_generic_must_deliver_has_no_workflow_scope_authority(tmp_path, monkeypatch):
    messages, _, _, capture, _ = _exercise(tmp_path, monkeypatch, generic=True)
    assert not messages[-1].get("_workflow_delivery_receipt_summary")
    assert capture.outcome.goal_achieved is False


def test_an_old_real_receipt_cannot_be_replayed_as_current_delivery(tmp_path, monkeypatch):
    first = tmp_path / "first"
    first.mkdir()
    _, _, receipts, _, _ = _exercise(first, monkeypatch)
    second = tmp_path / "second"
    second.mkdir()
    messages, _, _, capture, _ = _exercise(second, monkeypatch, replay=receipts[0])
    assert not messages[-1].get("_workflow_delivery_receipt_summary")
    assert (second / "allowed.txt").read_text() == "before"
    assert capture.outcome.goal_achieved is False
