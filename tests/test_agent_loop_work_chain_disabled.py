from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import minicode.agent_loop as agent_loop_module
import pytest
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import ContextManager
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.types import AgentStep, ChatMessage, ModelAdapter, ModelUsage


@dataclass
class RecordedEvent:
    event_type: str
    step: int | None
    payload: object


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[RecordedEvent] = []

    def emit(self, event_type: str, *, step=None, payload=None) -> None:
        self.events.append(RecordedEvent(event_type, step, payload))


class FailingSink:
    def emit(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("password=observer-secret")


class RaisingModel(ModelAdapter):
    def __init__(self, error: BaseException) -> None:
        self.calls = 0
        self.error = error

    def next(
        self,
        messages: list[ChatMessage],
        on_stream_chunk=None,
        store=None,
    ) -> AgentStep:
        self.calls += 1
        raise self.error


class ScriptedModel(ModelAdapter):
    def __init__(self, steps: list[AgentStep]) -> None:
        self.calls = 0
        self.steps = steps

    def next(
        self,
        messages: list[ChatMessage],
        on_stream_chunk=None,
        store=None,
    ) -> AgentStep:
        step = self.steps[self.calls]
        self.calls += 1
        return step


class AssistantModel(ModelAdapter):
    def __init__(self, *, content: str = "ok") -> None:
        self.calls = 0
        self.content = content
        self.received_messages: list[list[ChatMessage]] = []

    def next(
        self,
        messages: list[ChatMessage],
        on_stream_chunk=None,
        store=None,
    ) -> AgentStep:
        self.calls += 1
        self.received_messages.append([dict(message) for message in messages])
        return AgentStep(type="assistant", content=self.content)


def _messages() -> list[ChatMessage]:
    return [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]


def test_work_chain_disabled_returns_assistant_without_context_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = AssistantModel()
    messages = _messages()
    original = [dict(message) for message in messages]

    def fail_work_chain(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("disabled path must not build a Work Chain task")

    monkeypatch.setattr(agent_loop_module, "_build_work_chain_task", fail_work_chain)

    result = run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=messages,
        cwd=".",
        enable_work_chain=False,
    )

    assert result[-1] == {"role": "assistant", "content": "ok"}
    assert model.calls == 1
    assert messages == original


def test_work_chain_disabled_uses_plain_context_manager_without_controllers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = AssistantModel()
    context_manager = ContextManager(model="test")

    def fail_constructor(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("disabled path must not construct Work Chain controllers")

    for name in (
        "CyberneticOrchestrator",
        "ContextCompactor",
        "ContextCyberneticsOrchestrator",
        "CostControlLoop",
        "SelfHealingEngine",
        "FeedforwardController",
    ):
        monkeypatch.setattr(agent_loop_module, name, fail_constructor)

    result = run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=_messages(),
        cwd=".",
        enable_work_chain=False,
        context_manager=context_manager,
    )

    assert result[-1] == {"role": "assistant", "content": "ok"}
    assert context_manager.messages is result
    assert model.calls == 1


def test_work_chain_disabled_does_not_reenter_removed_legacy_compact_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = AssistantModel()
    context_manager = ContextManager(model="test")
    calls: list[str] = []
    def should_auto_compact() -> bool:
        calls.append("should_auto_compact")
        return True

    def compact_messages() -> list[ChatMessage]:
        calls.append("compact_messages")
        return context_manager.messages

    monkeypatch.setattr(context_manager, "should_auto_compact", should_auto_compact)
    monkeypatch.setattr(context_manager, "compact_messages", compact_messages)
    monkeypatch.setattr(
        context_manager,
        "get_context_summary",
        lambda: "controlled compact summary",
    )
    monkeypatch.setattr(
        agent_loop_module,
        "ContextCompactor",
        lambda *_args, **_kwargs: pytest.fail("ContextCompactor must stay disabled"),
    )
    monkeypatch.setattr(
        agent_loop_module,
        "ContextCyberneticsOrchestrator",
        lambda *_args, **_kwargs: pytest.fail("Cybernetics must stay disabled"),
    )
    summaries: list[str] = []

    result = run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=_messages(),
        cwd=".",
        enable_work_chain=False,
        context_manager=context_manager,
        on_assistant_message=summaries.append,
    )

    assert calls == []
    model_ledgers = [
        message
        for message in model.received_messages[0]
        if message.get("_task_ledger")
    ]
    assert len(model_ledgers) == 1
    assert [
        message
        for message in model.received_messages[0]
        if not message.get("_task_ledger")
    ] == _messages()
    assert [
        message for message in result if not message.get("_task_ledger")
    ] == [*_messages(), {"role": "assistant", "content": "ok"}]
    assert len([message for message in result if message.get("_task_ledger")]) == 1
    assert context_manager.messages is result
    assert summaries == ["ok"]


def test_work_chain_disabled_preserves_canonical_usage_and_duration_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = iter((10.0, 10.375))
    monkeypatch.setattr(agent_loop_module.time, "monotonic", lambda: next(readings))
    usage = ModelUsage(
        input_tokens=120,
        output_tokens=24,
        cache_read_tokens=8,
        cache_creation_tokens=0,
        source="provider",
    )
    model = AssistantModel()
    sink = RecordingSink()

    original_next = model.next

    def next_with_usage(messages, on_stream_chunk=None, store=None):
        step = original_next(messages, on_stream_chunk, store)
        step.usage = usage
        return step

    monkeypatch.setattr(model, "next", next_with_usage)

    result = run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=_messages(),
        cwd=".",
        enable_work_chain=False,
        event_sink=sink,
    )

    assert result[-1] == {"role": "assistant", "content": "ok"}
    assert model.calls == 1
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.completed",
        "model.costed",
        "working_memory.observed",
        "task.outcome",
    ]
    operation_id = sink.events[0].payload["operationId"]  # type: ignore[index]
    assert re.fullmatch(r"modelop_[0-9a-f]{32}", operation_id)
    assert sink.events[1].payload == {  # type: ignore[comparison-overlap]
        "operationId": operation_id,
        "resultType": "assistant",
        "contentPresent": True,
        "toolCallCount": 0,
        "usage": {
            "source": "provider",
            "inputTokens": 120,
            "outputTokens": 24,
            "cacheReadTokens": 8,
            "cacheCreationTokens": 0,
        },
        "durationMs": 375,
    }
    assert sink.events[2].payload == {  # type: ignore[comparison-overlap]
        "costVersion": 1,
        "operationId": operation_id,
        "status": "unavailable",
        "quality": "unavailable",
        "currency": "USD",
        "catalogId": "minicode-pricing-2026-07-17-v1",
        "reason": "model_unpriced",
    }


def test_work_chain_disabled_failing_sink_matches_no_sink() -> None:
    results: list[list[ChatMessage]] = []
    calls: list[int] = []

    for sink in (None, FailingSink()):
        model = AssistantModel(content="same")
        results.append(
            run_agent_turn(
                model=model,
                tools=ToolRegistry([]),
                messages=_messages(),
                cwd=".",
                enable_work_chain=False,
                event_sink=sink,
            )
        )
        calls.append(model.calls)

    assert results[0] == results[1]
    assert results[0][-1] == {"role": "assistant", "content": "same"}
    assert calls == [1, 1]


@pytest.mark.parametrize("sink", [None, FailingSink()])
def test_work_chain_disabled_sink_failure_does_not_replace_interrupt(sink) -> None:
    interrupt = KeyboardInterrupt()
    model = RaisingModel(interrupt)

    with pytest.raises(KeyboardInterrupt) as raised:
        run_agent_turn(
            model=model,
            tools=ToolRegistry([]),
            messages=_messages(),
            cwd=".",
            enable_work_chain=False,
            event_sink=sink,
        )

    assert raised.value is interrupt
    assert model.calls == 1


def test_work_chain_disabled_without_sink_skips_observation_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("observation must be absent without a sink")

    monkeypatch.setattr(agent_loop_module, "new_model_operation_id", unexpected)
    monkeypatch.setattr(agent_loop_module.time, "monotonic", unexpected)
    monkeypatch.setattr(agent_loop_module, "project_model_usage", unexpected)
    model = AssistantModel()

    result = run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=_messages(),
        cwd=".",
        enable_work_chain=False,
        event_sink=None,
    )

    assert result[-1] == {"role": "assistant", "content": "ok"}
    assert model.calls == 1


@pytest.mark.parametrize(
    ("error", "failure_kind", "fallback_fragment"),
    [
        (ConnectionError("network-secret"), "network", "Network error"),
        (TimeoutError("timeout-secret"), "timeout", "Model API timeout"),
        (RuntimeError("provider-secret"), "provider_error", "Model API error"),
    ],
)
def test_work_chain_disabled_preserves_model_failure_fallback_and_duration(
    error: Exception,
    failure_kind: str,
    fallback_fragment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = iter((20.0, 20.125))
    monkeypatch.setattr(agent_loop_module.time, "monotonic", lambda: next(readings))
    model = RaisingModel(error)
    sink = RecordingSink()

    result = run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=_messages(),
        cwd=".",
        enable_work_chain=False,
        event_sink=sink,
    )

    assert fallback_fragment in result[-1]["content"]
    assert model.calls == 1
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.failed",
        "task.outcome",
    ]
    assert sink.events[1].payload == {
        "operationId": sink.events[0].payload["operationId"],  # type: ignore[index]
        "failureKind": failure_kind,
        "durationMs": 125,
    }


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit(9)])
def test_work_chain_disabled_propagates_model_interrupt_with_failure_duration(
    interrupt: BaseException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = iter((30.0, 30.25))
    monkeypatch.setattr(agent_loop_module.time, "monotonic", lambda: next(readings))
    model = RaisingModel(interrupt)
    sink = RecordingSink()

    with pytest.raises(type(interrupt)) as raised:
        run_agent_turn(
            model=model,
            tools=ToolRegistry([]),
            messages=_messages(),
            cwd=".",
            enable_work_chain=False,
            event_sink=sink,
        )

    assert raised.value is interrupt
    assert model.calls == 1
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.failed",
        "task.outcome",
    ]
    assert sink.events[1].payload == {
        "operationId": sink.events[0].payload["operationId"],  # type: ignore[index]
        "failureKind": "interrupted",
        "durationMs": 250,
    }


def test_work_chain_disabled_executes_tools_and_callbacks_once() -> None:
    permission_sentinel = object()
    tool_calls: list[tuple[dict[str, object], object]] = []
    callbacks: list[tuple[str, str, object]] = []

    def run_echo(input_data: dict, context) -> ToolResult:
        tool_calls.append((input_data, context.permissions))
        return ToolResult(ok=True, output=f"echo:{input_data['text']}")

    tools = ToolRegistry(
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
                calls=[
                    {
                        "id": "call-1",
                        "toolName": "echo",
                        "input": {"text": "hi"},
                    }
                ],
            ),
            AgentStep(type="assistant", content="done"),
        ]
    )
    sink = RecordingSink()

    result = run_agent_turn(
        model=model,
        tools=tools,
        messages=_messages(),
        cwd=".",
        enable_work_chain=False,
        permissions=permission_sentinel,  # type: ignore[arg-type]
        event_sink=sink,
        on_tool_start=lambda name, value: callbacks.append(("start", name, value)),
        on_tool_result=lambda name, output, error: callbacks.append(
            ("result", name, (output, error))
        ),
        on_assistant_message=lambda content: callbacks.append(
            ("assistant", content, None)
        ),
    )

    assert result[-1] == {"role": "assistant", "content": "done"}
    assert model.calls == 2
    assert tool_calls == [({"text": "hi"}, permission_sentinel)]
    assert callbacks == [
        ("start", "echo", {"text": "hi"}),
        ("result", "echo", ("echo:hi", False)),
        ("assistant", "done", None),
    ]
    assert [message["role"] for message in result].count("assistant_tool_call") == 1
    assert [message["role"] for message in result].count("tool_result") == 1
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.completed",
        "model.costed",
        "model.started",
        "model.completed",
        "model.costed",
        "working_memory.observed",
        "task.outcome",
    ]


def test_work_chain_disabled_does_not_access_memory_manager() -> None:
    class InaccessibleMemoryManager:
        def __getattribute__(self, name: str) -> Any:
            raise AssertionError(f"disabled path accessed memory manager: {name}")

    model = AssistantModel()

    result = run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=_messages(),
        cwd=".",
        enable_work_chain=False,
        memory_manager=InaccessibleMemoryManager(),  # type: ignore[arg-type]
    )

    assert result[-1] == {"role": "assistant", "content": "ok"}
    assert model.calls == 1
