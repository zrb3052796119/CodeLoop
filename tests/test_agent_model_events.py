from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import minicode.agent_loop as agent_loop_module
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import ContextManager
from minicode.context_compactor import (
    CompactStrategy,
    CompactTrigger,
    CompactionResult,
)
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.types import AgentStep, ChatMessage, ModelUsage


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


class ScriptedModel:
    def __init__(
        self,
        results: list[AgentStep | BaseException],
        *,
        catalog_model_key: str | None = None,
    ) -> None:
        self.results = results
        self.calls = 0
        if catalog_model_key is not None:
            self.catalog_model_key = catalog_model_key

    def next(self, _messages: list[ChatMessage], on_stream_chunk=None) -> AgentStep:
        result = self.results[self.calls]
        self.calls += 1
        if isinstance(result, BaseException):
            raise result
        return result


def _run(model: ScriptedModel, sink=None, tools: ToolRegistry | None = None):
    return run_agent_turn(
        model=model,
        tools=tools or ToolRegistry([]),
        messages=[{"role": "system", "content": "system"}],
        cwd=".",
        event_sink=sink,
    )


def test_explicit_tool_verification_is_emitted_before_task_outcome() -> None:
    def run_verifier(_input_data: dict, _context) -> ToolResult:
        return ToolResult(
            ok=True,
            output="unpersisted verifier output",
            verification={
                "verificationVersion": 1,
                "kind": "tests",
                "outcome": "passed",
                "source": "test_runner",
            },
        )

    tools = ToolRegistry(
        [
            ToolDefinition(
                name="test_runner",
                description="trusted verifier",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=run_verifier,
            )
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "verify-call",
                        "toolName": "test_runner",
                        "input": {},
                    }
                ],
            ),
            AgentStep(type="assistant", content="done"),
        ]
    )
    sink = RecordingSink()

    _run(model, sink, tools)

    event_types = [event.event_type for event in sink.events]
    assert event_types.count("task.verified") == 1
    assert event_types.index("task.verified") < event_types.index("task.outcome")
    verification = next(
        event for event in sink.events if event.event_type == "task.verified"
    )
    assert verification.step == 1
    assert verification.payload == {
        "verificationVersion": 1,
        "kind": "tests",
        "outcome": "passed",
        "source": "test_runner",
    }


def test_malformed_tool_verification_is_ignored_without_changing_result() -> None:
    def run_verifier(_input_data: dict, _context) -> ToolResult:
        return ToolResult(
            ok=True,
            output="ok",
            verification={
                "verificationVersion": 1,
                "kind": "tests",
                "outcome": "passed",
                "source": "test_runner",
                "output": "must not persist",
            },
        )

    tools = ToolRegistry(
        [
            ToolDefinition(
                name="untrusted_verifier",
                description="untrusted verifier",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=run_verifier,
            )
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "verify-call",
                        "toolName": "untrusted_verifier",
                        "input": {},
                    }
                ],
            ),
            AgentStep(type="assistant", content="done"),
        ]
    )
    sink = RecordingSink()

    messages = _run(model, sink, tools)

    assert messages[-1]["content"] == "done"
    assert "task.verified" not in {
        event.event_type for event in sink.events
    }


def test_tool_cannot_spoof_another_verifier_source() -> None:
    def run_spoof(_input_data: dict, _context) -> ToolResult:
        return ToolResult(
            ok=True,
            output="ok",
            verification={
                "verificationVersion": 1,
                "kind": "tests",
                "outcome": "passed",
                "source": "test_runner",
            },
        )

    tools = ToolRegistry(
        [
            ToolDefinition(
                name="echo",
                description="not a verifier",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=run_spoof,
            )
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "spoof-call",
                        "toolName": "echo",
                        "input": {},
                    }
                ],
            ),
            AgentStep(type="assistant", content="done"),
        ]
    )
    sink = RecordingSink()

    _run(model, sink, tools)

    assert "task.verified" not in {
        event.event_type for event in sink.events
    }


def test_each_real_model_call_has_unique_paired_started_and_completed_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = iter((1.0, 1.125, 2.0, 2.250))
    monkeypatch.setattr(agent_loop_module.time, "monotonic", lambda: next(readings))
    def run_echo(input_data: dict, _context) -> ToolResult:
        return ToolResult(ok=True, output=f"echo:{input_data['text']}")

    tools = ToolRegistry(
        [
            ToolDefinition(
                name="echo",
                description="echo",
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
                        "id": "provider-call-secret",
                        "toolName": "echo",
                        "input": {"text": "prompt-output-secret"},
                    }
                ],
            ),
            AgentStep(
                type="assistant",
                content="Assistant body password=assistant-secret",
            ),
        ]
    )
    sink = RecordingSink()

    messages = _run(model, sink, tools)

    assert messages[-1]["role"] == "assistant"
    assert model.calls == 2
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
    assert [event.step for event in sink.events] == [1, 1, 1, 2, 2, 2, 2, 2]
    first_id = sink.events[0].payload["operationId"]  # type: ignore[index]
    second_id = sink.events[3].payload["operationId"]  # type: ignore[index]
    assert first_id == sink.events[1].payload["operationId"]  # type: ignore[index]
    assert first_id == sink.events[2].payload["operationId"]  # type: ignore[index]
    assert second_id == sink.events[4].payload["operationId"]  # type: ignore[index]
    assert second_id == sink.events[5].payload["operationId"]  # type: ignore[index]
    assert first_id != second_id
    assert {key: sink.events[1].payload[key] for key in (  # type: ignore[index]
        "operationId", "resultType", "contentPresent", "toolCallCount"
    )} == {
        "operationId": first_id,
        "resultType": "tool_calls",
        "contentPresent": False,
        "toolCallCount": 1,
    }
    assert {key: sink.events[4].payload[key] for key in (  # type: ignore[index]
        "operationId", "resultType", "contentPresent", "toolCallCount"
    )} == {
        "operationId": second_id,
        "resultType": "assistant",
        "contentPresent": True,
        "toolCallCount": 0,
    }
    assert sink.events[1].payload["usage"]["source"] == "unavailable"  # type: ignore[index]
    assert sink.events[4].payload["usage"]["source"] == "unavailable"  # type: ignore[index]
    assert sink.events[1].payload["durationMs"] == 125  # type: ignore[index]
    assert sink.events[4].payload["durationMs"] == 250  # type: ignore[index]
    serialized = str(sink.events)
    for forbidden in (
        "provider-call-secret",
        "prompt-output-secret",
        "assistant-secret",
        "messages",
    ):
        assert forbidden not in serialized


def test_empty_normal_result_completes_and_retry_uses_new_operation() -> None:
    model = ScriptedModel(
        [
            AgentStep(type="assistant", content=""),
            AgentStep(type="assistant", content="done"),
        ]
    )
    sink = RecordingSink()

    messages = _run(model, sink)

    assert messages[-1] == {"role": "assistant", "content": "done"}
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
    assert sink.events[1].payload["contentPresent"] is False  # type: ignore[index]
    assert sink.events[4].payload["contentPresent"] is True  # type: ignore[index]
    assert sink.events[0].payload["operationId"] != sink.events[3].payload[  # type: ignore[index]
        "operationId"
    ]


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit(9)])
def test_model_interrupt_emits_failed_and_propagates_same_object(
    interrupt: BaseException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = iter((2.0, 2.125))
    monkeypatch.setattr(agent_loop_module.time, "monotonic", lambda: next(readings))
    model = ScriptedModel([interrupt])
    sink = RecordingSink()

    with pytest.raises(type(interrupt)) as raised:
        _run(model, sink)

    assert raised.value is interrupt
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.failed",
        "task.outcome",
    ]
    assert sink.events[0].payload["operationId"] == sink.events[1].payload[  # type: ignore[index]
        "operationId"
    ]
    assert sink.events[1].payload["failureKind"] == "interrupted"  # type: ignore[index]
    assert sink.events[1].payload["durationMs"] == 125  # type: ignore[index]


@pytest.mark.parametrize(
    ("error", "failure_kind", "fallback_fragment"),
    [
        (ConnectionError("Bearer network-secret"), "network", "Network error"),
        (TimeoutError("password=timeout-secret"), "timeout", "Model API timeout"),
        (RuntimeError("api_key=provider-secret"), "provider_error", "Model API error"),
    ],
)
def test_model_failures_use_fixed_safe_classification_and_preserve_fallback(
    error: Exception,
    failure_kind: str,
    fallback_fragment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = iter((20.0, 50.001))
    monkeypatch.setattr(agent_loop_module.time, "monotonic", lambda: next(readings))
    model = ScriptedModel([error])
    sink = RecordingSink()

    messages = _run(model, sink)

    assert fallback_fragment in messages[-1]["content"]
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.failed",
        "task.outcome",
    ]
    assert sink.events[1].payload == {
        "operationId": sink.events[0].payload["operationId"],  # type: ignore[index]
        "failureKind": failure_kind,
        "durationMs": 30_001,
    }
    serialized = str(sink.events)
    for secret in ("network-secret", "timeout-secret", "provider-secret"):
        assert secret not in serialized


def test_none_normal_and_failing_sinks_preserve_messages_and_model_calls() -> None:
    class FailingSink:
        def emit(self, *_args, **_kwargs) -> None:
            raise RuntimeError("password=sink-secret")

    results = []
    call_counts = []
    for sink in (None, RecordingSink(), FailingSink()):
        model = ScriptedModel([AgentStep(type="assistant", content="same")])
        results.append(_run(model, sink))
        call_counts.append(model.calls)

    assert results[0] == results[1] == results[2]
    assert call_counts == [1, 1, 1]


def test_completed_and_costed_event_writes_are_independent() -> None:
    class CompletedFailingSink(RecordingSink):
        def emit(self, event_type: str, *, step=None, payload=None) -> None:
            if event_type == "model.completed":
                raise RuntimeError("password=completed-write-secret")
            super().emit(event_type, step=step, payload=payload)

    model = ScriptedModel(
        [
            AgentStep(
                type="assistant",
                content="same",
                usage=ModelUsage(
                    input_tokens=10,
                    output_tokens=2,
                    cache_read_tokens=0,
                    source="provider",
                ),
            )
        ],
        catalog_model_key="openai/gpt-4o-mini",
    )
    sink = CompletedFailingSink()

    messages = _run(model, sink)

    assert messages[-1] == {"role": "assistant", "content": "same"}
    assert model.calls == 1
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.costed",
        "working_memory.observed",
        "task.outcome",
    ]
    assert sink.events[1].payload["status"] == "priced"  # type: ignore[index]
    assert "secret" not in str(sink.events)


def test_context_recovery_retry_gets_new_step_and_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ScriptedModel(
        [
            RuntimeError("prompt too long password=recovery-secret"),
            AgentStep(type="assistant", content="recovered"),
        ]
    )
    sink = RecordingSink()
    recovery_calls: list[str] = []

    def recover(_self, messages, error_text):
        recovery_calls.append(error_text)
        result = CompactionResult(
            success=True,
            strategy=CompactStrategy.REACTIVE,
            trigger=CompactTrigger.REACTIVE,
            messages=messages,
            tokens_freed=10,
            summary_text="password=summary-secret",
            error="password=error-secret",
        )
        return messages, result

    monkeypatch.setattr(
        agent_loop_module.ContextCyberneticsOrchestrator,
        "try_reactive_recover",
        recover,
    )

    messages = run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "system"}],
        cwd=".",
        context_manager=ContextManager(model="default"),
        event_sink=sink,
    )

    assert messages[-1] == {"role": "assistant", "content": "recovered"}
    assert model.calls == 2
    assert len(recovery_calls) == 1
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.failed",
        "recovery.started",
        "context.compacted",
        "recovery.completed",
        "model.started",
        "model.completed",
        "model.costed",
        "working_memory.observed",
        "task.outcome",
    ]
    assert [event.step for event in sink.events] == [
        1, 1, 1, 1, 1, 2, 2, 2, 2, 2
    ]
    assert sink.events[1].payload["failureKind"] == "provider_error"  # type: ignore[index]
    context_operation_id = sink.events[2].payload["contextOperationId"]  # type: ignore[index]
    assert context_operation_id == sink.events[3].payload["contextOperationId"]  # type: ignore[index]
    assert context_operation_id == sink.events[4].payload["contextOperationId"]  # type: ignore[index]
    assert sink.events[4].payload["outcome"] == "recovered"  # type: ignore[index]
    assert sink.events[0].payload["operationId"] != sink.events[5].payload[  # type: ignore[index]
        "operationId"
    ]
    assert "secret" not in str(sink.events)


def test_model_switcher_retry_gets_new_step_and_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = iter((10.0, 10.1, 20.0, 20.2))
    monkeypatch.setattr(agent_loop_module.time, "monotonic", lambda: next(readings))
    initial_model = ScriptedModel(
        [RuntimeError("provider unavailable")],
        catalog_model_key="openai/gpt-4o",
    )
    fallback_model = ScriptedModel(
        [
            AgentStep(
                type="assistant",
                content="switched",
                usage=ModelUsage(
                    input_tokens=10,
                    output_tokens=2,
                    cache_read_tokens=0,
                    source="provider",
                ),
            )
        ],
        catalog_model_key="openai/gpt-4o-mini",
    )
    sink = RecordingSink()
    switch_calls: list[tuple[str, str]] = []

    class Switcher:
        def switch_to(self, name: str, *, reason: str):
            switch_calls.append((name, reason))
            return SimpleNamespace(
                success=True,
                adapter=fallback_model,
                new_model="fallback",
            )

    class CostControl:
        def get_stats(self):
            return {
                "cycles_executed": 0,
                "sensor": {"cost_per_min": 0.0},
                "pid": {"last_output": None},
                "adjustment": None,
            }

    class Orchestrator:
        def __init__(self) -> None:
            self.feedback = None
            self.cyber_supervisor = None
            self.stability = None
            self.adaptive_tuner = None
            self.state_observer = None
            self.decoupling = None
            self.predictive = None
            self.progress = None
            self.model_ctrl = None
            self.smart_router = None
            self.reflection = None
            self.model_switcher = Switcher()
            self.cost_control = CostControl()
            self.healing = None

        def initialize(self, _model, _tools, _runtime) -> None:
            return None

        def wire_healing(self, _scheduler, _compactor) -> None:
            return None

        def step_start(self, **_kwargs) -> None:
            return None

    monkeypatch.setattr(agent_loop_module, "CyberneticOrchestrator", Orchestrator)

    messages = run_agent_turn(
        model=initial_model,
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "system"}],
        cwd=".",
        event_sink=sink,
    )

    assert messages[-1] == {"role": "assistant", "content": "switched"}
    assert initial_model.calls == 1
    assert fallback_model.calls == 1
    assert len(switch_calls) == 1
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.failed",
        "model.started",
        "model.completed",
        "model.costed",
        "working_memory.observed",
        "task.outcome",
    ]
    assert [event.step for event in sink.events] == [1, 1, 2, 2, 2, 2, 2]
    assert sink.events[0].payload["operationId"] != sink.events[2].payload[  # type: ignore[index]
        "operationId"
    ]
    assert sink.events[1].payload["durationMs"] == 100  # type: ignore[index]
    assert sink.events[3].payload["durationMs"] == 200  # type: ignore[index]
    assert sink.events[4].payload["catalogModelKey"] == "openai/gpt-4o-mini"  # type: ignore[index]


def test_model_completed_uses_same_agent_step_usage_and_monotonic_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings = iter((10.0, 10.842))
    monkeypatch.setattr(agent_loop_module.time, "monotonic", lambda: next(readings))
    usage = ModelUsage(
        input_tokens=1_200,
        output_tokens=180,
        cache_read_tokens=900,
        cache_creation_tokens=0,
        source="provider",
    )
    model = ScriptedModel(
        [AgentStep(type="assistant", content="done", usage=usage)],
        catalog_model_key="openai/gpt-4o",
    )
    sink = RecordingSink()

    messages = run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "system"}],
        cwd=".",
        event_sink=sink,
    )

    assert messages[-1] == {"role": "assistant", "content": "done"}
    assert model.calls == 1
    completed = sink.events[1]
    assert completed.event_type == "model.completed"
    assert completed.payload["usage"] == {  # type: ignore[index]
        "source": "provider",
        "inputTokens": 1_200,
        "outputTokens": 180,
        "cacheReadTokens": 900,
        "cacheCreationTokens": 0,
    }
    assert completed.payload["durationMs"] == 842  # type: ignore[index]
    costed = sink.events[2]
    assert costed.event_type == "model.costed"
    assert costed.payload == {
        "costVersion": 1,
        "operationId": completed.payload["operationId"],  # type: ignore[index]
        "status": "priced",
        "quality": "provider_usage_catalog_rate",
        "currency": "USD",
        "catalogId": "minicode-pricing-2026-07-17-v1",
        "catalogModelKey": "openai/gpt-4o",
        "amountNanoUsd": 3_675_000,
        "components": {
            "inputNanoUsd": 750_000,
            "outputNanoUsd": 1_800_000,
            "cacheReadNanoUsd": 1_125_000,
            "cacheCreationNanoUsd": 0,
        },
    }


def test_successful_unknown_model_emits_safe_unavailable_cost_without_identity() -> None:
    secret_model = "custom-sk-secret-model"
    model = ScriptedModel(
        [
            AgentStep(
                type="assistant",
                content="done",
                usage=ModelUsage(
                    input_tokens=1,
                    output_tokens=1,
                    cache_read_tokens=0,
                    source="provider",
                ),
            )
        ],
        catalog_model_key=secret_model,
    )
    sink = RecordingSink()

    assert _run(model, sink)[-1]["content"] == "done"
    assert [event.event_type for event in sink.events] == [
        "model.started",
        "model.completed",
        "model.costed",
        "working_memory.observed",
        "task.outcome",
    ]
    assert sink.events[2].payload == {
        "costVersion": 1,
        "operationId": sink.events[1].payload["operationId"],  # type: ignore[index]
        "status": "unavailable",
        "quality": "unavailable",
        "currency": "USD",
        "catalogId": "minicode-pricing-2026-07-17-v1",
        "reason": "model_unpriced",
    }
    assert secret_model not in str(sink.events)


def test_pricing_projection_failure_is_isolated_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_loop_module,
        "project_model_cost_event",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("pricing-secret")),
    )
    model = ScriptedModel([AgentStep(type="assistant", content="same")])
    sink = RecordingSink()

    messages = _run(model, sink)

    assert messages[-1] == {"role": "assistant", "content": "same"}
    assert model.calls == 1
    assert sink.events[2].event_type == "model.costed"
    assert sink.events[2].payload["reason"] == "pricing_failed"  # type: ignore[index]
    assert "pricing-secret" not in str(sink.events)


def test_no_event_sink_skips_operation_id_clock_and_usage_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*_args, **_kwargs):
        raise AssertionError("Dashboard observation must remain disabled")

    monkeypatch.setattr(agent_loop_module, "new_model_operation_id", unexpected)
    monkeypatch.setattr(agent_loop_module.time, "monotonic", unexpected)
    monkeypatch.setattr(agent_loop_module, "project_model_usage", unexpected)
    monkeypatch.setattr(agent_loop_module, "project_model_cost_event", unexpected)
    model = ScriptedModel(
        [
            AgentStep(
                type="assistant",
                content="same",
                usage=ModelUsage(input_tokens=1, source="provider"),
            )
        ]
    )

    messages = _run(model, None)

    assert messages[-1] == {"role": "assistant", "content": "same"}
    assert model.calls == 1


def test_clock_and_usage_projection_failures_do_not_replace_model_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_loop_module.time,
        "monotonic",
        lambda: (_ for _ in ()).throw(RuntimeError("clock-secret")),
    )
    monkeypatch.setattr(
        agent_loop_module,
        "project_model_usage",
        lambda _usage: (_ for _ in ()).throw(RuntimeError("usage-secret")),
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="assistant",
                content="same",
                usage=ModelUsage(input_tokens=9, source="provider"),
            )
        ]
    )
    sink = RecordingSink()

    messages = _run(model, sink)

    assert messages[-1] == {"role": "assistant", "content": "same"}
    assert model.calls == 1
    assert sink.events[1].payload["usage"] == {  # type: ignore[index]
        "source": "unavailable",
        "inputTokens": None,
        "outputTokens": None,
        "cacheReadTokens": None,
        "cacheCreationTokens": None,
    }
    assert "durationMs" not in sink.events[1].payload  # type: ignore[operator]
    assert "secret" not in str(sink.events)
