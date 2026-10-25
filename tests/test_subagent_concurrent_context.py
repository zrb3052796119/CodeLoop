"""Real concurrent task calls preserve the owner's context and authority."""

from __future__ import annotations

import json
import re
import time

import minicode.subagent_model_routing as model_routing
import minicode.tools.task as task_module
from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.permissions import PermissionManager
from minicode.subagent_lifecycle import AsyncSubagentLifecycle
from minicode.subagent_mailbox import SubagentMailbox
from minicode.turn_cancellation import TurnCancellationToken
from minicode.subagent_result import extract_subagent_result
from minicode.subagent_sessions import SubagentSessionStore
from minicode.tooling import SkillLoadPolicy, ToolRegistry
from minicode.tools.todo_write import todo_write_tool
from minicode.types import AgentStep


def test_parallel_tasks_preserve_sessions_todo_deadline_and_skill_authority(monkeypatch, tmp_path):
    contexts = []
    children = []
    deadline = time.monotonic() + 60
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    cancellation = TurnCancellationToken("turn_" + "d" * 32)
    permissions = PermissionManager(str(tmp_path))
    mailbox = SubagentMailbox()
    lifecycle = AsyncSubagentLifecycle(max_workers=2)
    policy = SkillLoadPolicy(denied_qualified_names=frozenset({"private-skill"}), reason_code="batch-test")
    original_run = task_module._SYNC_RUN

    def observe_run(input_data, context):
        contexts.append(context)
        return original_run(input_data, context)

    class ChildModel:
        def __init__(self):
            self.runtime = {"model": "fake"}
            self.calls = 0
            self.followup = False
            self.result_messages = []

        def next(self, messages, on_stream_chunk=None):
            self.calls += 1
            self.followup = any("continuation of sub-agent" in str(message.get("content", "")) for message in messages)
            key = re.search(r"mailbox key `(subagent_result_[a-z0-9_]+)`", messages[0]["content"]).group(1)
            if self.calls == 1:
                report = json.dumps({"resultVersion": 1, "summary": "Inspected module.", "files": [], "risks": [], "verification": {"status": "not_run", "checks": []}})
                return AgentStep(type="tool_calls", calls=[
                    {"id": "child-todo", "toolName": "todo_write", "input": {"todos": [{"content": "Child-only investigation", "status": "completed"}]}},
                    {"id": "child-question", "toolName": "ask_user", "input": {"question": "This must not reach the user."}},
                    {"id": "child-report", "toolName": "subagent_note_write", "input": {"key": key, "content": report}},
                ])
            self.result_messages = list(messages)
            return AgentStep(type="assistant", content="<final> Child findings.")

    def child_factory(*args, **kwargs):
        child = ChildModel()
        children.append(child)
        return child

    monkeypatch.setattr(task_module, "_SYNC_RUN", observe_run)
    monkeypatch.setattr(model_routing, "create_subagent_model_adapter", child_factory)
    monkeypatch.setattr("minicode.intent_semantic_classifier.build_semantic_intent_adapter", lambda *args, **kwargs: None)

    class ParentModel:
        calls = 0

        def next(self, messages, on_stream_chunk=None):
            self.calls += 1
            if self.calls == 1:
                return AgentStep(type="tool_calls", calls=[{"id": "parent-todo", "toolName": "todo_write", "input": {"todos": [{"content": "Parent task", "status": "in_progress"}]}}])
            if self.calls == 2:
                return AgentStep(type="tool_calls", calls=[
                    {"id": "alpha", "toolName": "task", "input": {"description": "inspect alpha", "agent_type": "explore", "prompt": "Inspect alpha module."}},
                    {"id": "beta", "toolName": "task", "input": {"description": "inspect beta", "agent_type": "explore", "prompt": "Inspect beta module."}},
                ])
            if self.calls == 3:
                reports = [extract_subagent_result(message.get("content")) for message in messages if message.get("role") == "tool_result" and message.get("toolName") == "task"]
                if reports and all(reports):
                    return AgentStep(type="tool_calls", calls=[{"id": "followup", "toolName": "task", "input": {"description": "alpha followup", "agent_type": "explore", "prompt": "Explain the function already inspected.", "continue_subagent_id": reports[0]["subagentId"]}}])
            return AgentStep(type="assistant", content="Combined findings.")

    registry = ToolRegistry([task_module.task_tool, todo_write_tool])
    try:
        messages = run_agent_turn(
            model=ParentModel(), tools=registry,
            messages=[{"role": "user", "content": "Inspect both modules and follow up on the first."}],
            cwd=str(tmp_path), runtime={"model": "fake"}, enable_work_chain=False,
            allow_user_interaction=False, deadline_monotonic=deadline,
            skill_load_policy=policy, permissions=permissions,
            agent_budget=budget, cancellation_token=cancellation,
            subagent_mailbox=mailbox, subagent_lifecycle=lifecycle,
        )
    finally:
        registry.dispose()
        lifecycle.shutdown()

    # Inspect the actual public Task dispatch, with real nested loops and
    # actual tools; only provider adapters are replaced by scripted models.
    assert len(contexts) >= 2
    assert all(isinstance(context._subagent_sessions, SubagentSessionStore) for context in contexts)
    assert contexts[0]._subagent_sessions is contexts[1]._subagent_sessions
    assert all(context._agent_budget is budget for context in contexts)
    assert all(context._cancellation_token is cancellation for context in contexts)
    assert all(context.permissions is permissions for context in contexts)
    assert all(context._subagent_mailbox is mailbox for context in contexts)
    assert all(context._subagent_lifecycle is lifecycle for context in contexts)
    assert contexts[0]._todo_state is contexts[1]._todo_state
    assert [todo["content"] for todo in contexts[0]._todo_state["tasks"]] == ["Parent task"]
    assert all(context._task_deadline_monotonic == deadline for context in contexts)
    assert all(context._skill_load_policy is policy for context in contexts)
    task_results = [message for message in messages if message.get("role") == "tool_result" and message.get("toolName") == "task"]
    assert len(task_results) == 3
    assert all(not message["isError"] for message in task_results)
    assert len(children) == 3 and children[-1].followup
    assert all(any(message.get("toolName") == "ask_user" and message.get("isError") and "User interaction is unavailable" in message.get("content", "") for message in child.result_messages) for child in children)
