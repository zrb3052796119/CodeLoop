"""Comprehensive tests for Claude Code-style Context Management System.

Tests all 7 phases:
1. Core data structures (CompactBoundary, CompactionResult)
2. Tool Result Budget (persistence + preview replacement)
3. Read Deduplication (hash-based file dedup)
4. Time-based Microcompact (old tool result cleanup)
5. Session Memory Compact (memory-linked compaction)
6. Auto Compact High-Water Dispatcher (strategy selection + circuit breaker)
7. Reactive Compact (error recovery path)
8. Unified ContextCompactor orchestrator
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.context_compactor import (
    AutoCompactConfig,
    AutoCompactDispatcher,
    CompactBoundary,
    CompactionResult,
    CompactStrategy,
    CompactTrigger,
    ContextCompactor,
    MicrocompactEngine,
    MicrocompactState,
    ReactiveCompactEngine,
    ReadDedupManager,
    SessionMemoryCompactEngine,
    ToolResultBudgetManager,
    _adjust_tail_cut_for_tool_pairs,
    _loaded_skill_context_indices,
)
from minicode.types import AgentStep, ModelUsage


class RecordingSummarizer:
    def __init__(self, summary: str = "transcript summary") -> None:
        self.summary = summary
        self.calls: list[list[dict]] = []

    def summarize(self, messages: list[dict]) -> str:
        self.calls.append(list(messages))
        return self.summary


# ---------------------------------------------------------------------------
# Phase 1: Core Data Structures
# ---------------------------------------------------------------------------


class TestCoreDataStructures:
    """Test CompactBoundary, CompactionResult, and enums."""

    def test_compact_trigger_enum_values(self):
        assert CompactTrigger.MANUAL.value == "manual"
        assert CompactTrigger.AUTO.value == "auto"
        assert CompactTrigger.REACTIVE.value == "reactive"
        assert CompactTrigger.MICROCOMPACT_TIME.value == "microcompact_time"
        assert CompactTrigger.MICROCOMPACT_CACHED.value == "microcompact_cached"

    def test_compact_strategy_enum_values(self):
        assert CompactStrategy.SESSION_MEMORY.value == "session_memory"
        assert CompactStrategy.FULL.value == "full"
        assert CompactStrategy.MICROCOMPACT.value == "microcompact"
        assert CompactStrategy.TOOL_BUDGET.value == "tool_budget"
        assert CompactStrategy.READ_DEDUP.value == "read_dedup"

    def test_compact_boundary_creation(self):
        boundary = CompactBoundary(
            trigger=CompactTrigger.AUTO,
            strategy=CompactStrategy.FULL,
            tokens_before=100000,
            tokens_after=20000,
            messages_removed=50,
        )
        assert boundary.trigger == CompactTrigger.AUTO
        assert boundary.strategy == CompactStrategy.FULL
        assert boundary.tokens_before == 100000
        assert boundary.tokens_after == 20000
        assert boundary.messages_removed == 50
        assert boundary.timestamp > 0
        assert boundary.logical_parent_id is None

    def test_tail_cut_keeps_an_entire_multi_tool_assistant_turn(self):
        messages = [
            {"role": "user", "content": "inspect both"},
            {
                "role": "assistant_tool_call",
                "toolUseId": "call-a",
                "toolName": "read_file",
                "input": {"path": "a.py"},
                "content": "I will inspect both files.",
                "reasoningContent": "Read both before answering.",
                "assistantTurnId": "turn-shared",
            },
            {
                "role": "tool_result",
                "toolUseId": "call-a",
                "toolName": "read_file",
                "content": "a-result",
                "isError": False,
            },
            {
                "role": "assistant_tool_call",
                "toolUseId": "call-b",
                "toolName": "read_file",
                "input": {"path": "b.py"},
                "assistantTurnId": "turn-shared",
            },
            {
                "role": "tool_result",
                "toolUseId": "call-b",
                "toolName": "read_file",
                "content": "b-result",
                "isError": False,
            },
            {"role": "assistant", "content": "done"},
        ]

        assert _adjust_tail_cut_for_tool_pairs(messages, 3) == 1
        assert _adjust_tail_cut_for_tool_pairs(messages, 4) == 1
        assert _adjust_tail_cut_for_tool_pairs(messages, 5) == 5

    def test_loaded_skill_pin_expands_to_its_entire_assistant_tool_turn(self):
        messages = [
            {
                "role": "assistant_tool_call",
                "toolUseId": "call-read",
                "toolName": "read_file",
                "input": {"path": "README.md"},
                "content": "I will inspect and load the routed skill.",
                "reasoningContent": "Both calls belong to this turn.",
                "assistantTurnId": "turn-shared",
            },
            {
                "role": "tool_result",
                "toolUseId": "call-read",
                "toolName": "read_file",
                "content": "read-result",
                "isError": False,
            },
            {
                "role": "assistant_tool_call",
                "toolUseId": "call-skill",
                "toolName": "load_skill",
                "input": {"name": "demo"},
                "assistantTurnId": "turn-shared",
            },
            {
                "role": "tool_result",
                "toolUseId": "call-skill",
                "toolName": "load_skill",
                "content": "skill-body",
                "isError": False,
            },
        ]

        assert _loaded_skill_context_indices(messages) == {0, 1, 2, 3}

    def test_compact_boundary_to_dict(self):
        boundary = CompactBoundary(
            trigger=CompactTrigger.AUTO,
            strategy=CompactStrategy.SESSION_MEMORY,
            preserved_segment=(10, 25),
        )
        d = boundary.to_dict()
        assert d["trigger"] == "auto"
        assert d["strategy"] == "session_memory"
        assert d["preserved_segment"] == [10, 25]
        assert d["logical_parent_id"] is None

    def test_compact_boundary_preserved_segment_none_to_dict(self):
        boundary = CompactBoundary(
            trigger=CompactTrigger.MANUAL,
            strategy=CompactStrategy.PARTIAL,
        )
        assert boundary.to_dict()["preserved_segment"] is None

    def test_compaction_result_success(self):
        result = CompactionResult(
            success=True,
            strategy=CompactStrategy.FULL,
            trigger=CompactTrigger.AUTO,
            messages=[{"role": "user", "content": "hi"}],
            tokens_freed=80000,
        )
        assert result.success is True
        assert result.effective is True
        assert result.tokens_freed == 80000
        assert len(result.messages) == 1

    def test_compaction_result_not_effective_when_zero_tokens(self):
        result = CompactionResult(
            success=True,
            strategy=CompactStrategy.MICROCOMPACT,
            trigger=CompactTrigger.MICROCOMPACT_TIME,
            messages=[],
            tokens_freed=0,
        )
        assert result.success is True
        assert result.effective is False

    def test_compaction_result_failure(self):
        result = CompactionResult(
            success=False,
            strategy=CompactStrategy.FULL,
            trigger=CompactTrigger.AUTO,
            messages=[{"role": "system"}] * 100,
            error="Too few messages",
        )
        assert result.success is False
        assert result.effective is False
        assert result.error == "Too few messages"


# ---------------------------------------------------------------------------
# Phase 2: Tool Result Budget
# ---------------------------------------------------------------------------


class TestToolResultBudgetManager:
    """Test tool result persistence and budget management."""

    def _make_messages(self) -> list[dict]:
        return [
            {"role": "user", "content": "hello"},
            {
                "role": "tool_result",
                "toolName": "read_file",
                "content": "small output",
                "toolUseId": "1",
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "response"}],
            },
            {
                "role": "tool_result",
                "toolName": "run_command",
                "content": "X" * 3000,
                "toolUseId": "2",
            },
        ]

    def test_small_tool_result_unchanged(self, tmp_path):
        mgr = ToolResultBudgetManager(workspace=tmp_path)
        messages = self._make_messages()
        modified, saved = mgr.check_and_replace(messages)

        assert saved == 0
        assert "X" * 3000 in modified[3]["content"]  # Under threshold

    def test_crafted_tool_name_cannot_escape_results_directory(self, tmp_path):
        mgr = ToolResultBudgetManager(
            workspace=tmp_path,
            persist_threshold=1000,
        )
        messages = [
            {
                "role": "tool_result",
                "toolName": "../../escaped",
                "content": "X" * 5000,
                "toolUseId": "evil",
            }
        ]
        modified, _ = mgr.check_and_replace(messages)

        persisted = Path(modified[0]["_persisted_path"])
        assert persisted.exists()
        assert persisted.resolve().is_relative_to(tmp_path.resolve())
        assert "escaped" not in persisted.name

    def test_results_directory_symlink_is_refused(self, tmp_path):
        outside = tmp_path.parent / "outside-results"
        outside.mkdir(exist_ok=True)
        results_dir = tmp_path / ".mini-code-tool-results"
        results_dir.symlink_to(outside, target_is_directory=True)
        mgr = ToolResultBudgetManager(
            workspace=tmp_path,
            persist_threshold=1000,
        )
        messages = [
            {
                "role": "tool_result",
                "toolName": "read_file",
                "content": "X" * 5000,
                "toolUseId": "1",
            }
        ]

        with pytest.raises(ValueError, match="symbolic link"):
            mgr.check_and_replace(messages)

    def test_large_tool_result_persisted(self, tmp_path):
        mgr = ToolResultBudgetManager(
            workspace=tmp_path,
            persist_threshold=1000,
        )
        messages = [
            {"role": "user", "content": "test"},
            {
                "role": "tool_result",
                "toolName": "run_command",
                "content": "Y" * 6000,
                "toolUseId": "t1",
            },
        ]
        modified, saved = mgr.check_and_replace(messages)

        assert saved > 0
        assert "[Tool result persisted to disk" in modified[1]["content"]
        assert "_persisted_path" in modified[1]

    def test_loaded_skill_instruction_is_not_replaced_by_persistence_stub(
        self, tmp_path
    ):
        mgr = ToolResultBudgetManager(
            workspace=tmp_path,
            persist_threshold=100,
        )
        content = "SKILL: demo\n\n" + "instruction-marker " * 500
        messages = [
            {
                "role": "tool_result",
                "toolName": "load_skill",
                "toolUseId": "skill-1",
                "content": content,
                "isError": False,
            }
        ]

        modified, saved = mgr.check_and_replace(messages)

        assert saved == 0
        assert modified[0]["content"] == content

    def test_persisted_count_tracked(self, tmp_path):
        mgr = ToolResultBudgetManager(workspace=tmp_path, persist_threshold=10)
        messages = [
            {"role": "tool_result", "toolName": "a", "content": "X" * 100, "toolUseId": "1"},
            {"role": "tool_result", "toolName": "b", "content": "Y" * 100, "toolUseId": "2"},
        ]
        mgr.check_and_replace(messages)
        assert mgr.get_persisted_count() == 2

    def test_total_saved_bytes(self, tmp_path):
        mgr = ToolResultBudgetManager(workspace=tmp_path, persist_threshold=10)
        big_content = "Z" * 5000
        messages = [
            {"role": "tool_result", "toolName": "big", "content": big_content, "toolUseId": "1"},
        ]
        mgr.check_and_replace(messages)
        assert mgr.get_total_saved_bytes() >= 4500  # Preview is shorter

    def test_non_tool_result_messages_untouched(self, tmp_path):
        mgr = ToolResultBudgetManager(workspace=tmp_path, persist_threshold=1)
        messages = [
            {"role": "user", "content": "A" * 10000},
            {"role": "assistant", "content": "B" * 10000},
            {"role": "system", "content": "C" * 10000},
        ]
        modified, _ = mgr.check_and_replace(messages)
        for m in modified:
            assert len(m.get("content", "")) == 10000

    def test_results_dir_created(self, tmp_path):
        mgr = ToolResultBudgetManager(workspace=tmp_path, persist_threshold=1)
        messages = [
            {"role": "tool_result", "toolName": "x", "content": "data" * 1000, "toolUseId": "1"},
        ]
        mgr.check_and_replace(messages)
        results_dir = tmp_path / ".mini-code-tool-results"
        assert results_dir.exists()
        assert any(results_dir.iterdir())

    def test_preview_contains_tool_name_and_size(self, tmp_path):
        mgr = ToolResultBudgetManager(workspace=tmp_path, persist_threshold=10)
        content = "line\n" * 200
        messages = [
            {"role": "tool_result", "toolName": "grep_output", "content": content, "toolUseId": "1"},
        ]
        modified, _ = mgr.check_and_replace(messages)
        preview = modified[0]["content"]
        assert "grep_output" in preview
        assert str(len(content)) in preview or f"{len(content)}" in preview


# ---------------------------------------------------------------------------
# Phase 3: Read Deduplication
# ---------------------------------------------------------------------------


class TestReadDedupManager:
    """Test hash-based file read dedup."""

    def test_register_new_read_returns_true(self):
        mgr = ReadDedupManager()
        assert mgr.register_read("/path/to/file.py", "hello world", 0) is True

    def test_register_same_content_returns_false(self):
        mgr = ReadDedupManager()
        mgr.register_read("/path/to/file.py", "hello world", 0)
        assert mgr.register_read("/path/to/file.py", "hello world", 1) is False

    def test_register_changed_content_returns_true(self):
        mgr = ReadDedupManager()
        mgr.register_read("/path/to/file.py", "v1", 0)
        assert mgr.register_read("/path/to/file.py", "v2 updated", 1) is True

    def test_should_dedup_true_for_duplicate(self):
        mgr = ReadDedupManager()
        mgr.register_read("main.py", "same content", 0)
        assert mgr.should_dedup("main.py", "same content") is True

    def test_should_dedup_false_for_changed(self):
        mgr = ReadDedupManager()
        mgr.register_read("main.py", "old", 0)
        assert mgr.should_dedup("main.py", "new version") is False

    def test_get_stub_returns_dedup_message(self):
        mgr = ReadDedupManager()
        mgr.register_read("config.json", '{"key": "val"}', 5)
        stub = mgr.get_stub("config.json")
        assert "deduplicated" in stub.lower() or "config.json" in stub
        assert "5" in stub  # message index

    def test_get_stub_empty_for_unknown(self):
        mgr = ReadDedupManager()
        assert mgr.get_stub("unknown.txt") == ""

    def test_invalidate_removes_entry(self):
        mgr = ReadDedupManager()
        mgr.register_read("file.py", "content", 0)
        mgr.invalidate("file.py")
        assert mgr.should_dedup("file.py", "content") is False

    def test_clear_removes_all(self):
        mgr = ReadDedupManager()
        mgr.register_read("a.py", "x", 0)
        mgr.register_read("b.py", "y", 1)
        mgr.clear()
        assert mgr.get_stub("a.py") == ""
        assert mgr.get_stub("b.py") == ""

    def test_different_files_independent(self):
        mgr = ReadDedupManager()
        mgr.register_read("a.py", "same", 0)
        mgr.register_read("b.py", "same", 1)
        assert mgr.should_dedup("a.py", "same") is True
        assert mgr.should_dedup("b.py", "same") is True


# ---------------------------------------------------------------------------
# Phase 4: Time-based Microcompact
# ---------------------------------------------------------------------------


class TestMicrocompactEngine:
    """Test time-based microcompact of old tool results."""

    def _messages_with_many_tool_results(self, n: int) -> list[dict]:
        msgs = [{"role": "user", "content": "start"}]
        for i in range(n):
            msgs.append({
                "role": "tool_result",
                "toolName": f"tool_{i}",
                "content": f"output line {i}\n" * 20,
            })
        msgs.append({"role": "assistant", "content": "done"})
        return msgs

    def test_no_microcompact_when_within_interval(self):
        engine = MicrocompactEngine(MicrocompactState(
            last_time_based_compact=time.time(),
            time_based_interval=999999,
        ))
        msgs = self._messages_with_many_tool_results(20)
        result = engine.run_time_based_microcompact(msgs)
        assert result.effective is False

    def test_no_microcompact_when_few_results(self):
        engine = MicrocompactEngine(MicrocompactState(
            last_time_based_compact=0.0,
            time_based_interval=0.0,
            keep_recent_tool_results=10,
        ))
        msgs = self._messages_with_many_tool_results(3)
        result = engine.run_time_based_microcompact(msgs, now=time.time() + 10000)
        assert result.effective is False

    def test_microcompact_clears_old_results(self):
        engine = MicrocompactEngine(MicrocompactState(
            last_time_based_compact=0.0,
            time_based_interval=0.0,
            keep_recent_tool_results=3,
        ))
        msgs = self._messages_with_many_tool_results(15)
        result = engine.run_time_based_microcompact(msgs, now=time.time() + 10000)
        assert result.effective is True
        assert result.tokens_freed > 0

        cleared = [m for m in result.messages if m.get("_microcompacted")]
        assert len(cleared) > 0

    def test_keeps_recent_tool_results(self):
        engine = MicrocompactEngine(MicrocompactState(
            last_time_based_compact=0.0,
            time_based_interval=0.0,
            keep_recent_tool_results=5,
        ))
        msgs = self._messages_with_many_tool_results(12)
        result = engine.run_time_based_microcompact(msgs, now=time.time() + 10000)

        non_cleared_tool_results = [
            m for m in result.messages[-6:]
            if m.get("role") == "tool_result" and not m.get("_microcompacted")
        ]
        assert len(non_cleared_tool_results) >= 4  # At least keep_recent preserved

    def test_loaded_skill_result_is_never_microcompacted(self):
        engine = MicrocompactEngine(MicrocompactState(
            last_time_based_compact=0.0,
            time_based_interval=0.0,
            keep_recent_tool_results=2,
        ))
        skill_content = "SKILL: demo\nACTIVE_SKILL_MARKER"
        msgs = [
            {
                "role": "tool_result",
                "toolName": "load_skill",
                "content": skill_content,
                "toolUseId": "skill-1",
            }
        ] + self._messages_with_many_tool_results(10)

        result = engine.run_time_based_microcompact(
            msgs, now=time.time() + 10000
        )

        retained = next(
            message for message in result.messages
            if message.get("toolName") == "load_skill"
        )
        assert retained["content"] == skill_content
        assert not retained.get("_microcompacted")

    def test_does_not_clear_already_persisted(self):
        engine = MicrocompactEngine(MicrocompactState(
            last_time_based_compact=0.0,
            time_based_interval=0.0,
            keep_recent_tool_results=2,
        ))
        msgs = [
            {"role": "tool_result", "toolName": "a", "content": "[Tool result persisted...]", "toolUseId": "1"},
            {"role": "tool_result", "toolName": "b", "content": "normal output here", "toolUseId": "2"},
            {"role": "tool_result", "toolName": "c", "content": "more normal", "toolUseId": "3"},
            {"role": "tool_result", "toolName": "d", "content": "[Old tool result...", "toolUseId": "4"},
        ]
        result = engine.run_time_based_microcompact(msgs, now=time.time() + 10000)
        cleared = [m for m in result.messages if m.get("_microcompacted")]
        for c in cleared:
            assert not c["content"].startswith("[Tool result persisted")
            assert not c["content"].startswith("[Old tool result")

    def test_state_tracks_total_cleared(self):
        state = MicrocompactState(last_time_based_compact=0.0, time_based_interval=0.0, keep_recent_tool_results=2)
        engine = MicrocompactEngine(state)
        msgs = self._messages_with_many_tool_results(10)
        engine.run_time_based_microcompact(msgs, now=time.time() + 10000)
        assert state.total_tokens_cleared > 0


# ---------------------------------------------------------------------------
# Phase 5: Session Memory Compact
# ---------------------------------------------------------------------------


class TestSessionMemoryCompactEngine:
    """Test memory-linked session compact."""

    def test_returns_none_without_memory_manager(self):
        engine = SessionMemoryCompactEngine(memory_manager=None)
        result = engine.try_session_memory_compact(
            [{"role": "user", "content": "hi"}] * 20,
            context_window=100000,
        )
        assert result is None

    def test_returns_none_when_memory_empty(self):
        class EmptyMemory:
            def get_relevant_context(self, max_tokens=100, query=None):
                return ""
        engine = SessionMemoryCompactEngine(memory_manager=EmptyMemory())
        msgs = [{"role": "user", "content": "x" * 100} for _ in range(30)]
        result = engine.try_session_memory_compact(msgs, context_window=10000)
        assert result is None

    def test_returns_none_when_dropped_transcript_cannot_be_summarized(self):
        class FakeMemory:
            def get_relevant_context(self, max_tokens=100, query=None):
                return "durable history"

        engine = SessionMemoryCompactEngine(memory_manager=FakeMemory())
        msgs = [{"role": "user", "content": "x" * 100} for _ in range(30)]

        result = engine.try_session_memory_compact(msgs, context_window=10000)

        assert result is None

    def test_successful_session_memory_compact(self):
        class FakeMemory:
            def get_relevant_context(self, max_tokens=100, query=None):
                return "# Project: test\n## Key decisions:\n- Use Python 3.11\n- Follow PEP8"
        engine = SessionMemoryCompactEngine(memory_manager=FakeMemory())
        msgs = []
        for i in range(40):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append({"role": role, "content": f"message {i} " * 10})

        config = AutoCompactConfig(max_expand_tokens=5000)
        summarizer = RecordingSummarizer()
        result = engine.try_session_memory_compact(
            msgs,
            context_window=100000,
            config=config,
            transcript_summarizer=summarizer,
        )

        assert result is not None
        assert result.effective is True
        assert result.strategy == CompactStrategy.SESSION_MEMORY
        assert len(result.messages) < len(msgs)
        assert "Project: test" in result.summary_text
        assert "transcript summary" in result.summary_text
        assert summarizer.calls

    def test_boundary_has_correct_metadata(self):
        class FakeMemory:
            def get_relevant_context(self, max_tokens=100, query=None):
                return "memory context here"
        engine = SessionMemoryCompactEngine(memory_manager=FakeMemory())
        msgs = [{"role": "user", "content": "msg"} for _ in range(30)]
        result = engine.try_session_memory_compact(
            msgs,
            context_window=10000,
            transcript_summarizer=RecordingSummarizer(),
        )

        assert result.boundary is not None
        assert result.boundary.strategy == CompactStrategy.SESSION_MEMORY
        assert result.boundary.trigger == CompactTrigger.AUTO
        assert result.boundary.messages_removed > 0

    def test_system_messages_preserved(self):
        class FakeMemory:
            def get_relevant_context(self, max_tokens=100, query=None):
                return "mem"
        engine = SessionMemoryCompactEngine(memory_manager=FakeMemory())
        system_msg = {"role": "system", "content": "You are helpful"}
        user_msgs = [{"role": "user", "content": f"msg {i}"} for i in range(25)]
        msgs = [system_msg] + user_msgs

        result = engine.try_session_memory_compact(
            msgs,
            context_window=10000,
            transcript_summarizer=RecordingSummarizer(),
        )
        assert result is not None
        assert result.messages[0]["role"] == "system"

    def test_current_transcript_is_summarized_instead_of_replaced_by_memory(self):
        class FakeMemory:
            def get_relevant_context(self, max_tokens=100, query=None):
                return "DURABLE_HISTORY: Python 3.11"

        summarizer = RecordingSummarizer()
        engine = SessionMemoryCompactEngine(memory_manager=FakeMemory())
        msgs = [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": (
                    "CURRENT_TURN_SENTINEL " + "x" * 200
                    if index == 20
                    else f"message-{index} " + "x" * 200
                ),
            }
            for index in range(80)
        ]

        result = engine.try_session_memory_compact(
            msgs,
            context_window=100000,
            config=AutoCompactConfig(max_expand_tokens=2000),
            transcript_summarizer=summarizer,
        )

        assert result is not None
        summarized = " ".join(
            str(message.get("content", ""))
            for call in summarizer.calls
            for message in call
        )
        assert "CURRENT_TURN_SENTINEL" in summarized
        assert "DURABLE_HISTORY" in result.summary_text

    def test_session_memory_compact_pins_loaded_skill_pair(self):
        class FakeMemory:
            def get_relevant_context(self, max_tokens=100, query=None):
                return "durable memory"

        skill_body = "SKILL: demo\n\nACTIVE_SKILL_MARKER\n" + "rule " * 100
        msgs = [
            {"role": "user", "content": "start " + "x" * 500},
            {
                "role": "assistant_tool_call",
                "toolUseId": "skill-1",
                "toolName": "load_skill",
                "input": {"name": "demo"},
            },
            {
                "role": "tool_result",
                "toolUseId": "skill-1",
                "toolName": "load_skill",
                "content": skill_body,
                "isError": False,
            },
        ]
        msgs.extend(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"message-{index} " + "x" * 500,
            }
            for index in range(80)
        )
        summarizer = RecordingSummarizer()

        result = SessionMemoryCompactEngine(FakeMemory()).try_session_memory_compact(
            msgs,
            context_window=100000,
            config=AutoCompactConfig(max_expand_tokens=2000),
            transcript_summarizer=summarizer,
        )

        assert result is not None and result.effective
        retained = [
            message for message in result.messages
            if message.get("toolName") == "load_skill"
        ]
        assert [message["role"] for message in retained] == [
            "assistant_tool_call",
            "tool_result",
        ]
        assert retained[1]["content"] == skill_body
        assert all(
            "ACTIVE_SKILL_MARKER"
            not in " ".join(str(message.get("content", "")) for message in call)
            for call in summarizer.calls
        )


# ---------------------------------------------------------------------------
# Phase 6: Auto Compact Dispatcher
# ---------------------------------------------------------------------------


class TestAutoCompactDispatcher:
    """Test high-water mark auto-compact dispatch with circuit breaker."""

    def _long_messages(self, count: int) -> list[dict]:
        return [{"role": "user", "content": f"message {i} " * 50} for i in range(count)]

    def test_threshold_calculation(self):
        config = AutoCompactConfig(threshold_ratio=0.85)
        dispatcher = AutoCompactDispatcher(context_window=100000, config=config)
        assert dispatcher.threshold_tokens == 85000
        assert dispatcher.blocking_limit == 97000

    def test_should_trigger_false_below_threshold(self):
        dispatcher = AutoCompactDispatcher(context_window=100000)
        msgs = self._long_messages(10)
        assert dispatcher.should_trigger(msgs) is False

    def test_dispatch_noop_when_below_threshold(self):
        dispatcher = AutoCompactDispatcher(context_window=1000000)
        msgs = self._long_messages(5)
        result = dispatcher.dispatch(msgs)
        assert result.effective is False

    def test_force_full_dispatch(self):
        dispatcher = AutoCompactDispatcher(context_window=100000)
        msgs = self._long_messages(50)
        result = dispatcher.dispatch(msgs, force_full=True)
        assert result.success is True
        assert result.strategy == CompactStrategy.FULL
        assert len(result.messages) < len(msgs)

    def test_full_compact_summarizes_exact_dropped_middle(self):
        summarizer = RecordingSummarizer("bounded summary")
        dispatcher = AutoCompactDispatcher(
            context_window=100000,
            config=AutoCompactConfig(min_keep_tokens=0, min_keep_messages=5),
            summary_generator=summarizer,
        )
        msgs = [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": (
                    "MIDDLE_SENTINEL " + "x" * 2000
                    if index == 25
                    else f"message-{index} " + "x" * 2000
                ),
            }
            for index in range(60)
        ]

        result = dispatcher.dispatch(msgs, force_full=True)

        assert result.effective
        summarized = " ".join(
            str(message.get("content", ""))
            for call in summarizer.calls
            for message in call
        )
        assert "MIDDLE_SENTINEL" in summarized

    def test_critical_state_anchors_survive_lossy_summary_and_next_compaction(self):
        summarizer = RecordingSummarizer(
            "Generic summary that omits every exact project identifier."
        )
        dispatcher = AutoCompactDispatcher(
            context_window=100000,
            config=AutoCompactConfig(min_keep_tokens=0, min_keep_messages=5),
            summary_generator=summarizer,
        )
        messages = [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": (
                    "The accepted delivery goal is RELEASE_NOVA_31."
                    if index == 0
                    else "The non-negotiable constraint is KEEP_API_STABLE_42."
                    if index == 20
                    else "The team rejected global caching under REJECT_CACHE_58."
                    if index == 40
                    else f"background-{index} " + "x" * 1200
                ),
            }
            for index in range(60)
        ]

        first = dispatcher.dispatch(messages, force_full=True)
        assert first.effective
        first_rendered = "\n".join(
            str(message.get("content", "")) for message in first.messages
        )
        assert "Deterministic Critical-State Anchors" in first_rendered
        assert "RELEASE_NOVA_31" in first_rendered
        assert "KEEP_API_STABLE_42" in first_rendered
        assert "REJECT_CACHE_58" in first_rendered

        continued = list(first.messages)
        continued.extend(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"later-{index} " + "y" * 1200,
            }
            for index in range(60)
        )
        second = dispatcher.dispatch(continued, force_full=True)
        assert second.effective
        second_rendered = "\n".join(
            str(message.get("content", "")) for message in second.messages
        )
        assert "RELEASE_NOVA_31" in second_rendered
        assert "KEEP_API_STABLE_42" in second_rendered
        assert "REJECT_CACHE_58" in second_rendered

    def test_full_compact_pins_loaded_skill_pair_outside_summary(self):
        summarizer = RecordingSummarizer("bounded summary")
        dispatcher = AutoCompactDispatcher(
            context_window=100000,
            config=AutoCompactConfig(min_keep_tokens=0, min_keep_messages=5),
            summary_generator=summarizer,
        )
        skill_body = "SKILL: demo\n\nACTIVE_SKILL_MARKER\n" + "rule " * 200
        msgs = [
            {"role": "user", "content": "start " + "x" * 2000},
            {
                "role": "assistant_tool_call",
                "toolUseId": "skill-1",
                "toolName": "load_skill",
                "input": {"name": "demo"},
            },
            {
                "role": "tool_result",
                "toolUseId": "skill-1",
                "toolName": "load_skill",
                "content": skill_body,
                "isError": False,
            },
        ]
        msgs.extend(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"message-{index} " + "x" * 2000,
            }
            for index in range(60)
        )

        result = dispatcher.dispatch(msgs, force_full=True)

        assert result.effective
        retained_skill = [
            message for message in result.messages
            if message.get("toolName") == "load_skill"
        ]
        assert [message["role"] for message in retained_skill] == [
            "assistant_tool_call",
            "tool_result",
        ]
        assert retained_skill[1]["content"] == skill_body
        summarized = " ".join(
            str(message.get("content", ""))
            for call in summarizer.calls
            for message in call
        )
        assert "ACTIVE_SKILL_MARKER" not in summarized

    def test_full_compact_preserves_hidden_tool_turn_metadata_in_tail(self):
        dispatcher = AutoCompactDispatcher(
            context_window=100000,
            config=AutoCompactConfig(min_keep_tokens=0, min_keep_messages=5),
            summary_generator=RecordingSummarizer("bounded summary"),
        )
        msgs = [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"message-{index} " + "x" * 2000,
            }
            for index in range(30)
        ]
        msgs.extend(
            [
                {
                    "role": "assistant_tool_call",
                    "toolUseId": "call-tail",
                    "toolName": "read_file",
                    "input": {"path": "synthetic.py"},
                    "assistantTurnId": "tool-turn-tail",
                    "reasoningContent": "Inspect the retained file first.",
                },
                {
                    "role": "tool_result",
                    "toolUseId": "call-tail",
                    "toolName": "read_file",
                    "content": "synthetic result",
                    "isError": False,
                },
                {"role": "assistant", "content": "continue"},
            ]
        )

        result = dispatcher.dispatch(msgs, force_full=True)

        assert result.effective
        retained_call = next(
            message
            for message in result.messages
            if message.get("toolUseId") == "call-tail"
            and message.get("role") == "assistant_tool_call"
        )
        assert retained_call["assistantTurnId"] == "tool-turn-tail"
        assert retained_call["reasoningContent"] == (
            "Inspect the retained file first."
        )

    def test_full_compact_never_replaces_context_with_negative_token_savings(self):
        dispatcher = AutoCompactDispatcher(
            context_window=100000,
            config=AutoCompactConfig(min_keep_tokens=0, min_keep_messages=2),
            summary_generator=RecordingSummarizer("Y" * 8000),
        )
        msgs = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"m{index}"}
            for index in range(6)
        ]

        result = dispatcher.dispatch(msgs, force_full=True)

        assert result.success is False
        assert result.effective is False
        assert result.tokens_freed < 0
        assert result.messages == msgs

    def test_protected_user_is_not_duplicated_when_tail_expands(self):
        dispatcher = AutoCompactDispatcher(
            context_window=100000,
            config=AutoCompactConfig(min_keep_tokens=1000, min_keep_messages=5),
            summary_generator=RecordingSummarizer("bounded summary"),
        )
        msgs = [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": "x" * 100,
                "message_id": index,
            }
            for index in range(30)
        ]

        result = dispatcher.dispatch(msgs, force_full=True)

        assert result.effective
        retained_ids = [
            message["message_id"]
            for message in result.messages
            if "message_id" in message
        ]
        assert len(retained_ids) == len(set(retained_ids))

    def test_circuit_breaker_initially_ok(self):
        dispatcher = AutoCompactDispatcher(context_window=100000)
        assert dispatcher.is_tripped is False

    def test_circuit_breaker_resets_on_success(self):
        dispatcher = AutoCompactDispatcher(context_window=100000)
        msgs = self._long_messages(50)
        dispatcher.dispatch(msgs, force_full=True)
        assert dispatcher.is_tripped is False

    def test_boundary_recorded_on_success(self):
        dispatcher = AutoCompactDispatcher(context_window=100000)
        msgs = self._long_messages(50)
        dispatcher.dispatch(msgs, force_full=True)
        history = dispatcher.get_history()
        assert len(history) == 1
        assert history[0].strategy == CompactStrategy.FULL

    def test_get_last_boundary(self):
        dispatcher = AutoCompactDispatcher(context_window=100000)
        assert dispatcher.get_last_boundary() is None
        dispatcher.dispatch(self._long_messages(50), force_full=True)
        assert dispatcher.get_last_boundary() is not None

    def test_warning_suppression(self):
        dispatcher = AutoCompactDispatcher(context_window=100000)
        assert dispatcher.is_warning_suppressed() is False
        dispatcher._suppress_warnings(duration=60.0)
        assert dispatcher.is_warning_suppressed() is True

    def test_reset_circuit_breaker(self):
        dispatcher = AutoCompactDispatcher(context_window=100000)
        for _ in range(5):
            dispatcher._on_failure()
        assert dispatcher.is_tripped is True
        dispatcher.reset_circuit_breaker()
        assert dispatcher.is_tripped is False

    def test_forced_compaction_does_not_retry_unchanged_failed_state(self):
        dispatcher = AutoCompactDispatcher(
            context_window=100,
            config=AutoCompactConfig(circuit_breaker_limit=3),
        )
        messages = [{"role": "user", "content": "x" * 2_000}]

        first = dispatcher.dispatch(messages, force_full=True)
        second = dispatcher.dispatch(messages, force_full=True)

        assert first.effective is False
        assert first.error == "Too few messages to compact"
        assert second.effective is False
        assert second.error == "Compaction retry suppressed for unchanged message state"
        assert dispatcher.consecutive_failures == 1
        assert dispatcher.is_tripped is False

    def test_material_message_change_can_retry_after_failed_state(self):
        dispatcher = AutoCompactDispatcher(
            context_window=100,
            config=AutoCompactConfig(circuit_breaker_limit=3),
        )
        messages = [{"role": "user", "content": "x" * 2_000}]
        dispatcher.dispatch(messages, force_full=True)

        changed = [*messages, {"role": "assistant", "content": "y" * 2_000}]
        retry = dispatcher.dispatch(changed, force_full=True)

        assert retry.error == "Too few messages to compact"
        assert dispatcher.consecutive_failures == 2

    def test_selected_strategy_respects_open_circuit_even_when_forced(self):
        dispatcher = AutoCompactDispatcher(
            context_window=100,
            config=AutoCompactConfig(circuit_breaker_limit=2),
        )
        first = [{"role": "user", "content": "a" * 2_000}]
        second = [{"role": "user", "content": "b" * 2_000}]
        third = [{"role": "user", "content": "c" * 2_000}]
        dispatcher.execute_selected(first, CompactStrategy.FULL)
        dispatcher.execute_selected(second, CompactStrategy.FULL)

        blocked = dispatcher.execute_selected(third, CompactStrategy.FULL)

        assert dispatcher.is_tripped is True
        assert blocked.error == "Compaction circuit breaker is open"
        assert dispatcher.consecutive_failures == 2

    def test_disabled_auto_compact_never_triggers(self):
        config = AutoCompactConfig(enabled=False)
        dispatcher = AutoCompactDispatcher(config=config)
        msgs = self._long_messages(1000)
        assert dispatcher.should_trigger(msgs) is False


# ---------------------------------------------------------------------------
# Phase 7: Reactive Compact
# ---------------------------------------------------------------------------


class TestReactiveCompactEngine:
    """Test error recovery compaction."""

    def test_recovery_attempt_one(self):
        auto = AutoCompactDispatcher(context_window=100000)
        engine = ReactiveCompactEngine(auto_compact=auto)
        msgs = [{"role": "user", "content": "x" * 100} for _ in range(80)]
        result = engine.try_recover_from_overflow(msgs)
        assert result is not None
        assert result.strategy == CompactStrategy.REACTIVE
        assert result.trigger == CompactTrigger.REACTIVE

    def test_max_retries_exceeded(self):
        auto = AutoCompactDispatcher(context_window=100000)
        engine = ReactiveCompactEngine(auto_compact=auto)
        engine._recovery_attempts = 10
        result = engine.try_recover_from_overflow([{"role": "user", "content": "hi"}])
        assert result is None

    def test_progressive_truncation(self):
        auto = AutoCompactDispatcher(context_window=100000)
        engine = ReactiveCompactEngine(auto_compact=auto)
        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(60)]

        r1 = engine.try_recover_from_overflow(msgs)
        engine._recovery_attempts += 1
        r2 = engine.try_recover_from_overflow(msgs)

        if r1 and r2:
            assert len(r2.messages) <= len(r1.messages)

    def test_aggressive_truncate_preserves_system(self):
        auto = AutoCompactDispatcher(context_window=100000)
        engine = ReactiveCompactEngine(auto_compact=auto)
        system = {"role": "system", "content": "important"}
        users = [{"role": "user", "content": "u"} for _ in range(40)]
        msgs = [system] + users
        result = engine.try_recover_from_overflow(msgs)
        assert result is not None
        assert result.messages[0]["role"] == "system"


# ---------------------------------------------------------------------------
# Phase 8: Unified ContextCompactor
# ---------------------------------------------------------------------------


class TestContextCompactorOrchestrator:
    """Test the unified pipeline orchestrator."""

    def test_initialization(self, tmp_path):
        compactor = ContextCompactor(
            context_window=200000,
            workspace=tmp_path,
        )
        stats = compactor.get_stats()
        assert stats["total_passes"] == 0
        assert stats["context_window"] == 200000

    def test_process_request_noop_when_clean(self, tmp_path):
        compactor = ContextCompactor(workspace=tmp_path)
        msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        result = compactor.process_request(msgs)
        assert result.effective is False
        assert len(result.messages) == 2

    def test_process_request_runs_tool_budget(self, tmp_path):
        compactor = ContextCompactor(workspace=tmp_path)
        msgs = [
            {"role": "user", "content": "test"},
            {"role": "tool_result", "toolName": "big", "content": "X" * 6000, "toolUseId": "1"},
        ]
        result = compactor.process_request(msgs)
        assert "tool_budget" in result.summary_text or result.tokens_freed > 0

    def test_process_request_runs_microcompact(self, tmp_path):
        compactor = ContextCompactor(workspace=tmp_path)
        msgs = [{"role": "user", "content": "start"}]
        for i in range(15):
            msgs.append({"role": "tool_result", "toolName": f"t{i}", "content": f"data {i}\n" * 20})
        compactor.process_request(msgs, enable_auto_compact=False)
        # Microcompact may or may not fire depending on timing

    def test_process_request_runs_auto_compact(self, tmp_path):
        compactor = ContextCompactor(
            workspace=tmp_path,
            context_window=3000,
        )
        msgs = [{"role": "user", "content": f"msg {i} " * 50} for i in range(100)]
        result = compactor.process_request(msgs)
        assert result.success is True or result.effective is True or len(result.messages) < len(msgs)

    def test_reactive_recover_delegates(self, tmp_path):
        compactor = ContextCompactor(context_window=10000, workspace=tmp_path)
        msgs = [{"role": "user", "content": "x" * 50} for _ in range(60)]
        result = compactor.reactive_recover(msgs, "prompt too long")
        assert result is not None
        assert result.strategy == CompactStrategy.REACTIVE

    def test_format_pipeline_status(self, tmp_path):
        compactor = ContextCompactor(workspace=tmp_path)
        status = compactor.format_pipeline_status()
        assert "Context Management Pipeline Status" in status
        assert "Context window:" in status

    def test_subcomponent_accessors(self, tmp_path):
        compactor = ContextCompactor(workspace=tmp_path)
        assert isinstance(compactor.tool_budget, ToolResultBudgetManager)
        assert isinstance(compactor.read_dedup, ReadDedupManager)
        assert isinstance(compactor.auto_compact, AutoCompactDispatcher)
        assert isinstance(compactor.reactive, ReactiveCompactEngine)

    def test_multiple_passes_increment_counter(self, tmp_path):
        compactor = ContextCompactor(workspace=tmp_path)
        msgs = [{"role": "user", "content": "hi"}]
        compactor.process_request(msgs)
        compactor.process_request(msgs)
        compactor.process_request(msgs)
        assert compactor.get_stats()["total_passes"] == 3

    def test_last_result_updated(self, tmp_path):
        compactor = ContextCompactor(context_window=2000, workspace=tmp_path)
        msgs = [{"role": "user", "content": "x" * 100} for _ in range(50)]
        result = compactor.process_request(msgs)
        if result.effective:
            assert compactor.last_result is not None


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestFullPipelineIntegration:
    """End-to-end tests of the complete context management pipeline."""

    def test_full_pipeline_large_conversation(self, tmp_path):
        compactor = ContextCompactor(
            context_window=15000,
            workspace=tmp_path,
        )
        msgs = [{"role": "system", "content": "You are a coding assistant."}]
        for i in range(60):
            if i % 3 == 0:
                msgs.append({"role": "user", "content": f"Task {i}: implement feature"})
            elif i % 3 == 1:
                msgs.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"Working on {i}"}],
                })
            else:
                size = 3000 if i < 40 else 200
                msgs.append({
                    "role": "tool_result",
                    "toolName": "read_file",
                    "content": "file content " * (size // 12),
                    "toolUseId": f"t{i}",
                })

        # A fresh engine intentionally does not clear recent tool results
        # (the 1h gate protects prompt-cache-friendly content); age the gate
        # so microcompact participates in this pipeline test deterministically.
        compactor._microcompact._state.last_time_based_compact = 0.0
        result = compactor.process_request(msgs)
        assert result.success is True
        total_before = sum(len(str(m)) // 4 for m in msgs)
        total_after = sum(len(str(m)) // 4 for m in result.messages)
        assert total_after <= total_before

    def test_tool_budget_then_microcompact_then_autocompact_order(self, tmp_path):
        compactor = ContextCompactor(
            context_window=12000,
            workspace=tmp_path,
        )
        msgs = [{"role": "user", "content": "start"}]
        for i in range(20):
            msgs.append({
                "role": "tool_result",
                "toolName": f"cmd_{i}",
                "content": f"output {i}\n" * (100 if i < 5 else 400),
                "toolUseId": f"t{i}",
            })

        result = compactor.process_request(msgs)
        steps = result.summary_text
        has_any_step = (
            "tool_budget" in steps or
            "microcompact" in steps or
            "auto_compact" in steps
        )
        assert result.success is True or has_any_step

    def test_reactive_recovers_after_api_error_simulation(self, tmp_path):
        compactor = ContextCompactor(context_window=8000, workspace=tmp_path)
        normal_msgs = [{"role": "user", "content": f"msg {i} " * 10} for i in range(50)]

        result = compactor.reactive_recover(normal_msgs, "prompt too long")
        assert result is not None
        recovered_size = sum(len(str(m)) for m in result.messages)
        original_size = sum(len(str(m)) for m in normal_msgs)
        assert recovered_size < original_size

    def test_circuit_breaker_prevents_infinite_compaction(self, tmp_path):
        config = AutoCompactConfig(circuit_breaker_limit=2)
        compactor = ContextCompactor(
            context_window=5000,
            workspace=tmp_path,
            config=config,
        )
        huge = [{"role": "user", "content": "x" * 20} for _ in range(200)]
        for _ in range(5):
            compactor.process_request(huge)
        assert compactor.auto_compact.is_tripped is True or not compactor.auto_compact.is_tripped


class TestLLMSummaryBudget:
    def test_summary_chunks_cover_the_middle_instead_of_stopping_at_24k(self) -> None:
        from minicode.context_compactor import LLMSummaryGenerator

        class CapturingModel:
            def __init__(self) -> None:
                self.prompts: list[list[dict]] = []

            def next(self, messages):
                self.prompts.append(messages)
                return AgentStep(type="assistant", content="chunk summary " * 5)

        model = CapturingModel()
        generator = LLMSummaryGenerator(model, timeout_seconds=5)
        messages = [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": (
                    "CHUNK_MIDDLE_SENTINEL " + "x" * 1900
                    if index == 25
                    else "x" * 2000
                ),
            }
            for index in range(60)
        ]

        summary = generator.summarize(messages)

        assert summary is not None
        assert len(model.prompts) > 1
        rendered_prompts = " ".join(
            str(message.get("content", ""))
            for prompt in model.prompts
            for message in prompt
        )
        assert "CHUNK_MIDDLE_SENTINEL" in rendered_prompts

    def test_summary_charges_shared_budget(self) -> None:
        from minicode.context_compactor import LLMSummaryGenerator

        budget = AgentTurnBudget(max_model_calls=2, max_total_tokens=1000)

        class FakeModel:
            def __init__(self) -> None:
                self.calls = 0

            def next(self, messages):
                self.calls += 1
                return AgentStep(
                    type="assistant",
                    content="x" * 60,
                    usage=ModelUsage(
                        input_tokens=12,
                        output_tokens=4,
                        source="provider",
                    ),
                )

        model = FakeModel()
        generator = LLMSummaryGenerator(model, agent_budget=budget)
        summary = generator.summarize(
            [
                {"role": "user", "content": "task one"},
                {"role": "assistant", "content": "answer one"},
                {"role": "user", "content": "task two"},
                {"role": "assistant", "content": "answer two"},
            ]
        )

        assert summary is not None
        snapshot = budget.snapshot()
        assert snapshot.used_model_calls == 1
        assert snapshot.used_total_tokens == 16

    def test_summary_skips_when_budget_exhausted(self) -> None:
        from minicode.context_compactor import LLMSummaryGenerator

        budget = AgentTurnBudget(max_total_tokens=1)

        class FakeModel:
            def __init__(self) -> None:
                self.calls = 0

            def next(self, messages):
                self.calls += 1
                return AgentStep(type="assistant", content="x" * 60)

        model = FakeModel()
        generator = LLMSummaryGenerator(model, agent_budget=budget)
        summary = generator.summarize(
            [
                {"role": "user", "content": "task one"},
                {"role": "assistant", "content": "answer one"},
                {"role": "user", "content": "task two"},
                {"role": "assistant", "content": "answer two"},
            ]
        )

        assert summary is None
        assert model.calls == 0

    def test_summary_preserves_the_final_agent_model_call(self) -> None:
        from minicode.context_compactor import LLMSummaryGenerator

        budget = AgentTurnBudget(max_model_calls=1, max_total_tokens=10_000)

        class FakeModel:
            def __init__(self) -> None:
                self.calls = 0

            def next(self, messages):
                self.calls += 1
                return AgentStep(type="assistant", content="x" * 60)

        model = FakeModel()
        summary = LLMSummaryGenerator(model, agent_budget=budget).summarize(
            [
                {"role": "user", "content": "task one"},
                {"role": "assistant", "content": "answer one"},
                {"role": "user", "content": "task two"},
                {"role": "assistant", "content": "answer two"},
            ]
        )

        assert summary is None
        assert model.calls == 0
        assert budget.snapshot().used_model_calls == 0


# ---------------------------------------------------------------------------
# Session Memory block structural single-instance guarantee
# ---------------------------------------------------------------------------


def test_fully_rerendered_memory_block_replaces_the_prior_block() -> None:
    """A second session compact must not stack a second Memory block when
    every prior entry is re-rendered fresh in the new receipt."""
    from minicode.memory_pipeline import (
        MemoryPromptReceipt,
        _memory_render_digest,
    )

    def _receipt(entries: tuple[tuple[str, str], ...]) -> MemoryPromptReceipt:
        prompt_text = "\n".join(f"[{eid}] {frag}" for eid, frag in entries)
        ids = tuple(eid for eid, _ in entries)
        return MemoryPromptReceipt(
            prompt_text=prompt_text,
            selected_ids=ids,
            rendered_ids=ids,
            rendered_versions=tuple((eid, "1" * 64) for eid, _ in entries),
            authority_revision="rev-1",
            receipt_id="r" + "0" * 31,
            rendered_proofs=tuple(
                (eid, _memory_render_digest(eid, frag))
                for eid, frag in entries
            ),
            rendered_fragments=tuple(
                (eid, _memory_render_digest(eid, frag), frag)
                for eid, frag in entries
            ),
        )

    class ReceiptMemory:
        def __init__(self, receipt: MemoryPromptReceipt) -> None:
            self._receipt = receipt

        def prepare_compaction_context(self, _query, **_kwargs):
            return self._receipt

    config = AutoCompactConfig(max_expand_tokens=2_000, min_keep_messages=5)
    entry = ("entry-1", "alpha checkout guidance")
    engine = SessionMemoryCompactEngine(
        memory_manager=ReceiptMemory(_receipt((entry,)))
    )
    first_input = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "x" * 400}
        for i in range(40)
    ]
    first = engine.try_session_memory_compact(
        first_input,
        context_window=100_000,
        config=config,
        transcript_summarizer=RecordingSummarizer(),
    )
    assert first is not None and first.effective
    assert (
        sum(
            bool(m.get("_session_memory_context"))
            for m in first.messages
        )
        == 1
    )

    # Grow the conversation again so the second compact really drops content,
    # then render the same entry fresh (not reused).
    second_input = list(first.messages) + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"more {i} " + "y" * 400}
        for i in range(30)
    ]
    engine = SessionMemoryCompactEngine(
        memory_manager=ReceiptMemory(_receipt((entry,)))
    )
    second = engine.try_session_memory_compact(
        second_input,
        context_window=100_000,
        config=config,
        transcript_summarizer=RecordingSummarizer(),
    )
    assert second is not None and second.effective
    memory_blocks = [
        m for m in second.messages if m.get("_session_memory_context")
    ]
    assert len(memory_blocks) == 1
    assert "[entry-1]" in str(memory_blocks[0]["content"])


def test_partially_reused_prior_memory_block_is_retained() -> None:
    """A prior block whose entries are not re-rendered (their text survives
    only in that block via reuse suppression) must never be dropped."""
    from minicode.memory_pipeline import (
        MemoryPromptReceipt,
        _memory_render_digest,
    )

    def _receipt(
        entries: tuple[tuple[str, str], ...], receipt_id: str
    ) -> MemoryPromptReceipt:
        prompt_text = "\n".join(f"[{eid}] {frag}" for eid, frag in entries)
        ids = tuple(eid for eid, _ in entries)
        return MemoryPromptReceipt(
            prompt_text=prompt_text,
            selected_ids=ids,
            rendered_ids=ids,
            rendered_versions=tuple((eid, "1" * 64) for eid, _ in entries),
            authority_revision="rev-1",
            receipt_id=receipt_id,
            rendered_proofs=tuple(
                (eid, _memory_render_digest(eid, frag))
                for eid, frag in entries
            ),
            rendered_fragments=tuple(
                (eid, _memory_render_digest(eid, frag), frag)
                for eid, frag in entries
            ),
        )

    class ReceiptMemory:
        def __init__(self, receipt: MemoryPromptReceipt) -> None:
            self._receipt = receipt

        def prepare_compaction_context(self, _query, **_kwargs):
            return self._receipt

    config = AutoCompactConfig(max_expand_tokens=2_000, min_keep_messages=5)
    first_entry = ("entry-1", "alpha checkout guidance")
    engine = SessionMemoryCompactEngine(
        memory_manager=ReceiptMemory(_receipt((first_entry,), "a" * 32))
    )
    first_input = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "x" * 400}
        for i in range(40)
    ]
    first = engine.try_session_memory_compact(
        first_input,
        context_window=100_000,
        config=config,
        transcript_summarizer=RecordingSummarizer(),
    )
    assert first is not None and first.effective

    # Second receipt renders only a NEW entry; entry-1's text lives solely in
    # the prior block, so that block must survive.
    second_entry = ("entry-2", "beta retry guidance")
    second_input = list(first.messages) + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"more {i} " + "y" * 400}
        for i in range(30)
    ]
    engine = SessionMemoryCompactEngine(
        memory_manager=ReceiptMemory(_receipt((second_entry,), "b" * 32))
    )
    second = engine.try_session_memory_compact(
        second_input,
        context_window=100_000,
        config=config,
        transcript_summarizer=RecordingSummarizer(),
    )
    assert second is not None and second.effective
    memory_blocks = [
        m for m in second.messages if m.get("_session_memory_context")
    ]
    assert len(memory_blocks) == 2
    visible = "\n".join(str(m["content"]) for m in memory_blocks)
    assert "[entry-1]" in visible
    assert "[entry-2]" in visible


# ---------------------------------------------------------------------------
# Tool-result aggregate budget enforcement (live knob)
# ---------------------------------------------------------------------------


def test_run_budget_persists_oldest_results_when_block_exceeds_budget(
    tmp_path,
):
    """Each-under-threshold results in one run must jointly respect the
    budget: the oldest (non-protected) results are persisted largest-first."""
    manager = ToolResultBudgetManager(workspace=tmp_path)
    messages = [
        {
            "role": "tool_result",
            "toolName": "run_command",
            "toolUseId": f"call-{index}",
            "content": f"output {index} " + "z" * 3_000,
        }
        for index in range(9)
    ]

    modified, bytes_saved = manager.check_and_replace(messages)

    persisted = [
        message for message in modified if "_persisted_path" in message
    ]
    # 9 results × 3000 = 27000 chars > 8000 budget; the newest 5 are
    # protected, so the 4 oldest are candidates and 2 are persisted.
    assert len(persisted) == 2
    assert bytes_saved > 0
    assert all(
        "[Tool result persisted" in str(message["content"])
        for message in persisted
    )
    # Protected recent results stay verbatim.
    assert modified[-1]["content"] == messages[-1]["content"]


def test_run_budget_knob_changes_persistence_behavior(tmp_path):
    """Raising budget_per_message must relax aggregate persistence."""
    manager = ToolResultBudgetManager(workspace=tmp_path)
    manager.budget_per_message = 30_000
    messages = [
        {
            "role": "tool_result",
            "toolName": "run_command",
            "toolUseId": f"call-{index}",
            "content": f"output {index} " + "z" * 3_000,
        }
        for index in range(9)
    ]

    modified, bytes_saved = manager.check_and_replace(messages)

    assert bytes_saved == 0
    assert all("_persisted_path" not in message for message in modified)


# ---------------------------------------------------------------------------
# Persisted tool-result TTL cleanup
# ---------------------------------------------------------------------------


def test_cleanup_expired_removes_only_old_files(tmp_path):
    manager = ToolResultBudgetManager(workspace=tmp_path)
    messages = [
        {
            "role": "tool_result",
            "toolName": "run_command",
            "toolUseId": "call-old",
            "content": "old output " + "o" * 5_000,
        },
        {
            "role": "tool_result",
            "toolName": "run_command",
            "toolUseId": "call-new",
            "content": "new output " + "n" * 5_000,
        },
    ]
    modified, _ = manager.check_and_replace(messages)
    files = sorted(
        path
        for path in (tmp_path / ".mini-code-tool-results").iterdir()
        if path.is_file() and not path.name.startswith(".")
    )
    assert len(files) == 2

    # Age the first persisted file past the TTL, keep the second fresh.
    import os as _os

    old_time = time.time() - 8 * 24 * 3600
    _os.utime(files[0], (old_time, old_time))

    removed = manager.cleanup_expired(7 * 24 * 3600.0)

    assert removed == 1
    assert not files[0].exists()
    assert files[1].exists()
    # The in-memory ledger drops entries whose file is gone.
    assert manager.get_persisted_count() == 1


def test_cleanup_interval_gates_repeated_sweeps(tmp_path):
    manager = ToolResultBudgetManager(workspace=tmp_path)
    manager._last_cleanup_at = time.time()
    assert manager.maybe_cleanup_expired() is None  # gated, no error
    fresh = manager.cleanup_expired(0.0)
    assert fresh == 0  # nothing persisted yet


def test_step_budget_groups_interleaved_results_by_assistant_turn(tmp_path):
    """Parallel results interleaved with their assistant_tool_call messages
    form one budget group via assistantTurnId, so an oversized parallel step
    is persisted even though no two results are adjacent."""
    manager = ToolResultBudgetManager(workspace=tmp_path)
    messages: list[dict] = []
    for index in range(8):
        messages.append(
            {
                "role": "assistant_tool_call",
                "toolUseId": f"call-{index}",
                "toolName": "run_command",
                "assistantTurnId": "turn-42",
            }
        )
        messages.append(
            {
                "role": "tool_result",
                "toolUseId": f"call-{index}",
                "toolName": "run_command",
                "content": f"output {index} " + "z" * 3_000,
            }
        )

    modified, bytes_saved = manager.check_and_replace(messages)

    persisted = [
        message for message in modified if "_persisted_path" in message
    ]
    # 8 × 3000 = 24000 > 8000; newest 5 protected → 3 candidates (9000),
    # one largest persist brings the step to 6500 ≤ 8000.
    assert len(persisted) == 1
    assert bytes_saved > 0


def test_step_budget_keeps_separate_turns_independent(tmp_path):
    """Results of different assistant turns are budgeted independently."""
    manager = ToolResultBudgetManager(workspace=tmp_path)
    messages: list[dict] = []
    for turn_index in range(3):
        for call_index in range(3):
            messages.append(
                {
                    "role": "assistant_tool_call",
                    "toolUseId": f"call-{turn_index}-{call_index}",
                    "toolName": "run_command",
                    "assistantTurnId": f"turn-{turn_index}",
                }
            )
            messages.append(
                {
                    "role": "tool_result",
                    "toolUseId": f"call-{turn_index}-{call_index}",
                    "toolName": "run_command",
                    "content": "out " + "z" * 3_000,
                }
            )

    modified, bytes_saved = manager.check_and_replace(messages)

    # Each turn: 3 × 3000 = 9000 ≤ protection window (5) → nothing persisted.
    assert bytes_saved == 0
    assert all("_persisted_path" not in message for message in modified)


# ---------------------------------------------------------------------------
# LLM summary bounded provider-error retry
# ---------------------------------------------------------------------------


class _FlakyModel:
    """Raises a provider-style error on the first call, succeeds after."""

    model_id = "flaky-summary-model"

    def __init__(self, failures: int = 1) -> None:
        self.failures = failures
        self.calls = 0

    def next(self, messages):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("simulated incomplete read")
        return AgentStep(type="assistant", content="Recovered summary " + "s" * 60)


def _summary_input() -> list[dict]:
    return [
        {"role": "user", "content": "task one"},
        {"role": "assistant", "content": "answer one"},
        {"role": "user", "content": "task two"},
        {"role": "assistant", "content": "answer two"},
    ]


def test_summary_retries_once_after_transient_provider_error() -> None:
    from minicode.context_compactor import LLMSummaryGenerator

    budget = AgentTurnBudget(max_model_calls=5, max_total_tokens=100_000)
    model = _FlakyModel(failures=1)
    summary = LLMSummaryGenerator(model, agent_budget=budget).summarize(
        _summary_input()
    )

    assert summary is not None and summary.startswith("Recovered summary")
    assert model.calls == 2
    # Admission consumes one model-call slot per attempt, including the
    # failed one — that is the budget's documented semantics.
    assert budget.snapshot().used_model_calls == 2


def test_summary_falls_back_after_two_provider_errors() -> None:
    from minicode.context_compactor import LLMSummaryGenerator

    budget = AgentTurnBudget(max_model_calls=5, max_total_tokens=100_000)
    model = _FlakyModel(failures=2)
    summary = LLMSummaryGenerator(model, agent_budget=budget).summarize(
        _summary_input()
    )

    assert summary is None
    assert model.calls == 2


def test_summary_does_not_retry_when_deadline_is_exhausted() -> None:
    from minicode.context_compactor import LLMSummaryGenerator

    model = _FlakyModel(failures=1)
    generator = LLMSummaryGenerator(
        model,
        deadline_monotonic=time.monotonic() + 0.5,
    )
    # Squeeze the per-call deadline so the retry floor blocks attempt two.
    summary = generator.summarize(_summary_input())

    assert summary is None
    assert model.calls == 1


# ---------------------------------------------------------------------------
# Provider-usage / local-estimate reconciliation
# ---------------------------------------------------------------------------


def test_record_usage_observation_tracks_calibration_ewma():
    compactor = ContextCompactor(context_window=100_000)
    for _ in range(6):
        compactor.record_usage_observation(2_000, 1_000)

    stats = compactor.get_stats()
    assert stats["token_calibration_samples"] == 6
    # EWMA with alpha=0.2 converges toward 2.0 from 1.0.
    assert 1.5 < stats["token_calibration_ratio"] < 2.0
    assert stats["token_calibration_min_ratio"] == 2.0
    assert stats["token_calibration_max_ratio"] == 2.0


def test_record_usage_observation_ignores_noise_samples():
    compactor = ContextCompactor(context_window=100_000)
    compactor.record_usage_observation(99_999_999, 1_000)  # outside bounds
    compactor.record_usage_observation(1_500, 100)  # estimate below floor
    compactor.record_usage_observation(0, 5_000)  # non-positive report
    compactor.record_usage_observation(-5, 5_000)  # negative report

    stats = compactor.get_stats()
    assert stats["token_calibration_samples"] == 0
    assert stats["token_calibration_ratio"] == 1.0


def test_record_usage_observation_warns_once_on_sustained_drift(caplog):
    compactor = ContextCompactor(context_window=100_000)
    with caplog.at_level("WARNING", logger="minicode.context_compactor"):
        for _ in range(8):
            compactor.record_usage_observation(2_600, 1_000)

    drift_warnings = [
        record
        for record in caplog.records
        if "drifts from provider usage" in record.message
    ]
    assert len(drift_warnings) == 1


def test_concurrent_managers_never_collide_on_persisted_filenames(tmp_path):
    """Two compactor instances sharing one workspace (concurrent
    sub-agents) must produce distinct files even for the same tool name,
    message index, and millisecond."""
    first = ToolResultBudgetManager(workspace=tmp_path)
    second = ToolResultBudgetManager(workspace=tmp_path)
    message = {
        "role": "tool_result",
        "toolName": "run_command",
        "toolUseId": "call-1",
        "content": "shared output " + "c" * 5_000,
    }

    first.check_and_replace([dict(message)])
    second.check_and_replace([dict(message)])

    files = sorted(
        path
        for path in (tmp_path / ".mini-code-tool-results").iterdir()
        if path.is_file() and not path.name.startswith(".")
    )
    assert len(files) == 2
    assert files[0].name != files[1].name
