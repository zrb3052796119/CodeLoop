"""Behavioral counterexamples for the residual 2026-08-19 P2 audit."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

import minicode.tools.task as task_module
from minicode.agent_budget import AgentTurnBudget
from minicode.context_compactor import (
    ContextCompactor,
    LLMSummaryGenerator,
    MicrocompactState,
)
from minicode.memory import MemoryManager
from minicode.memory_pipeline import MemoryPipeline
from minicode.project_facts import ProjectFactsStore
from minicode.subagent_mailbox import SubagentMailbox
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext, ToolResult


def _context(workspace: Path, **overrides: object) -> ToolContext:
    values: dict[str, object] = {
        "cwd": str(workspace),
        "_runtime": {"model": "fake"},
    }
    values.update(overrides)
    return ToolContext(**values)


def _approve_review(mailbox: SubagentMailbox, prompt: str) -> None:
    match = re.search(r"review verdict key `([^`]+)`", prompt)
    assert match is not None
    mailbox.write(
        match.group(1),
        json.dumps(
            {
                "reviewVersion": 1,
                "verdict": "approved",
                "blockingFindings": [],
                "warnings": [],
            }
        ),
        author="reviewer",
    )


def test_workflow_has_one_overall_monotonic_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mailbox = SubagentMailbox()
    calls: list[str] = []
    monkeypatch.setenv("MINICODE_TASK_TIMEOUT_SECONDS", "0.005")

    def fake_run(input_data: dict, _context: ToolContext) -> ToolResult:
        description = str(input_data["description"])
        calls.append(description)
        if description.startswith("plan:"):
            time.sleep(0.03)
        if description.startswith("review:"):
            _approve_review(mailbox, input_data["prompt"])
        return ToolResult(ok=True, output="phase complete")

    monkeypatch.setattr(task_module, "_run", fake_run)
    result = task_module.task_tool.run(
        {
            "description": "deadline probe",
            "prompt": "Plan, implement and review a small change.",
            "agent_type": "workflow",
        },
        _context(tmp_path, _subagent_mailbox=mailbox),
    )

    assert result.ok is False
    assert "deadline_exceeded" in result.output
    assert len(calls) == 1


def test_general_subagent_checks_deadline_after_model_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINICODE_TASK_TIMEOUT_SECONDS", "0.005")

    def slow_turn(**kwargs):
        time.sleep(0.03)
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "late success"}]

    monkeypatch.setattr(task_module, "run_agent_turn", slow_turn)
    result = task_module.task_tool.run(
        {
            "description": "general deadline",
            "prompt": "Complete one bounded task.",
            "agent_type": "general",
        },
        _context(tmp_path),
    )

    assert result.ok is False
    assert "deadline_exceeded" in result.output


def test_microcompact_invalidates_dedup_source_that_it_erases(
    tmp_path: Path,
) -> None:
    compactor = ContextCompactor(workspace=tmp_path)
    compactor._microcompact._state = MicrocompactState(
        last_time_based_compact=0.0,
        time_based_interval=0.0,
        keep_recent_tool_results=5,
    )
    compactor.read_dedup.register_read("demo.py", "UNIQUE_FILE_CONTENT", 0)
    messages = [
        {
            "role": "tool_result",
            "toolName": "read_file" if index == 0 else "other",
            "toolUseId": f"call-{index}",
            "content": "UNIQUE_FILE_CONTENT" if index == 0 else f"result-{index}",
        }
        for index in range(7)
    ]

    result = compactor.process_request(
        messages,
        enable_tool_budget=False,
        enable_auto_compact=False,
    )

    assert result.effective
    assert compactor.read_dedup.should_dedup(
        "demo.py", "UNIQUE_FILE_CONTENT"
    ) is False


def test_failed_summary_model_call_settles_token_reservation() -> None:
    class FailingModel:
        def next(self, _messages):
            raise ConnectionError("synthetic provider outage")

    budget = AgentTurnBudget(max_total_tokens=10_000, max_model_calls=5)
    generator = LLMSummaryGenerator(FailingModel(), agent_budget=budget)

    assert generator.summarize([{"role": "user", "content": "z" * 1000}]) is None
    snapshot = budget.snapshot()

    assert snapshot.used_model_calls == 1
    assert snapshot.reserved_total_tokens == 0


def test_project_facts_inject_without_a_matching_lesson(tmp_path: Path) -> None:
    manager = MemoryManager(
        project_root=tmp_path,
        data_root=tmp_path / "user-memory",
    )
    facts = ProjectFactsStore(tmp_path)
    facts.observe_dependencies(
        ["confirmed-demo-dependency"],
        provenance={"test": True},
    )
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(tmp_path))
    messages = [{"role": "system", "content": "base system"}]

    output = pipeline.inject(
        "an unrelated query with no matching approved lesson",
        current_files=[],
        messages=messages,
        context_usage=0.5,
    )

    assert "confirmed-demo-dependency" in output[0]["content"]
    assert pipeline.last_retrieval_result is not None
    assert pipeline.last_retrieval_result.no_match is True


def test_unwired_reranker_feature_flag_fails_fast(
    tmp_path: Path,
) -> None:
    pipeline = MemoryPipeline(
        MemoryManager(
            project_root=tmp_path,
            data_root=tmp_path / "user-memory",
        )
    )

    with pytest.raises(ValueError, match="canonical retrieval"):
        pipeline.initialize(
            model_adapter=object(),
            workspace_path=str(tmp_path),
            enable_reranker=True,
        )
