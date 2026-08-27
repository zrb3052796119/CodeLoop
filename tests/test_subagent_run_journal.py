"""Tests for the bounded sidecar sub-agent Run journal and shared budget."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import minicode.subagent_journal as subagent_journal_module
from minicode.agent_budget import (
    AgentBudgetExceeded,
    AgentTurnBudget,
)
from minicode.concurrent_tool_terminal import (
    project_concurrent_tool_terminal,
)
from minicode.run_journal import RunJournal
from minicode.subagent_journal import (
    SubagentJournalError,
    SubagentRunJournal,
    new_subagent_id,
)
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tools.task import _SubagentJournalSink


def test_subagent_journal_records_bounded_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    record = journal.create_run(title="parent", source="headless")
    journal.transition(record.id, "running")

    subjournal = journal.open_subagent_journal(record.id)
    subagent_id = new_subagent_id()
    budget = AgentTurnBudget(max_model_calls=3, max_total_tokens=1000)
    budget.reserve_model_call(800)
    budget.record_model_call(input_tokens=700, output_tokens=50)

    subjournal.start(
        subagent_id=subagent_id,
        agent_type="explore",
        max_turns=12,
        budget=budget.snapshot(),
    )
    subjournal.append_event(
        subagent_id,
        sequence=1,
        event_type="task.outcome",
        step=1,
        payload={
            "outcomeVersion": 1,
            "outcomeStatus": "success",
            "goalAchieved": True,
            "learningSuccess": True,
            "hadToolErrors": False,
            "errorsRecovered": False,
            "toolErrorCount": 0,
            "content": "must never be persisted",
        },
    )
    subjournal.finish(
        subagent_id,
        outcome="completed",
        model_turns=2,
        tool_calls=1,
        duration_ms=1200,
        max_turns=12,
        result_truncated=False,
        budget=budget.snapshot(),
    )

    summaries = journal.list_subagent_runs(record.id)
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.parent_run_id == record.id
    assert summary.agent_type == "explore"
    assert summary.outcome == "completed"
    assert summary.event_count == 1
    assert summary.budget_snapshot["usedTotalTokens"] == 750

    events = journal.list_subagent_events(record.id, subagent_id)
    assert len(events) == 1
    serialized = str(events[0].payload)
    assert "must never be persisted" not in serialized
    assert "content" not in events[0].payload


def test_subagent_journal_round_trips_concurrent_tool_terminal_event(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    record = journal.create_run(title="parent", source="headless")
    journal.transition(record.id, "running")
    subjournal = journal.open_subagent_journal(record.id)
    subagent_id = new_subagent_id()
    subjournal.start(
        subagent_id=subagent_id,
        agent_type="explore",
        max_turns=2,
        budget=None,
    )
    payload = project_concurrent_tool_terminal(
        call_id="call-1",
        tool_name="read_file",
        status="cancelled_before_start",
    )

    subjournal.append_event(
        subagent_id,
        sequence=1,
        event_type="tool.concurrent_terminal",
        step=1,
        payload=payload,
    )

    events = journal.list_subagent_events(record.id, subagent_id)
    assert len(events) == 1
    assert events[0].type == "tool.concurrent_terminal"
    assert events[0].payload == payload

    with pytest.raises(ValueError):
        subjournal.append_event(
            subagent_id,
            sequence=2,
            event_type="tool.concurrent_terminal",
            step=1,
            payload={**payload, "unexpected": True},
        )


def test_subagent_journal_rejects_tampered_concurrent_terminal_on_read(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / ("run_" + "a" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    subagent_id = new_subagent_id()
    journal.start(
        subagent_id=subagent_id,
        agent_type="explore",
        max_turns=2,
        budget=None,
    )
    payload = project_concurrent_tool_terminal(
        call_id="call-1",
        tool_name="read_file",
        status="abandoned_running",
    )
    journal.append_event(
        subagent_id,
        sequence=1,
        event_type="tool.concurrent_terminal",
        step=1,
        payload=payload,
    )

    path = run_dir / "subagent-runs" / f"{subagent_id}.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records[1]["payload"]["executionMayContinue"] = False
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    assert journal.list_events(subagent_id) == ()


def test_subagent_terminal_quota_survives_full_standard_event_budget(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / ("run_" + "a" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    subagent_id = new_subagent_id()
    journal.start(
        subagent_id=subagent_id,
        agent_type="explore",
        max_turns=2,
        budget=None,
    )
    sink = _SubagentJournalSink(journal, subagent_id)
    for _ in range(500):
        sink.emit("model.completed", step=1, payload={})
    sink.emit("model.completed", step=1, payload={"dropped": True})
    terminals = [
        project_concurrent_tool_terminal(
            call_id=f"call-after-standard-budget-{index}",
            tool_name="t" * 128,
            status="cancelled_before_start",
        )
        for index in range(1_000)
    ]
    for terminal in terminals:
        sink.emit(
            "tool.concurrent_terminal",
            step=1_000_000,
            payload=terminal,
        )
    sink.emit(
        "tool.concurrent_terminal",
        step=1,
        payload=project_concurrent_tool_terminal(
            call_id="terminal-overflow",
            tool_name="t" * 128,
            status="cancelled_before_start",
        ),
    )

    events = journal.list_events(subagent_id)
    assert len(events) == 1_500
    assert [event.sequence for event in events] == list(range(1, 1_501))
    assert events[500].payload == terminals[0]
    assert events[-1].type == "tool.concurrent_terminal"
    assert events[-1].payload == terminals[-1]


def test_subagent_terminal_has_reserved_bytes_after_large_standard_events(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / ("run_" + "a" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    subagent_id = new_subagent_id()
    journal.start(
        subagent_id=subagent_id,
        agent_type="explore",
        max_turns=2,
        budget=None,
    )
    sink = _SubagentJournalSink(journal, subagent_id)
    large_payload = {
        f"field{index}": "x" * 240 for index in range(40)
    }
    for _ in range(500):
        sink.emit("model.completed", step=1, payload=large_payload)
    for _ in range(500):
        sink.emit("model.completed", step=1, payload={})
    terminal = project_concurrent_tool_terminal(
        call_id="call-after-standard-byte-budget",
        tool_name="read_file",
        status="abandoned_running",
    )

    sink.emit(
        "tool.concurrent_terminal",
        step=1,
        payload=terminal,
    )

    events = journal.list_events(subagent_id)
    assert events
    assert events[-1].type == "tool.concurrent_terminal"
    assert events[-1].payload == terminal


def test_worst_case_terminal_record_fits_reserved_per_record_bytes(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / ("run_" + "a" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir, clock=lambda: 9_999_999_999.999999)
    subagent_id = new_subagent_id()
    journal.start(
        subagent_id=subagent_id,
        agent_type="explore",
        max_turns=2,
        budget=None,
    )
    journal.append_event(
        subagent_id,
        sequence=1_500,
        event_type="tool.concurrent_terminal",
        step=1_000_000,
        payload=project_concurrent_tool_terminal(
            call_id="longest-valid-terminal-call",
            tool_name="t" * 128,
            status="cancelled_before_start",
        ),
    )

    path = run_dir / "subagent-runs" / f"{subagent_id}.jsonl"
    terminal_record = path.read_bytes().splitlines(keepends=True)[-1]
    assert len(terminal_record) <= (
        subagent_journal_module._MAX_TERMINAL_RECORD_BYTES
    )


def test_usage_scan_rejects_symlink_and_oversized_file_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / ("run_" + "a" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    root = run_dir / "subagent-runs"
    root.mkdir()
    subagent_id = new_subagent_id()
    path = root / f"{subagent_id}.jsonl"
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(b"{}\n")
    path.symlink_to(outside)

    with pytest.raises(SubagentJournalError, match="unsafe"):
        journal._load_byte_usage(path)

    path.unlink()
    with path.open("wb") as handle:
        handle.truncate(subagent_journal_module._MAX_FILE_BYTES + 1)

    def unexpected_read(*_args, **_kwargs):
        pytest.fail("oversized journal must be rejected before reading")

    monkeypatch.setattr(subagent_journal_module.os, "read", unexpected_read)
    with pytest.raises(SubagentJournalError, match="unsafe"):
        journal._load_byte_usage(path)


def test_append_rejects_file_changed_after_usage_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / ("run_" + "a" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    subagent_id = new_subagent_id()
    journal.start(
        subagent_id=subagent_id,
        agent_type="explore",
        max_turns=2,
        budget=None,
    )
    path = run_dir / "subagent-runs" / f"{subagent_id}.jsonl"
    fresh_writer = SubagentRunJournal(run_dir)
    original_load = fresh_writer._load_byte_usage

    def load_then_change(candidate: Path) -> tuple[int, int]:
        usage = original_load(candidate)
        with candidate.open("ab") as handle:
            handle.write(b"{}\n")
        return usage

    monkeypatch.setattr(fresh_writer, "_load_byte_usage", load_then_change)
    with pytest.raises(SubagentJournalError, match="changed outside"):
        fresh_writer.append_event(
            subagent_id,
            sequence=1,
            event_type="model.completed",
            step=1,
            payload={},
        )

    assert path.read_bytes().endswith(b"{}\n")


def test_concurrent_terminal_projection_is_content_free(tmp_path: Path) -> None:
    secret = "sk-secret-call-content"

    payload = project_concurrent_tool_terminal(
        call_id=secret,
        tool_name="read_file\n" + secret,
        status="abandoned_running",
    )
    serialized = json.dumps(payload, sort_keys=True)

    assert re.fullmatch(r"callref_[0-9a-f]{32}", str(payload["callId"]))
    assert payload["toolName"] == "unknown"
    assert secret not in serialized
    assert "\\n" not in serialized

    run_dir = tmp_path / ("run_" + "a" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    subagent_id = new_subagent_id()
    journal.start(
        subagent_id=subagent_id,
        agent_type="explore",
        max_turns=2,
        budget=None,
    )
    journal.append_event(
        subagent_id,
        sequence=1,
        event_type="tool.concurrent_terminal",
        step=1,
        payload=payload,
    )
    persisted = (
        run_dir / "subagent-runs" / f"{subagent_id}.jsonl"
    ).read_text(encoding="utf-8")
    assert secret not in persisted


def test_parallel_first_writers_create_shared_journal_root_without_race(
    tmp_path: Path,
) -> None:
    import threading

    run_dir = tmp_path / ("run_" + "a" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def start_one() -> None:
        subagent_id = new_subagent_id()
        barrier.wait()
        try:
            journal.start(
                subagent_id=subagent_id,
                agent_type="explore",
                max_turns=2,
                budget=None,
            )
        except BaseException as error:  # noqa: BLE001 - test captures all workers
            errors.append(error)

    workers = [threading.Thread(target=start_one) for _ in range(3)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(2)

    assert errors == []
    assert len(tuple((run_dir / "subagent-runs").glob("*.jsonl"))) == 3


def test_shared_budget_rejects_parallel_overspend() -> None:
    budget = AgentTurnBudget(max_model_calls=10, max_total_tokens=100)
    budget.reserve_model_call(90)
    budget.reserve_model_call(5)

    with pytest.raises(AgentBudgetExceeded):
        budget.reserve_model_call(10)


def test_shared_budget_reservations_are_atomic_across_threads() -> None:
    import threading

    budget = AgentTurnBudget(max_model_calls=10, max_total_tokens=100)
    barrier = threading.Barrier(3)
    outcomes: list[str] = []

    def reserve() -> None:
        barrier.wait()
        try:
            budget.reserve_model_call(60)
            outcomes.append("admitted")
        except AgentBudgetExceeded:
            outcomes.append("rejected")

    workers = [threading.Thread(target=reserve) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(2)

    assert sorted(outcomes) == ["admitted", "rejected"]
    assert budget.snapshot().reserved_total_tokens == 60


def test_budget_without_limits_never_blocks() -> None:
    budget = AgentTurnBudget()
    for _ in range(200):
        budget.reserve_model_call(100_000)
    assert budget.snapshot().used_model_calls == 200


def test_task_tool_writes_subagent_journal_and_passes_shared_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import minicode.tools.task as task_module
    from minicode.tooling import ToolContext

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    record = journal.create_run(title="parent", source="headless")
    journal.transition(record.id, "running")
    subjournal = journal.open_subagent_journal(record.id)
    shared_budget = AgentTurnBudget(max_model_calls=4, max_total_tokens=10_000)

    class ParentSink:
        def __init__(self) -> None:
            self.summary_events: list[str] = []

        @property
        def run_id(self) -> str:
            return record.id

        def emit(self, event_type: str, *, step=None, payload=None) -> None:
            self.summary_events.append(event_type)

        def open_subagent_journal(self) -> SubagentRunJournal:
            return subjournal

        def record_written_memory_ids(self, entry_ids: list[str]) -> None:
            return None

    captured: dict = {}

    def fake_run_agent_turn(**kwargs):
        captured.update(kwargs)
        kwargs["outcome_capture"].record(
            canonicalize_task_outcome("success", 0)
        )
        sink = kwargs.get("event_sink")
        if sink is not None:
            sink.emit(
                "task.outcome",
                step=1,
                payload={
                    "outcomeVersion": 1,
                    "outcomeStatus": "success",
                    "goalAchieved": True,
                    "learningSuccess": True,
                    "hadToolErrors": False,
                    "errorsRecovered": False,
                    "toolErrorCount": 0,
                },
            )
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr(task_module, "run_agent_turn", fake_run_agent_turn)

    result = task_module.task_tool.run(
        {
            "description": "explore",
            "prompt": "look around",
            "agent_type": "explore",
        },
        ToolContext(
            cwd=str(workspace),
            _runtime={"model": "fake"},
            _agent_depth=0,
            _event_sink=ParentSink(),
            _agent_budget=shared_budget,
        ),
    )

    assert result.ok is True
    assert captured["agent_budget"] is shared_budget
    assert captured["budget_exhausted_policy"] == "raise"

    summaries = journal.list_subagent_runs(record.id)
    assert len(summaries) == 1
    assert summaries[0].outcome == "completed"
    assert summaries[0].model_turns == 1
    events = journal.list_subagent_events(record.id, summaries[0].subagent_id)
    assert [event.type for event in events] == ["skill.routed", "task.outcome"]


def test_task_tool_records_budget_exhausted_sub_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import minicode.tools.task as task_module
    from minicode.tooling import ToolContext

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    record = journal.create_run(title="parent", source="headless")
    journal.transition(record.id, "running")
    subjournal = journal.open_subagent_journal(record.id)

    class ParentSink:
        def __init__(self) -> None:
            self.payloads: list[dict] = []

        def emit(self, event_type: str, *, step=None, payload=None) -> None:
            self.payloads.append(payload)

        def open_subagent_journal(self) -> SubagentRunJournal:
            return subjournal

    exhausted = AgentTurnBudget(max_model_calls=0)
    snapshot = exhausted.snapshot()

    def fake_run_agent_turn(**_kwargs):
        raise AgentBudgetExceeded("token budget exhausted", snapshot)

    monkeypatch.setattr(task_module, "run_agent_turn", fake_run_agent_turn)

    sink = ParentSink()
    result = task_module.task_tool.run(
        {
            "description": "explore",
            "prompt": "look around",
            "agent_type": "explore",
        },
        ToolContext(
            cwd=str(workspace),
            _runtime={"model": "fake"},
            _agent_depth=0,
            _event_sink=sink,
            _agent_budget=exhausted,
        ),
    )

    assert result.ok is False
    assert "agent_budget_exceeded" in result.output
    assert sink.payloads[-1]["outcome"] == "budget_exceeded"

    summaries = journal.list_subagent_runs(record.id)
    assert len(summaries) == 1
    assert summaries[0].outcome == "budget_exceeded"
