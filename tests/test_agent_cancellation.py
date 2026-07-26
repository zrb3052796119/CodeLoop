from __future__ import annotations

import threading

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.turn_cancellation import (
    TurnCancellationRequested,
    TurnCancellationToken,
)
from minicode.types import AgentStep


TURN_ID = "turn_" + "a" * 32


class BlockingModel:
    def __init__(self, started: threading.Event, release: threading.Event, result) -> None:
        self.started = started
        self.release = release
        self.result = result
        self.calls = 0

    def next(self, _messages, on_stream_chunk=None):
        self.calls += 1
        self.started.set()
        assert self.release.wait(5)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class ScriptedModel:
    def __init__(self, steps) -> None:
        self.steps = list(steps)
        self.calls = 0

    def next(self, _messages, on_stream_chunk=None):
        step = self.steps[self.calls]
        self.calls += 1
        return step


def _run_in_thread(**kwargs):
    outcome: dict[str, object] = {}

    def target() -> None:
        try:
            outcome["result"] = run_agent_turn(**kwargs)
        except BaseException as error:  # test captures exact control-flow identity
            outcome["error"] = error

    worker = threading.Thread(target=target)
    worker.start()
    return worker, outcome


def test_cancel_before_first_model_sends_no_provider_request() -> None:
    token = TurnCancellationToken(TURN_ID)
    token.request()
    model = ScriptedModel([AgentStep(type="assistant", content="forbidden")])

    with pytest.raises(TurnCancellationRequested):
        run_agent_turn(
            model=model,
            tools=ToolRegistry([]),
            messages=[{"role": "system", "content": "safe"}],
            cwd=".",
            enable_work_chain=False,
            cancellation_token=token,
        )

    assert model.calls == 0


def test_cancel_during_model_stops_after_return_without_assistant_or_next_work() -> None:
    started = threading.Event()
    release = threading.Event()
    token = TurnCancellationToken(TURN_ID)
    model = BlockingModel(started, release, AgentStep(type="assistant", content="late"))
    worker, outcome = _run_in_thread(
        model=model,
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
        cancellation_token=token,
    )

    assert started.wait(5)
    token.request()
    release.set()
    worker.join(5)

    assert not worker.is_alive()
    assert isinstance(outcome.get("error"), TurnCancellationRequested)
    assert model.calls == 1
    assert "result" not in outcome


def test_cancel_before_tool_execution_does_not_invoke_tool() -> None:
    token = TurnCancellationToken(TURN_ID)
    calls: list[str] = []

    def forbidden(_input, _context):
        calls.append("tool")
        return ToolResult(ok=True, output="forbidden")

    tools = ToolRegistry([
        ToolDefinition(
            name="write_safe",
            description="safe fixture",
            input_schema={"type": "object"},
            validator=lambda value: value,
            run=forbidden,
        )
    ])

    class CancellingModel:
        def next(self, _messages, on_stream_chunk=None):
            token.request()
            return AgentStep(
                type="tool_calls",
                calls=[{"id": "1", "toolName": "write_safe", "input": {}}],
            )

    with pytest.raises(TurnCancellationRequested):
        run_agent_turn(
            model=CancellingModel(),
            tools=tools,
            messages=[{"role": "system", "content": "safe"}],
            cwd=".",
            enable_work_chain=False,
            cancellation_token=token,
        )

    assert calls == []


def test_cancel_during_tool_allows_started_tool_to_finish_then_stops() -> None:
    started = threading.Event()
    release = threading.Event()
    token = TurnCancellationToken(TURN_ID)
    tool_calls: list[str] = []
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[{"id": "1", "toolName": "side_effect", "input": {}}],
            ),
            AgentStep(type="assistant", content="forbidden"),
        ]
    )

    def side_effect(_input, _context):
        tool_calls.append("started")
        started.set()
        assert release.wait(5)
        tool_calls.append("finished")
        return ToolResult(ok=True, output="effect happened")

    tools = ToolRegistry([
        ToolDefinition(
            name="side_effect",
            description="fixture",
            input_schema={"type": "object"},
            validator=lambda value: value,
            run=side_effect,
        )
    ])
    worker, outcome = _run_in_thread(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
        cancellation_token=token,
    )

    assert started.wait(5)
    token.request()
    release.set()
    worker.join(5)

    assert tool_calls == ["started", "finished"]
    assert model.calls == 1
    assert isinstance(outcome.get("error"), TurnCancellationRequested)


def test_cancel_before_concurrent_batch_schedules_no_tool() -> None:
    token = TurnCancellationToken(TURN_ID)
    calls: list[str] = []

    def forbidden(_input, _context):
        calls.append("tool")
        return ToolResult(ok=True, output="forbidden")

    tools = ToolRegistry([
        ToolDefinition(
            name=name,
            description="read fixture",
            input_schema={"type": "object"},
            validator=lambda value: value,
            run=forbidden,
        )
        for name in ("read_file", "list_files")
    ])

    class CancellingBatchModel:
        def next(self, _messages, on_stream_chunk=None):
            token.request()
            return AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "1", "toolName": "read_file", "input": {}},
                    {"id": "2", "toolName": "list_files", "input": {}},
                ],
            )

    with pytest.raises(TurnCancellationRequested):
        run_agent_turn(
            model=CancellingBatchModel(),
            tools=tools,
            messages=[{"role": "system", "content": "safe"}],
            cwd=".",
            enable_work_chain=False,
            cancellation_token=token,
        )

    assert calls == []


def test_cancel_after_concurrent_batch_starts_waits_for_batch_then_stops() -> None:
    token = TurnCancellationToken(TURN_ID)
    all_started = threading.Barrier(3)
    release = threading.Event()
    finished: list[str] = []

    def definition(name: str) -> ToolDefinition:
        def run(_input, _context):
            all_started.wait(5)
            assert release.wait(5)
            finished.append(name)
            return ToolResult(ok=True, output=name)

        return ToolDefinition(
            name=name,
            description="read fixture",
            input_schema={"type": "object"},
            validator=lambda value: value,
            run=run,
        )

    tools = ToolRegistry([definition("read_file"), definition("list_files")])
    model = ScriptedModel([
        AgentStep(
            type="tool_calls",
            calls=[
                {"id": "1", "toolName": "read_file", "input": {}},
                {"id": "2", "toolName": "list_files", "input": {}},
            ],
        ),
        AgentStep(type="assistant", content="forbidden"),
    ])
    worker, outcome = _run_in_thread(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
        cancellation_token=token,
    )

    all_started.wait(5)
    token.request()
    release.set()
    worker.join(5)

    assert sorted(finished) == ["list_files", "read_file"]
    assert model.calls == 1
    assert isinstance(outcome.get("error"), TurnCancellationRequested)


def test_cancel_during_provider_error_bypasses_recovery_and_fallback() -> None:
    started = threading.Event()
    release = threading.Event()
    token = TurnCancellationToken(TURN_ID)
    model = BlockingModel(started, release, ConnectionError("fixture network"))
    worker, outcome = _run_in_thread(
        model=model,
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
        cancellation_token=token,
    )

    assert started.wait(5)
    token.request()
    release.set()
    worker.join(5)

    assert isinstance(outcome.get("error"), TurnCancellationRequested)
    assert model.calls == 1


def test_keyboard_interrupt_identity_is_preserved_with_uncancelled_token() -> None:
    started = threading.Event()
    release = threading.Event()
    release.set()
    model = BlockingModel(started, release, KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        run_agent_turn(
            model=model,
            tools=ToolRegistry([]),
            messages=[{"role": "system", "content": "safe"}],
            cwd=".",
            enable_work_chain=False,
            cancellation_token=TurnCancellationToken(TURN_ID),
        )
