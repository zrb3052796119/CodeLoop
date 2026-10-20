"""Real workflow parser and pytest execution preserve keyword selection."""
from __future__ import annotations

import shlex
import subprocess
import sys

import pytest

from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.multi_agent_contracts import parse_workflow_request
from minicode.tools.task import (
    _WORKFLOW_PROMPT_MAX_CHARS,
    _auto_construct_workflow_request,
    task_tool,
)
from minicode.verification_policy import verification_argv_is_safe


SELECTOR = "scheduled_concurrently or stay_serial or mixed_sub_agent_batch or undecidable"
PUBLIC_ARGV = [
    "python3", "-m", "pytest", "-q", "tests/test_sub_agent_isolation.py",
    "-k", SELECTOR,
]


def workflow_command(argv: list[str], *, command_string: bool = False):
    validated = task_tool.validator({
        "agent_type": "workflow",
        "description": "Repair concurrent task contexts",
        "prompt": "Repair the concurrency defect and retain the public verification selector.",
        "write_paths": ["minicode/agent_loop.py", "tests/test_eval_regression.py"],
        "verification_commands": [shlex.join(argv) if command_string else argv],
    })
    request, error = _auto_construct_workflow_request(validated)
    assert error is None
    parsed = parse_workflow_request(request, legacy_task_id="task_selector_test")
    assert parsed.verification_checks[0].argv == tuple(argv)
    return VerificationCheck(
        check_id="check_selector", kind="tests", argv=tuple(argv), timeout_seconds=30,
    )


@pytest.mark.parametrize("command_string", [False, True])
def test_public_verification_selector_survives_every_workflow_gate(command_string):
    assert workflow_command(PUBLIC_ARGV, command_string=command_string).argv == tuple(PUBLIC_ARGV)


@pytest.mark.parametrize("prefix", [["pytest"], ["python", "-m", "pytest"], ["python3", "-m", "pytest"]])
@pytest.mark.parametrize("selector_args", [
    ["-k", "not (old_a or old_b) and (new or foo[case-1])"],
    ["-kfoo[case-1]"], ["-k=foo[case-1]"], ["-knot old_a"],
    ["-k", "name.with:characters+and/path\\suffix"],
])
def test_pytest_keyword_forms_pass_the_real_contract(prefix, selector_args):
    workflow_command([*prefix, "-q", "tests/test_example.py", *selector_args])


@pytest.mark.parametrize("args", [
    ["-k"], ["-k", ""], ["-k="], ["-k", "--collect-only"],
    ["-k", "foo or"], ["-k", "(foo"], ["-k", "foo)"],
    ["-k", "()"], ["-k", "foo()"], ["-k", "foo(x=1)"],
    ["-k", "foo AND bar"], ["-k", "foo;touch /tmp/answer"],
    ["-k", "foo | bar"], ["-k", "$(id)"], ["-k", "foo\nbar"],
    ["-k", "foo", "../outside.py"], ["-k", "foo", "/tmp/hidden.py"],
    ["-k", "foo", "-p", "external_plugin"], ["-k", "foo", "-c/tmp/config"],
    ["-k", "foo", "--rootdir=/tmp"], ["-k", "foo", "--collect-only"],
    ["-k", "foo", "tests/test_(shell).py"], ["-k", "foo", "&&", "python3"],
])
def test_selector_exception_does_not_open_shell_paths_plugins_or_noop_options(args):
    argv = ["python3", "-m", "pytest", "-q", *args]
    assert not verification_argv_is_safe(argv)
    with pytest.raises(ValueError, match="safe argv"):
        VerificationCheck(check_id="check_selector", kind="tests", argv=tuple(argv), timeout_seconds=30)


def test_keyword_option_is_not_enabled_for_other_verifiers():
    assert not verification_argv_is_safe(["python3", "-m", "unittest", "-k", "test_name"])
    assert not verification_argv_is_safe(["ruff", "check", "-k", "test_name"])


def test_parsed_selector_reaches_real_pytest_without_broadening_execution(tmp_path):
    (tmp_path / "test_cases.py").write_text(
        "def test_scheduled_concurrently(): assert True\n"
        "def test_stay_serial(): assert True\n"
        "def test_unrelated_failure(): assert False\n"
    )
    argv = ["python3", "-m", "pytest", "-q", "test_cases.py", "-k",
            "(scheduled_concurrently or stay_serial) and not unrelated"]
    check = workflow_command(argv, command_string=True)
    completed = []
    evidence = []

    def run_pytest(argv, **kwargs):
        assert argv == check.argv
        kwargs["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        process = subprocess.run([sys.executable, *argv[1:]], **kwargs)
        completed.append(process)
        return process

    result = DeterministicVerifier(command_runner=run_pytest).run(
        checks=(check,), cwd=str(tmp_path), workflow_id="wf_selector_test",
        task_id="task_selector_test", execution_assignment_id="asg_selector_test",
        patch_digest="sha256:" + "a" * 64,
        evidence_sink=lambda _artifact_id, value: evidence.append(value),
    )
    assert result["status"] == "passed"
    assert result["checks"][0]["exitCode"] == 0
    assert evidence[0]["argv"] == list(check.argv)
    assert b"2 passed, 1 deselected" in completed[0].stdout


def test_workflow_prompt_schema_forwards_the_user_request_instead_of_rejecting_length():
    description = task_tool.input_schema["properties"]["prompt"]["description"]
    assert "do not paste the user's spec" in description
    assert "forwarded" in description
    assert "rejected" in description
    validated = task_tool.validator({
        "description": "Repair task",
        "agent_type": "workflow",
        "prompt": "x" * (_WORKFLOW_PROMPT_MAX_CHARS + 1),
    })
    assert len(validated["prompt"]) == _WORKFLOW_PROMPT_MAX_CHARS + 1
