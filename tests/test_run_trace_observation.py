from __future__ import annotations

import logging
import re
from pathlib import Path
from types import SimpleNamespace

from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run


def test_observation_records_safe_fifo_tool_and_one_assistant_trace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"

    with observe_run(
        workspace=workspace,
        source="headless",
        title="Trace task",
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    ) as observation:
        observation.tool_started("read_file")
        observation.tool_started("read_file")
        observation.tool_started("run_command")
        observation.tool_finished("read_file", is_error=False)
        observation.tool_finished("run_command", is_error=True)
        observation.tool_finished("read_file", is_error=False)
        observation.assistant_completed(content_present=True, content_length=428)
        observation.assistant_completed(content_present=True, content_length=999)

    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.list_runs().items[0]
    events = journal.list_events(record.id, limit=100).items

    assert [event.type for event in events] == [
        "run.queued",
        "run.started",
        "tool.started",
        "tool.started",
        "tool.started",
        "tool.finished",
        "tool.finished",
        "tool.finished",
        "assistant.completed",
        "run.completed",
    ]
    assert all(event.step is None for event in events)
    first_read_id = events[2].payload["operationId"]
    second_read_id = events[3].payload["operationId"]
    command_id = events[4].payload["operationId"]
    assert re.fullmatch(r"toolop_[0-9a-f]{32}", first_read_id)
    assert len({first_read_id, second_read_id, command_id}) == 3
    assert events[5].payload == {
        "toolName": "read_file",
        "operationId": first_read_id,
        "outcome": "success",
        "paired": True,
    }
    assert events[6].payload == {
        "toolName": "run_command",
        "operationId": command_id,
        "outcome": "error",
        "paired": True,
    }
    assert events[7].payload["operationId"] == second_read_id
    assert events[8].payload == {
        "contentPresent": True,
        "contentLength": 428,
        "kind": "returned_assistant",
    }
    serialized = str([event.to_dict() for event in events])
    for forbidden in ("duration", "toolInput", "toolOutput", "content\":"):
        assert forbidden not in serialized


def test_unpaired_result_is_safe_and_dangling_start_is_not_fabricated(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"

    with observe_run(
        workspace=workspace,
        source="tui",
        title="Pairing",
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    ) as observation:
        observation.tool_finished("read_file", is_error=False)
        observation.tool_started("write_file")

    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.list_runs().items[0]
    events = journal.list_events(record.id).items

    assert events[2].type == "tool.finished"
    assert events[2].payload == {
        "toolName": "read_file",
        "outcome": "success",
        "paired": False,
    }
    assert [event.type for event in events].count("tool.finished") == 1
    assert [event.type for event in events].count("tool.started") == 1


def test_trace_input_is_normalized_without_leaking_or_raising(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"

    with observe_run(
        workspace=workspace,
        source="gateway",
        title="Normalize",
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    ) as observation:
        observation.tool_started("../password=hidden-value")
        observation.tool_finished("../password=hidden-value", is_error=True)
        observation.assistant_completed(content_present=True, content_length=True)

    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.list_runs().items[0]
    events = journal.list_events(record.id).items
    serialized = str([event.to_dict() for event in events])

    assert events[2].payload["toolName"] == "unknown"
    assert events[3].payload["toolName"] == "unknown"
    assert events[4].payload == {
        "contentPresent": True,
        "contentLength": 0,
        "kind": "returned_assistant",
    }
    assert "hidden-value" not in serialized


def test_trace_append_failures_and_terminal_calls_are_noop(
    tmp_path: Path,
    caplog,
) -> None:
    class AppendFailingJournal:
        def __init__(self) -> None:
            self.transitions: list[str] = []
            self.append_calls = 0

        def create_run(self, **_kwargs):
            return SimpleNamespace(id="run_" + "a" * 32)

        def transition(self, _run_id: str, status: str, *, reason=None):
            self.transitions.append(status)

        def append_event(self, *_args, **_kwargs):
            self.append_calls += 1
            raise OSError("Bearer journal-secret")

    journal = AppendFailingJournal()
    caplog.set_level(logging.WARNING, logger="minicode.run_lifecycle")

    with observe_run(
        workspace=tmp_path,
        source="headless",
        title="Best effort",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        observation.tool_started("read_file")
        observation.tool_finished("read_file", is_error=False)
        observation.assistant_completed(content_present=False, content_length=0)

    calls_before_terminal = journal.append_calls
    observation.tool_started("write_file")
    observation.tool_finished("write_file", is_error=True)
    observation.assistant_completed(content_present=True, content_length=10)

    assert journal.transitions == ["running", "completed"]
    assert calls_before_terminal == 3
    assert journal.append_calls == calls_before_terminal
    assert "observation unavailable during event" in caplog.text
    assert "journal-secret" not in caplog.text


def test_disabled_and_start_failed_observations_return_safe_handles(
    tmp_path: Path,
) -> None:
    with observe_run(
        workspace=tmp_path,
        source="headless",
        title="Disabled",
        enabled=False,
    ) as disabled:
        disabled.tool_started("read_file")
        disabled.tool_finished("read_file", is_error=False)
        disabled.assistant_completed(content_present=False, content_length=0)

    class StartFailingJournal:
        def create_run(self, **_kwargs):
            return SimpleNamespace(id="run_" + "b" * 32)

        def transition(self, *_args, **_kwargs):
            raise OSError("password=hidden")

        def append_event(self, *_args, **_kwargs):
            raise AssertionError("append must not be called")

    with observe_run(
        workspace=tmp_path,
        source="headless",
        title="Start fails",
        journal_factory=lambda _workspace: StartFailingJournal(),
    ) as failed:
        failed.tool_started("read_file")
        failed.tool_finished("read_file", is_error=False)
        failed.assistant_completed(content_present=False, content_length=0)


def test_run_observation_is_an_event_sink_and_preserves_real_step(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    operation_id = "modelop_" + "a" * 32

    with observe_run(
        workspace=workspace,
        source="headless",
        title="Model event",
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    ) as observation:
        observation.emit(
            "model.started",
            step=3,
            payload={"operationId": operation_id},
        )
        observation.emit(
            "model.completed",
            step=3,
            payload={
                "operationId": operation_id,
                "resultType": "assistant",
                "contentPresent": True,
                "toolCallCount": 0,
            },
        )

    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.list_runs().items[0]
    events = journal.list_events(record.id).items

    assert [event.type for event in events] == [
        "run.queued",
        "run.started",
        "model.started",
        "model.completed",
        "run.completed",
    ]
    assert [event.step for event in events] == [None, None, 3, 3, None]
    assert events[2].payload == {"operationId": operation_id}
    assert events[3].payload["toolCallCount"] == 0

    observation.emit("model.started", step=4, payload={"operationId": operation_id})
    assert len(journal.list_events(record.id).items) == 5
