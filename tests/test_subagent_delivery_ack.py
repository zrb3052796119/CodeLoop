"""Only the owning loop can acknowledge an asynchronous result's delivery."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from minicode.agent_loop import _execute_single_tool, run_agent_turn
from minicode.subagent_lifecycle import (
    AsyncSubagentLifecycle,
    SubagentLifecycleError,
    SubagentLifecycleNotFound,
)
from minicode.tooling import ToolContext, ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.task import task_tool
from minicode.types import AgentStep

_EVIDENCE = "Independent child result: auth.py owns issue_token."


class _ObservedLifecycle(AsyncSubagentLifecycle):
    def __init__(self):
        super().__init__(max_workers=1)
        self.poll_started = threading.Event()
        self.poll_returned = threading.Event()
        self.ack_threads = []

    def poll(self, *args, **kwargs):
        self.poll_started.set()
        result = super().poll(*args, **kwargs)
        self.poll_returned.set()
        return result

    def acknowledge_delivery(self, ids):
        self.ack_threads.append(threading.get_ident())
        super().acknowledge_delivery(ids)


def _spawn_completed(lifecycle):
    spawned = lifecycle.spawn(agent_type="explore", runner=lambda _id, _cancel: ToolResult(ok=True, output=_EVIDENCE))
    terminal = lifecycle.poll(str(spawned["subagentId"]), wait_seconds=1)
    assert terminal["terminal"], terminal
    return terminal


def test_abandoned_poll_cannot_consume_an_undelivered_terminal_result(tmp_path):
    lifecycle = _ObservedLifecycle()
    release = threading.Event()
    registry = ToolRegistry([task_tool])

    def runner(_id, _cancel):
        assert release.wait(5)
        return ToolResult(ok=True, output=_EVIDENCE)

    try:
        spawned = lifecycle.spawn(agent_type="explore", runner=runner)
        result = _execute_single_tool(
            call={"id": "poll", "toolName": "task", "input": {"action": "poll", "subagent_id": spawned["subagentId"], "wait_seconds": 30}},
            tools=registry, cwd=str(tmp_path), permissions=None,
            runtime={"model": "fake"}, store=None, step=1,
            on_tool_start=None, on_tool_result=None,
            tool_scheduler=SimpleNamespace(_force_tool_timeout=1),
            subagent_lifecycle=lifecycle,
        )
        assert lifecycle.poll_started.is_set()
        assert not result.ok and "timed out" in result.output
        assert _EVIDENCE not in result.output
        release.set()
        assert lifecycle.poll_returned.wait(2)
        snapshot = lifecycle.finalization_barrier()
        assert snapshot["ready"] is False, "timed-out polling must leave its result available"
        assert [item["result"]["output"] for item in snapshot["completed"]] == [_EVIDENCE]
        assert lifecycle.ack_threads == []
    finally:
        release.set()
        lifecycle.shutdown()
        registry.dispose()


def test_barrier_projection_keeps_results_until_explicit_owner_receipt():
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    try:
        terminal = _spawn_completed(lifecycle)
        first = lifecycle.finalization_barrier()
        second = lifecycle.finalization_barrier()
        assert first == second
        assert first["ready"] is False
        assert first["completed"][0]["result"]["output"] == _EVIDENCE
        ids = (str(terminal["subagentId"]),)
        lifecycle.acknowledge_delivery(ids)
        lifecycle.acknowledge_delivery(ids)
        assert lifecycle.finalization_barrier()["ready"] is True
    finally:
        lifecycle.shutdown()


@pytest.mark.parametrize("action", ["poll", "cancel"])
def test_terminal_task_snapshot_attaches_receipt_without_acking_in_worker(tmp_path, action):
    lifecycle = _ObservedLifecycle()
    try:
        terminal = _spawn_completed(lifecycle)
        result = task_tool.run(
            {"action": action, "subagent_id": terminal["subagentId"]},
            ToolContext(cwd=str(tmp_path), _subagent_lifecycle=lifecycle),
        )
        assert result.ok
        assert json.loads(result.output)["terminal"] is True
        assert lifecycle.finalization_barrier()["ready"] is False
        assert lifecycle.ack_threads == []
        result._delivery_ack()
        assert lifecycle.finalization_barrier()["ready"] is True
    finally:
        lifecycle.shutdown()


def test_receipt_cannot_ack_foreign_or_unfinished_jobs():
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    release = threading.Event()
    try:
        terminal = _spawn_completed(lifecycle)
        running = lifecycle.spawn(agent_type="plan", runner=lambda _id, _cancel: (release.wait(2) and ToolResult(ok=True, output="late")))
        with pytest.raises(SubagentLifecycleError, match="terminal"):
            lifecycle.acknowledge_delivery((str(terminal["subagentId"]), str(running["subagentId"])))
        assert lifecycle.finalization_barrier()["completed"][0]["result"]["output"] == _EVIDENCE
        with pytest.raises(SubagentLifecycleNotFound):
            lifecycle.acknowledge_delivery(("sub_" + "f" * 32,))
    finally:
        release.set()
        lifecycle.shutdown()


class _ScriptedModel:
    def __init__(self, steps):
        self.steps = iter(steps)

    def next(self, messages, on_stream_chunk=None):
        step = next(self.steps)
        return step(messages) if callable(step) else step


@pytest.mark.parametrize("committed_batch", [False, True])
def test_owner_confirms_poll_only_after_transcript_append(tmp_path, committed_batch):
    lifecycle = _ObservedLifecycle()
    owner_thread = threading.get_ident()
    definitions = [task_tool]
    if committed_batch:
        definitions.append(ToolDefinition(
            name="commit", description="already finalized workflow receipt",
            input_schema={"type": "object"}, validator=lambda value: value,
            run=lambda _input, _context: ToolResult(ok=True, output="Immutable workflow receipt.", _must_deliver=True, _continue_parent=True),
        ))
    registry = ToolRegistry(definitions)
    try:
        terminal = _spawn_completed(lifecycle)
        calls = [{"id": "poll", "toolName": "task", "input": {"action": "poll", "subagent_id": terminal["subagentId"], "wait_seconds": 0}}]
        if committed_batch:
            calls.append({"id": "commit", "toolName": "commit", "input": {}})

        def synthesize(messages):
            assert any(message.get("role") == "tool_result" and _EVIDENCE in message.get("content", "") for message in messages)
            assert lifecycle.ack_threads == [owner_thread]
            assert lifecycle.finalization_barrier()["ready"] is True
            return AgentStep(type="assistant", content="Combined final.")

        messages = run_agent_turn(
            model=_ScriptedModel([AgentStep(type="tool_calls", calls=calls), synthesize]),
            tools=registry, messages=[{"role": "user", "content": "Collect child evidence."}],
            cwd=str(tmp_path), enable_work_chain=False, subagent_lifecycle=lifecycle,
        )
        assert messages[-1]["content"] == "Combined final."
    finally:
        lifecycle.shutdown()
        registry.dispose()


def test_failed_barrier_delivery_leaves_results_unread(tmp_path):
    lifecycle = _ObservedLifecycle()

    def fail_progress(_content):
        raise RuntimeError("progress observer rejected insertion")

    try:
        _spawn_completed(lifecycle)
        with pytest.raises(RuntimeError, match="progress observer"):
            run_agent_turn(
                model=_ScriptedModel([AgentStep(type="assistant", content="Final before synthesis.")]),
                tools=ToolRegistry([]), messages=[{"role": "user", "content": "Collect child evidence."}],
                cwd=str(tmp_path), enable_work_chain=False, subagent_lifecycle=lifecycle,
                on_progress_message=fail_progress,
            )
        assert lifecycle.ack_threads == []
        assert lifecycle.finalization_barrier()["completed"][0]["result"]["output"] == _EVIDENCE
    finally:
        lifecycle.shutdown()
