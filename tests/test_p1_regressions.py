"""Behavioral regressions for the 2026-08-19 P1 coding-agent audit."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import minicode.tools.task as task_module
from minicode.capability_registry import CapabilityRegistry
from minicode.context_compactor import (
    AutoCompactConfig,
    AutoCompactDispatcher,
)
from minicode.intent_parser import parse_intent
from minicode.skill_router import (
    SkillRouter,
    required_skill_names_for_routing,
)
from minicode.subagent_mailbox import SubagentMailbox
from minicode.tooling import ToolContext, ToolResult


class _ChainedSummaryProbe:
    """Make loss of a previous summary observable in the second output."""

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def summarize(self, messages: list[dict]) -> str:
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            return "EARLY_DECISION_MARKER must survive future compactions"
        transcript = "\n".join(str(message.get("content", "")) for message in messages)
        return (
            "CHAIN_OK: EARLY_DECISION_MARKER retained"
            if "EARLY_DECISION_MARKER" in transcript
            else "CHAIN_BROKEN: prior summary was not supplied"
        )


def _long_conversation(prefix: str, count: int = 60) -> list[dict]:
    return [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"{prefix}-{index} " + "x" * 1600,
        }
        for index in range(count)
    ]


def test_repeated_full_compaction_carries_the_previous_summary_forward() -> None:
    summarizer = _ChainedSummaryProbe()
    dispatcher = AutoCompactDispatcher(
        context_window=100_000,
        config=AutoCompactConfig(min_keep_tokens=0, min_keep_messages=5),
        summary_generator=summarizer,
    )

    first = dispatcher.dispatch(_long_conversation("first"), force_full=True)
    assert first.effective
    second_input = [*first.messages, *_long_conversation("second")]
    second = dispatcher.dispatch(second_input, force_full=True)

    assert second.effective
    assert len(summarizer.calls) == 2
    assert any(
        "CHAIN_OK" in str(message.get("content", ""))
        for message in second.messages
    )


def test_generic_skill_name_is_not_an_explicit_request_in_ordinary_prose() -> None:
    routing = SkillRouter().route(
        [
            {
                "name": "test",
                "qualified_name": "test",
                "description": "Run project tests and report failures",
                "path": "/tmp/test/SKILL.md",
                "source": "project",
                "keywords": ["test", "tests"],
            }
        ],
        parse_intent("Please test this code and report any failures."),
        CapabilityRegistry(),
        top_k=1,
    )

    assert routing.selected
    assert routing.selected[0].explicitly_requested is False
    assert required_skill_names_for_routing(routing) == []


def test_rejected_workflow_discards_execute_phase_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mailbox = SubagentMailbox()

    def fake_run(input_data: dict, context: ToolContext) -> ToolResult:
        description = input_data["description"]
        if description.startswith("execute:"):
            Path(context.cwd, "rejected-change.txt").write_text(
                "must never reach the parent workspace",
                encoding="utf-8",
            )
        if description.startswith("review:"):
            match = re.search(
                r"review verdict key `([^`]+)`",
                input_data["prompt"],
            )
            assert match is not None
            mailbox.write(
                match.group(1),
                json.dumps(
                    {
                        "reviewVersion": 1,
                        "verdict": "changes_required",
                        "blockingFindings": ["regression probe rejection"],
                        "warnings": [],
                    }
                ),
                author="reviewer",
            )
        return ToolResult(ok=True, output=f"{description} complete")

    monkeypatch.setattr(task_module, "_run", fake_run)
    result = task_module.task_tool.run(
        {
            "description": "isolation probe",
            "prompt": "Make a change, then have it rejected by review.",
            "agent_type": "workflow",
        },
        ToolContext(
            cwd=str(tmp_path),
            _runtime={"model": "fake"},
            _subagent_mailbox=mailbox,
        ),
    )

    assert result.ok is False
    assert not (tmp_path / "rejected-change.txt").exists()
