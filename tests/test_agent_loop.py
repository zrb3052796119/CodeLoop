from minicode.agent_loop import run_agent_turn
from minicode.state import create_app_store
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.types import AgentStep, ChatMessage, ModelAdapter, StepDiagnostics


class ScriptedModel(ModelAdapter):
    def __init__(self, steps: list[AgentStep]) -> None:
        self._steps = steps
        self.calls = 0

    def next(self, messages: list[ChatMessage], on_stream_chunk=None) -> AgentStep:
        step = self._steps[self.calls]
        self.calls += 1
        return step


class StoreCapturingModel(ModelAdapter):
    def __init__(self) -> None:
        self.received_store = None

    def next(self, messages: list[ChatMessage], on_stream_chunk=None, store=None) -> AgentStep:
        self.received_store = store
        return AgentStep(type="assistant", content="done")


def test_agent_turn_executes_tool_and_returns_assistant() -> None:
    def run_echo(input_data: dict, _context) -> ToolResult:
        return ToolResult(ok=True, output=f"echo:{input_data['text']}")

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="echo",
                description="echo tool",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=run_echo,
            )
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[{"id": "1", "toolName": "echo", "input": {"text": "hi"}}],
            ),
            AgentStep(type="assistant", content="done"),
        ]
    )

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
    )

    assert messages[-1] == {"role": "assistant", "content": "done"}
    assert any(message["role"] == "tool_result" for message in messages)


def test_tool_timeout_returns_error_without_blocking(monkeypatch) -> None:
    """A hung tool must yield a timeout error promptly instead of blocking
    until the tool finishes, and must not be executed a second time."""
    import threading
    import time

    monkeypatch.setenv("MINICODE_TOOL_TIMEOUT", "1")
    release = threading.Event()
    executions: list[int] = []

    def run_hang(_input_data: dict, _context) -> ToolResult:
        executions.append(1)
        release.wait(timeout=30)
        return ToolResult(ok=True, output="finally finished")

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="hang",
                description="hangs",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=run_hang,
            )
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(type="tool_calls", calls=[{"id": "1", "toolName": "hang", "input": {}}]),
            AgentStep(type="assistant", content="done"),
        ]
    )

    started = time.monotonic()
    try:
        messages = run_agent_turn(
            model=model,
            tools=registry,
            messages=[{"role": "system", "content": "sys"}],
            cwd=".",
        )
    finally:
        release.set()
    elapsed = time.monotonic() - started

    assert elapsed < 10, f"turn blocked on hung tool for {elapsed:.1f}s"
    tool_results = [m for m in messages if m["role"] == "tool_result"]
    assert tool_results and "timed out" in tool_results[0]["content"]
    assert len(executions) == 1, "tool must not be re-executed after a failure"


def test_agent_turn_emits_callbacks() -> None:
    events: list[tuple[str, str]] = []

    def run_echo(input_data: dict, _context) -> ToolResult:
        return ToolResult(ok=True, output=f"echo:{input_data['text']}")

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="echo",
                description="echo tool",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=run_echo,
            )
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(type="tool_calls", content="working", contentKind="progress", calls=[{"id": "1", "toolName": "echo", "input": {"text": "hi"}}]),
            AgentStep(type="assistant", content="done"),
        ]
    )

    run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        on_tool_start=lambda name, _input: events.append(("start", name)),
        on_tool_result=lambda name, _output, _error: events.append(("result", name)),
        on_assistant_message=lambda content: events.append(("assistant", content)),
        on_progress_message=lambda content: events.append(("progress", content)),
    )

    assert ("progress", "working") in events
    assert ("start", "echo") in events
    assert ("result", "echo") in events
    assert ("assistant", "done") in events


def test_agent_turn_retries_empty_response_then_continues() -> None:
    model = ScriptedModel(
        [
            AgentStep(type="assistant", content=""),
            AgentStep(type="assistant", content="done"),
        ]
    )
    registry = ToolRegistry([])

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
    )

    assert messages[-1] == {"role": "assistant", "content": "done"}
    assert any(
        message["role"] == "user" and "last response was empty" in message["content"]
        for message in messages
    )


def test_agent_turn_handles_recoverable_pause_turn() -> None:
    model = ScriptedModel(
        [
            AgentStep(
                type="assistant",
                content="",
                diagnostics=StepDiagnostics(stopReason="pause_turn", ignoredBlockTypes=["thinking"]),
            ),
            AgentStep(type="assistant", content="done"),
        ]
    )
    registry = ToolRegistry([])
    progress_events: list[str] = []

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        on_progress_message=progress_events.append,
    )

    assert messages[-1] == {"role": "assistant", "content": "done"}
    assert any("pause_turn" in event for event in progress_events)


def test_agent_turn_returns_fallback_after_repeated_empty_responses() -> None:
    model = ScriptedModel(
        [
            AgentStep(type="assistant", content=""),
            AgentStep(type="assistant", content=""),
            AgentStep(type="assistant", content=""),
        ]
    )
    registry = ToolRegistry([])

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
    )

    assert "empty response" in messages[-1]["content"].lower()


def test_tool_registry_dispose_calls_disposer() -> None:
    disposed: list[bool] = []
    registry = ToolRegistry([], disposer=lambda: disposed.append(True))

    registry.dispose()

    assert disposed == [True]


def test_agent_turn_passes_store_to_provider_adapter() -> None:
    model = StoreCapturingModel()
    registry = ToolRegistry([])
    store = create_app_store()

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        store=store,
    )

    assert messages[-1] == {"role": "assistant", "content": "done"}
    assert model.received_store is store
