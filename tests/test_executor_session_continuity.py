"""A host-owned Executor resumes its real conversation across repair orders."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools import task as task_module
from minicode.types import AgentStep, ModelUsage


def _session(owner, root):
    # The real V17 baseline predates the host capability. Pass an opaque host
    # token there so the same test reaches _run and fails on lost messages,
    # rather than an import error or a synthetic production mutation.
    try:
        from minicode.executor_session import ExecutorSession
    except ModuleNotFoundError as error:
        if error.name != "minicode.executor_session":
            raise
        return SimpleNamespace(owner=owner, workspace_root=str(root))
    return ExecutorSession(owner=owner, workspace_root=str(root))


def _context(root, **overrides):
    values = dict(
        cwd=str(root), _runtime={"model": "fake"},
        _logical_agent_role="executor", _workflow_phase=True,
        _logical_agent_max_model_calls=8,
        _logical_agent_scope={"read_roots": (".",),
                              "write_paths": ("app.py",),
                              "forbidden_paths": (".git",)},
        _logical_agent_workspace_root=str(root),
        _agent_budget=AgentTurnBudget(max_model_calls=40, max_total_tokens=500000),
    )
    values.update(overrides)
    return ToolContext(**values)


def _input(session, owner, prompt):
    return {"agent_type": "general", "description": "implement", "prompt": prompt,
            "_executor_session": session, "_executor_session_owner": owner}


@pytest.fixture(autouse=True)
def local_registry(monkeypatch):
    from minicode.tools.edit_file import edit_file_tool
    from minicode.tools.read_file import read_file_tool

    monkeypatch.setattr("minicode.tools.create_default_tool_registry",
                        lambda *_a, **_k: ToolRegistry([read_file_tool, edit_file_tool]))


def _tool(name, values, call_id):
    return AgentStep(type="tool_calls", calls=[
        {"id": call_id, "toolName": name, "input": values}],
        usage=ModelUsage(input_tokens=100, output_tokens=10, source="provider"))


def test_real_task_repair_keeps_tool_evidence_and_only_charges_new_calls(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("value = 'before'\n# unique source finding: receipt_491\n")
    owner = object()
    session = _session(owner, tmp_path)
    context = _context(tmp_path)
    seen, models = [], []
    steps = iter([
        _tool("read_file", {"path": "app.py"}, "read-1"),
        _tool("edit_file", {"path": "app.py", "old": "'before'", "new": "'almost'"}, "edit-1"),
        AgentStep(type="assistant", content="<final> First candidate ready.", kind="final",
                  usage=ModelUsage(input_tokens=100, output_tokens=10, source="provider")),
        _tool("edit_file", {"path": "app.py", "old": "'almost'", "new": "'after'"}, "repair-1"),
        AgentStep(type="assistant", content="<final> Repaired candidate ready.", kind="final",
                  usage=ModelUsage(input_tokens=100, output_tokens=10, source="provider")),
    ])

    class Model:
        def next(self, messages, **_kwargs):
            seen.append(deepcopy(messages))
            return next(steps)

    def factory(*_args, **_kwargs):
        model = Model()
        models.append(model)
        return model

    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    first = task_module._run(_input(session, owner, "FIRST_ASSIGNMENT: update app.py."), context)
    assert first.ok, first.output
    first_usage = context._agent_budget.snapshot()
    second = task_module._run(_input(session, owner,
        "REPAIR_ASSIGNMENT: AssertionError: expected after, got almost. Fix the existing candidate."), context)
    assert second.ok, second.output
    inherited = seen[3]
    assert any(message.get("toolUseId") == "read-1" and "receipt_491" in message.get("content", "")
               for message in inherited), "repair discarded the first Executor's actual read_file result"
    assert any(message.get("toolUseId") == "edit-1" for message in inherited)
    assert len(models) == 1, "repair replaced the Executor adapter instead of resuming it"
    assert (tmp_path / "app.py").read_text().startswith("value = 'after'")
    assert first_usage.used_model_calls == 3
    final_usage = context._agent_budget.snapshot()
    assert final_usage.used_model_calls == 5
    assert final_usage.used_total_tokens - first_usage.used_total_tokens == 220
    assert "Model turns: 2 (tool calls: 1)" in second.output
    assert sum(message.get("toolName") == "read_file" and message.get("role") == "assistant_tool_call"
               for message in seen[-1]) == 1


@pytest.mark.parametrize("mismatch", ["owner", "cwd", "scope", "role", "json"])
def test_session_refuses_foreign_host_context_before_model_call(tmp_path, monkeypatch, mismatch):
    owner = object()
    session = _session(owner, tmp_path)
    context = _context(tmp_path)
    calls = []

    class Model:
        def next(self, _messages, **_kwargs):
            calls.append(1)
            return AgentStep(type="assistant", content="<final> No changes required.", kind="final")

    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter",
                        lambda *_a, **_k: Model())
    assert task_module._run(_input(session, owner, "FIRST_ASSIGNMENT"), context).ok
    data = _input(session, owner, "FOREIGN_ASSIGNMENT")
    if mismatch == "owner":
        data["_executor_session_owner"] = object()
    elif mismatch == "cwd":
        other = tmp_path / "other"
        other.mkdir()
        context = replace(context, cwd=str(other), _logical_agent_workspace_root=str(other))
    elif mismatch == "scope":
        context = replace(context, _logical_agent_scope={**context._logical_agent_scope,
                                                       "write_paths": ("different.py",)})
    elif mismatch == "role":
        context = replace(context, _logical_agent_role="analyst")
        data["agent_type"] = "plan"
    else:
        data["_executor_session"] = {"owner": "workflow", "workspace_root": str(tmp_path)}
    result = task_module._run(data, context)
    assert not result.ok, "a foreign context was allowed to resume a host-owned Executor"
    assert "executor_session" in result.output
    assert len(calls) == 1


def _submit_binding(number):
    from minicode.executor_result_submission import ExecutorResultChannel, create_executor_submit_tool
    from minicode.multi_agent_contracts import parse_assignment, parse_implementation_order
    from minicode.scheduler_artifacts import SchedulerArtifactStore

    assignment = parse_assignment({
        "assignmentVersion": 1, "workflowId": "wf_session", "taskId": "task_session",
        "assignmentId": f"asg_session_{number}", "role": "executor", "mode": "implement",
        "objective": "Update app.py", "scope": {"readRoots": ["."],
            "writePaths": ["app.py"], "forbiddenPaths": [".git"]},
        "constraints": [], "acceptanceCriteria": [{"id": "ac_result", "kind": "behavior",
                                                  "text": "The value is correct."}],
        "inputRefs": [], "risk": "low", "budget": {"deadlineAt": "2027-01-01T00:00:00Z",
            "maxModelCalls": 8, "maxInputTokens": 100000, "maxOutputTokens": 10000},
        "expectedResultType": "ExecutionResult.v1",
    })
    order = parse_implementation_order(assignment, {
        "orderVersion": 1, "orderId": f"order_session_{number}",
        "assignmentId": assignment.assignment_id, "baseDigest": "sha256:" + "a" * 64,
        "allowedPaths": ["app.py"], "steps": [{"id": "s_update", "action": "Update the value.",
            "allowedPaths": ["app.py"], "criterionIds": ["ac_result"]}],
        "criterionIds": ["ac_result"], "verificationRequests": ["pytest -q"],
    })
    store = SchedulerArtifactStore(scheduler_authority=object())
    channel = ExecutorResultChannel(store.create_assignment_channel(
        readable_keys=(), write_key=f"artifact_session_{number}"), assignment, order)
    return assignment, store, channel, create_executor_submit_tool(assignment, order, channel)


def test_resumed_adapter_uses_fresh_submit_authority_and_system_binding(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("value = 'before'\n")
    owner = object()
    session = _session(owner, tmp_path)
    context = _context(tmp_path)
    first_assignment, first_store, first_channel, first_tool = _submit_binding(1)
    second_assignment, second_store, second_channel, second_tool = _submit_binding(2)
    submission = {"status": "completed", "steps": [{"step": 1, "status": "completed"}],
                  "deviations": [], "blockers": []}
    seen = []

    class Model:
        tools = None

        def next(self, messages, **_kwargs):
            seen.append((deepcopy(messages), next(t for t in self.tools.list() if t.name == "executor_submit")))
            return _tool("executor_submit", submission, f"submit-{len(seen)}")

    def factory(*_args, **kwargs):
        model = Model()
        model.tools = kwargs["tools"]
        return model

    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    for assignment, channel, tool in ((first_assignment, first_channel, first_tool),
                                      (second_assignment, second_channel, second_tool)):
        data = {**_input(session, owner, f"Work on {assignment.assignment_id}"),
                "_executor_submit_tool": tool,
                "_result_key": channel.write_key,
                "_result_instruction": f"CURRENT_BINDING: {assignment.assignment_id}"}
        result = task_module._run(data, replace(context, _subagent_mailbox=channel))
        assert result.ok, result.output
        channel.close()
    assert len(seen) == 2
    assert seen[0][1] is first_tool and seen[1][1] is second_tool
    assert "asg_session_2" in seen[1][0][0]["content"]
    assert "asg_session_1" not in seen[1][0][0]["content"]
    assert any(m.get("toolUseId") == "submit-1" for m in seen[1][0])
    assert json.loads(first_store.read(first_channel.write_key).content)["assignmentId"] == "asg_session_1"
    assert json.loads(second_store.read(second_channel.write_key).content)["assignmentId"] == "asg_session_2"
    stale = ToolRegistry([first_tool]).execute("executor_submit", submission,
        replace(context, _subagent_mailbox=second_channel))
    assert not stale.ok and "executor_submit_owner" in stale.output


@pytest.mark.parametrize("interruption", ["cancelled", "budget_exceeded"])
def test_interrupted_executor_keeps_observed_tools_without_marking_complete(tmp_path, monkeypatch, interruption):
    from minicode.turn_cancellation import TurnCancellationRequested

    (tmp_path / "app.py").write_text("value = 'before'\n# durable receipt_817\n")
    owner = object()
    session = _session(owner, tmp_path)
    context = _context(tmp_path)
    seen = []
    model_calls = 0

    class Model:
        def next(self, messages, **_kwargs):
            nonlocal model_calls
            model_calls += 1
            seen.append(deepcopy(messages))
            if model_calls == 1:
                return _tool("read_file", {"path": "app.py"}, "interrupt-read")
            if model_calls == 2:
                if interruption == "cancelled":
                    raise TurnCancellationRequested("turn_" + "a" * 32)
                raise AgentBudgetExceeded("fixture interrupted after observed read", context._agent_budget.snapshot())
            return AgentStep(type="assistant", content="<final> Existing investigation retained.", kind="final")

    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter",
                        lambda *_a, **_k: Model())
    data = _input(session, owner, "Inspect app.py for this implementation.")
    if interruption == "cancelled":
        with pytest.raises(TurnCancellationRequested):
            task_module._run(data, context)
    else:
        assert not task_module._run(data, context).ok
    if hasattr(session, "last_outcome"):
        assert session.last_outcome == interruption
    result = task_module._run(_input(session, owner, "Continue from the interrupted investigation."), context)
    assert result.ok, result.output
    assert any(m.get("toolUseId") == "interrupt-read" and "receipt_817" in m.get("content", "")
               for m in seen[2]), "interrupted Executor lost an already observed tool result"


def test_host_close_discards_continuity_and_public_input_cannot_restore_it(tmp_path, monkeypatch):
    from minicode.executor_session import ExecutorSession

    owner = object()
    session = ExecutorSession(owner=owner, workspace_root=str(tmp_path))
    context = _context(tmp_path)

    class Model:
        def next(self, _messages, **_kwargs):
            return AgentStep(type="assistant", content="<final> Finished.", kind="final")

    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter",
                        lambda *_a, **_k: Model())
    data = _input(session, owner, "FIRST_ASSIGNMENT")
    assert task_module._run(data, context).ok
    session.close()
    assert session.model is None and session.context_manager is None
    result = task_module._run(data, context)
    assert not result.ok and "executor_session" in result.output
    public = task_module._validate(data)
    assert "_executor_session" not in public and "_executor_session_owner" not in public
    with pytest.raises(ValueError, match="only valid for read-only"):
        task_module._validate({**data, "continue_subagent_id": "sub_" + "a" * 32})
