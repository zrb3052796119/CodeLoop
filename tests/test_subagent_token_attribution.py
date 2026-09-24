"""Per-sub-agent token attribution and parent-run coordination telemetry.

Covers the two multi-agent cost telemetry gaps: the sub-agent journal's
finish record now carries the token delta attributed to that sub-agent (not
the session-cumulative budget counters), and the parent Run's terminal
record separates the parent's own model tokens from the sub-agent totals.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import minicode.tools.task as task_module
from minicode.agent_budget import AgentTurnBudget
from minicode.run_journal import RunJournal
from minicode.subagent_journal import (
    SubagentRunJournal,
    new_subagent_id,
)
from minicode.subagent_supervisor import SubagentProcessOutcome
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext


def _parent_sink(record_id: str, subjournal: SubagentRunJournal):
    class ParentSink:
        @property
        def run_id(self) -> str:
            return record_id

        def emit(self, event_type: str, *, step=None, payload=None) -> None:
            return None

        def open_subagent_journal(self) -> SubagentRunJournal:
            return subjournal

        def record_written_memory_ids(self, entry_ids: list[str]) -> None:
            return None

    return ParentSink()


def _open_journaled_run(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    record = journal.create_run(title="parent", source="headless")
    journal.transition(record.id, "running")
    return journal, record, journal.open_subagent_journal(record.id)


def _fake_turn(records: list[tuple[int, int]], captured: list | None = None):
    """Stand-in agent loop recording the given (input, output) token calls."""

    def fake_run_agent_turn(**kwargs):
        if captured is not None:
            captured.append(kwargs)
        budget = kwargs.get("agent_budget")
        for input_tokens, output_tokens in records:
            budget.record_model_call(
                input_tokens=input_tokens, output_tokens=output_tokens
            )
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "done"}]

    return fake_run_agent_turn


def test_attribution_views_keep_parallel_deltas_separate() -> None:
    """Two parallel consumers on one shared budget must not cross-attribute.

    A global start/finish diff would assign one sibling's consumption to the
    other whenever their lifetimes overlap; tagging at the accounting point
    (the per-sub-agent view) keeps each delta exact under interleaving.
    """
    budget = AgentTurnBudget(max_model_calls=100, max_total_tokens=1_000_000)
    barrier = threading.Barrier(2)

    def consume(key: str, calls: list[tuple[int, int]]) -> tuple[int, int]:
        view = budget.attribution_view(key)
        barrier.wait(timeout=5)
        for input_tokens, output_tokens in calls:
            reservation = view.reserve_model_call(input_tokens)
            view.record_model_call(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reservation=reservation,
            )
        return view.attributed_tokens()

    calls_a = [(100, 10), (200, 20)]
    calls_b = [(900, 90)]
    results: dict[str, tuple[int, int]] = {}
    threads = [
        threading.Thread(
            target=lambda: results.setdefault("a", consume("sub_a", calls_a))
        ),
        threading.Thread(
            target=lambda: results.setdefault("b", consume("sub_b", calls_b))
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert results["a"] == (300, 30)
    assert results["b"] == (900, 90)
    snapshot = budget.snapshot()
    assert snapshot.used_total_tokens == 1320
    assert snapshot.used_input_tokens == 1200
    assert snapshot.used_output_tokens == 120
    assert snapshot.used_model_calls == 3


def test_nested_attribution_view_reports_subtree_totals() -> None:
    """A workflow view wraps its phases: the parent sees the subtree, each
    leaf only itself, and the root budget still sees the exact total once."""
    budget = AgentTurnBudget()
    workflow = budget.attribution_view("sub_workflow")
    analyst = workflow.attribution_view("sub_analyst")
    executor = workflow.attribution_view("sub_executor")

    analyst.record_model_call(input_tokens=100, output_tokens=10)
    executor.record_model_call(input_tokens=500, output_tokens=50)
    # The orchestrator's own call (no phase) lands on the workflow view.
    workflow.record_model_call(input_tokens=40, output_tokens=4)

    assert analyst.attributed_tokens() == (100, 10)
    assert executor.attributed_tokens() == (500, 50)
    assert workflow.attributed_tokens() == (640, 64)
    assert budget.snapshot().used_total_tokens == 704


def test_attribution_view_rejects_an_invalid_key() -> None:
    budget = AgentTurnBudget()
    with pytest.raises(ValueError):
        budget.attribution_view("")


def test_parallel_sub_agents_attribute_their_own_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two explore sub-agents interleaving on the shared budget: each finish
    record carries exactly its own token delta, never the sibling's."""
    journal, record, subjournal = _open_journaled_run(tmp_path)
    shared_budget = AgentTurnBudget(max_model_calls=100, max_total_tokens=10**7)
    entered = threading.Barrier(2)
    sibling_recorded = threading.Event()

    def fake_run_agent_turn(**kwargs):
        prompt = kwargs["messages"][-1]["content"]
        mine, sibling = (100, 10), (900, 90)
        if "bravo" in prompt:
            mine, sibling = sibling, mine
        budget = kwargs["agent_budget"]
        entered.wait(timeout=5)
        if "alpha" in prompt:
            # Alpha records first, then waits until the sibling has recorded
            # too: a global diff at finish would wrongly include the sibling.
            budget.record_model_call(input_tokens=mine[0], output_tokens=mine[1])
            sibling_recorded.wait(timeout=5)
        else:
            budget.record_model_call(input_tokens=mine[0], output_tokens=mine[1])
            sibling_recorded.set()
        kwargs["outcome_capture"].record(canonicalize_task_outcome("success", 0))
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(task_module, "run_agent_turn", fake_run_agent_turn)

    def run_one(marker: str):
        return task_module.task_tool.run(
            {
                "description": f"explore {marker}",
                "prompt": f"look around {marker}",
                "agent_type": "explore",
            },
            ToolContext(
                cwd=str(tmp_path / "workspace"),
                _runtime={"model": "fake"},
                _agent_depth=0,
                _event_sink=_parent_sink(record.id, subjournal),
                _agent_budget=shared_budget,
            ),
        )

    outcomes: dict[str, object] = {}
    threads = [
        threading.Thread(target=lambda m=m: outcomes.setdefault(m, run_one(m)))
        for m in ("alpha", "bravo")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(result.ok for result in outcomes.values())

    summaries = {
        summary.subagent_id: summary
        for summary in journal.list_subagent_runs(record.id)
    }
    assert len(summaries) == 2
    token_pairs = sorted(
        (s.input_tokens, s.output_tokens) for s in summaries.values()
    )
    assert token_pairs == [(100, 10), (900, 90)]
    # The shared counters still reflect the exact total exactly once.
    assert shared_budget.snapshot().used_total_tokens == 1100


def test_subprocess_result_tokens_reach_the_journal_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The general-path worker reports its local token split in the result
    payload; the parent projects it into the sub-agent journal finish."""
    journal, record, subjournal = _open_journaled_run(tmp_path)

    def fake_supervisor(request, **kwargs):
        lease = request["budget_lease"]
        return SubagentProcessOutcome(
            status="completed",
            result={
                "version": 1,
                "status": "completed",
                "final_message": "subprocess done",
                "report_content": None,
                "model_turns": 2,
                "tool_calls": 1,
                "result_truncated": False,
                "error": None,
                "input_tokens": 4321,
                "output_tokens": 234,
                "budget_usage": {
                    "budgetUsageVersion": 2,
                    "leaseId": lease["leaseId"],
                    "leaseNonce": lease["leaseNonce"],
                    "usedModelCalls": 2,
                    "inputTokens": 4321,
                    "outputTokens": 234,
                    "usedCostUsd": "0",
                },
            },
            error=None,
            duration_ms=5,
            pid=None,
        )

    monkeypatch.setattr(task_module, "run_subagent_process", fake_supervisor)

    result = task_module.task_tool.run(
        {
            "description": "subprocess tokens",
            "prompt": "Apply the bounded change.",
            "agent_type": "general",
        },
        ToolContext(
            cwd=str(tmp_path / "workspace"),
            _runtime={"model": "fake"},
            _agent_depth=0,
            _event_sink=_parent_sink(record.id, subjournal),
            _agent_budget=AgentTurnBudget(),
        ),
    )

    assert result.ok is True
    summaries = journal.list_subagent_runs(record.id)
    assert len(summaries) == 1
    assert summaries[0].outcome == "completed"
    assert summaries[0].input_tokens == 4321
    assert summaries[0].output_tokens == 234


def test_subprocess_invalid_result_tokens_are_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A compromised or buggy child cannot smuggle invalid counters into the
    journal: an unpaired or negative token split degrades to absent."""
    journal, record, subjournal = _open_journaled_run(tmp_path)

    def fake_supervisor(request, **kwargs):
        return SubagentProcessOutcome(
            status="completed",
            result={
                "version": 1,
                "status": "completed",
                "final_message": "subprocess done",
                "report_content": None,
                "model_turns": 1,
                "tool_calls": 0,
                "result_truncated": False,
                "error": None,
                "input_tokens": -5,
                "output_tokens": None,
            },
            error=None,
            duration_ms=5,
            pid=None,
        )

    monkeypatch.setattr(task_module, "run_subagent_process", fake_supervisor)

    result = task_module.task_tool.run(
        {
            "description": "subprocess tokens",
            "prompt": "Apply the bounded change.",
            "agent_type": "general",
        },
        ToolContext(
            cwd=str(tmp_path / "workspace"),
            _runtime={"model": "fake"},
            _agent_depth=0,
            _event_sink=_parent_sink(record.id, subjournal),
            _agent_budget=AgentTurnBudget(),
        ),
    )

    assert result.ok is True
    summaries = journal.list_subagent_runs(record.id)
    assert len(summaries) == 1
    assert summaries[0].input_tokens is None
    assert summaries[0].output_tokens is None
    assert "inputTokens" not in summaries[0].to_dict()


def test_pre_attribution_journal_files_still_read(
    tmp_path: Path,
) -> None:
    """Journals written before token attribution existed (no token keys in
    the finish record) replay with the new fields as absent."""
    journal, record, subjournal = _open_journaled_run(tmp_path)
    subagent_id = new_subagent_id()
    run_dir = subjournal._path(subagent_id)
    run_dir.parent.mkdir(mode=0o700, exist_ok=True)
    old_records = [
        {
            "recordType": "start",
            "journalVersion": 1,
            "subagentId": subagent_id,
            "parentRunId": record.id,
            "agentType": "explore",
            "maxTurns": 15,
            "limitKind": "model_turns",
            "startedAt": 1.0,
            "budgetAtStart": {},
        },
        {
            "recordType": "finish",
            "journalVersion": 1,
            "outcome": "completed",
            "modelTurns": 3,
            "toolCalls": 5,
            "durationMs": 1200,
            "maxTurns": 15,
            "resultTruncated": False,
            "completedAt": 2.0,
            "budgetAtFinish": {"usedTotalTokens": 750},
        },
    ]
    run_dir.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in old_records),
        encoding="utf-8",
    )

    summaries = journal.list_subagent_runs(record.id)
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.outcome == "completed"
    assert summary.budget_snapshot["usedTotalTokens"] == 750
    assert summary.input_tokens is None
    assert summary.output_tokens is None
    assert "inputTokens" not in summary.to_dict()


def test_finish_requires_a_paired_nonnegative_token_split(
    tmp_path: Path,
) -> None:
    _, _, subjournal = _open_journaled_run(tmp_path)
    subagent_id = new_subagent_id()
    subjournal.start(
        subagent_id=subagent_id,
        agent_type="explore",
        max_turns=15,
        budget=None,
    )
    with pytest.raises(Exception, match="pair"):
        subjournal.finish(
            subagent_id,
            outcome="completed",
            model_turns=1,
            tool_calls=0,
            duration_ms=1,
            max_turns=15,
            result_truncated=False,
            budget=None,
            input_tokens=10,
        )
    with pytest.raises(Exception, match="output_tokens"):
        subjournal.finish(
            subagent_id,
            outcome="completed",
            model_turns=1,
            tool_calls=0,
            duration_ms=1,
            max_turns=15,
            result_truncated=False,
            budget=None,
            input_tokens=10,
            output_tokens=-1,
        )


def test_terminal_run_record_separates_parent_and_subagent_tokens(
    tmp_path: Path,
) -> None:
    """The parent Run's terminal event splits its own model tokens from the
    attributed sub-agent totals recorded in the sidecar journal."""
    journal, record, subjournal = _open_journaled_run(tmp_path)
    # Parent-only model calls: sub-agent loops write to the sidecar, never
    # to this stream, so these usage sums are exactly the coordination cost.
    journal.append_event(
        record.id,
        "model.completed",
        payload={
            "operationId": "op1",
            "resultType": "assistant",
            "usage": {"inputTokens": 1000, "outputTokens": 100},
        },
    )
    journal.append_event(
        record.id,
        "model.completed",
        payload={
            "operationId": "op2",
            "resultType": "tool_calls",
            "usage": {"inputTokens": 2000, "outputTokens": 200},
        },
    )
    budget = AgentTurnBudget()
    for tokens in ((300, 30), (500, 50)):
        subagent_id = new_subagent_id()
        subjournal.start(
            subagent_id=subagent_id,
            agent_type="explore",
            max_turns=15,
            budget=budget.snapshot(),
        )
        subjournal.finish(
            subagent_id,
            outcome="completed",
            model_turns=2,
            tool_calls=3,
            duration_ms=100,
            max_turns=15,
            result_truncated=False,
            budget=budget.snapshot(),
            input_tokens=tokens[0],
            output_tokens=tokens[1],
        )
    # A pre-attribution (or killed) sub-agent contributes nothing but does
    # not block the split.
    legacy_id = new_subagent_id()
    subjournal.start(
        subagent_id=legacy_id,
        agent_type="general",
        max_turns=15,
        budget=None,
    )
    subjournal.finish(
        legacy_id,
        outcome="timed_out",
        model_turns=0,
        tool_calls=0,
        duration_ms=5,
        max_turns=15,
        result_truncated=False,
        budget=None,
    )

    completed = journal.transition(record.id, "completed")
    assert completed.status == "completed"

    events, diagnostics = journal._read_events(record.id)
    assert diagnostics == []
    terminal = [event for event in events if event.type == "run.completed"]
    assert len(terminal) == 1
    coordination = terminal[0].payload["coordination"]
    assert coordination == {
        "parentInputTokens": 3000,
        "parentOutputTokens": 300,
        "subagentInputTokens": 800,
        "subagentOutputTokens": 80,
    }


def test_terminal_record_omits_coordination_without_telemetry(
    tmp_path: Path,
) -> None:
    """Runs with no model usage and no attributed sub-agents keep the exact
    pre-existing terminal payload shape."""
    journal, record, _ = _open_journaled_run(tmp_path)

    journal.transition(record.id, "completed")

    events, _ = journal._read_events(record.id)
    terminal = [event for event in events if event.type == "run.completed"]
    assert len(terminal) == 1
    assert terminal[0].payload == {}
