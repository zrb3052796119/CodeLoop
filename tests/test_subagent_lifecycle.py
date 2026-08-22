from __future__ import annotations

import json
import threading
import time

import pytest

from minicode.subagent_lifecycle import (
    AsyncSubagentLifecycle,
    SubagentLifecycleError,
    SubagentLifecycleNotFound,
    SubagentWorkerCancelled,
)
from minicode.tooling import ToolContext, ToolResult
from minicode.types import AgentStep, ChatMessage, ModelAdapter


def _wait_for_terminal(
    lifecycle: AsyncSubagentLifecycle,
    subagent_id: str,
    *,
    timeout: float = 1.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = lifecycle.poll(subagent_id)
        if snapshot["terminal"]:
            return snapshot
        time.sleep(0.001)
    raise AssertionError(f"sub-agent {subagent_id} did not finish")


def test_spawn_is_nonblocking_and_poll_returns_correlatable_result() -> None:
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    started = threading.Event()
    release = threading.Event()
    received: list[tuple[str, threading.Event]] = []

    def runner(subagent_id: str, cancel_event: threading.Event) -> ToolResult:
        received.append((subagent_id, cancel_event))
        started.set()
        assert release.wait(timeout=1.0)
        return ToolResult(ok=True, output="analysis complete")

    try:
        before = time.monotonic()
        spawned = lifecycle.spawn(agent_type="explore", runner=runner)
        elapsed = time.monotonic() - before

        assert elapsed < 0.2
        assert spawned["subagentId"].startswith("sub_")
        assert spawned["agentType"] == "explore"
        assert spawned["status"] in {"queued", "running"}
        assert spawned["terminal"] is False
        assert started.wait(timeout=1.0)
        assert received[0][0] == spawned["subagentId"]

        release.set()
        completed = _wait_for_terminal(lifecycle, str(spawned["subagentId"]))
        assert completed == {
            "lifecycleVersion": 1,
            "subagentId": spawned["subagentId"],
            "agentType": "explore",
            "status": "completed",
            "terminal": True,
            "result": {
                "ok": True,
                "output": "analysis complete",
                "truncated": False,
            },
        }
    finally:
        release.set()
        lifecycle.shutdown()


def test_poll_can_wait_for_completion_without_model_busy_loop() -> None:
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    release = threading.Event()

    def runner(_id: str, _cancel: threading.Event) -> ToolResult:
        assert release.wait(timeout=1.0)
        return ToolResult(ok=True, output="ready")

    try:
        spawned = lifecycle.spawn(agent_type="explore", runner=runner)
        timer = threading.Timer(0.02, release.set)
        timer.start()
        snapshot = lifecycle.poll(
            str(spawned["subagentId"]),
            wait_seconds=0.5,
        )
        timer.join(timeout=1.0)
        assert snapshot["terminal"] is True
        assert snapshot["result"]["output"] == "ready"
    finally:
        release.set()
        lifecycle.shutdown()


def test_finalization_barrier_requires_terminal_results_to_be_observed() -> None:
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    release = threading.Event()

    def runner(_id: str, _cancel: threading.Event) -> ToolResult:
        assert release.wait(timeout=1.0)
        return ToolResult(ok=True, output="bounded findings")

    try:
        spawned = lifecycle.spawn(agent_type="explore", runner=runner)

        pending = lifecycle.finalization_barrier()
        assert pending["ready"] is False
        assert [item["subagentId"] for item in pending["pending"]] == [
            spawned["subagentId"]
        ]
        assert pending["completed"] == []

        release.set()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            completed = lifecycle.finalization_barrier()
            if completed["completed"]:
                break
            time.sleep(0.001)
        else:
            raise AssertionError("finalization barrier did not observe completion")

        assert completed["ready"] is False
        assert completed["pending"] == []
        assert completed["completed"][0]["result"]["output"] == "bounded findings"
        assert lifecycle.finalization_barrier() == {
            "lifecycleVersion": 1,
            "ready": True,
            "pending": [],
            "completed": [],
        }
    finally:
        release.set()
        lifecycle.shutdown()


def test_polling_terminal_result_satisfies_finalization_barrier() -> None:
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    try:
        spawned = lifecycle.spawn(
            agent_type="plan",
            runner=lambda _id, _cancel: ToolResult(ok=True, output="reported"),
        )
        terminal = _wait_for_terminal(lifecycle, str(spawned["subagentId"]))
        assert terminal["terminal"] is True
        assert lifecycle.finalization_barrier()["ready"] is True
    finally:
        lifecycle.shutdown()


def test_cancel_is_idempotent_and_signals_the_worker() -> None:
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    started = threading.Event()
    observed_cancel = threading.Event()
    finish = threading.Event()

    def runner(_subagent_id: str, cancel_event: threading.Event) -> ToolResult:
        started.set()
        if cancel_event.wait(timeout=1.0):
            observed_cancel.set()
        assert finish.wait(timeout=1.0)
        raise SubagentWorkerCancelled()

    try:
        spawned = lifecycle.spawn(agent_type="plan", runner=runner)
        assert started.wait(timeout=1.0)

        first = lifecycle.cancel(str(spawned["subagentId"]))
        second = lifecycle.cancel(str(spawned["subagentId"]))

        assert first == second
        assert first["status"] == "cancelling"
        assert first["terminal"] is False
        assert first["result"] is None
        assert observed_cancel.wait(timeout=1.0)
        finish.set()
        terminal = _wait_for_terminal(
            lifecycle,
            str(spawned["subagentId"]),
        )
        assert terminal["status"] == "cancelled"
        assert terminal["terminal"] is True
    finally:
        finish.set()
        lifecycle.shutdown()


def test_late_cancel_cannot_relabel_an_already_successful_worker() -> None:
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    completion_emitted = threading.Event()
    return_result = threading.Event()

    def runner(_subagent_id: str, _cancel_event: threading.Event) -> ToolResult:
        # Models the real task runner after it has emitted subagent.completed
        # but just before Future marks the call done.
        completion_emitted.set()
        assert return_result.wait(timeout=1.0)
        return ToolResult(ok=True, output="completed before cancellation")

    try:
        spawned = lifecycle.spawn(agent_type="explore", runner=runner)
        assert completion_emitted.wait(timeout=1.0)
        requested = lifecycle.cancel(str(spawned["subagentId"]))
        assert requested["status"] == "cancelling"
        return_result.set()

        terminal = _wait_for_terminal(
            lifecycle,
            str(spawned["subagentId"]),
        )
        assert terminal["status"] == "completed"
        assert terminal["result"]["ok"] is True
    finally:
        return_result.set()
        lifecycle.shutdown()


def test_lifecycle_rejects_write_capable_agents_and_foreign_ids() -> None:
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    try:
        with pytest.raises(SubagentLifecycleError, match="explore or plan"):
            lifecycle.spawn(
                agent_type="general",
                runner=lambda _id, _event: ToolResult(ok=True, output="bad"),
            )
        with pytest.raises(SubagentLifecycleNotFound, match="not owned"):
            lifecycle.poll("sub_00000000000000000000000000000000")
    finally:
        lifecycle.shutdown()


def test_lifecycle_bounds_job_count_output_and_worker_errors() -> None:
    lifecycle = AsyncSubagentLifecycle(
        max_workers=1,
        max_jobs=1,
        max_result_chars=256,
    )
    release = threading.Event()

    def long_result(_id: str, _cancel: threading.Event) -> ToolResult:
        assert release.wait(timeout=1.0)
        return ToolResult(ok=True, output="x" * 400)

    try:
        spawned = lifecycle.spawn(agent_type="explore", runner=long_result)
        with pytest.raises(SubagentLifecycleError, match="capacity"):
            lifecycle.spawn(agent_type="plan", runner=long_result)
        release.set()
        terminal = _wait_for_terminal(
            lifecycle,
            str(spawned["subagentId"]),
        )
        result = terminal["result"]
        assert result["truncated"] is True
        assert len(result["output"]) <= 256
        assert "result truncated" in result["output"]
    finally:
        release.set()
        lifecycle.shutdown()

    failing = AsyncSubagentLifecycle(max_workers=1)

    def raise_secret(_id: str, _cancel: threading.Event) -> ToolResult:
        raise RuntimeError("password=worker-secret")

    try:
        spawned = failing.spawn(agent_type="plan", runner=raise_secret)
        terminal = _wait_for_terminal(failing, str(spawned["subagentId"]))
        assert terminal["status"] == "failed"
        assert terminal["result"]["output"].endswith("RuntimeError")
        assert "worker-secret" not in terminal["result"]["output"]
    finally:
        failing.shutdown()


def test_task_tool_exposes_spawn_poll_and_preserves_sync_default(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import minicode.tools.task as task_module

    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    release = threading.Event()
    started = threading.Event()
    received: list[tuple[dict[str, object], ToolContext]] = []

    def fake_run(input_data: dict, context: ToolContext) -> ToolResult:
        received.append((input_data, context))
        started.set()
        assert release.wait(timeout=1.0)
        return ToolResult(ok=True, output="async findings")

    monkeypatch.setattr(task_module, "_run", fake_run)
    context = ToolContext(
        cwd=str(tmp_path),
        _subagent_lifecycle=lifecycle,
    )
    try:
        sync_input = task_module.task_tool.validator(
            {"description": "inspect code", "prompt": "Inspect the code"}
        )
        assert sync_input["action"] == "run"
        assert sync_input["agent_type"] == "general"

        spawn_input = task_module.task_tool.validator(
            {
                "action": "spawn",
                "description": "inspect code",
                "prompt": "Inspect the code",
                "agent_type": "explore",
            }
        )
        spawn_result = task_module.task_tool.run(spawn_input, context)
        assert spawn_result.ok is True
        spawned = json.loads(spawn_result.output)
        assert spawned["subagentId"].startswith("sub_")
        assert started.wait(timeout=1.0)
        assert received[0][0]["_subagent_id"] == spawned["subagentId"]
        assert received[0][1]._tool_abandoned is not None

        release.set()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            poll_input = task_module.task_tool.validator(
                {"action": "poll", "subagent_id": spawned["subagentId"]}
            )
            poll_result = task_module.task_tool.run(poll_input, context)
            assert poll_result.ok is True
            polled = json.loads(poll_result.output)
            if polled["terminal"]:
                break
            time.sleep(0.001)
        else:
            raise AssertionError("task poll did not reach a terminal state")
        assert polled["status"] == "completed"
        assert polled["result"]["output"] == "async findings"
    finally:
        release.set()
        lifecycle.shutdown()


def test_task_tool_rejects_async_general_and_unknown_job(tmp_path) -> None:
    import minicode.tools.task as task_module

    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    context = ToolContext(cwd=str(tmp_path), _subagent_lifecycle=lifecycle)
    try:
        with pytest.raises(ValueError, match="read-only"):
            task_module.task_tool.validator(
                {
                    "action": "spawn",
                    "description": "change code",
                    "prompt": "Modify a file",
                    "agent_type": "general",
                }
            )

        poll_input = task_module.task_tool.validator(
            {
                "action": "poll",
                "subagent_id": "sub_00000000000000000000000000000000",
            }
        )
        result = task_module.task_tool.run(poll_input, context)
        assert result.ok is False
        assert "sub_agent_not_found" in result.output
    finally:
        lifecycle.shutdown()


def test_task_cancel_action_is_idempotent(tmp_path, monkeypatch) -> None:
    import minicode.tools.task as task_module

    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    started = threading.Event()
    observed_cancel = threading.Event()
    finish = threading.Event()

    def fake_run(_input: dict, context: ToolContext) -> ToolResult:
        started.set()
        if context._tool_abandoned.wait(timeout=1.0):
            observed_cancel.set()
        assert finish.wait(timeout=1.0)
        return ToolResult(ok=False, output="error[tool_abandoned]: cancellation")

    monkeypatch.setattr(task_module, "_run", fake_run)
    context = ToolContext(cwd=str(tmp_path), _subagent_lifecycle=lifecycle)
    try:
        spawn_result = task_module.task_tool.run(
            task_module.task_tool.validator(
                {
                    "action": "spawn",
                    "description": "inspect code",
                    "prompt": "Inspect the code",
                    "agent_type": "plan",
                }
            ),
            context,
        )
        subagent_id = json.loads(spawn_result.output)["subagentId"]
        assert started.wait(timeout=1.0)
        cancel_input = task_module.task_tool.validator(
            {"action": "cancel", "subagent_id": subagent_id}
        )
        first = task_module.task_tool.run(cancel_input, context)
        second = task_module.task_tool.run(cancel_input, context)

        assert (first.ok, second.ok) == (True, True)
        assert json.loads(first.output) == json.loads(second.output)
        assert json.loads(first.output)["status"] == "cancelling"
        assert json.loads(first.output)["terminal"] is False
        assert observed_cancel.wait(timeout=1.0)
        finish.set()
        terminal = _wait_for_terminal(lifecycle, subagent_id)
        assert terminal["status"] == "cancelled"
    finally:
        finish.set()
        lifecycle.shutdown()


class _ScriptedModel(ModelAdapter):
    def __init__(self, steps: list[AgentStep]) -> None:
        self._steps = steps
        self._index = 0

    def next(self, messages: list[ChatMessage], on_stream_chunk=None) -> AgentStep:
        step = self._steps[self._index]
        self._index += 1
        return step


class _RecordingLifecycle:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class _ReleasingModel(ModelAdapter):
    def __init__(self, release: threading.Event) -> None:
        self.release = release
        self.calls = 0

    def next(self, messages: list[ChatMessage], on_stream_chunk=None) -> AgentStep:
        self.calls += 1
        if self.calls == 1:
            return AgentStep(type="assistant", content="Both are still running.")
        if self.calls == 2:
            self.release.set()
            return AgentStep(type="assistant", content="I should synthesize now.")
        if any(
            "sub-agent results are now available" in str(message.get("content", ""))
            for message in messages
        ):
            return AgentStep(type="assistant", content="Synthesized final answer.")
        return AgentStep(type="assistant", content="Still waiting for results.")


def test_agent_turn_rejects_final_until_async_children_are_observed() -> None:
    from minicode.agent_loop import run_agent_turn
    from minicode.tooling import ToolRegistry

    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    release = threading.Event()

    def runner(_id: str, _cancel: threading.Event) -> ToolResult:
        assert release.wait(timeout=1.0)
        return ToolResult(ok=True, output="implementation facts")

    try:
        lifecycle.spawn(
            agent_type="explore",
            runner=runner,
        )
        model = _ReleasingModel(release)
        messages = run_agent_turn(
            model=model,
            tools=ToolRegistry([]),
            messages=[{"role": "system", "content": "system"}],
            cwd=".",
            enable_work_chain=False,
            subagent_lifecycle=lifecycle,
        )

        assert 3 <= model.calls <= 4
        assert messages[-1] == {
            "role": "assistant",
            "content": "Synthesized final answer.",
        }
        assert not any(
            message.get("role") == "assistant"
            and message.get("content") == "Both are still running."
            for message in messages
        )
        assert any(
            "implementation facts" in str(message.get("content", ""))
            for message in messages
        )
    finally:
        release.set()
        lifecycle.shutdown()


def test_agent_turn_owns_default_lifecycle_and_shuts_it_down(monkeypatch) -> None:
    import minicode.agent_loop as agent_loop_module
    from minicode.tooling import ToolRegistry

    lifecycle = _RecordingLifecycle()
    monkeypatch.setattr(
        agent_loop_module,
        "AsyncSubagentLifecycle",
        lambda: lifecycle,
    )

    agent_loop_module.run_agent_turn(
        model=_ScriptedModel([AgentStep(type="assistant", content="done")]),
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "system"}],
        cwd=".",
        enable_work_chain=False,
    )

    assert lifecycle.shutdown_calls == 1


def test_nested_agent_turn_shares_lifecycle_without_closing_it() -> None:
    from minicode.agent_loop import run_agent_turn
    from minicode.tooling import ToolDefinition, ToolRegistry

    lifecycle = _RecordingLifecycle()
    captured: list[object] = []

    def inspect_context(_input: dict, context: ToolContext) -> ToolResult:
        captured.append(context._subagent_lifecycle)
        return ToolResult(ok=True, output="seen")

    run_agent_turn(
        model=_ScriptedModel(
            [
                AgentStep(
                    type="tool_calls",
                    calls=[{"id": "1", "toolName": "inspect", "input": {}}],
                ),
                AgentStep(type="assistant", content="done"),
            ]
        ),
        tools=ToolRegistry(
            [
                ToolDefinition(
                    name="inspect",
                    description="inspect context",
                    input_schema={"type": "object"},
                    validator=lambda value: value,
                    run=inspect_context,
                )
            ]
        ),
        messages=[{"role": "system", "content": "system"}],
        cwd=".",
        enable_work_chain=False,
        subagent_lifecycle=lifecycle,
    )

    assert captured == [lifecycle]
    assert lifecycle.shutdown_calls == 0


def test_async_task_result_and_parent_event_join_on_spawned_id(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import minicode.tools.task as task_module
    from minicode.subagent_mailbox import SubagentMailbox
    from minicode.subagent_result import extract_subagent_result
    from minicode.task_outcome import canonicalize_task_outcome

    payloads: list[dict[str, object]] = []

    class Sink:
        def emit(self, event_type, *, step=None, payload=None) -> None:
            if event_type == "subagent.completed":
                payloads.append(payload)

    def reported_turn(**kwargs):
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "async inspected"}]

    monkeypatch.setattr(task_module, "run_agent_turn", reported_turn)
    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    context = ToolContext(
        cwd=str(tmp_path),
        _runtime={"model": "fake"},
        _subagent_mailbox=SubagentMailbox(),
        _subagent_lifecycle=lifecycle,
        _event_sink=Sink(),
    )
    try:
        spawn_result = task_module.task_tool.run(
            task_module.task_tool.validator(
                {
                    "action": "spawn",
                    "description": "inspect async",
                    "prompt": "Inspect the task implementation.",
                    "agent_type": "explore",
                }
            ),
            context,
        )
        spawned = json.loads(spawn_result.output)

        terminal = _wait_for_terminal(
            lifecycle,
            str(spawned["subagentId"]),
        )
        assert terminal["status"] == "completed"
        structured = extract_subagent_result(terminal["result"]["output"])
        assert structured is not None
        assert structured["subagentId"] == spawned["subagentId"]
        assert payloads[0]["subagentId"] == spawned["subagentId"]
    finally:
        lifecycle.shutdown()
