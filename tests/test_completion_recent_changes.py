"""A large reproduction fixture must not hide the final code/test changes."""
from pathlib import Path

import pytest

from minicode.tooling import ToolResult
from minicode.turn_completion import TurnCompletionPolicy


def record_write(policy, root, path, content, step):
    call = {"toolName": "write_file", "input": {"path": path, "content": content}}
    before = policy.capture_before([call])
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    policy.observe_results([(call, ToolResult(ok=True, output="written"))], before, step)


@pytest.mark.parametrize("fixture_count", [13, 70])
def test_summary_keeps_late_source_and_test_changes(tmp_path: Path, fixture_count):
    policy = TurnCompletionPolicy(50, 35, str(tmp_path))
    for index in range(fixture_count):
        record_write(policy, tmp_path, f"repro/test_{index:03d}.py", "fixture", 1)
    record_write(policy, tmp_path, "minicode/tools/test_runner.py", "fixed", 2)
    record_write(policy, tmp_path, "tests/test_tools.py", "regression", 3)
    summary = policy.summary("cost budget exhausted")
    assert "minicode/tools/test_runner.py" in summary
    assert "tests/test_tools.py" in summary
    assert "repro/test_000.py" not in summary
    assert len(summary) < 2500


def test_revisiting_a_file_refreshes_its_place_in_the_summary(tmp_path: Path):
    policy = TurnCompletionPolicy(50, 35, str(tmp_path))
    record_write(policy, tmp_path, "source.py", "first change", 1)
    for index in range(13):
        record_write(policy, tmp_path, f"repro/test_{index:03d}.py", "fixture", 2)
    record_write(policy, tmp_path, "source.py", "corrected change", 3)
    summary = policy.summary("cost budget exhausted")
    assert "source.py" in summary
    assert "repro/test_000.py" not in summary


def test_large_batch_keeps_late_changes_in_call_order(tmp_path: Path):
    policy = TurnCompletionPolicy(50, 35, str(tmp_path))
    paths = [f"repro/test_{index:03d}.py" for index in range(70)]
    paths += ["app/login.py", "app/test_login.py"]
    calls = [
        {"toolName": "write_file", "input": {"path": path, "content": "changed"}}
        for path in paths
    ]
    before = policy.capture_before(calls)
    for path in paths:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("changed")
    policy.observe_results(
        [(call, ToolResult(ok=True, output="written")) for call in calls], before, 1
    )
    summary = policy.summary("cost budget exhausted")
    assert "app/login.py" in summary
    assert "app/test_login.py" in summary
    assert "repro/test_000.py" not in summary
