"""A synchronous task stays owned until it delivers or its own control stops it."""

from __future__ import annotations

import json
from pathlib import Path
import re
import threading
import time

import pytest

import minicode.subagent_model_routing as model_routing
import minicode.tools.task as task_module
from minicode.agent_loop import (
    AgentTurnDeadlineExceeded, _execute_single_tool, run_agent_turn,
)
from minicode.model_call_control import checkpoint_model_call
from minicode.subagent_lifecycle import AsyncSubagentLifecycle
from minicode.subagent_result import extract_subagent_result
from minicode.tooling import ToolRegistry, ToolResult
from minicode.tools.task import task_tool
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from minicode.types import AgentStep


def _report_step(messages: list[dict]) -> AgentStep:
    key = re.search(
        r"mailbox key `(subagent_result_[a-z0-9_]+)`", messages[0]["content"],
    ).group(1)
    report = json.dumps({
        "resultVersion": 1, "summary": "Inspected the requested module.",
        "files": [], "risks": [],
        "verification": {"status": "not_run", "checks": []},
    })
    return AgentStep(type="tool_calls", calls=[{
        "id": "report", "toolName": "subagent_note_write",
        "input": {"key": key, "content": report},
    }])


class _Parent:
    def __init__(self, kinds=("plan",)):
        self.calls = 0
        self.kinds = kinds
        self.received = []

    def next(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return AgentStep(type="tool_calls", calls=[{
                "id": f"task-{index}", "toolName": "task", "input": {
                    "agent_type": kind, "description": f"inspect {index}",
                    "prompt": f"Inspect module {index} and report the findings.",
                },
            } for index, kind in enumerate(self.kinds)])
        self.received = [message for message in messages
                         if message.get("role") == "tool_result"
                         and message.get("toolName") == "task"]
        return AgentStep(type="assistant", content="Combined the received findings.")


class _ReportingChild:
    runtime = {"model": "fake"}

    def __init__(self, *, delay=0, barrier=None):
        self.calls = 0
        self.delay = delay
        self.barrier = barrier
        self.threads = []
        self.first_returned = threading.Event()

    def next(self, messages, **kwargs):
        self.calls += 1
        self.threads.append(threading.get_ident())
        if self.calls == 1:
            if self.barrier is not None:
                self.barrier.wait(timeout=2)
            time.sleep(self.delay)
            self.first_returned.set()
            return _report_step(messages)
        return AgentStep(
            type="assistant", content="<final> Module inspection complete.",
        )


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setenv("MINICODE_TOOL_TIMEOUT", "1")
    monkeypatch.setenv("MINICODE_TASK_TIMEOUT_SECONDS", "10")
    monkeypatch.setattr(
        "minicode.intent_semantic_classifier.build_semantic_intent_adapter",
        lambda *args, **kwargs: None,
    )
    return monkeypatch


def _run(parent: _Parent, workspace: Path, **kwargs) -> list[dict]:
    registry = ToolRegistry([task_tool])
    try:
        return run_agent_turn(
            model=parent, tools=registry, cwd=str(workspace),
            messages=[{"role": "user", "content": "Inspect the requested modules."}],
            runtime={"model": "fake"}, enable_work_chain=False,
            allow_user_interaction=False, **kwargs,
        )
    finally:
        registry.dispose()


@pytest.mark.parametrize("kind", ["plan", "code-surveyor"])
def test_real_sync_plan_exceeding_generic_timeout_is_delivered(offline, tmp_path, kind):
    if kind == "code-surveyor":
        profile = tmp_path / ".mini-code" / "agents" / "code-surveyor.md"
        profile.parent.mkdir(parents=True)
        profile.write_text(
            "---\nname: code-surveyor\ndescription: Inspect the requested module.\n"
            "max_turns: 4\nallowed_tools: read_file, list_files\n---\n"
            "Report the module's responsibilities.\n"
        )
    child = _ReportingChild(delay=1.15)
    offline.setattr(model_routing, "create_subagent_model_adapter",
                    lambda *args, **kwargs: child)
    owner_thread = threading.get_ident()
    parent = _Parent((kind,))
    try:
        _run(parent, tmp_path, deadline_monotonic=time.monotonic() + 10)
    finally:
        assert child.first_returned.wait(3)
    assert parent.calls == 2 and child.calls == 2
    assert child.threads == [owner_thread, owner_thread]
    assert len(parent.received) == 1 and not parent.received[0]["isError"]
    report = extract_subagent_result(parent.received[0]["content"])
    assert report["outcome"] == "completed" and not report.get("partial", False)
    assert report["summary"] == "Inspected the requested module."


def test_spawn_registration_cannot_continue_after_generic_timeout(offline, tmp_path):
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    original_spawn = lifecycle.spawn
    registration_threads = []
    returned = threading.Event()

    def slow_spawn(**kwargs):
        registration_threads.append(threading.get_ident())
        time.sleep(1.15)
        try:
            return original_spawn(**kwargs)
        finally:
            returned.set()

    offline.setattr(lifecycle, "spawn", slow_spawn)
    offline.setattr(task_module, "_run", lambda *args, **kwargs:
                    ToolResult(ok=True, output="Spawned child findings."))
    registry = ToolRegistry([task_tool])
    try:
        result = _execute_single_tool(
            call={"id": "spawn", "toolName": "task", "input": {
                "action": "spawn", "agent_type": "plan", "description": "inspect",
                "prompt": "Inspect the module.",
            }},
            tools=registry, cwd=str(tmp_path), permissions=None,
            runtime={"model": "fake"}, store=None, step=1,
            on_tool_start=None, on_tool_result=None, subagent_lifecycle=lifecycle,
        )
        assert result.ok, result.output
        assert registration_threads == [threading.get_ident()]
        identity = json.loads(result.output)["subagentId"]
        terminal = lifecycle.poll(identity, wait_seconds=1)
        assert terminal["terminal"]
        assert terminal["result"]["output"] == "Spawned child findings."
    finally:
        assert returned.wait(3)
        lifecycle.shutdown()
        registry.dispose()


@pytest.mark.parametrize("control", ["deadline", "cancel"])
def test_task_inherits_parent_control_and_does_not_start_late_requests(
    offline, tmp_path, control,
):
    token = TurnCancellationToken("turn_" + "a" * 32)
    deadline = time.monotonic() + 0.6
    observed = []
    active = threading.Event()

    class ControlledChild:
        runtime = {"model": "fake"}

        def next(self, messages, *, cancellation_token=None,
                 deadline_monotonic=None, **kwargs):
            observed.append((cancellation_token, deadline_monotonic))
            active.set()
            try:
                if control == "cancel":
                    token.request()
                while True:
                    checkpoint_model_call(
                        cancellation_token=cancellation_token,
                        deadline_monotonic=deadline_monotonic,
                    )
                    time.sleep(0.005)
            finally:
                active.clear()

    offline.setattr(model_routing, "create_subagent_model_adapter",
                    lambda *args, **kwargs: ControlledChild())
    parent = _Parent()
    expected = (AgentTurnDeadlineExceeded if control == "deadline"
                else TurnCancellationRequested)
    with pytest.raises(expected):
        _run(parent, tmp_path, cancellation_token=token,
             deadline_monotonic=deadline)
    assert observed == [(token, deadline)]
    assert not active.is_set() and parent.calls == 1
    time.sleep(0.03)
    assert observed == [(token, deadline)]


def test_two_readonly_tasks_still_execute_concurrently_and_both_deliver(
    offline, tmp_path,
):
    barrier = threading.Barrier(2)
    children = []

    def factory(*args, **kwargs):
        child = _ReportingChild(barrier=barrier)
        children.append(child)
        return child

    offline.setattr(model_routing, "create_subagent_model_adapter", factory)
    parent = _Parent(("plan", "explore"))
    _run(parent, tmp_path, deadline_monotonic=time.monotonic() + 10)
    assert len(children) == 2 and all(child.calls == 2 for child in children)
    assert len({child.threads[0] for child in children}) == 2
    assert all(child.threads[0] == child.threads[1] for child in children)
    assert len(parent.received) == 2
    assert all(not result["isError"] and extract_subagent_result(result["content"])
               for result in parent.received)
