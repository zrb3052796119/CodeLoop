from __future__ import annotations

from copy import deepcopy

import pytest

from minicode.agent_loop import (
    _append_tool_trace_events,
    _append_trace_event,
)
from minicode.tooling import ToolResult


def test_trace_events_receive_stable_unique_v2_ids() -> None:
    trace: list[dict] = []

    _append_trace_event(trace, {"type": "assistant_step", "step": 1})
    _append_trace_event(trace, {"type": "assistant_step", "step": 2})
    copied = deepcopy(trace)
    _append_trace_event(copied, {"type": "task_result", "step": 2})

    assert [event["event_id"] for event in trace] == [
        "event-000001",
        "event-000002",
    ]
    assert [event["event_id"] for event in copied] == [
        "event-000001",
        "event-000002",
        "event-000003",
    ]
    assert all(event["trace_schema_version"] == 2 for event in copied)


def test_explicit_event_id_collision_is_rejected_without_mutating_trace() -> None:
    trace: list[dict] = []
    _append_trace_event(trace, {"type": "assistant_step", "event_id": "custom-event"})

    with pytest.raises(ValueError, match="event_id"):
        _append_trace_event(trace, {"type": "task_result", "event_id": "custom-event"})

    assert len(trace) == 1


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "expected_field", "expected_path"),
    [
        ("read_file", {"path": "src/auth.py"}, "files_read", "src/auth.py"),
        ("grep_files", {"path": "src", "files": ["src/auth.py"]}, "files_read", "src/auth.py"),
        ("edit_file", {"file_path": "src/config.py"}, "files_changed", "src/config.py"),
        ("write_file", {"path": "docs/guide.md"}, "files_changed", "docs/guide.md"),
        ("run_command", {"command": "pytest tests/test_auth.py -q"}, "referenced_files", "tests/test_auth.py"),
    ],
)
def test_tool_trace_assigns_explicit_file_roles(
    tool_name: str,
    tool_input: dict,
    expected_field: str,
    expected_path: str,
) -> None:
    trace: list[dict] = []

    _append_tool_trace_events(
        trace,
        {"id": "call-1", "toolName": tool_name, "input": tool_input},
        ToolResult(ok=True, output="ok"),
        step=1,
    )

    assert expected_path in trace[0][expected_field]
    assert expected_path in trace[1][expected_field]


def test_command_path_parser_rejects_command_url_flags_and_environment_assignments() -> None:
    trace: list[dict] = []
    command = (
        "TOKEN=config/secret.txt pytest tests/test_auth.py -q "
        "--config=conf/test.toml https://example.invalid/a.py"
    )

    _append_tool_trace_events(
        trace,
        {"id": "call-1", "toolName": "run_command", "input": {"command": command}},
        ToolResult(ok=True, output="1 passed"),
        step=1,
    )

    assert trace[0]["referenced_files"] == ["tests/test_auth.py"]
    assert command not in trace[0].get("files", [])


def test_unknown_tool_does_not_guess_file_role_from_arbitrary_payload() -> None:
    trace: list[dict] = []

    _append_tool_trace_events(
        trace,
        {
            "id": "call-1",
            "toolName": "inspect_payload",
            "input": {"payload": {"path": "src/not-a-real-file.py"}},
        },
        ToolResult(ok=True, output="ok"),
        step=1,
    )

    assert not trace[0].get("files_read")
    assert not trace[0].get("files_changed")
    assert not trace[0].get("referenced_files")


def test_retry_nudge_is_a_recovery_suggestion_not_a_recovery() -> None:
    trace: list[dict] = []

    _append_tool_trace_events(
        trace,
        {"id": "call-1", "toolName": "read_file", "input": {"path": "src/auth.py"}},
        ToolResult(ok=False, output="[PermissionError] denied"),
        step=1,
        recovery_note="Retry with an allowed path.",
    )

    assert [event["type"] for event in trace] == [
        "tool_call",
        "tool_result",
        "error",
        "recovery_suggestion",
    ]
    assert trace[-1]["suggestion"] == "Retry with an allowed path."
    assert "action" not in trace[-1]
