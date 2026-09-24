from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

import minicode.memory as memory_module
from minicode.agent_loop import (
    AgentTurnDeadlineExceeded,
    _apply_control_signal,
    run_agent_turn,
)
from minicode.context_compactor import (
    AutoCompactConfig,
    CompactStrategy,
    CompactTrigger,
    CompactionMaterializationState,
    CompactionResult,
    ContextCompactor,
)
from minicode.context_cybernetics import ContextCyberneticsOrchestrator
from minicode.context_manager import ContextManager
from minicode.feedback_controller import FeedbackController
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.predictive_controller import PredictiveController
from minicode.run_events import (
    new_context_operation_id,
    project_context_compaction_failure_event,
)
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.types import AgentStep
from minicode.web.context_aggregation import (
    aggregate_run_context,
    project_context_event_detail,
    project_context_metric,
)


class _AssistantModel:
    model_id = "p2-red-regression-assistant"

    def next(self, _messages, **_kwargs):
        return AgentStep(type="assistant", content="done")


class _ThreeToolModel:
    model_id = "p2-red-regression-tools"

    def __init__(self) -> None:
        self.calls = 0

    def next(self, _messages, **_kwargs):
        self.calls += 1
        if self.calls <= 3:
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": f"audit-tool-{self.calls}",
                        "toolName": "small_read",
                        "input": {"index": self.calls},
                    }
                ],
            )
        return AgentStep(type="assistant", content="done")


class _Summary:
    def summarize(self, _messages: list[dict[str, object]]) -> str:
        return (
            "The checkout investigation retained its verified rounding "
            "decision and current implementation state."
        )


def _tools() -> ToolRegistry:
    return ToolRegistry(
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


def _control_signal() -> SimpleNamespace:
    return SimpleNamespace(
        confidence=0.9,
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


def _control_case(kind: str) -> tuple[BaseException, type[BaseException]]:
    if kind == "cancel":
        return (
            TurnCancellationRequested(
                "turn_00000000000000000000000000000000"
            ),
            TurnCancellationRequested,
        )
    return (
        ModelCallDeadlineExceeded("controlled compaction deadline"),
        AgentTurnDeadlineExceeded,
    )


def _raise(signal: BaseException) -> Callable[..., object]:
    def raising(*_args, **_kwargs):
        raise signal

    return raising


@pytest.mark.parametrize("path", ["cybernetic", "direct"])
@pytest.mark.parametrize("kind", ["cancel", "deadline"])
def test_pre_request_compaction_normalizes_control_signals(
    path: str,
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal, expected = _control_case(kind)
    if path == "cybernetic":
        monkeypatch.setattr(
            ContextCyberneticsOrchestrator,
            "run_cycle",
            _raise(signal),
        )
        enable_work_chain = True
    else:
        # ContextManager normally owns a paired compactor/cybernetic controller.
        # Make only the controller false-y so the supported direct-compactor
        # fallback is reached without replacing the production compactor.
        monkeypatch.setattr(
            ContextCyberneticsOrchestrator,
            "__bool__",
            lambda _self: False,
            raising=False,
        )
        monkeypatch.setattr(
            ContextCompactor,
            "process_request",
            _raise(signal),
        )
        enable_work_chain = True

    with pytest.raises(expected):
        run_agent_turn(
            model=_AssistantModel(),
            tools=ToolRegistry([]),
            messages=[
                {"role": "system", "content": "SYSTEM"},
                {"role": "user", "content": "inspect checkout behavior"},
            ],
            cwd=str(tmp_path),
            context_manager=ContextManager(context_window=7_000),
            enable_work_chain=enable_work_chain,
            max_steps=1,
        )


@pytest.mark.parametrize("kind", ["cancel", "deadline"])
def test_predictive_compaction_propagates_control_signals(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal, expected = _control_case(kind)
    monkeypatch.setattr(
        PredictiveController,
        "generate_predictive_actions",
        lambda _self: [
            SimpleNamespace(
                urgency=0.95,
                recommended_action="trigger_compaction",
                horizon="short",
            )
        ],
    )
    monkeypatch.setattr(
        ContextCompactor,
        "compact_messages",
        _raise(signal),
    )

    with pytest.raises(expected):
        run_agent_turn(
            model=_ThreeToolModel(),
            tools=_tools(),
            messages=[
                {"role": "system", "content": "SYSTEM"},
                {"role": "user", "content": "inspect checkout behavior"},
            ],
            cwd=str(tmp_path),
            context_manager=ContextManager(context_window=7_000),
            enable_work_chain=True,
            max_steps=6,
        )


@pytest.mark.parametrize("kind", ["cancel", "deadline"])
def test_feedback_forced_compaction_propagates_control_signals(
    kind: str,
) -> None:
    signal, expected = _control_case(kind)

    class RaisingCompactor:
        last_result = None

        def compact_messages(self, *_args, **_kwargs):
            raise signal

    with pytest.raises(expected):
        _apply_control_signal(
            control_signal=_control_signal(),
            system_state=None,
            max_steps=7,
            tool_scheduler=SimpleNamespace(),
            context_compactor=RaisingCompactor(),
            model_switcher=None,
            current_messages=[{"role": "system", "content": "SYSTEM"}],
        )


@pytest.mark.parametrize("kind", ["cancel", "deadline"])
def test_final_feedback_forced_compaction_propagates_control_signals(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal, expected = _control_case(kind)
    monkeypatch.setattr(
        FeedbackController,
        "observe",
        lambda _self, _state: _control_signal(),
    )
    monkeypatch.setattr(
        ContextCompactor,
        "compact_messages",
        _raise(signal),
    )

    with pytest.raises(expected):
        run_agent_turn(
            model=_AssistantModel(),
            tools=ToolRegistry([]),
            messages=[
                {"role": "system", "content": "SYSTEM"},
                {"role": "user", "content": "inspect checkout behavior"},
            ],
            cwd=str(tmp_path),
            context_manager=ContextManager(context_window=7_000),
            enable_work_chain=True,
            max_steps=1,
        )


def _pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str,
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
        content,
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


def _compactor(
    tmp_path: Path,
    pipeline: MemoryPipeline,
) -> ContextCompactor:
    return ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(
            max_expand_tokens=2_500,
            min_keep_messages=2,
        ),
        summary_generator=_Summary(),
    )


def test_unverified_rendered_id_cannot_suppress_memory_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "UNVERIFIED_RENDERED_ID_MARKER"
    pipeline, _manager, entry_id = _pipeline(
        tmp_path,
        monkeypatch,
        f"{marker}: sum line amounts before final rounding.",
    )
    system_message = {
        "role": "system",
        "content": "SYSTEM_WITHOUT_MEMORY_TEXT",
        "_memory_rendered_ids": (entry_id,),
    }

    result = _compactor(tmp_path, pipeline).execute_strategy(
        _long_messages(system_message),
        CompactStrategy.SESSION_MEMORY,
    )

    rendered = "\n".join(
        str(message.get("content", "")) for message in result.messages
    )
    assert result.memory_receipt is not None
    assert rendered.count(marker) == 1


def test_same_memory_id_content_revision_replaces_stale_rendered_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_marker = "OLD_MEMORY_REVISION_MARKER"
    new_marker = "NEW_MEMORY_REVISION_MARKER"
    pipeline, manager, entry_id = _pipeline(
        tmp_path,
        monkeypatch,
        f"{old_marker}: checkout total rounding uses the old rule.",
    )
    task_start = pipeline.inject(
        "fix checkout total rounding",
        [],
        [{"role": "system", "content": "SYSTEM"}],
    )
    assert old_marker in str(task_start[0]["content"])

    assert manager.update_entry(
        MemoryScope.PROJECT,
        entry_id,
        f"{new_marker}: checkout total rounding uses the corrected rule.",
    )
    approval = manager.approve_entry(
        entry_id,
        actor="test",
        reason="controlled revision",
    )
    assert "approved" in approval.lower()

    result = _compactor(tmp_path, pipeline).execute_strategy(
        _long_messages(task_start[0]),
        CompactStrategy.SESSION_MEMORY,
    )

    rendered = "\n".join(
        str(message.get("content", "")) for message in result.messages
    )
    assert rendered.count(new_marker) == 1
    assert old_marker not in rendered


def _partial_failure_payload() -> dict[str, object]:
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
    return project_context_compaction_failure_event(
        result,
        context_operation_id=new_context_operation_id(),
        path="pre_request_compactor",
        consecutive_failures=1,
        circuit_breaker_tripped=False,
    )


def test_dashboard_projects_partial_compaction_fields() -> None:
    details = project_context_event_detail(
        "context.compaction.failed",
        _partial_failure_payload(),
    )

    assert details["partialEffective"] is True
    assert details["tokensFreed"] == 120


def test_dashboard_aggregate_counts_partial_compaction_savings() -> None:
    payload = _partial_failure_payload()
    aggregate = aggregate_run_context(
        [
            {
                "type": "context.compaction.failed",
                "payload": payload,
                "sequence": 1,
            }
        ],
        run_source="headless",
    )
    metric = project_context_metric(aggregate)

    assert metric["value"] is not None
    assert metric["value"]["knownTokensFreed"] == 120
