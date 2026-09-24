from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest

import minicode.memory as memory_module
from minicode.agent_loop import AgentTurnDeadlineExceeded, run_agent_turn
from minicode.context_compactor import (
    AutoCompactConfig,
    AutoCompactDispatcher,
    CompactionMaterializationState,
    CompactionResult,
    CompactStrategy,
    CompactTrigger,
    ContextCompactor,
    LLMSummaryGenerator,
)
from minicode.context_manager import ContextManager
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline, MemoryPromptReceipt
from minicode.memory_store import MemoryStoreConflict
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.run_events import (
    new_context_operation_id,
    project_context_compaction_failure_event,
)
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.types import AgentStep


class _Summary:
    def summarize(self, _messages: list[dict[str, object]]) -> str:
        return (
            "The checkout investigation retained its verified rounding "
            "decision and current implementation state."
        )


def _pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MemoryPipeline, MemoryManager, str]:
    monkeypatch.setattr(
        memory_module,
        "MINI_CODE_DIR",
        tmp_path / "home" / ".mini-code",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "failure-recovery",
        "ROUND_ONCE_MARKER: sum decimal line amounts before final rounding.",
        tags=["checkout", "total", "rounding"],
        source="test",
    )
    assert entry is not None
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(
        workspace_path=str(workspace),
        enable_reranker=False,
        enable_vector=False,
    )
    return pipeline, manager, entry.id


def _long_messages(system_message: dict) -> list[dict]:
    return [
        dict(system_message),
        *[
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": (
                    f"checkout total rounding discussion {index} "
                    + "x" * 800
                ),
            }
            for index in range(24)
        ],
    ]


def test_prompt_receipt_replay_is_fully_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, manager, entry_id = _pipeline(tmp_path, monkeypatch)
    pipeline.read(
        "fix checkout total rounding",
        _record_retrieval=False,
    )
    result = pipeline.last_retrieval_result
    assert result is not None and result.rendered_ids == (entry_id,)
    receipt = MemoryPromptReceipt(
        prompt_text=result.prompt_text,
        selected_ids=result.selected_ids,
        rendered_ids=result.rendered_ids,
        authority_revision=result.authority_revision,
        receipt_id="receipt-replay-test",
    )
    messages = [{"role": "system", "content": "SYSTEM"}]
    original = list(messages)
    assert pipeline.materialize_prompt_receipt(
        receipt,
        lambda: messages[0].__setitem__(
            "content",
            messages[0]["content"] + "\n" + receipt.prompt_text,
        ),
        lambda: messages.__setitem__(slice(None), original),
    )

    assert pipeline.commit_prompt_receipt(receipt, record_retrieval=True) == (
        entry_id,
    )
    assert pipeline.commit_prompt_receipt(receipt, record_retrieval=True) == ()

    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.retrieval_count == 1
    assert stored.injection_count == 1


def test_prompt_receipt_id_cannot_be_reused_for_different_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, manager, entry_id = _pipeline(tmp_path, monkeypatch)
    pipeline.read(
        "fix checkout total rounding",
        _record_retrieval=False,
    )
    result = pipeline.last_retrieval_result
    assert result is not None and result.rendered_ids == (entry_id,)
    receipt = MemoryPromptReceipt(
        prompt_text=result.prompt_text,
        selected_ids=result.selected_ids,
        rendered_ids=result.rendered_ids,
        authority_revision=result.authority_revision,
        receipt_id="receipt-payload-binding-test",
    )

    assert pipeline.commit_prompt_receipt(receipt, record_retrieval=True) == (
        entry_id,
    )

    with pytest.raises(ValueError, match="different payload"):
        pipeline.commit_prompt_receipt(
            replace(receipt, prompt_text=receipt.prompt_text + "\nforged"),
            record_retrieval=True,
        )

    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.retrieval_count == 1
    assert stored.injection_count == 1


def test_distinct_prompt_receipts_keep_real_retrieval_accounting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, manager, entry_id = _pipeline(tmp_path, monkeypatch)
    pipeline.read(
        "fix checkout total rounding",
        _record_retrieval=False,
    )
    result = pipeline.last_retrieval_result
    assert result is not None and result.rendered_ids == (entry_id,)

    first = MemoryPromptReceipt(
        prompt_text=result.prompt_text,
        selected_ids=result.selected_ids,
        rendered_ids=result.rendered_ids,
        authority_revision=result.authority_revision,
        receipt_id="receipt-first-query",
    )
    second = MemoryPromptReceipt(
        prompt_text=result.prompt_text,
        selected_ids=result.selected_ids,
        rendered_ids=result.rendered_ids,
        authority_revision=result.authority_revision,
        receipt_id="receipt-second-query",
    )

    assert pipeline.commit_prompt_receipt(first, record_retrieval=True) == (
        entry_id,
    )
    assert pipeline.commit_prompt_receipt(second, record_retrieval=True) == ()

    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.retrieval_count == 2
    assert stored.injection_count == 1


def test_session_compaction_reuses_task_start_memory_without_duplicate_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, manager, entry_id = _pipeline(tmp_path, monkeypatch)
    messages = pipeline.inject(
        "fix checkout total rounding",
        [],
        [{"role": "system", "content": "SYSTEM"}],
    )
    assert str(messages[0]["content"]).count("ROUND_ONCE_MARKER") == 1
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_Summary(),
    )

    result = compactor.execute_strategy(
        _long_messages(messages[0]),
        CompactStrategy.SESSION_MEMORY,
    )

    assert result.effective is True
    assert result.memory_receipt is None
    assert (
        result.materialization_state
        is CompactionMaterializationState.NOT_REQUIRED
    )
    rendered = "\n".join(str(message.get("content", "")) for message in result.messages)
    assert rendered.count("ROUND_ONCE_MARKER") == 1
    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.injection_count == 1


def test_project_facts_survive_memory_authority_guard_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, manager, entry_id = _pipeline(tmp_path, monkeypatch)
    assert pipeline._project_facts is not None
    assert pipeline._project_facts.observe_dependencies(["tenacity"]) == 1

    def fail_guard(*_args, **_kwargs) -> bool:
        raise MemoryStoreConflict("controlled authority failure")

    monkeypatch.setattr(pipeline, "materialize_prompt_receipt", fail_guard)
    messages = pipeline.inject(
        "fix checkout total rounding",
        [],
        [{"role": "system", "content": "SYSTEM"}],
    )
    system = str(messages[0]["content"])
    assert "## Project Facts" in system
    assert "tenacity" in system
    assert "ROUND_ONCE_MARKER" not in system
    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.injection_count == 0


def test_project_facts_survive_lesson_retrieval_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, _manager, _entry_id = _pipeline(tmp_path, monkeypatch)
    assert pipeline._project_facts is not None
    assert pipeline._project_facts.observe_dependencies(["tenacity"]) == 1

    def fail_read(*_args, **_kwargs) -> str:
        raise OSError("controlled lesson-store read failure")

    monkeypatch.setattr(pipeline, "read", fail_read)
    messages = pipeline.inject(
        "fix checkout total rounding",
        [],
        [{"role": "system", "content": "SYSTEM"}],
    )

    system = str(messages[0]["content"])
    assert "## Project Facts" in system
    assert "tenacity" in system
    assert "ROUND_ONCE_MARKER" not in system


def test_pending_materialization_outcomes_are_strictly_bounded() -> None:
    dispatcher = AutoCompactDispatcher(context_window=10_000)
    first: CompactionResult | None = None
    for index in range(1_100):
        result = CompactionResult(
            success=True,
            strategy=CompactStrategy.SESSION_MEMORY,
            trigger=CompactTrigger.AUTO,
            messages=[],
            tokens_freed=1,
            memory_receipt=MemoryPromptReceipt(
                prompt_text="memory",
                rendered_ids=("entry",),
                authority_revision="revision",
                receipt_id=f"pending-{index}",
            ),
            materialization_state=CompactionMaterializationState.PENDING,
        )
        dispatcher._record_success_or_defer(result)
        if first is None:
            first = result

    assert len(dispatcher._materialization_outcomes) <= 1_024
    assert first is not None
    assert dispatcher.confirm_materialized(first) is True
    assert len(dispatcher._materialization_outcomes) <= 1_024


def test_process_request_reports_actual_session_strategy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, _manager, _entry_id = _pipeline(tmp_path, monkeypatch)
    compactor = ContextCompactor(
        context_window=12_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_Summary(),
    )

    result = compactor.process_request(
        _long_messages({"role": "system", "content": "SYSTEM"})
    )

    assert result.effective is True
    assert result.strategy is CompactStrategy.SESSION_MEMORY


def test_rejected_memory_candidate_reports_safe_prefix_token_savings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, _manager, _entry_id = _pipeline(tmp_path, monkeypatch)
    messages = _long_messages({"role": "system", "content": "SYSTEM"})
    messages.insert(
        -1,
        {
            "role": "tool_result",
            "toolName": "run_command",
            "toolUseId": "large",
            "content": "large checkout output " + "L" * 30_000,
        },
    )
    compactor = ContextCompactor(
        context_window=12_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_Summary(),
    )

    result = compactor.process_request(messages)
    assert result.materialization_state is CompactionMaterializationState.PENDING
    assert result.memory_free_tokens_freed > 0

    assert compactor.discard_unmaterialized(result, messages) is True
    assert result.partial_effective is True
    assert result.success is False
    assert result.tokens_freed == result.memory_free_tokens_freed


def test_partial_compaction_failure_event_reports_committed_safe_savings() -> None:
    result = CompactionResult(
        success=False,
        strategy=CompactStrategy.SESSION_MEMORY,
        trigger=CompactTrigger.AUTO,
        messages=[],
        tokens_freed=120,
        error="Memory authority changed before compaction install",
        materialization_state=CompactionMaterializationState.REJECTED,
        memory_free_tokens_freed=120,
        memory_free_strategy=CompactStrategy.TOOL_BUDGET,
        memory_free_messages_before=10,
        memory_free_messages_after=8,
    )

    payload = project_context_compaction_failure_event(
        result,
        context_operation_id=new_context_operation_id(),
        path="pre_request_compactor",
        consecutive_failures=1,
        circuit_breaker_tripped=False,
    )

    assert payload["partialEffective"] is True
    assert payload["partialStrategy"] == "tool_budget"
    assert payload["tokensFreed"] == 120


class _TenToolModel:
    model_id = "p2-control-signal-test"

    def __init__(self) -> None:
        self.calls = 0

    def next(self, _messages, on_stream_chunk=None, **_kwargs):
        self.calls += 1
        if self.calls <= 10:
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": f"tool-{self.calls}",
                        "toolName": "small_read",
                        "input": {"index": self.calls},
                    }
                ],
            )
        return AgentStep(type="assistant", content="done")


@pytest.mark.parametrize(
    ("signal", "expected"),
    [
        (
            TurnCancellationRequested("turn_00000000000000000000000000000000"),
            TurnCancellationRequested,
        ),
        (
            ModelCallDeadlineExceeded("controlled compaction deadline"),
            AgentTurnDeadlineExceeded,
        ),
    ],
)
def test_in_loop_compaction_never_swallows_control_signals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signal: BaseException,
    expected: type[BaseException],
) -> None:
    original_process = ContextCompactor.process_request

    def raise_in_loop(self, messages, **kwargs):
        tool_results = sum(
            message.get("role") == "tool_result" for message in messages
        )
        if tool_results >= 10:
            raise signal
        return original_process(self, messages, **kwargs)

    monkeypatch.setattr(ContextCompactor, "process_request", raise_in_loop)
    monkeypatch.setattr(
        AutoCompactDispatcher,
        "should_trigger",
        lambda _self, messages: sum(
            message.get("role") == "tool_result" for message in messages
        )
        >= 10,
    )
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="small_read",
                description="return one deterministic read result",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda value, _context: ToolResult(
                    ok=True,
                    output=f"result {value['index']}",
                ),
            )
        ]
    )

    with pytest.raises(expected):
        run_agent_turn(
            model=_TenToolModel(),
            tools=tools,
            messages=[
                {"role": "system", "content": "SYSTEM"},
                {"role": "user", "content": "inspect checkout behavior"},
            ],
            cwd=str(tmp_path),
            context_manager=ContextManager(context_window=7_000),
            enable_work_chain=True,
            max_steps=12,
        )


@pytest.mark.parametrize(
    "strategy",
    [CompactStrategy.SESSION_MEMORY, CompactStrategy.FULL],
)
@pytest.mark.parametrize(
    "signal",
    [
        TurnCancellationRequested("turn_11111111111111111111111111111111"),
        ModelCallDeadlineExceeded("controlled summary deadline"),
    ],
)
def test_compaction_strategies_propagate_summary_control_signals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strategy: CompactStrategy,
    signal: BaseException,
) -> None:
    pipeline, _manager, _entry_id = _pipeline(tmp_path, monkeypatch)

    class RaisingSummary:
        def summarize(self, _messages):
            raise signal

    compactor = ContextCompactor(
        context_window=12_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=RaisingSummary(),
    )

    with pytest.raises(type(signal)):
        compactor.execute_strategy(
            _long_messages({"role": "system", "content": "SYSTEM"}),
            strategy,
        )


def test_llm_summary_propagates_caller_deadline_but_not_local_timeout() -> None:
    class DeadlineModel:
        def next(self, _messages, **_kwargs):
            raise ModelCallDeadlineExceeded("controlled provider deadline")

    caller_owned = LLMSummaryGenerator(
        DeadlineModel(),
        timeout_seconds=20.0,
        deadline_monotonic=time.monotonic() + 1.0,
    )
    with pytest.raises(ModelCallDeadlineExceeded):
        caller_owned.summarize([{"role": "user", "content": "x" * 100}])

    local_owned = LLMSummaryGenerator(
        DeadlineModel(),
        timeout_seconds=0.1,
        deadline_monotonic=time.monotonic() + 20.0,
    )
    assert local_owned.summarize(
        [{"role": "user", "content": "x" * 100}]
    ) is None
