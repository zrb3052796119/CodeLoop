"""Continuity checks for every compaction path that replaces a boundary."""
from __future__ import annotations

from minicode.context_compactor import (
    AutoCompactConfig,
    AutoCompactDispatcher,
    ReactiveCompactEngine,
    SessionMemoryCompactEngine,
)


class _Memory:
    def get_relevant_context(self, max_tokens=100, query=None):
        return "durable project context"


class _ChainSummarizer:
    def __init__(self) -> None:
        self.calls = 0

    def summarize(self, messages: list[dict]) -> str:
        self.calls += 1
        if self.calls == 1:
            return "EARLY_SESSION_DECISION"
        transcript = "\n".join(str(message.get("content", "")) for message in messages)
        return "SESSION_CHAIN_OK" if "EARLY_SESSION_DECISION" in transcript else "LOST"


def _messages(prefix: str) -> list[dict]:
    return [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"{prefix}-{index} " + "x" * 500,
        }
        for index in range(80)
    ]


def test_session_memory_compaction_summarizes_the_previous_boundary() -> None:
    summarizer = _ChainSummarizer()
    engine = SessionMemoryCompactEngine(_Memory())
    config = AutoCompactConfig(max_expand_tokens=2_000)

    first = engine.try_session_memory_compact(
        _messages("first"),
        context_window=100_000,
        config=config,
        transcript_summarizer=summarizer,
    )
    assert first is not None and first.effective
    second = engine.try_session_memory_compact(
        [*first.messages, *_messages("second")],
        context_window=100_000,
        config=config,
        transcript_summarizer=summarizer,
    )

    assert second is not None and second.effective
    assert "SESSION_CHAIN_OK" in second.summary_text


def test_aggressive_recovery_carries_the_previous_boundary() -> None:
    messages = [
        {
            "role": "system",
            "content": "previous summary: EARLY_REACTIVE_DECISION",
            "_compact_boundary": True,
        },
        *[
            {"role": "user", "content": f"message-{index}"}
            for index in range(40)
        ],
    ]

    result = ReactiveCompactEngine(auto_compact=None).try_recover_from_overflow(
        messages
    )

    assert result is not None
    assert any(
        "EARLY_REACTIVE_DECISION" in str(message.get("content", ""))
        for message in result.messages
    )


def test_heuristic_full_compaction_keeps_previous_summary() -> None:
    dispatcher = AutoCompactDispatcher(
        context_window=100_000,
        config=AutoCompactConfig(min_keep_tokens=0, min_keep_messages=5),
    )
    first_input = _messages("first")
    first_input[0]["content"] = "EARLY_HEURISTIC_DECISION " + "x" * 500

    first = dispatcher.dispatch(first_input, force_full=True)
    assert first.effective
    second = dispatcher.dispatch(
        [*first.messages, *_messages("second")],
        force_full=True,
    )

    assert second.effective
    assert any(
        "EARLY_HEURISTIC_DECISION" in str(message.get("content", ""))
        for message in second.messages
    )
