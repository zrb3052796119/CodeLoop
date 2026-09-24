"""Task admission observes completed work across both delegation APIs."""

from __future__ import annotations

import concurrent.futures
import json
import threading
from dataclasses import replace

import pytest

from minicode.subagent_lifecycle import AsyncSubagentLifecycle
from minicode.subagent_mailbox import SubagentMailbox
from minicode.tooling import ToolContext, ToolResult, ToolTerminationReason
from minicode.tools import task as task_module


@pytest.fixture
def context(tmp_path):
    lifecycle = AsyncSubagentLifecycle(max_workers=2)
    ctx = ToolContext(
        cwd=str(tmp_path),
        _subagent_mailbox=SubagentMailbox(),
        _subagent_lifecycle=lifecycle,
    )
    yield ctx
    lifecycle.shutdown()


def dispatch(context, *, action="run", **fields):
    payload = {"action": action, **fields}
    if action in {"run", "spawn"}:
        payload.update(
            agent_type="explore", description="inspect", prompt="Inspect code."
        )
    return task_module.task_tool.run(
        task_module.task_tool.validator(payload), context
    )


def complete_async(context):
    admitted = dispatch(context, action="spawn")
    assert admitted.ok, admitted.output
    subagent_id = json.loads(admitted.output)["subagentId"]
    polled = dispatch(
        context, action="poll", subagent_id=subagent_id, wait_seconds=1
    )
    terminal = json.loads(polled.output)
    assert terminal["terminal"]
    return subagent_id, terminal


def install_worker(monkeypatch, worker):
    monkeypatch.setattr(task_module, "_SYNC_RUN", worker)
    monkeypatch.setattr(task_module, "_run", worker)


@pytest.mark.parametrize("next_action", ["run", "spawn"])
def test_sync_failures_reject_both_delegation_apis(context, monkeypatch, next_action):
    calls = []

    def worker(_input, _context):
        calls.append(1)
        return ToolResult(ok=False, output="worker failed")

    install_worker(monkeypatch, worker)
    dispatch(context)
    dispatch(context)
    rejected = dispatch(context, action=next_action)
    assert not rejected.ok
    assert "delegation_circuit_open" in rejected.output
    assert len(calls) == 2


@pytest.mark.parametrize("next_action", ["run", "spawn"])
@pytest.mark.parametrize("failure", ["result", "fallback", "exception", "invalid"])
def test_async_terminal_failures_close_both_apis(
    context, monkeypatch, next_action, failure
):
    def worker(_input, _context):
        if failure == "exception":
            raise RuntimeError("worker failed")
        if failure == "invalid":
            return None
        if failure == "fallback":
            return ToolResult(ok=True, output='{"contractStatus":"fallback"}')
        return ToolResult(ok=False, output="worker failed")

    install_worker(monkeypatch, worker)
    complete_async(context)
    complete_async(context)
    install_worker(monkeypatch, lambda _i, _c: ToolResult(ok=True, output="new work"))
    rejected = dispatch(context, action=next_action)
    assert not rejected.ok
    assert "delegation_circuit_open" in rejected.output


def test_poll_cancel_and_finalization_do_not_settle_failure_twice(context, monkeypatch):
    install_worker(
        monkeypatch, lambda _i, _c: ToolResult(ok=False, output="worker failed")
    )
    subagent_id, _ = complete_async(context)
    for action in ("poll", "poll", "cancel", "cancel"):
        dispatch(context, action=action, subagent_id=subagent_id)
    context._subagent_lifecycle.finalization_barrier()
    # One failure is below the threshold; this real second execution is admitted.
    second = dispatch(context)
    assert second.output == "worker failed"
    assert "delegation_circuit_open" in dispatch(context).output


def test_async_success_resets_consecutive_sync_failure(context, monkeypatch):
    outcomes = iter([False, True, False, True])
    install_worker(
        monkeypatch,
        lambda _i, _c: ToolResult(ok=next(outcomes), output="worker returned"),
    )
    dispatch(context)
    _, completed = complete_async(context)
    assert completed["status"] == "completed"
    dispatch(context)
    assert dispatch(context).ok


@pytest.mark.parametrize(
    "termination", [ToolTerminationReason.ABANDONED, ToolTerminationReason.TURN_CANCELLED]
)
def test_sync_cancellation_does_not_count_as_delegation_failure(
    context, monkeypatch, termination
):
    outcomes = iter([
        ToolResult(ok=False, output="first worker failed"),
        ToolResult(ok=False, output="cancelled", _termination_reason=termination),
        ToolResult(ok=True, output="new worker completed"),
    ])
    install_worker(monkeypatch, lambda _i, _c: next(outcomes))
    dispatch(context)
    dispatch(context)
    assert dispatch(context).output == "new worker completed"


def test_running_async_cancel_does_not_count_or_reset_prior_failure(
    context, monkeypatch
):
    started = threading.Event()

    def worker(_input, child_context):
        if child_context._tool_abandoned is None:
            return ToolResult(ok=False, output="ordinary failure")
        started.set()
        assert child_context._tool_abandoned.wait(2)
        return ToolResult(
            ok=False, output="cancelled", _termination_reason=ToolTerminationReason.ABANDONED
        )

    install_worker(monkeypatch, worker)
    dispatch(context)
    admitted = dispatch(context, action="spawn")
    subagent_id = json.loads(admitted.output)["subagentId"]
    assert started.wait(2)
    dispatch(context, action="cancel", subagent_id=subagent_id)
    terminal = dispatch(context, action="poll", subagent_id=subagent_id, wait_seconds=1)
    assert json.loads(terminal.output)["status"] == "cancelled"
    assert dispatch(context).output == "ordinary failure"
    assert "delegation_circuit_open" in dispatch(context).output


def test_concurrent_sync_and_async_failures_share_settlement(context, monkeypatch):
    rendezvous = threading.Barrier(3)
    lock = threading.Lock()
    calls = 0

    def worker(_input, _context):
        nonlocal calls
        with lock:
            calls += 1
            initial_worker = calls <= 2
        if initial_worker:
            rendezvous.wait(2)
        return ToolResult(ok=False, output="concurrent failure")

    install_worker(monkeypatch, worker)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        synchronous = pool.submit(dispatch, context)
        admitted = dispatch(context, action="spawn")
        subagent_id = json.loads(admitted.output)["subagentId"]
        rendezvous.wait(2)
        assert not synchronous.result(timeout=2).ok
        terminal = dispatch(
            context, action="poll", subagent_id=subagent_id, wait_seconds=1
        )
        assert json.loads(terminal.output)["status"] == "failed"
    assert "delegation_circuit_open" in dispatch(context).output


def test_cancelled_queued_work_does_not_settle_or_reset_failure(context, monkeypatch):
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    queued_context = replace(context, _subagent_lifecycle=lifecycle)
    release = threading.Event()
    started = threading.Event()

    def occupy_worker(_id, _cancel):
        started.set()
        assert release.wait(2)
        return ToolResult(ok=True, output="slot released")

    install_worker(
        monkeypatch, lambda _i, _c: ToolResult(ok=False, output="ordinary failure")
    )
    try:
        lifecycle.spawn(agent_type="explore", runner=occupy_worker)
        assert started.wait(2)
        dispatch(queued_context)
        admitted = dispatch(queued_context, action="spawn")
        subagent_id = json.loads(admitted.output)["subagentId"]
        cancelled = dispatch(queued_context, action="cancel", subagent_id=subagent_id)
        assert json.loads(cancelled.output)["status"] == "cancelled"
        assert dispatch(queued_context).output == "ordinary failure"
        assert "delegation_circuit_open" in dispatch(queued_context).output
    finally:
        release.set()
        lifecycle.shutdown()


def test_queued_work_rechecks_circuit_before_running(context, monkeypatch):
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    queued_context = replace(context, _subagent_lifecycle=lifecycle)
    release = threading.Event()
    started = threading.Event()
    calls = []

    def occupy_worker(_id, _cancel):
        started.set()
        assert release.wait(2)
        return ToolResult(ok=True, output="slot released")

    def worker(_input, _context):
        calls.append(1)
        return ToolResult(ok=False, output="ordinary failure")

    install_worker(monkeypatch, worker)
    try:
        lifecycle.spawn(agent_type="explore", runner=occupy_worker)
        assert started.wait(2)
        admitted = dispatch(queued_context, action="spawn")
        subagent_id = json.loads(admitted.output)["subagentId"]
        dispatch(queued_context)
        dispatch(queued_context)
        release.set()
        terminal = dispatch(
            queued_context, action="poll", subagent_id=subagent_id, wait_seconds=1
        )
        result = json.loads(terminal.output)["result"]
        assert "delegation_circuit_open" in result["output"]
        assert len(calls) == 2
    finally:
        release.set()
        lifecycle.shutdown()


def test_late_admitted_success_does_not_reopen_circuit(context, monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def worker(_input, child_context):
        if child_context._tool_abandoned is None:
            return ToolResult(ok=False, output="ordinary failure")
        started.set()
        assert release.wait(2)
        return ToolResult(ok=True, output="late success")

    install_worker(monkeypatch, worker)
    try:
        admitted = dispatch(context, action="spawn")
        subagent_id = json.loads(admitted.output)["subagentId"]
        assert started.wait(2)
        dispatch(context)
        dispatch(context)
        release.set()
        terminal = dispatch(
            context, action="poll", subagent_id=subagent_id, wait_seconds=1
        )
        assert json.loads(terminal.output)["status"] == "completed"
        assert "delegation_circuit_open" in dispatch(context).output
    finally:
        release.set()
