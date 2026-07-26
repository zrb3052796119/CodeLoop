from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

import minicode.agent_loop as agent_loop_module
from minicode.agent_loop import run_agent_turn
from minicode.context_compactor import (
    CompactStrategy,
    CompactTrigger,
    CompactionResult,
)
from minicode.run_events import (
    new_context_operation_id,
    project_context_compaction_event,
    project_recovery_completed_event,
    project_recovery_started_event,
)
from minicode.context_manager import ContextManager
from minicode.tooling import ToolRegistry
from minicode.types import AgentStep


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, int | None, object]] = []

    def emit(self, event_type: str, *, step=None, payload=None) -> None:
        self.events.append((event_type, step, payload))


class AssistantModel:
    def __init__(self) -> None:
        self.received: list[list[dict[str, object]]] = []

    def next(self, messages, on_stream_chunk=None):
        self.received.append([dict(message) for message in messages])
        return AgentStep(type="assistant", content="done")


class ScriptedModel:
    def __init__(self, results: list[AgentStep | BaseException]) -> None:
        self.results = results
        self.calls = 0

    def next(self, _messages, on_stream_chunk=None):
        result = self.results[self.calls]
        self.calls += 1
        if isinstance(result, BaseException):
            raise result
        return result


def test_context_compaction_projector_emits_only_safe_effective_facts() -> None:
    operation_id = new_context_operation_id()
    result = CompactionResult(
        success=True,
        strategy=CompactStrategy.FULL,
        trigger=CompactTrigger.AUTO,
        messages=[{"role": "system", "content": "do-not-persist"}],
        tokens_freed=1_200,
        summary_text="secret-summary",
        error="secret-error",
    )

    assert re.fullmatch(r"ctxop_[0-9a-f]{32}", operation_id)
    assert project_context_compaction_event(
        result,
        context_operation_id=operation_id,
        path="pre_request_compactor",
        messages_before=32,
        messages_after=18,
    ) == {
        "contextVersion": 1,
        "contextOperationId": operation_id,
        "path": "pre_request_compactor",
        "trigger": "auto",
        "strategy": "full",
        "effective": True,
        "tokensFreed": 1_200,
        "messagesBefore": 32,
        "messagesAfter": 18,
        "messagesRemoved": 14,
    }


def test_recovery_projectors_share_safe_operation_facts() -> None:
    operation_id = new_context_operation_id()
    result = CompactionResult(
        success=True,
        strategy=CompactStrategy.REACTIVE,
        trigger=CompactTrigger.REACTIVE,
        messages=[{"role": "system", "content": "secret"}],
        tokens_freed=900,
        summary_text="secret-summary",
        error="secret-overflow-error",
    )

    assert project_recovery_started_event(
        context_operation_id=operation_id,
        kind="cybernetic",
    ) == {
        "recoveryVersion": 1,
        "contextOperationId": operation_id,
        "kind": "cybernetic",
        "reason": "context_overflow",
    }
    assert project_recovery_completed_event(
        result,
        context_operation_id=operation_id,
        kind="cybernetic",
        messages_before=12,
        messages_after=7,
    ) == {
        "recoveryVersion": 1,
        "contextOperationId": operation_id,
        "kind": "cybernetic",
        "outcome": "recovered",
        "tokensFreed": 900,
        "messagesBefore": 12,
        "messagesAfter": 7,
    }


def test_context_manager_fallback_omits_unreliable_token_count() -> None:
    operation_id = new_context_operation_id()

    assert project_context_compaction_event(
        None,
        context_operation_id=operation_id,
        path="context_manager_auto",
        messages_before=9,
        messages_after=6,
        trigger="auto",
        strategy="context_manager",
    ) == {
        "contextVersion": 1,
        "contextOperationId": operation_id,
        "path": "context_manager_auto",
        "trigger": "auto",
        "strategy": "context_manager",
        "effective": True,
        "messagesBefore": 9,
        "messagesAfter": 6,
        "messagesRemoved": 3,
    }


def test_not_recovered_completion_has_no_compaction_claim() -> None:
    operation_id = new_context_operation_id()

    assert project_recovery_completed_event(
        None,
        context_operation_id=operation_id,
        kind="compactor",
        messages_before=4,
        messages_after=4,
    ) == {
        "recoveryVersion": 1,
        "contextOperationId": operation_id,
        "kind": "compactor",
        "outcome": "not_recovered",
        "messagesBefore": 4,
        "messagesAfter": 4,
    }


@pytest.mark.parametrize(
    "override",
    [
        {"context_operation_id": "ctxop_bad"},
        {"path": "arbitrary-secret-path"},
        {"messages_before": True},
        {"messages_after": -1},
        {"messages_before": 4, "messages_after": 5},
    ],
)
def test_context_projector_rejects_invalid_ids_enums_and_counts(
    override: dict[str, object],
) -> None:
    result = CompactionResult(
        success=True,
        strategy=CompactStrategy.FULL,
        trigger=CompactTrigger.AUTO,
        messages=[],
        tokens_freed=1,
    )
    arguments: dict[str, object] = {
        "context_operation_id": new_context_operation_id(),
        "path": "pre_request_cybernetic",
        "messages_before": 4,
        "messages_after": 2,
    }
    arguments.update(override)

    with pytest.raises(ValueError):
        project_context_compaction_event(result, **arguments)


@pytest.mark.parametrize(
    ("success", "tokens_freed"),
    [(False, 100), (True, 0)],
)
def test_ineffective_results_cannot_be_projected_as_compaction(
    success: bool, tokens_freed: int
) -> None:
    result = CompactionResult(
        success=success,
        strategy=CompactStrategy.FULL,
        trigger=CompactTrigger.AUTO,
        messages=[],
        tokens_freed=tokens_freed,
    )

    with pytest.raises(ValueError):
        project_context_compaction_event(
            result,
            context_operation_id=new_context_operation_id(),
            path="pre_request_compactor",
            messages_before=2,
            messages_after=1,
        )


def test_agent_observes_effective_pre_request_cybernetic_compaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compacted = [{"role": "system", "content": "compacted"}]
    result = CompactionResult(
        success=True,
        strategy=CompactStrategy.FULL,
        trigger=CompactTrigger.AUTO,
        messages=compacted,
        tokens_freed=120,
    )

    def run_cycle(_self, _messages, **_kwargs):
        return compacted, result, None

    monkeypatch.setattr(
        agent_loop_module.ContextCyberneticsOrchestrator,
        "run_cycle",
        run_cycle,
    )
    model = AssistantModel()
    sink = RecordingSink()

    returned = run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "request"},
        ],
        cwd=".",
        context_manager=ContextManager(model="default"),
        event_sink=sink,
    )

    assert returned[-1] == {"role": "assistant", "content": "done"}
    assert model.received == [[{"role": "system", "content": "compacted"}]]
    context_event = sink.events[0]
    assert context_event[0] == "context.compacted"
    assert context_event[1] is None
    assert context_event[2] == {
        "contextVersion": 1,
        "contextOperationId": context_event[2]["contextOperationId"],
        "path": "pre_request_cybernetic",
        "trigger": "auto",
        "strategy": "full",
        "effective": True,
        "tokensFreed": 120,
        "messagesBefore": 2,
        "messagesAfter": 1,
        "messagesRemoved": 1,
    }


def test_agent_observes_effective_direct_compactor_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = CompactionResult(
        success=True,
        strategy=CompactStrategy.MICROCOMPACT,
        trigger=CompactTrigger.MICROCOMPACT_TIME,
        messages=[{"role": "system", "content": "smaller"}],
        tokens_freed=12,
    )
    monkeypatch.setattr(
        agent_loop_module,
        "ContextCyberneticsOrchestrator",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_loop_module.ContextCompactor,
        "process_request",
        lambda _self, _messages: result,
    )
    sink = RecordingSink()

    returned = run_agent_turn(
        model=AssistantModel(),
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "system"}],
        cwd=".",
        context_manager=ContextManager(model="default"),
        event_sink=sink,
    )

    assert returned[-1] == {"role": "assistant", "content": "done"}
    assert sink.events[0][0] == "context.compacted"
    assert sink.events[0][2]["path"] == "pre_request_compactor"
    assert sink.events[0][2]["strategy"] == "microcompact"
    assert sink.events[0][2]["trigger"] == "microcompact_time"


def test_agent_observes_changed_context_manager_fallback_without_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ContextManager(model="default")
    compacted = [{"role": "system", "content": "compacted"}]
    monkeypatch.setattr(manager, "should_auto_compact", lambda: True)

    def compact_messages():
        manager.messages = compacted
        return compacted

    monkeypatch.setattr(manager, "compact_messages", compact_messages)
    sink = RecordingSink()

    returned = run_agent_turn(
        model=AssistantModel(),
        tools=ToolRegistry([]),
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "request"},
        ],
        cwd=".",
        context_manager=manager,
        enable_work_chain=False,
        event_sink=sink,
    )

    assert returned[-1] == {"role": "assistant", "content": "done"}
    assert sink.events[0][0] == "context.compacted"
    assert sink.events[0][2]["path"] == "context_manager_auto"
    assert sink.events[0][2]["strategy"] == "context_manager"
    assert "tokensFreed" not in sink.events[0][2]


def test_cybernetic_not_recovered_emits_completed_without_compaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_loop_module.ContextCyberneticsOrchestrator,
        "try_reactive_recover",
        lambda _self, messages, _error: (messages, None),
    )
    from minicode.model_switcher import ModelSwitcher

    monkeypatch.setattr(
        ModelSwitcher,
        "switch_to",
        lambda *_args, **_kwargs: type(
            "SwitchResult",
            (),
            {"success": False, "adapter": None},
        )(),
    )
    sink = RecordingSink()

    returned = run_agent_turn(
        model=ScriptedModel([RuntimeError("prompt too long secret-overflow")]),
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "system"}],
        cwd=".",
        context_manager=ContextManager(model="default"),
        event_sink=sink,
    )

    assert "Model API error" in returned[-1]["content"]
    assert [event[0] for event in sink.events] == [
        "model.started",
        "model.failed",
        "recovery.started",
        "recovery.completed",
    ]
    assert sink.events[3][2]["outcome"] == "not_recovered"
    assert sink.events[2][2]["contextOperationId"] == sink.events[3][2][
        "contextOperationId"
    ]
    assert "secret-overflow" not in str(sink.events)


def test_direct_compactor_recovery_emits_recovered_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compacted = [{"role": "system", "content": "smaller"}]
    recovered = CompactionResult(
        success=True,
        strategy=CompactStrategy.REACTIVE,
        trigger=CompactTrigger.REACTIVE,
        messages=compacted,
        tokens_freed=100,
    )
    monkeypatch.setattr(
        agent_loop_module,
        "ContextCyberneticsOrchestrator",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_loop_module.ContextCompactor,
        "process_request",
        lambda _self, messages: CompactionResult(
            success=False,
            strategy=CompactStrategy.FULL,
            trigger=CompactTrigger.AUTO,
            messages=messages,
        ),
    )
    monkeypatch.setattr(
        agent_loop_module.ContextCompactor,
        "reactive_recover",
        lambda _self, _messages, _error: recovered,
    )
    sink = RecordingSink()

    returned = run_agent_turn(
        model=ScriptedModel(
            [
                RuntimeError("prompt exceeds context password=overflow-secret"),
                AgentStep(type="assistant", content="recovered"),
            ]
        ),
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "system"}],
        cwd=".",
        context_manager=ContextManager(model="default"),
        event_sink=sink,
    )

    assert returned[-1] == {"role": "assistant", "content": "recovered"}
    event_types = [event[0] for event in sink.events]
    assert event_types == [
        "model.started",
        "model.failed",
        "recovery.started",
        "context.compacted",
        "recovery.completed",
        "model.started",
        "model.completed",
        "model.costed",
        "working_memory.observed",
    ]
    context_id = sink.events[2][2]["contextOperationId"]
    assert sink.events[3][2]["contextOperationId"] == context_id
    assert sink.events[4][2]["contextOperationId"] == context_id
    assert sink.events[2][2]["kind"] == "compactor"
    assert "overflow-secret" not in str(sink.events)


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("recovery failure"), KeyboardInterrupt(), SystemExit(7)],
)
def test_recovery_exception_keeps_started_dangling_and_preserves_identity(
    failure: BaseException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_recovery(_self, _messages, _error):
        raise failure

    monkeypatch.setattr(
        agent_loop_module.ContextCyberneticsOrchestrator,
        "try_reactive_recover",
        fail_recovery,
    )
    sink = RecordingSink()

    with pytest.raises(type(failure)) as raised:
        run_agent_turn(
            model=ScriptedModel([RuntimeError("prompt too long")]),
            tools=ToolRegistry([]),
            messages=[{"role": "system", "content": "system"}],
            cwd=".",
            context_manager=ContextManager(model="default"),
            event_sink=sink,
        )

    assert raised.value is failure
    assert [event[0] for event in sink.events] == [
        "model.started",
        "model.failed",
        "recovery.started",
    ]


def test_no_sink_skips_context_id_and_projector_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compacted = [{"role": "system", "content": "smaller"}]
    result = CompactionResult(
        success=True,
        strategy=CompactStrategy.FULL,
        trigger=CompactTrigger.AUTO,
        messages=compacted,
        tokens_freed=10,
    )
    monkeypatch.setattr(
        agent_loop_module.ContextCyberneticsOrchestrator,
        "run_cycle",
        lambda _self, _messages, **_kwargs: (compacted, result, None),
    )
    monkeypatch.setattr(
        agent_loop_module,
        "new_context_operation_id",
        lambda: pytest.fail("no sink must not generate a Context operation ID"),
    )
    monkeypatch.setattr(
        agent_loop_module,
        "emit_context_compaction_safely",
        lambda *_args, **_kwargs: pytest.fail("no sink must not project Context"),
    )

    returned = run_agent_turn(
        model=AssistantModel(),
        tools=ToolRegistry([]),
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "request"},
        ],
        cwd=".",
        context_manager=ContextManager(model="default"),
        event_sink=None,
    )

    assert returned[-1] == {"role": "assistant", "content": "done"}


def test_ineffective_pre_request_result_generates_no_context_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = CompactionResult(
        success=False,
        strategy=CompactStrategy.FULL,
        trigger=CompactTrigger.AUTO,
        messages=[],
        tokens_freed=0,
    )
    monkeypatch.setattr(
        agent_loop_module.ContextCyberneticsOrchestrator,
        "run_cycle",
        lambda _self, messages, **_kwargs: (messages, result, None),
    )
    monkeypatch.setattr(
        agent_loop_module,
        "new_context_operation_id",
        lambda: pytest.fail("ineffective compaction must not get an operation ID"),
    )
    sink = RecordingSink()

    run_agent_turn(
        model=AssistantModel(),
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "system"}],
        cwd=".",
        context_manager=ContextManager(model="default"),
        event_sink=sink,
    )

    assert "context.compacted" not in [event[0] for event in sink.events]


def test_context_id_failure_does_not_change_effective_compaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compacted = [{"role": "system", "content": "smaller"}]
    result = CompactionResult(
        success=True,
        strategy=CompactStrategy.FULL,
        trigger=CompactTrigger.AUTO,
        messages=compacted,
        tokens_freed=10,
    )
    monkeypatch.setattr(
        agent_loop_module.ContextCyberneticsOrchestrator,
        "run_cycle",
        lambda _self, _messages, **_kwargs: (compacted, result, None),
    )
    monkeypatch.setattr(
        agent_loop_module,
        "new_context_operation_id",
        lambda: (_ for _ in ()).throw(RuntimeError("id-secret")),
    )
    model = AssistantModel()
    sink = RecordingSink()

    returned = run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "system"}],
        cwd=".",
        context_manager=ContextManager(model="default"),
        event_sink=sink,
    )

    assert returned[-1] == {"role": "assistant", "content": "done"}
    assert model.received == [[{"role": "system", "content": "smaller"}]]
    assert "context.compacted" not in [event[0] for event in sink.events]
    assert "id-secret" not in str(sink.events)


def test_feedback_forced_compaction_has_existing_mismatched_seam(
    tmp_path,
) -> None:
    compactor = agent_loop_module.ContextCompactor(workspace=tmp_path)
    assert not hasattr(compactor, "compact_messages")
    signal = SimpleNamespace(
        confidence=1.0,
        limit_max_steps=None,
        adjust_token_budget=1.0,
        reduce_parallelism=False,
        adjust_concurrency=0,
        increase_model_level=False,
        decrease_model_level=False,
        suggest_memory_persistence=False,
        recommend_skill_update=False,
        reduce_tool_timeout=None,
        increase_nudge_frequency=False,
        promote_pattern=None,
        force_compaction=True,
    )

    assert agent_loop_module._apply_control_signal(
        control_signal=signal,
        system_state=SimpleNamespace(),
        max_steps=9,
        tool_scheduler=agent_loop_module.ToolScheduler(),
        context_compactor=compactor,
        model_switcher=None,
    ) == 9
