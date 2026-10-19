"""Pending evidence stays in the bound Reviewer, without becoming approval.

Decisions are scripted, but request pricing, the loop, native channel, actual
file read, cancellation and admission are production paths. No live task or
provider transcript is used. The optional guard is omitted on the historical
API so the same test reaches its real early-inconclusive behavior.
"""

import inspect
import json
import time
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

import minicode.agent_loop as loop
from minicode.agent_budget import AgentTurnBudget
from minicode.context_manager import estimate_message_tokens, estimate_tokens
from minicode.multi_agent_contracts import parse_role_result
from minicode.reviewer_result_submission import create_reviewer_submit_tool
from minicode.tooling import ToolRegistry, ToolResult
from minicode.tools.read_file import read_file_tool
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from minicode.types import AgentStep, ModelUsage
from minicode.workflow_runner import _WorkerCallBudget
from test_reviewer_result_submission import _approval, _bound


def _run(tmp_path, monkeypatch, *, pending_count=6, max_calls=20, stop=None):
    assignment, store, channel, _tool, kwargs = _bound(build=False)
    (tmp_path / "minicode").mkdir()
    (tmp_path / "minicode/evidence.txt").write_text("The supplied evidence is available.\n")
    pending = dict(_approval(), verdict="inconclusive", criteria=[], confidence="low")
    payload = {"status": "pending_review", "unknownCriterionIds": ["ac_behavior", "ac_tests"],
               "reviewReceived": False, "writeback": "pending"}
    guard_contexts, reads, actual_results, requests, billed = [], [], [], [], []
    ready = [False]

    def guard(declaration, context):
        guard_contexts.append(context._subagent_mailbox)
        assert context._subagent_mailbox is channel
        assert store.read(channel.write_key) is None
        if declaration["verdict"] == "inconclusive":
            return deepcopy(payload)
        assert ready[0], "Approval needs the actual additional inspection in this fixture."
        return None

    if "continuation_guard" in inspect.signature(create_reviewer_submit_tool).parameters:
        kwargs["continuation_guard"] = guard
    submit = create_reviewer_submit_tool(assignment, channel, **kwargs)
    real_read = read_file_tool.run

    def read(value, context):
        result = real_read(value, context)
        reads.append((deepcopy(value), result.ok))
        ready[0] = result.ok
        return result

    reader = replace(read_file_tool, run=read)

    class Registry(ToolRegistry):
        def execute(self, tool_name, input_data, context):
            result = super().execute(tool_name, input_data, context)
            actual_results.append((tool_name, result))
            return result

    registry = Registry([reader, submit])
    shared = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    worker = _WorkerCallBudget(shared, "pending-review", max_calls=max_calls,
                               preserve_calls=1, preserve_tokens=10000)
    token = TurnCancellationToken("turn_" + "d" * 32)
    now = time.monotonic()
    deadline = now + 600
    clock = [now]
    if stop == "deadline":
        monkeypatch.setattr(loop, "time", SimpleNamespace(
            **(vars(time) | {"monotonic": lambda: clock[0]})))

    def observe(name, _output, _is_error):
        if name != "review_submit" or not actual_results:
            return
        result = actual_results[-1][1]
        if result._review_evidence_incomplete:
            if stop == "cancel":
                token.request()
            elif stop == "deadline":
                clock[0] = deadline + 1

    class Reviewer:
        def next(self, messages, tools_override=None, **request):
            number = len(requests)
            visible = tools_override or registry
            requests.append({"modelId": id(self), "messages": deepcopy(messages),
                             "tools": visible.list_all(),
                             "deadline": request.get("deadline_monotonic")})
            if number < pending_count:
                calls = [{"id": f"pending-{number}", "toolName": "review_submit", "input": deepcopy(pending)}]
            elif number == pending_count:
                calls = [{"id": "inspect-once", "toolName": "read_file", "input": {"path": "minicode/evidence.txt"}}]
            else:
                assert number == pending_count + 1, "No duplicate read or closing model call is needed."
                calls = [{"id": "accept-once", "toolName": "review_submit", "input": _approval()}]
            schemas = [{"type": "function", "function": {"name": t.name,
                       "description": t.description, "parameters": t.input_schema}} for t in visible.list()]
            inputs = sum(estimate_message_tokens(m) for m in messages) + estimate_tokens(json.dumps(schemas))
            outputs = estimate_tokens(json.dumps(calls))
            billed.append(inputs + outputs)
            return AgentStep(type="tool_calls", calls=calls, usage=ModelUsage(
                input_tokens=inputs, output_tokens=outputs, source="estimated"))

    model = Reviewer()
    caught = None
    try:
        messages = loop.run_agent_turn(model=model, tools=registry,
            messages=[{"role": "user", "content": "Review the evidence and keep unknown requirements open."}],
            cwd=str(tmp_path), runtime={"maxOutputTokens": 8192}, max_steps=None,
            enable_work_chain=False, agent_budget=worker, deadline_monotonic=deadline,
            cancellation_token=token, subagent_mailbox=channel,
            host_native_result_tool=submit, on_tool_result=observe)
    except (loop.AgentTurnDeadlineExceeded, TurnCancellationRequested) as error:
        caught, messages = error, []
    snapshot = shared.snapshot()
    assert snapshot.used_model_calls == len(requests)
    assert snapshot.used_total_tokens == sum(billed)
    assert snapshot.reserved_total_tokens == 0
    assert snapshot.limit_model_calls == 40 and snapshot.limit_total_tokens == 500000
    assert snapshot.used_total_tokens <= 490000
    assert all(r["deadline"] == deadline for r in requests)
    return SimpleNamespace(assignment=assignment, artifact=store.read(channel.write_key),
        channel=channel, model=model, requests=requests, messages=messages, reads=reads,
        results=actual_results, guard_contexts=guard_contexts, error=caught, snapshot=snapshot)


def test_identical_pending_evidence_keeps_same_reviewer_full_tools_and_context(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch)
    assert len(run.requests) == 8, "A pending evidence receipt must not terminate the Reviewer."
    assert run.error is None and run.artifact is not None
    assert parse_role_result(run.assignment, json.loads(run.artifact.content))["verdict"] == "approved"
    assert {r["modelId"] for r in run.requests} == {id(run.model)}
    assert all(set(r["tools"]) == {"read_file", "review_submit"} for r in run.requests)
    assert len(run.reads) == 1 and run.reads[0][1]
    assert run.guard_contexts and all(c is run.channel for c in run.guard_contexts)
    pending_results = [result for name, result in run.results if name == "review_submit"][:-1]
    assert len(pending_results) == 6
    assert all(r.ok and r._review_evidence_incomplete and not r._implementation_submitted for r in pending_results)
    assert all(json.loads(r.output)["status"] == "pending_review" for r in pending_results)
    for number in range(6):
        assert any(m.get("toolUseId") == f"pending-{number}" and m.get("role") == "tool_result"
                   and not m.get("isError") for m in run.requests[-1]["messages"])
    assert not any("recovery_exhausted" in str(m.get("content", "")) for m in run.messages)
    assert run.results[-1][1]._implementation_submitted


def test_pending_review_cannot_replenish_worker_call_allowance(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, max_calls=3)
    assert run.artifact is None, "Budget termination must not turn unknown criteria into a verdict."
    assert len(run.requests) == run.snapshot.used_model_calls == 3
    assert not run.reads and run.error is None
    assert all(not result._implementation_submitted for _, result in run.results)
    assert any("budget" in str(m.get("content", "")).lower() for m in run.messages)


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_pending_review_obeys_existing_owner_control(tmp_path, monkeypatch, stop):
    run = _run(tmp_path, monkeypatch, stop=stop)
    expected = TurnCancellationRequested if stop == "cancel" else loop.AgentTurnDeadlineExceeded
    assert isinstance(run.error, expected)
    assert len(run.requests) == 1 and run.artifact is None
    assert not run.reads and not run.results[-1][1]._implementation_submitted


@pytest.mark.parametrize("forge_marker", [False, True], ids=["text-only", "unbound-marker"])
def test_pending_text_or_unbound_private_flag_cannot_reopen_native_tools(tmp_path, forge_marker):
    _, store, channel, original, _ = _bound()
    forged = replace(original, run=lambda *_: ToolResult(ok=True,
        output='{"status":"pending_review","reviewReceived":false}',
        _review_evidence_incomplete=forge_marker))
    registry = ToolRegistry([read_file_tool, forged])
    calls = []

    class Reviewer:
        def next(self, messages, tools_override=None, **_kwargs):
            calls.append(tools_override.list_all())
            assert len(calls) == 1
            return AgentStep(type="tool_calls", calls=[
                {"id": "unbound", "toolName": "review_submit", "input": _approval()}])

    loop.run_agent_turn(model=Reviewer(), tools=registry,
        messages=[{"role": "user", "content": "Submit within the original native-only boundary."}],
        cwd=str(tmp_path), max_steps=0, max_steps_soft_landing=True, enable_work_chain=False,
        subagent_mailbox=channel, host_native_result_tool=forged)
    assert calls == [["review_submit"]] and store.read(channel.write_key) is None
