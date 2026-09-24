from pathlib import Path
import os

import pytest

from minicode.tooling import ToolResult
from minicode.turn_completion import TurnCompletionPolicy
from minicode.verification_observation import project_verification


def _write(path: str = "change.py", content: str = "new") -> dict:
    return {"toolName": "write_file", "input": {"path": path, "content": content}}


def _check(target: str = "tests/test_one.py", *, passed: bool = True):
    call = {"toolName": "run_command", "input": {
        "command": "python3", "args": ["-m", "pytest", target],
    }}
    result = ToolResult(
        ok=passed, output="private raw command output",
        verification=project_verification(kind="tests", passed=passed, source="run_command_exit"),
    )
    return call, result


def _mutate(policy, root, *, step, content="new"):
    call = _write(content=content)
    before = policy.capture_before([call])
    (root / "change.py").write_text(content)
    policy.observe_results([(call, ToolResult(ok=True, output="written"))], before, step)


def _symlink(link: Path, target: Path, *, directory: bool = False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("This host does not permit test symlink creation")


def test_real_file_change_earns_only_one_extension(tmp_path: Path):
    policy = TurnCompletionPolicy(50, 35, str(tmp_path))
    (tmp_path / "change.py").write_text("old")
    _mutate(policy, tmp_path, step=34)

    assert policy.mutation_epoch == 1
    assert policy.extend(35, 35, True) == 40
    assert policy.extend(40, 40, True) == 40
    _mutate(policy, tmp_path, step=39, content="next")
    assert policy.extend(40, 40, True) == 45
    assert "change.py" in policy.summary("soft limit")


def test_new_file_counts_but_unchanged_write_and_successful_reads_do_not(tmp_path: Path):
    policy = TurnCompletionPolicy(50, 35, str(tmp_path))
    _mutate(policy, tmp_path, step=30)
    assert policy.extend(35, 35, True) == 40
    _mutate(policy, tmp_path, step=39)
    policy.observe_results([
        ({"toolName": "read_file", "input": {"path": "change.py"}}, ToolResult(ok=True, output="new")),
        ({"toolName": "todo_write", "input": {}}, ToolResult(ok=True, output="done")),
    ], {}, 40)
    assert policy.mutation_epoch == 1
    assert policy.extend(40, 40, True) == 40


def test_failed_write_does_not_claim_progress_even_if_bytes_changed(tmp_path: Path):
    policy = TurnCompletionPolicy(50, 35, str(tmp_path))
    call = _write()
    before = policy.capture_before([call])
    (tmp_path / "change.py").write_text("new")
    policy.observe_results([(call, ToolResult(ok=False, output="failed"))], before, 35)
    assert policy.mutation_epoch == 0
    assert policy.extend(35, 35, True) == 35


@pytest.mark.parametrize("mode", ["outside", "file_symlink", "parent_symlink", "large", "directory"])
def test_unobservable_or_outside_targets_do_not_earn_progress(tmp_path: Path, mode: str):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("old")
    target = root / "change.py"
    path = "change.py"
    if mode == "outside":
        path = "../outside.py"
        target = outside
    elif mode == "file_symlink":
        _symlink(target, outside)
    elif mode == "parent_symlink":
        _symlink(root / "linked", tmp_path, directory=True)
        path = "linked/outside.py"
        target = root / path
    elif mode == "large":
        target.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    else:
        target.mkdir()
    policy = TurnCompletionPolicy(50, 35, str(root))
    call = _write(path)
    before = policy.capture_before([call])
    assert not before
    if mode != "directory":
        target.write_text("changed")
    policy.observe_results([(call, ToolResult(ok=True, output="written"))], before, 35)
    assert policy.extend(35, 35, True) == 35


def test_replacing_target_with_outside_symlink_after_capture_is_not_read(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    policy = TurnCompletionPolicy(50, 35, str(root))
    call = _write()
    before = policy.capture_before([call])
    outside = tmp_path / "private.py"
    outside.write_text("secret")
    _symlink(root / "change.py", outside)
    policy.observe_results([(call, ToolResult(ok=True, output="written"))], before, 35)
    assert policy.extend(35, 35, True) == 35
    assert "secret" not in policy.summary("limit")


def test_portable_capture_records_real_file_changes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(os, "supports_dir_fd", set())
    policy = TurnCompletionPolicy(50, 35, str(tmp_path))
    _mutate(policy, tmp_path, step=34)
    assert policy.extend(35, 35, True) == 40
    _mutate(policy, tmp_path, step=39, content="next")
    assert policy.extend(40, 40, True) == 45


def test_portable_capture_rejects_linked_parent(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(os, "supports_dir_fd", set())
    root = tmp_path / "workspace"
    root.mkdir()
    (tmp_path / "private.py").write_text("private")
    _symlink(root / "linked", tmp_path, directory=True)
    policy = TurnCompletionPolicy(50, 35, str(root))
    assert policy.capture_before([_write("linked/private.py")]) == {}


def test_passed_check_deduplicates_by_actual_command_epoch_and_outcome(tmp_path: Path):
    policy = TurnCompletionPolicy(50, 35, str(tmp_path))
    check = _check()
    policy.observe_results([check], {}, 34)
    assert policy.extend(35, 35, True) == 40
    policy.observe_results([check], {}, 39)
    assert policy.extend(40, 40, True) == 40
    policy.observe_results([_check("tests/test_two.py")], {}, 40)
    assert policy.extend(40, 40, True) == 45
    _mutate(policy, tmp_path, step=43)
    assert policy.extend(45, 45, True) == 49
    policy.observe_results([check], {}, 48)
    assert policy.summary("limit").count("change epoch 1") == 1
    assert policy.extend(49, 49, True) == 49


def test_failed_checks_remain_evidence_and_only_recovered_check_earns_progress(tmp_path: Path):
    policy = TurnCompletionPolicy(50, 35, str(tmp_path))
    policy.observe_results([_check(passed=False)], {}, 34)
    assert policy.extend(35, 35, True) == 35
    policy.observe_results([_check(passed=True)], {}, 35)
    assert policy.extend(35, 35, True) == 40
    summary = policy.summary("limit")
    assert "failed" in summary and "passed" in summary
    assert "private raw command output" not in summary
    assert "Overall completion is unconfirmed" in summary


def test_verification_marker_must_match_tool_and_success(tmp_path: Path):
    policy = TurnCompletionPolicy(50, 35, str(tmp_path))
    call, result = _check()
    call["toolName"] = "read_file"
    policy.observe_results([(call, result)], {}, 35)
    assert policy.extend(35, 35, True) == 35
    call, result = _check()
    result.ok = False
    policy.observe_results([(call, result)], {}, 35)
    assert policy.extend(35, 35, True) == 35


def test_test_runner_input_identifies_distinct_checks(tmp_path: Path):
    policy = TurnCompletionPolicy(50, 35, str(tmp_path))
    result = ToolResult(ok=True, output="", verification=project_verification(
        kind="tests", passed=True, source="test_runner",
    ))
    first = {"toolName": "test_runner", "input": {"path": "tests/one"}}
    second = {"toolName": "test_runner", "input": {"path": "tests/two"}}
    policy.observe_results([(first, result)], {}, 35)
    assert policy.extend(35, 35, True) == 40
    policy.observe_results([(first, result)], {}, 40)
    assert policy.extend(40, 40, True) == 40
    policy.observe_results([(second, result)], {}, 40)
    assert policy.extend(40, 40, True) == 45


@pytest.mark.parametrize(("hard", "soft", "expected"), [(50, 35, 35), (3, 35, 2), (1, 35, 0), (0, 35, 0), (-1, 35, 0), (50, -1, 0)])
def test_work_limit_leaves_closing_slot_within_hard_limit(tmp_path: Path, hard, soft, expected):
    policy = TurnCompletionPolicy(hard, soft, str(tmp_path))
    assert policy.work_limit == expected
    policy.observe_results([_check()], {}, expected)
    assert policy.extend(expected, expected, True) <= max(0, hard - 1)


def test_stale_progress_and_unhealthy_turns_do_not_extend(tmp_path: Path):
    policy = TurnCompletionPolicy(50, 35, str(tmp_path))
    policy.observe_results([_check()], {}, 29)
    assert policy.extend(35, 35, True) == 35
    policy.observe_results([_check("tests/new.py")], {}, 35)
    assert policy.extend(35, 35, False) == 35
    assert policy.extend(34, 35, True) == 35


def test_lower_safety_limit_permanently_disables_extensions(tmp_path: Path):
    policy = TurnCompletionPolicy(50, 35, str(tmp_path))
    policy.lower_limit(20)
    policy.observe_results([_check()], {}, 20)
    assert policy.work_limit == 20
    assert policy.extend(20, 20, True) == 20
    policy.lower_limit(45)
    assert policy.work_limit == 20
    assert policy.extend(45, 45, True) == 20


def test_external_lower_limit_is_never_overridden(tmp_path: Path):
    policy = TurnCompletionPolicy(50, 35, str(tmp_path))
    policy.observe_results([_check()], {}, 20)
    assert policy.extend(20, 20, True) == 20


def test_summary_is_bounded_does_not_leak_arguments_and_closing_is_partial(tmp_path: Path):
    policy = TurnCompletionPolicy(50, 35, str(tmp_path))
    for index in range(100):
        call, result = _check(f"tests/{index}.py")
        call["input"]["args"] += ["--token", "never-print-this-value"]
        policy.observe_results([(call, result)], {}, index)
    summary = policy.summary("limit reached " + "x" * 10000)
    assert len(summary) < 5000
    assert "never-print-this-value" not in summary
    assert "private raw command output" not in summary
    assert "python3" in summary
    assert "tests/99.py" in summary
    assert "-m pytest" in summary
    prompt = policy.closing_prompt("soft limit")
    assert "Do NOT call tools" in prompt
    assert "user's language" in prompt
    assert "Do not claim the whole task succeeded" in prompt
    assert "Overall completion is unconfirmed" in prompt
