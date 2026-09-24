"""Recovery exceptions must preserve the failed operation's proof boundary."""

from __future__ import annotations

from typing import Any

import pytest

from minicode.agent_loop import _append_tool_trace_events, _append_trace_event
from minicode.agent_reflection import ReflectionEngine
from minicode.tooling import ToolResult
from minicode.tools.list_files import list_files_tool
from minicode.tools.read_file import read_file_tool
from minicode.tools.run_command import run_command_tool


def _reflect(steps: list[tuple[str, dict[str, Any], bool, str]]):
    tools = {
        "run_command": run_command_tool,
        "read_file": read_file_tool,
        "list_files": list_files_tool,
    }
    trace: list[dict[str, Any]] = []
    for index, (name, payload, ok, output) in enumerate(steps, start=1):
        _append_tool_trace_events(
            trace,
            {"id": f"call-{index}", "toolName": name,
             "input": tools[name].validator(payload)},
            ToolResult(ok=ok, output=output), index,
        )
    _append_trace_event(trace, {"type": "task_result", "status": "success"})
    return ReflectionEngine().reflect("Review and verify the project", trace)


@pytest.mark.parametrize(
    "successful_targets",
    [
        ["backend/tests/test_auth.py"],
        ["backend/tests/test_missing.py"],
    ],
)
def test_relocation_must_repair_the_missing_object_and_cover_other_inputs(
    successful_targets: list[str],
) -> None:
    result = _reflect([
        ("run_command", {"command": "pytest", "args": [
            "tests/test_auth.py", "tests/test_missing.py",
        ]}, False, "ERROR: file or directory not found: tests/test_missing.py"),
        ("run_command", {"command": "pytest", "args": successful_targets}, True, "1 passed"),
    ])

    assert not result.task_evidence.recoveries
    assert not result.value_decision.accepted


def test_explicit_bare_directory_is_not_a_default_scope_retry() -> None:
    result = _reflect([
        ("run_command", {"command": "pytest", "args": ["tests/test_auth.py"]},
         False, "ERROR: file or directory not found: tests/test_auth.py"),
        ("run_command", {"command": "pytest", "args": ["billing"]}, True, "1 passed"),
    ])

    assert not result.task_evidence.recoveries
    assert not result.value_decision.accepted


@pytest.mark.parametrize(
    "name,failed_input,successful_input,message,output",
    [
        ("read_file", {"path": "src/auth.py"}, {"path": "backend/src/auth.py"},
         "[FileNotFoundError] src/auth.py does not exist", "class AuthService: pass"),
        ("list_files", {"path": "config"}, {"path": ""},
         "Path does not exist: config", "dir app\nfile README.md"),
        ("run_command", {"command": "pytest", "args": ["tests/test_auth.py", "tests/test_missing.py"]},
         {"command": "pytest", "args": ["tests/test_auth.py", "backend/tests/test_missing.py"]},
         "ERROR: file or directory not found: tests/test_missing.py", "2 passed"),
    ],
)
def test_proven_path_correction_keeps_complete_scope(
    name: str, failed_input: dict[str, Any], successful_input: dict[str, Any],
    message: str, output: str,
) -> None:
    result = _reflect([
        (name, failed_input, False, message),
        (name, successful_input, True, output),
    ])

    assert len(result.task_evidence.recoveries) == 1
    assert result.value_decision.accepted


@pytest.mark.parametrize("failure_output", [
    "F. [100%]\n=== FAILURES ===\n"
    "tests/test_cli.py:4: in test_nested_helper\nE SystemExit: 2\n"
    "--- Captured stderr call ---\nusage: nested-helper [-h]\n"
    "nested-helper: error: unrecognized arguments: --run-integration\n"
    "FAILED tests/test_cli.py::test_nested_helper - SystemExit: 2\n1 failed, 1 passed",
    "usage: nested-helper [-h]\n"
    "nested-helper: error: unrecognized arguments: --run-integration",
    "UsageError: unrecognized arguments: --run-integration\n1 failed, 1 passed",
])
def test_nested_or_executed_argument_error_cannot_waive_parent_scope(
    failure_output: str,
) -> None:
    result = _reflect([
        ("run_command", {"command": "pytest", "args": [
            "tests/test_cli.py", "--run-integration",
        ]}, False, failure_output),
        ("run_command", {"command": "pytest", "args": ["tests/test_cli.py"]},
         True, "1 passed, 1 skipped"),
    ])

    assert not result.task_evidence.recoveries
    assert not result.value_decision.accepted


@pytest.mark.parametrize("failure_output", [
    "UsageError: unrecognized arguments: --bad-option",
    "ERROR: usage: python -m pytest [options] [file_or_dir] [...]\n"
    "python -m pytest: error: unrecognized arguments: --bad-option\n"
    "  inifile: None\n  rootdir: /workspace",
])
def test_outer_parser_rejection_allows_removing_invalid_argument(
    failure_output: str,
) -> None:
    result = _reflect([
        ("run_command", {"command": "pytest", "args": [
            "tests/test_auth.py", "--bad-option",
        ]}, False, failure_output),
        ("run_command", {"command": "pytest", "args": ["tests/test_auth.py", "-q"]},
         True, "1 passed"),
    ])

    assert len(result.task_evidence.recoveries) == 1
    assert result.value_decision.accepted
