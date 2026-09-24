"""Recovery admission must preserve argv boundaries and execution modes."""
from __future__ import annotations

import shlex

import pytest

from minicode.agent_loop import _append_tool_trace_events, _append_trace_event
from minicode.agent_reflection import ReflectionEngine
from minicode.reflection_evidence import call_objective, objective_relation
from minicode.tooling import ToolResult
from minicode.tools.run_command import run_command_tool


def _reflect(first: list[str], second: list[str], output: str = "2 passed"):
    trace = []
    for index, (argv, ok, result) in enumerate([
        (first, False, "AssertionError: expired auth token accepted"),
        (second, True, output),
    ], 1):
        payload = run_command_tool.validator({"command": argv[0], "args": argv[1:]})
        _append_tool_trace_events(
            trace, {"id": f"call-{index}", "toolName": "run_command", "input": payload},
            ToolResult(ok=ok, output=result), index,
        )
    _append_trace_event(trace, {"type": "task_result", "status": "success"})
    return ReflectionEngine().reflect("Repair and verify authentication", trace)


@pytest.mark.parametrize("display", ["-q", "-v", "-s", "--quiet"])
def test_boolean_display_flags_do_not_hide_replaced_test_targets(display: str) -> None:
    first = ["pytest", display, "tests/test_auth.py", "tests/test_smoke.py"]
    second = ["pytest", display, "tests/test_billing.py", "tests/test_smoke.py"]
    assert objective_relation(call_objective(shlex.join(first)), call_objective(shlex.join(second))) == "conflict"
    assert not _reflect(first, second).value_decision.accepted


@pytest.mark.parametrize("wrapped", [False, True])
def test_complete_quoted_selection_expression_controls_admission(wrapped: bool) -> None:
    first = ["pytest", "tests/", "-k", "test_smoke or test_expired_auth"]
    second = ["pytest", "tests/", "-k", "test_smoke and not test_expired_auth"]
    if wrapped:
        first, second = ["bash", "-lc", shlex.join(first)], ["bash", "-lc", shlex.join(second)]
    assert objective_relation(call_objective(shlex.join(first)), call_objective(shlex.join(second))) == "conflict"
    assert not _reflect(first, second, "1 passed, 1 deselected").value_decision.accepted


def test_identical_quoted_selection_survives_wrapper_and_display_changes() -> None:
    command = ["pytest", "tests/", "-k", "test_smoke or test_expired_auth"]
    first = call_objective(shlex.join(command))
    second = call_objective(shlex.join(["bash", "-lc", shlex.join([*command, "-q"])]))
    assert first.selection == ("-k=test_smoke or test_expired_auth",)
    assert objective_relation(first, second) == "match"


@pytest.mark.parametrize("flag,output", [
    ("--version", "pytest 9.1.1"),
    ("-V", "pytest 9.1.1"),
    ("--help", "usage: pytest [options] [file_or_dir]"),
    ("--collect-only", "1 test collected"),
])
def test_non_executing_modes_do_not_supply_passing_verification(flag: str, output: str) -> None:
    first = ["pytest", "tests/test_auth.py"]
    reflection = _reflect(first, [*first, flag], output)
    assert not reflection.value_decision.accepted
    assert not [item for item in reflection.task_evidence.verification
                if item.call_id == "call-2" and item.result == "passed"]


def test_unknown_tool_does_not_inherit_pytest_display_flag_semantics() -> None:
    first = call_objective("python scripts/report.py -q")
    second = call_objective("python scripts/report.py")
    assert objective_relation(first, second) == "conflict"


def test_unknown_argument_values_and_repeated_flag_order_are_retained() -> None:
    pairs = [
        ("pytest tests/ --custom alpha", "pytest tests/ --custom beta"),
        ("pytest tests/ --custom=alpha --custom=beta", "pytest tests/ --custom=beta --custom=alpha"),
    ]
    for first, second in pairs:
        assert objective_relation(call_objective(first), call_objective(second)) == "conflict"


def test_malformed_shell_cannot_become_a_confirmed_target() -> None:
    objective = call_objective("pytest tests/ -k 'unterminated")
    assert objective_relation(objective, objective) != "match"


@pytest.mark.parametrize("command", [
    ["bash", "-lc", "pytest tests/test_auth.py --help $FLAGS"],
    ["bash", "-lc", "pytest tests/test_auth.py -k 'unterminated"],
    ["pytest", *(f"tests/test_{index}.py" for index in range(34))],
])
def test_incomplete_invocation_cannot_supply_passing_verification(command: list[str]) -> None:
    reflection = _reflect(["pytest", "tests/test_auth.py"], command, "usage: pytest --fixtures")
    assert not [item for item in reflection.task_evidence.verification
                if item.call_id == "call-2" and item.result == "passed"]
    assert not reflection.value_decision.accepted


@pytest.mark.parametrize("args,expected", [
    (["-q", "tests/test_auth.py"], "targeted"),
    (["-q", "-k", "test_auth"], "targeted"),
    (["-q"], "full"),
])
def test_quiet_flag_does_not_declare_a_full_suite(args: list[str], expected: str) -> None:
    reflection = _reflect(["pytest", "tests/test_auth.py"], ["pytest", *args], "1 passed")
    check = next(item for item in reflection.task_evidence.verification if item.call_id == "call-2")
    assert check.scope == expected
