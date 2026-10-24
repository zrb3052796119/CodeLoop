from __future__ import annotations

import inspect
from contextlib import contextmanager
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_parent_fast_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    """These scenarios exercise the denial/compaction feedback loop; the
    parent small-diff fast path would auto-approve their edits."""
    monkeypatch.setenv("MINI_CODE_PARENT_FAST_EDIT", "0")

import minicode.memory as memory_module
from minicode.context_compactor import (
    AutoCompactConfig,
    CompactionMaterializationState,
    CompactionResult,
    CompactStrategy,
    CompactTrigger,
    ContextCompactor,
    ToolResultBudgetManager,
)
from minicode.context_cybernetics import (
    ContextCyberneticsOrchestrator,
    ControlAction,
)
from minicode.context_manager import ContextManager
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import (
    MemoryPipeline,
    MemoryPromptReceipt,
    verified_materialized_memory_fragments,
)
from minicode.memory_store import MemoryStoreBusy, MemoryStoreConflict
from minicode.mock_model import MockModelAdapter
from minicode.run_events import new_context_operation_id
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.types import AgentStep


class _TranscriptSummarizer:
    def summarize(self, _messages: list[dict[str, object]]) -> str:
        return "The earlier discussion established the checkout repair plan."


class _CapturingAssistantModel:
    model_id = "memory-boundary-capture"

    def __init__(self) -> None:
        self.inputs: list[list[dict[str, object]]] = []

    def next(self, messages, **_kwargs):
        self.inputs.append([dict(message) for message in messages])
        return AgentStep(type="assistant", content="done")


class _ContaminationSpy:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.inputs: list[list[dict[str, object]]] = []

    def summarize(self, messages: list[dict[str, object]]) -> str:
        copied = [dict(message) for message in messages]
        self.inputs.append(copied)
        visible = "\n".join(
            str(message.get("content", "")) for message in copied
        )
        return (
            "CONTAMINATED_SECOND_SUMMARY"
            if self.marker in visible
            else "CLEAN_SECOND_SUMMARY"
        )


class _ProvenanceSink:
    def __init__(self) -> None:
        self.batches: list[object] = []

    def record_memory_provenance(self, batch: object) -> None:
        self.batches.append(batch)


class _CompactionTrigger:
    def __init__(self, *, in_loop: bool) -> None:
        self.in_loop = in_loop
        self.consecutive_failures = 0
        self.is_tripped = False

    def should_trigger(self, messages: list[dict[str, object]]) -> bool:
        if not self.in_loop:
            return False
        return sum(
            message.get("role") == "tool_result" for message in messages
        ) >= 10


class _ControlledCompactor:
    def __init__(
        self,
        receipt: MemoryPromptReceipt,
        *,
        workspace: Path,
        in_loop: bool = False,
    ) -> None:
        self.receipt = receipt
        self.auto_compact = _CompactionTrigger(in_loop=in_loop)
        self._context_window = 7_000
        self._workspace = workspace
        self.tool_budget = ToolResultBudgetManager(workspace=workspace)
        self._estimate = lambda message: len(
            str(message.get("content", ""))
        )
        self.last_result: CompactionResult | None = None
        self.calls = 0

    def process_request(
        self,
        messages: list[dict[str, object]],
        **_kwargs,
    ) -> CompactionResult:
        self.calls += 1
        if self.auto_compact.in_loop and not self.auto_compact.should_trigger(
            messages
        ):
            result = CompactionResult(
                success=False,
                strategy=CompactStrategy.SESSION_MEMORY,
                trigger=CompactTrigger.AUTO,
                messages=list(messages),
                error="Context usage below auto-compact threshold",
            )
            self.last_result = result
            return result
        compacted = [dict(messages[0])]
        if len(messages) > 1:
            compacted.append(dict(messages[-1]))
        compacted[0]["content"] = (
            str(compacted[0].get("content", ""))
            + "\n"
            + self.receipt.prompt_text
        )
        result = CompactionResult(
            success=True,
            strategy=CompactStrategy.SESSION_MEMORY,
            trigger=CompactTrigger.AUTO,
            messages=compacted,
            tokens_freed=100,
            summary_text="controlled compact summary",
            memory_receipt=self.receipt,
            materialization_state=CompactionMaterializationState.PENDING,
        )
        self.last_result = result
        return result

    @staticmethod
    def confirm_materialized(result: CompactionResult) -> bool:
        result.materialization_state = CompactionMaterializationState.INSTALLED
        return True

    @staticmethod
    def rejection_fallback_messages(
        _result: CompactionResult,
        original_messages: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return list(original_messages)

    def get_stats(self) -> dict[str, object]:
        return {
            "total_passes": self.calls,
            "tool_results_persisted": 0,
            "read_dedup_entries": 0,
            "microcompact_tokens_cleared": 0,
            "auto_compact_boundaries": int(
                self.last_result is not None
                and self.last_result.materialization_state
                is CompactionMaterializationState.INSTALLED
            ),
            "circuit_breaker_tripped": False,
        }


class _PreRequestCybernetics:
    def __init__(
        self,
        compactor: _ControlledCompactor,
        *,
        falsey: bool = False,
        compact: bool = True,
    ) -> None:
        self.compactor = compactor
        self.falsey = falsey
        self.compact = compact
        self.enabled = not falsey

    def __bool__(self) -> bool:
        return not self.falsey

    def set_intent(self, _intent: str) -> None:
        pass

    def run_cycle(self, messages, **_kwargs):
        if not self.compact:
            return messages, None, None
        result = self.compactor.process_request(messages)
        # The real controller is only needed for the pre-request decision in
        # these focused doubles.  Becoming false-y afterwards keeps unrelated
        # end-of-turn PID reporting outside the test's behavioral boundary.
        self.falsey = True
        action = type(
            "ControlledAction",
            (),
            {"reason": "controlled", "compaction_intensity": 0.5},
        )()
        return result.messages, result, action

    @staticmethod
    def finalize_materialization(
        _result: CompactionResult,
        *,
        installed: bool,
    ) -> bool:
        return installed

    @staticmethod
    def get_stats() -> dict[str, object]:
        return {}


class _TenReadModel:
    model_id = "controlled-in-loop-compaction"

    def __init__(self) -> None:
        self.calls = 0

    def next(self, _messages, **_kwargs):
        self.calls += 1
        if self.calls <= 10:
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": f"read-{self.calls}",
                        "toolName": "small_read",
                        "input": {"index": self.calls},
                    }
                ],
            )
        return AgentStep(type="assistant", content="done")


def _memory_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    content: str | None = None,
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
        content
        or (
            "When checkout total rounding fails, sum decimal line amounts "
            "before applying one final rounding operation."
        ),
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


def _installed_compaction_only(
    tmp_path: Path,
    pipeline: MemoryPipeline,
    entry_id: str,
) -> tuple[object, list[dict[str, object]]]:
    from minicode.agent_loop import _commit_materialized_compaction_memory

    original = _long_checkout_transcript()
    active: list[dict[str, object]] = [dict(message) for message in original]
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(
            max_expand_tokens=2_500,
            min_keep_messages=2,
        ),
        summary_generator=_TranscriptSummarizer(),
    )
    result = compactor.execute_strategy(
        original,
        CompactStrategy.SESSION_MEMORY,
    )
    assert result.effective is True
    assert result.memory_receipt is not None
    installed, committed_ids = _commit_materialized_compaction_memory(
        result,
        pipeline,
        lambda: active.__setitem__(slice(None), result.messages),
        lambda: active.__setitem__(slice(None), original),
    )
    assert installed is True
    assert committed_ids == (entry_id,)
    return result, active


def _long_checkout_transcript() -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": "SYSTEM"},
    ]
    for index in range(24):
        role = "user" if index % 2 == 0 else "assistant"
        messages.append(
            {
                "role": role,
                "content": f"checkout total rounding discussion {index} " + "x" * 800,
            }
        )
    return messages


def _tool_heavy_checkout_transcript() -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "fix checkout total rounding"},
    ]
    for index in range(10):
        messages.append(
            {
                "role": "tool_result",
                "toolName": "run_command",
                "toolUseId": f"small-{index}",
                "content": f"small output {index} " + "s" * 3_000,
            }
        )
    messages.append(
        {
            "role": "tool_result",
            "toolName": "run_command",
            "toolUseId": "large",
            "content": "large output " + "L" * 6_000,
        }
    )
    messages.append(
        {"role": "user", "content": "continue checkout total rounding"}
    )
    return messages


def test_effective_session_compaction_returns_exact_uncommitted_memory_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, manager, entry_id = _memory_pipeline(tmp_path, monkeypatch)
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(
            max_expand_tokens=2_500,
            min_keep_messages=2,
        ),
        summary_generator=_TranscriptSummarizer(),
    )

    result = compactor.execute_strategy(
        _long_checkout_transcript(),
        CompactStrategy.SESSION_MEMORY,
    )

    assert result.effective is True
    assert result.memory_receipt is not None
    assert result.memory_receipt.rendered_ids == (entry_id,)
    assert entry_id in result.memory_receipt.selected_ids
    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.retrieval_count == 1
    assert stored.injection_count == 0


def test_ineffective_session_compaction_records_retrieval_not_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, manager, entry_id = _memory_pipeline(tmp_path, monkeypatch)
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(
            max_expand_tokens=2_500,
            min_keep_messages=2,
        ),
        summary_generator=None,
    )

    result = compactor.execute_strategy(
        _long_checkout_transcript(),
        CompactStrategy.SESSION_MEMORY,
    )

    assert result.effective is False
    assert result.memory_receipt is None
    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.retrieval_count == 1
    assert stored.injection_count == 0


def test_materialized_session_compaction_commits_receipt_to_turn_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, manager, entry_id = _memory_pipeline(tmp_path, monkeypatch)
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(
            max_expand_tokens=2_500,
            min_keep_messages=2,
        ),
        summary_generator=_TranscriptSummarizer(),
    )
    result = compactor.execute_strategy(
        _long_checkout_transcript(),
        CompactStrategy.SESSION_MEMORY,
    )
    assert result.effective is True
    assert result.memory_receipt is not None

    pipeline.commit_prompt_receipt(result.memory_receipt)

    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.injection_count == 1
    assert pipeline.injected_memory_ids == (entry_id,)


def test_task_start_and_compaction_dedupe_injection_and_final_feedback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, manager, entry_id = _memory_pipeline(tmp_path, monkeypatch)
    messages = pipeline.inject(
        "fix checkout total rounding",
        [],
        [{"role": "system", "content": "SYSTEM"}],
    )
    assert "sum decimal line amounts" in messages[0]["content"]

    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(
            max_expand_tokens=2_500,
            min_keep_messages=2,
        ),
        summary_generator=_TranscriptSummarizer(),
    )
    result = compactor.execute_strategy(
        _long_checkout_transcript(),
        CompactStrategy.SESSION_MEMORY,
    )
    assert result.effective is True
    assert result.memory_receipt is not None

    assert pipeline.commit_prompt_receipt(result.memory_receipt) == ()
    pipeline.feedback(False)
    pipeline.feedback(False)

    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.injection_count == 1
    assert stored.failure_count == 1
    assert pipeline.injected_memory_ids == (entry_id,)


def test_agent_feedback_includes_memory_materialized_only_by_compaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pipeline, manager, entry_id = _memory_pipeline(tmp_path, monkeypatch)
    context_manager = ContextManager(context_window=7_000)
    # Simulate a reused session already above the critical-pressure threshold.
    # Task-start injection therefore abstains, while the fresh transcript below
    # still lands in the Session Memory compaction band.
    context_manager.messages = [
        {"role": "user", "content": "prior session pressure " + "p" * 1_400}
        for _index in range(24)
    ]
    messages = [
        {"role": "system", "content": "SYSTEM"},
        *[
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": (
                    f"checkout total rounding discussion {index} " + "x" * 1_000
                ),
            }
            for index in range(24)
        ],
    ]

    from minicode.agent_loop import run_agent_turn

    model = MockModelAdapter()
    tools = ToolRegistry([])
    for _cycle in range(2):
        run_agent_turn(
            model=model,
            tools=tools,
            messages=messages,
            cwd=str(tmp_path / "workspace"),
            context_manager=context_manager,
            memory_manager=manager,
            enable_work_chain=True,
            max_steps=0,
        )

    result = context_manager._context_compactor.last_result
    assert result is not None and result.effective
    assert result.strategy is CompactStrategy.SESSION_MEMORY
    assert result.memory_receipt is not None
    assert result.memory_receipt.rendered_ids == (entry_id,)
    assert (
        result.materialization_state
        is CompactionMaterializationState.INSTALLED
    )
    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.retrieval_count == 1
    assert stored.injection_count == 1
    assert stored.failure_count == 1


def test_compaction_only_install_attaches_exact_verified_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "COMPACTION_ONLY_PROOF_MARKER"
    pipeline, _manager, entry_id = _memory_pipeline(
        tmp_path,
        monkeypatch,
        content=(
            f"{marker}: sum decimal checkout line amounts before final rounding."
        ),
    )

    result, active = _installed_compaction_only(
        tmp_path,
        pipeline,
        entry_id,
    )

    proofs = verified_materialized_memory_fragments(active)
    assert len(proofs) == 1
    proof_id, proof_digest, fragment = proofs[0]
    assert proof_id == entry_id
    assert marker in fragment
    assert (proof_id, proof_digest) in result.memory_receipt.rendered_proofs
    visible = "\n".join(str(message.get("content", "")) for message in active)
    assert visible.count(marker) == 1


def test_rejected_compaction_memory_is_removed_before_next_turn_model_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.agent_loop import run_agent_turn
    from minicode.agent_runtime import prepare_conversation_messages

    marker = "REVOKED_COMPACTION_MEMORY_MARKER"
    pipeline, _manager, entry_id = _memory_pipeline(
        tmp_path,
        monkeypatch,
        content=(
            f"{marker}: sum decimal checkout line amounts before final rounding."
        ),
    )
    _result, active = _installed_compaction_only(
        tmp_path,
        pipeline,
        entry_id,
    )
    rejecting_manager = MemoryManager(project_root=tmp_path / "workspace")
    rejection = rejecting_manager.reject_entry(
        entry_id,
        reason="obsolete recovery",
    )
    assert "rejected" in rejection.lower()
    prepared = prepare_conversation_messages(
        active,
        system_prompt="CURRENT SYSTEM",
        user_message="continue checkout total rounding",
    )
    model = _CapturingAssistantModel()

    run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=prepared,
        cwd=str(tmp_path / "workspace"),
        memory_manager=MemoryManager(project_root=tmp_path / "workspace"),
        enable_work_chain=True,
        max_steps=1,
    )

    assert len(model.inputs) == 1
    visible = "\n".join(
        str(message.get("content", "")) for message in model.inputs[0]
    )
    assert marker not in visible
    assert "checkout repair plan" in visible
    assert verified_materialized_memory_fragments(model.inputs[0]) == ()
    stored = rejecting_manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.is_active is False
    assert stored.injection_count == 1


def test_external_rejection_is_applied_before_the_next_same_turn_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.agent_loop import run_agent_turn

    marker = "SAME_TURN_REVOCATION_MARKER"
    _pipeline, _manager, entry_id = _memory_pipeline(
        tmp_path,
        monkeypatch,
        content=(
            f"{marker}: sum decimal checkout line amounts before final rounding."
        ),
    )
    class RevokingModel:
        model_id = "memory-same-turn-revocation"

        def __init__(self) -> None:
            self.inputs: list[list[dict[str, object]]] = []

        def next(self, messages, **_kwargs):
            self.inputs.append([dict(message) for message in messages])
            if len(self.inputs) == 1:
                assert marker in "\n".join(
                    str(message.get("content", ""))
                    for message in self.inputs[0]
                )
                rejection = MemoryManager(
                    project_root=tmp_path / "workspace"
                ).reject_entry(
                    entry_id,
                    reason="revoked between provider calls",
                )
                assert "rejected" in rejection.lower()
                return AgentStep(
                    type="tool_calls",
                    calls=[
                        {
                            "id": "same-turn-noop",
                            "toolName": "noop",
                            "input": {},
                        }
                    ],
                )
            return AgentStep(type="assistant", content="done")

    model = RevokingModel()
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="noop",
                description="return one bounded result",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _value, _context: ToolResult(ok=True, output="ok"),
            )
        ]
    )

    run_agent_turn(
        model=model,
        tools=tools,
        messages=[
            {"role": "system", "content": "CURRENT SYSTEM"},
            {"role": "user", "content": "fix checkout total rounding"},
        ],
        cwd=str(tmp_path / "workspace"),
        memory_manager=MemoryManager(project_root=tmp_path / "workspace"),
        enable_work_chain=True,
        max_steps=3,
    )

    assert len(model.inputs) == 2
    assert marker not in "\n".join(
        str(message.get("content", "")) for message in model.inputs[1]
    )


def test_irrelevant_next_turn_does_not_inherit_approved_compaction_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.agent_loop import run_agent_turn
    from minicode.agent_runtime import prepare_conversation_messages

    marker = "IRRELEVANT_NEXT_TURN_MEMORY_MARKER"
    pipeline, _manager, entry_id = _memory_pipeline(
        tmp_path,
        monkeypatch,
        content=(
            f"{marker}: sum decimal checkout line amounts before final rounding."
        ),
    )
    _result, active = _installed_compaction_only(
        tmp_path,
        pipeline,
        entry_id,
    )
    prepared = prepare_conversation_messages(
        active,
        system_prompt="CURRENT SYSTEM",
        user_message="rename the CSS theme color token",
    )
    next_turn_manager = MemoryManager(project_root=tmp_path / "workspace")
    model = _CapturingAssistantModel()

    run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=prepared,
        cwd=str(tmp_path / "workspace"),
        memory_manager=next_turn_manager,
        enable_work_chain=True,
        max_steps=1,
    )

    visible = "\n".join(
        str(message.get("content", "")) for message in model.inputs[0]
    )
    assert marker not in visible
    assert "checkout repair plan" in visible
    stored = next_turn_manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.is_active is True

    relevant_pipeline = MemoryPipeline(
        MemoryManager(project_root=tmp_path / "workspace")
    )
    relevant_pipeline.initialize(
        workspace_path=str(tmp_path / "workspace"),
        enable_reranker=False,
        enable_vector=False,
    )
    reinjected = relevant_pipeline.inject(
        "fix checkout total rounding",
        [],
        [{"role": "system", "content": "CLEAN SYSTEM"}],
    )
    assert str(reinjected[0]["content"]).count(marker) == 1


def test_compaction_memory_never_enters_second_full_summary_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.agent_loop import run_agent_turn
    from minicode.agent_runtime import prepare_conversation_messages

    marker = "SECOND_SUMMARY_CONTAMINATION_MARKER"
    pipeline, _manager, entry_id = _memory_pipeline(
        tmp_path,
        monkeypatch,
        content=(
            f"{marker}: sum decimal checkout line amounts before final rounding."
        ),
    )
    _result, active = _installed_compaction_only(
        tmp_path,
        pipeline,
        entry_id,
    )
    prepared = prepare_conversation_messages(
        active,
        system_prompt="CURRENT SYSTEM",
        user_message="rename the CSS theme color token",
    )
    model = _CapturingAssistantModel()
    cleaned = run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=prepared,
        cwd=str(tmp_path / "workspace"),
        memory_manager=MemoryManager(project_root=tmp_path / "workspace"),
        enable_work_chain=True,
        max_steps=1,
    )
    for index in range(30):
        cleaned.append(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"CSS theme token discussion {index} " + "z" * 800,
            }
        )
    spy = _ContaminationSpy(marker)
    compactor = ContextCompactor(
        context_window=50_000,
        workspace=tmp_path,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(min_keep_tokens=0, min_keep_messages=2),
        summary_generator=spy,
    )

    second = compactor.execute_strategy(cleaned, CompactStrategy.FULL)

    assert second.effective is True
    assert spy.inputs
    assert all(
        marker
        not in "\n".join(
            str(message.get("content", "")) for message in summary_input
        )
        for summary_input in spy.inputs
    )
    assert "CONTAMINATED_SECOND_SUMMARY" not in second.summary_text
    assert marker not in "\n".join(
        str(message.get("content", "")) for message in second.messages
    )


def test_legacy_session_boundary_memory_is_removed_but_summary_survives(
    tmp_path: Path,
) -> None:
    from minicode.agent_loop import run_agent_turn

    marker = "LEGACY_PROOFLESS_MEMORY_MARKER"
    model = _CapturingAssistantModel()
    legacy_boundary = {
        "role": "system",
        "content": (
            "[Context compacted via Session Memory]\n\n"
            "## Dropped Conversation Summary\n\n"
            "Keep the verified checkout repair plan.\n\n"
            "## Relevant Durable Memory\n\n"
            f"1. [failure-recovery] {marker}\n\n"
            "--- Recent conversation continues below ---"
        ),
        "_compact_boundary": True,
    }

    run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=[
            {"role": "system", "content": "CURRENT SYSTEM"},
            legacy_boundary,
            {"role": "user", "content": "continue checkout review"},
        ],
        cwd=str(tmp_path),
        enable_work_chain=False,
        max_steps=1,
    )

    visible = "\n".join(
        str(message.get("content", "")) for message in model.inputs[0]
    )
    assert marker not in visible
    assert "Keep the verified checkout repair plan." in visible


def test_agent_pre_request_guard_rejection_installs_memory_free_safe_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pipeline, manager, entry_id = _memory_pipeline(tmp_path, monkeypatch)
    context_manager = ContextManager(context_window=7_000)
    # Keep task-start retrieval out of the prompt while allowing the fresh
    # transcript to enter the pre-request compaction pipeline.
    context_manager.messages = [
        {"role": "user", "content": "prior session pressure " + "p" * 1_400}
        for _index in range(24)
    ]

    materialization_attempts: list[str] = []

    def reject_receipt(
        _self: MemoryPipeline,
        receipt,
        _materialize,
        _rollback,
    ) -> bool:
        materialization_attempts.append(receipt.receipt_id)
        return False

    monkeypatch.setattr(
        MemoryPipeline,
        "materialize_prompt_receipt",
        reject_receipt,
    )

    from minicode.agent_loop import run_agent_turn

    messages = [
        {"role": "system", "content": "SYSTEM"},
        *[
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": (
                    f"checkout total rounding discussion {index} "
                    + "x" * 1_000
                ),
            }
            for index in range(24)
        ],
    ]
    returned_messages = messages
    for _cycle in range(2):
        returned_messages = run_agent_turn(
            model=MockModelAdapter(),
            tools=ToolRegistry([]),
            messages=messages,
            cwd=str(tmp_path / "workspace"),
            context_manager=context_manager,
            memory_manager=manager,
            enable_work_chain=True,
            max_steps=0,
        )

    result = context_manager._context_compactor.last_result
    assert materialization_attempts
    assert result is not None
    assert result.materialization_state is CompactionMaterializationState.REJECTED
    assert result.effective is False
    assert context_manager.messages == returned_messages
    assert returned_messages != messages
    assert all(
        "Relevant Durable Memory" not in str(message)
        for message in returned_messages
    )
    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.injection_count == 0
    assert stored.failure_count == 0


def test_agent_in_loop_guard_rejection_keeps_live_context_memory_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pipeline, manager, entry_id = _memory_pipeline(tmp_path, monkeypatch)
    context_manager = ContextManager(context_window=7_000)
    # The previous session pressure makes task-start Memory abstain. The live
    # turn starts small, then crosses the high-water mark after ten tools.
    context_manager.messages = [
        {"role": "user", "content": "prior session pressure " + "p" * 1_400}
        for _index in range(24)
    ]
    materialization_attempts: list[str] = []

    def reject_receipt(
        _self: MemoryPipeline,
        receipt,
        _materialize,
        _rollback,
    ) -> bool:
        materialization_attempts.append(receipt.receipt_id)
        return False

    monkeypatch.setattr(
        MemoryPipeline,
        "materialize_prompt_receipt",
        reject_receipt,
    )

    from minicode.agent_loop import run_agent_turn
    from minicode.feedforward_controller import FeedforwardController, PreemptiveConfig
    from minicode.predictive_controller import PredictiveController

    # The feedforward step cap is orthogonal budget policy; at this scenario's
    # scale it would cut the 12-step budget below the eleventh model call the
    # rejection-restore contract needs to observe.
    monkeypatch.setattr(
        FeedforwardController,
        "preconfigure",
        lambda _self, _intent, _raw_input: PreemptiveConfig(confidence=0.0),
    )
    monkeypatch.setattr(
        PredictiveController,
        "generate_predictive_actions",
        lambda _self: [],
    )

    class InLoopModel:
        model_id = "in-loop-compaction-test"

        def __init__(self) -> None:
            self.primary_calls = 0
            self.final_input_messages = None

        def next(self, messages, on_stream_chunk=None, **_kwargs):
            if (
                messages
                and "precise context-compaction assistant"
                in str(messages[0].get("content", ""))
            ):
                return AgentStep(
                    type="assistant",
                    content=(
                        "The checkout investigation produced repeated large "
                        "tool outputs and must preserve the rounding fix."
                    ),
                )
            self.primary_calls += 1
            if self.primary_calls <= 10:
                return AgentStep(
                    type="tool_calls",
                    calls=[
                        {
                            "id": f"bulk-{self.primary_calls}",
                            "toolName": "bulk_read",
                            "input": {"index": self.primary_calls},
                        }
                    ],
                )
            self.final_input_messages = list(messages)
            return AgentStep(type="assistant", content="done")

    tools = ToolRegistry(
        [
            ToolDefinition(
                name="bulk_read",
                description="return a large read-only checkout trace",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda value, _context: ToolResult(
                    ok=True,
                    output=(
                        f"checkout total rounding trace {value['index']} "
                        + "x" * 3_500
                    ),
                ),
            )
        ]
    )

    model = InLoopModel()
    returned_messages = run_agent_turn(
        model=model,
        tools=tools,
        messages=[
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "review checkout total rounding"},
        ],
        cwd=str(tmp_path / "workspace"),
        context_manager=context_manager,
        memory_manager=manager,
        enable_work_chain=True,
        max_steps=12,
    )

    result = context_manager._context_compactor.last_result
    assert materialization_attempts
    assert result is not None
    assert result.strategy is CompactStrategy.SESSION_MEMORY
    assert result.materialization_state is CompactionMaterializationState.REJECTED
    assert result.effective is False
    assert model.final_input_messages is not None
    assert context_manager.messages == returned_messages
    assert returned_messages[-1] == {"role": "assistant", "content": "done"}
    assert returned_messages[:-1] == model.final_input_messages
    assert all(
        "Relevant Durable Memory" not in str(message)
        for message in returned_messages
    )
    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.injection_count == 0
    assert stored.failure_count == 0


def test_agent_assembly_keeps_reflection_engine_storage_free() -> None:
    from minicode.agent_loop import run_agent_turn

    source = inspect.getsource(run_agent_turn)
    assert "reflection_engine.memory =" not in source
    assert "orch.wire_memory(memory_mgr)" in source


def test_stale_receipt_never_runs_prompt_materializer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, manager, entry_id = _memory_pipeline(tmp_path, monkeypatch)
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_TranscriptSummarizer(),
    )
    result = compactor.execute_strategy(
        _long_checkout_transcript(),
        CompactStrategy.SESSION_MEMORY,
    )
    assert result.memory_receipt is not None
    assert result.memory_receipt.authority_revision.startswith(
        "memorypromptrev_"
    )
    manager.reject_entry(entry_id, reason="stale receipt test")
    calls: list[str] = []

    materialized = pipeline.materialize_prompt_receipt(
        result.memory_receipt,
        lambda: calls.append("materialized"),
        lambda: calls.append("rolled_back"),
    )

    assert materialized is False
    assert calls == []
    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.injection_count == 0
    assert pipeline.injected_memory_ids == ()


def test_stale_compaction_receipt_cannot_install_messages_or_feedback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.agent_loop import _commit_materialized_compaction_memory

    pipeline, manager, entry_id = _memory_pipeline(tmp_path, monkeypatch)
    original_messages = _long_checkout_transcript()
    active_messages = list(original_messages)
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_TranscriptSummarizer(),
    )
    result = compactor.execute_strategy(
        original_messages,
        CompactStrategy.SESSION_MEMORY,
    )
    assert result.effective is True
    assert result.memory_receipt is not None
    manager.reject_entry(entry_id, reason="invalidate before install")

    installed, committed_ids = _commit_materialized_compaction_memory(
        result,
        pipeline,
        lambda: active_messages.__setitem__(slice(None), result.messages),
        lambda: active_messages.__setitem__(slice(None), original_messages),
    )
    pipeline.feedback(False)

    assert installed is False
    assert committed_ids == ()
    assert active_messages == original_messages
    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.injection_count == 0
    assert stored.failure_count == 0
    assert pipeline.injected_memory_ids == ()


def test_compaction_commit_records_exact_provenance_and_journal_dedupes_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.agent_loop import _commit_materialized_compaction_memory

    pipeline, manager, entry_id = _memory_pipeline(tmp_path, monkeypatch)
    original_messages = _long_checkout_transcript()
    active_messages = list(original_messages)
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_TranscriptSummarizer(),
    )
    result = compactor.execute_strategy(
        original_messages,
        CompactStrategy.SESSION_MEMORY,
    )
    receipt = result.memory_receipt
    assert result.effective is True
    assert receipt is not None
    assert receipt.rendered_versions == (
        (
            entry_id,
            manager.memories[MemoryScope.PROJECT]
            ._id_index[entry_id]
            .approval_content_hash,
        ),
    )

    workspace = tmp_path / "workspace"
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "journal-home" / ".mini-code",
    )
    context_operation_id = new_context_operation_id()
    with observe_run(
        workspace=workspace,
        source="headless",
        title="Install compaction Memory",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        run_id = observation.run_id
        first = _commit_materialized_compaction_memory(
            result,
            pipeline,
            lambda: active_messages.__setitem__(slice(None), result.messages),
            lambda: active_messages.__setitem__(slice(None), original_messages),
            event_sink=observation,
            context_operation_id=context_operation_id,
        )
        replay = _commit_materialized_compaction_memory(
            result,
            pipeline,
            lambda: active_messages.__setitem__(slice(None), result.messages),
            lambda: active_messages.__setitem__(slice(None), original_messages),
            event_sink=observation,
            context_operation_id=context_operation_id,
        )

    assert run_id is not None
    assert first == (True, (entry_id,))
    assert replay == (True, ())
    snapshot = journal.get_memory_provenance(run_id, "rendered")
    assert snapshot.materializations == receipt.rendered_versions
    assert snapshot.coverage == "complete"
    assert len(snapshot.records) == 1
    record = snapshot.records[0]
    assert record.actor.kind == "run_root"
    assert record.actor.subagent_id is None
    assert record.batch.effect == "rendered"
    assert record.batch.origin == "context_compaction"
    assert record.batch.authority_versions == receipt.rendered_versions
    assert record.batch.receipt_id == receipt.receipt_id
    assert record.batch.context_operation_id == context_operation_id


@pytest.mark.parametrize(
    "path",
    ["pre_request_cybernetic", "pre_request_compactor"],
)
def test_pre_request_compaction_event_and_provenance_share_operation_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    from minicode.agent_loop import run_agent_turn

    _pipeline, manager, entry_id = _memory_pipeline(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    entry = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    receipt = MemoryPromptReceipt(
        prompt_text="CONTROLLED_PRE_REQUEST_MEMORY",
        selected_ids=(entry_id,),
        rendered_ids=(entry_id,),
        rendered_versions=((entry_id, entry.approval_content_hash),),
        authority_revision=manager.prompt_authority_revision(),
        receipt_id="9" * 32,
    )
    compactor = _ControlledCompactor(receipt, workspace=workspace)
    cybernetics = _PreRequestCybernetics(
        compactor,
        falsey=path == "pre_request_compactor",
    )
    model = _CapturingAssistantModel()
    context_manager = ContextManager(context_window=7_000)
    context_manager._context_compactor = compactor
    context_manager._context_cybernetics = cybernetics
    context_manager._controller_model = model.model_id
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "journal-home" / ".mini-code",
    )

    with observe_run(
        workspace=workspace,
        source="headless",
        title=f"Trigger {path}",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        run_id = observation.run_id
        run_agent_turn(
            model=model,
            tools=ToolRegistry([]),
            messages=[
                {"role": "system", "content": "SYSTEM"},
                {
                    "role": "user",
                    "content": "inspect checkout total rounding behavior",
                },
            ],
            cwd=str(workspace),
            context_manager=context_manager,
            memory_manager=manager,
            event_sink=observation,
            max_steps=2,
        )

    assert run_id is not None
    assert compactor.calls == 1
    events = journal.list_events(run_id, limit=100).items
    compacted_events = [
        event
        for event in events
        if event.type == "context.compacted"
        and event.payload.get("path") == path
    ]
    assert len(compacted_events) == 1
    provenance = journal.get_memory_provenance(run_id, "rendered")
    compaction_records = [
        record
        for record in provenance.records
        if record.batch.origin == "context_compaction"
        and record.batch.receipt_id == receipt.receipt_id
    ]
    assert len(compaction_records) == 1
    batch = compaction_records[0].batch
    assert batch.authority_versions == receipt.rendered_versions
    assert (
        batch.context_operation_id
        == compacted_events[0].payload["contextOperationId"]
    )
    assert batch.coverage == "complete"


def test_in_loop_compaction_event_and_provenance_share_operation_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.agent_loop import run_agent_turn
    from minicode.feedforward_controller import FeedforwardController, PreemptiveConfig

    # Same budget-policy pin as the guard-rejection scenario: the eleventh
    # model call must fit inside the caller's explicit max_steps.
    monkeypatch.setattr(
        FeedforwardController,
        "preconfigure",
        lambda _self, _intent, _raw_input: PreemptiveConfig(confidence=0.0),
    )

    _pipeline, manager, entry_id = _memory_pipeline(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    entry = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    receipt = MemoryPromptReceipt(
        prompt_text="CONTROLLED_COMPACTION_MEMORY",
        selected_ids=(entry_id,),
        rendered_ids=(entry_id,),
        rendered_versions=((entry_id, entry.approval_content_hash),),
        authority_revision=manager.prompt_authority_revision(),
        receipt_id="a" * 32,
    )
    compactor = _ControlledCompactor(receipt, workspace=workspace, in_loop=True)
    # A false-y cached cybernetics double drives the non-cybernetic
    # pre-request branch.  Its first controlled result is ineffective; only
    # the real step-10 high-water branch installs the effective result.
    cybernetics = _PreRequestCybernetics(
        compactor,
        falsey=True,
        compact=False,
    )
    model = _TenReadModel()
    context_manager = ContextManager(context_window=7_000)
    context_manager._context_compactor = compactor
    context_manager._context_cybernetics = cybernetics
    context_manager._controller_model = model.model_id
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
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "journal-home" / ".mini-code",
    )

    with observe_run(
        workspace=workspace,
        source="headless",
        title="Trigger in-loop compaction",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        run_id = observation.run_id
        result_messages = run_agent_turn(
            model=model,
            tools=tools,
            messages=[
                {"role": "system", "content": "SYSTEM"},
                {
                    "role": "user",
                    "content": "inspect checkout total rounding behavior",
                },
            ],
            cwd=str(workspace),
            context_manager=context_manager,
            memory_manager=manager,
            event_sink=observation,
            max_steps=12,
        )

    assert run_id is not None
    assert result_messages[-1] == {"role": "assistant", "content": "done"}
    assert model.calls == 11
    events = journal.list_events(run_id, limit=100).items
    compacted_events = [
        event
        for event in events
        if event.type == "context.compacted"
        and event.payload.get("path") == "in_loop_compactor"
    ]
    assert len(compacted_events) == 1
    event = compacted_events[0]
    assert event.step == 10
    provenance = journal.get_memory_provenance(run_id, "rendered")
    compaction_records = [
        record
        for record in provenance.records
        if record.batch.origin == "context_compaction"
        and record.batch.receipt_id == receipt.receipt_id
    ]
    assert len(compaction_records) == 1
    batch = compaction_records[0].batch
    assert batch.authority_versions == receipt.rendered_versions
    assert batch.context_operation_id == event.payload["contextOperationId"]
    assert batch.coverage == "complete"


def test_compaction_guard_store_error_rejects_candidate_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.agent_loop import _commit_materialized_compaction_memory

    pipeline, _manager, _entry_id = _memory_pipeline(tmp_path, monkeypatch)
    original_messages = _long_checkout_transcript()
    active_messages = list(original_messages)
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_TranscriptSummarizer(),
    )
    result = compactor.execute_strategy(
        original_messages,
        CompactStrategy.SESSION_MEMORY,
    )
    assert result.memory_receipt is not None

    def busy_guard(_receipt, _materialize, _rollback) -> bool:
        raise MemoryStoreBusy("controlled contention")

    monkeypatch.setattr(pipeline, "materialize_prompt_receipt", busy_guard)

    installed, committed_ids = _commit_materialized_compaction_memory(
        result,
        pipeline,
        lambda: active_messages.__setitem__(slice(None), result.messages),
        lambda: active_messages.__setitem__(slice(None), original_messages),
    )

    assert installed is False
    assert committed_ids == ()
    assert active_messages == original_messages
    assert pipeline.injected_memory_ids == ()


def test_guard_false_after_callback_is_defensively_rolled_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.agent_loop import _commit_materialized_compaction_memory

    pipeline, _manager, _entry_id = _memory_pipeline(tmp_path, monkeypatch)
    original_messages = _long_checkout_transcript()
    active_messages = list(original_messages)
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_TranscriptSummarizer(),
    )
    result = compactor.execute_strategy(
        original_messages,
        CompactStrategy.SESSION_MEMORY,
    )

    def malformed_guard(_receipt, materialize, _rollback) -> bool:
        materialize()
        return False

    monkeypatch.setattr(
        pipeline,
        "materialize_prompt_receipt",
        malformed_guard,
    )
    sink = _ProvenanceSink()

    installed, committed_ids = _commit_materialized_compaction_memory(
        result,
        pipeline,
        lambda: active_messages.__setitem__(slice(None), result.messages),
        lambda: active_messages.__setitem__(slice(None), original_messages),
        event_sink=sink,
        context_operation_id=new_context_operation_id(),
    )

    assert installed is False
    assert committed_ids == ()
    assert active_messages == original_messages
    assert pipeline.injected_memory_ids == ()
    assert sink.batches == []


def test_materialized_ledger_survives_durable_counter_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.agent_loop import _commit_materialized_compaction_memory

    pipeline, manager, entry_id = _memory_pipeline(tmp_path, monkeypatch)
    original_messages = _long_checkout_transcript()
    active_messages = list(original_messages)
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_TranscriptSummarizer(),
    )
    result = compactor.execute_strategy(
        original_messages,
        CompactStrategy.SESSION_MEMORY,
    )

    def fail_counter(_entry_ids: list[str]) -> None:
        raise OSError("controlled counter failure")

    monkeypatch.setattr(manager, "record_injections", fail_counter)
    sink = _ProvenanceSink()
    context_operation_id = new_context_operation_id()
    installed, committed_ids = _commit_materialized_compaction_memory(
        result,
        pipeline,
        lambda: active_messages.__setitem__(slice(None), result.messages),
        lambda: active_messages.__setitem__(slice(None), original_messages),
        event_sink=sink,
        context_operation_id=context_operation_id,
    )
    pipeline.feedback(False)

    assert installed is True
    assert committed_ids == (entry_id,)
    assert active_messages == result.messages
    assert pipeline.injected_memory_ids == (entry_id,)
    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.injection_count == 0
    assert stored.failure_count == 1
    assert len(sink.batches) == 1
    provenance = sink.batches[0]
    assert provenance.origin == "context_compaction"
    assert provenance.authority_versions == result.memory_receipt.rendered_versions
    assert provenance.receipt_id == result.memory_receipt.receipt_id
    assert provenance.context_operation_id == context_operation_id


def test_post_materialize_snapshot_error_rolls_back_compaction_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.agent_loop import _commit_materialized_compaction_memory

    pipeline, manager, _entry_id = _memory_pipeline(tmp_path, monkeypatch)
    original_messages = _long_checkout_transcript()
    active_messages = list(original_messages)
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_TranscriptSummarizer(),
    )
    result = compactor.execute_strategy(
        original_messages,
        CompactStrategy.SESSION_MEMORY,
    )
    original_snapshot = manager.retrieval_snapshot

    @contextmanager
    def fail_after_yield():
        with original_snapshot() as revision:
            yield revision
        raise MemoryStoreConflict("controlled post-materialize conflict")

    monkeypatch.setattr(manager, "retrieval_snapshot", fail_after_yield)

    installed, committed_ids = _commit_materialized_compaction_memory(
        result,
        pipeline,
        lambda: active_messages.__setitem__(slice(None), result.messages),
        lambda: active_messages.__setitem__(slice(None), original_messages),
    )

    assert installed is False
    assert committed_ids == ()
    assert active_messages == original_messages
    assert pipeline.injected_memory_ids == ()


def test_post_materialize_snapshot_error_rolls_back_task_start_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, manager, _entry_id = _memory_pipeline(tmp_path, monkeypatch)
    pipeline.read(
        "fix checkout total rounding",
        _record_retrieval=False,
    )
    cached_result = pipeline._last_retrieval_result
    assert cached_result is not None and cached_result.prompt_text

    def use_cached_result(*_args, **_kwargs):
        pipeline._last_retrieval_result = cached_result
        return []

    original_snapshot = manager.retrieval_snapshot

    @contextmanager
    def fail_after_yield():
        with original_snapshot() as revision:
            yield revision
        raise MemoryStoreConflict("controlled task prompt conflict")

    monkeypatch.setattr(pipeline, "read", use_cached_result)
    monkeypatch.setattr(manager, "retrieval_snapshot", fail_after_yield)
    messages = [{"role": "system", "content": "SYSTEM"}]

    returned = pipeline.inject(
        "fix checkout total rounding",
        [],
        messages,
    )

    assert returned == [{"role": "system", "content": "SYSTEM"}]
    assert pipeline.injected_memory_ids == ()
    assert pipeline._last_retrieval_result is not None
    assert pipeline._last_retrieval_result.rendered_ids == ()


def test_task_start_prompt_uses_same_ledger_when_accounting_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, manager, entry_id = _memory_pipeline(tmp_path, monkeypatch)

    def fail_accounting(
        _selected_ids: list[str],
        _rendered_ids: list[str],
    ) -> None:
        raise OSError("controlled combined-accounting failure")

    monkeypatch.setattr(
        manager,
        "record_retrievals_and_injections",
        fail_accounting,
    )
    messages = pipeline.inject(
        "fix checkout total rounding",
        [],
        [{"role": "system", "content": "SYSTEM"}],
    )
    pipeline.feedback(False)

    assert "sum decimal line amounts" in messages[0]["content"]
    assert pipeline.injected_memory_ids == (entry_id,)
    stored = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert stored.injection_count == 0
    assert stored.failure_count == 1


def test_dispatcher_success_state_waits_for_materialized_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, _manager, _entry_id = _memory_pipeline(tmp_path, monkeypatch)
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_TranscriptSummarizer(),
    )
    result = compactor.execute_strategy(
        _long_checkout_transcript(),
        CompactStrategy.SESSION_MEMORY,
    )

    assert result.materialization_state is CompactionMaterializationState.PENDING
    assert compactor.auto_compact.get_history() == []
    assert compactor.auto_compact.is_warning_suppressed() is False

    assert compactor.confirm_materialized(result) is True

    assert result.materialization_state is CompactionMaterializationState.INSTALLED
    assert compactor.auto_compact.get_history() == [result.boundary]
    assert compactor.auto_compact.is_warning_suppressed() is True


def test_rejected_prompt_candidate_records_failure_not_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, _manager, _entry_id = _memory_pipeline(tmp_path, monkeypatch)
    original_messages = _long_checkout_transcript()
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_TranscriptSummarizer(),
    )
    result = compactor.execute_strategy(
        original_messages,
        CompactStrategy.SESSION_MEMORY,
    )

    assert compactor.discard_unmaterialized(result, original_messages) is True

    assert result.materialization_state is CompactionMaterializationState.REJECTED
    assert result.effective is False
    assert result.error == "Memory authority changed before compaction install"
    assert compactor.auto_compact.get_history() == []
    assert compactor.auto_compact.consecutive_failures == 1
    assert compactor.auto_compact.is_warning_suppressed() is False


def test_process_request_rejection_invalidates_outer_and_last_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, _manager, _entry_id = _memory_pipeline(tmp_path, monkeypatch)
    original_messages = _long_checkout_transcript()
    compactor = ContextCompactor(
        context_window=12_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_TranscriptSummarizer(),
    )
    outer_result = compactor.process_request(original_messages)
    inner_result = compactor.last_result

    assert outer_result.materialization_state is CompactionMaterializationState.PENDING
    assert inner_result is not None and inner_result.effective

    assert compactor.discard_unmaterialized(
        outer_result,
        original_messages,
    ) is True

    assert outer_result.materialization_state is CompactionMaterializationState.REJECTED
    assert outer_result.effective is False
    assert inner_result.materialization_state is CompactionMaterializationState.REJECTED
    assert inner_result.effective is False


def test_rejected_session_candidate_keeps_memory_free_prefix_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, _manager, _entry_id = _memory_pipeline(tmp_path, monkeypatch)
    original_messages = _tool_heavy_checkout_transcript()
    compactor = ContextCompactor(
        context_window=12_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_TranscriptSummarizer(),
    )
    compactor._microcompact._state.last_time_based_compact = 0.0

    safe_messages = compactor.compact_messages(original_messages)

    assert safe_messages != original_messages
    assert all("Relevant Durable Memory" not in str(message) for message in safe_messages)
    assert any(message.get("_persisted_path") for message in safe_messages)
    assert any(message.get("_microcompacted") for message in safe_messages)
    assert compactor._microcompact._state.total_tokens_cleared > 0
    assert compactor.auto_compact.consecutive_failures == 1


@pytest.mark.parametrize("first_result_name", ["outer", "inner"])
def test_shared_receipt_confirmation_is_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_result_name: str,
) -> None:
    pipeline, _manager, _entry_id = _memory_pipeline(tmp_path, monkeypatch)
    compactor = ContextCompactor(
        context_window=20_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_TranscriptSummarizer(),
    )
    outer_result = compactor.process_request(_long_checkout_transcript())
    inner_result = compactor.last_result
    assert inner_result is not None
    first_result = outer_result if first_result_name == "outer" else inner_result
    second_result = inner_result if first_result_name == "outer" else outer_result

    assert compactor.confirm_materialized(first_result) is True
    assert compactor.confirm_materialized(second_result) is False

    assert len(compactor.auto_compact.get_history()) == 1
    assert outer_result.materialization_state is CompactionMaterializationState.INSTALLED
    assert inner_result.materialization_state is CompactionMaterializationState.INSTALLED


@pytest.mark.parametrize("first_result_name", ["outer", "inner"])
def test_shared_receipt_rejection_is_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_result_name: str,
) -> None:
    pipeline, _manager, _entry_id = _memory_pipeline(tmp_path, monkeypatch)
    original_messages = _long_checkout_transcript()
    compactor = ContextCompactor(
        context_window=20_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_TranscriptSummarizer(),
    )
    outer_result = compactor.process_request(original_messages)
    inner_result = compactor.last_result
    assert inner_result is not None
    first_result = outer_result if first_result_name == "outer" else inner_result
    second_result = inner_result if first_result_name == "outer" else outer_result

    assert compactor.discard_unmaterialized(
        first_result,
        original_messages,
    ) is True
    assert compactor.discard_unmaterialized(
        second_result,
        original_messages,
    ) is False

    assert compactor.auto_compact.consecutive_failures == 1
    assert compactor.auto_compact.get_history() == []
    assert outer_result.materialization_state is CompactionMaterializationState.REJECTED
    assert inner_result.materialization_state is CompactionMaterializationState.REJECTED
    assert outer_result.effective is False
    assert inner_result.effective is False


def test_cybernetic_feedback_waits_for_materialization_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, _manager, _entry_id = _memory_pipeline(tmp_path, monkeypatch)
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_TranscriptSummarizer(),
    )
    cybernetics = ContextCyberneticsOrchestrator(compactor)
    cybernetics.selector.select = lambda **_kwargs: ControlAction(
        compaction_intensity=0.5,
        strategy=CompactStrategy.SESSION_MEMORY,
        force_execution=True,
        reason="controlled materialization test",
    )

    _messages, result, _action = cybernetics.run_cycle(
        _long_checkout_transcript(),
        turn_id=1,
    )

    assert result is not None and result.effective
    assert result.materialization_state is CompactionMaterializationState.PENDING
    assert cybernetics.feedback.get_stats()["total_compactions"] == 0
    assert cybernetics.finalize_materialization(result, installed=True) is False
    assert cybernetics.feedback.get_stats()["total_compactions"] == 0

    assert compactor.confirm_materialized(result) is True
    assert cybernetics.finalize_materialization(result, installed=False) is False
    assert cybernetics.feedback.get_stats()["total_compactions"] == 0
    assert cybernetics.finalize_materialization(result, installed=True) is True

    stats = cybernetics.feedback.get_stats()
    assert stats["total_compactions"] == 1
    assert stats["effective_compactions"] == 1


def test_cybernetic_feedback_does_not_finalize_if_dispatcher_transition_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.agent_loop import _finalize_compaction_materialization_state

    pipeline, _manager, _entry_id = _memory_pipeline(tmp_path, monkeypatch)
    compactor = ContextCompactor(
        context_window=30_000,
        workspace=tmp_path,
        memory_manager=pipeline,
        estimate_fn=lambda message: len(str(message.get("content", ""))),
        config=AutoCompactConfig(max_expand_tokens=2_500, min_keep_messages=2),
        summary_generator=_TranscriptSummarizer(),
    )
    result = compactor.execute_strategy(
        _long_checkout_transcript(),
        CompactStrategy.SESSION_MEMORY,
    )

    class BrokenCompactor:
        @staticmethod
        def confirm_materialized(_result) -> bool:
            raise MemoryStoreBusy("controlled dispatcher failure")

    class FeedbackSpy:
        def __init__(self) -> None:
            self.calls = 0

        def finalize_materialization(self, _result, *, installed: bool) -> bool:
            self.calls += 1
            return installed

    feedback = FeedbackSpy()
    _finalize_compaction_materialization_state(
        result,
        BrokenCompactor(),
        installed=True,
        original_messages=_long_checkout_transcript(),
        context_cybernetics=feedback,
    )

    assert result.materialization_state is CompactionMaterializationState.PENDING
    assert feedback.calls == 0
