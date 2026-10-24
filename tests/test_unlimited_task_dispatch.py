"""Task timeouts default to 900s; explicit unlimited and ancestor bounds survive."""

import time

import pytest

from minicode import subagent_types
from minicode.task_deadline import TaskDeadlineExceeded, raise_task_deadline, task_timeout_seconds
from minicode.tooling import ToolContext, ToolResult
from minicode.tools import task
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from minicode.workflow_workspace import WorkflowWorkspaceTransaction


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    monkeypatch.delenv("MINICODE_TASK_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(subagent_types, "_read_max_turns_override", lambda name: None)


@pytest.mark.parametrize("configured,expected", [(None, None), ("0", None), ("401", 401)])
def test_subagent_turn_count_has_no_default_or_hidden_upper_clamp(monkeypatch, configured, expected):
    monkeypatch.setattr(subagent_types, "_read_max_turns_override", lambda name: configured)
    assert subagent_types.resolve_max_turns("general") == expected


@pytest.mark.parametrize("configured", ["-1", "bad", "1.5", "true"])
def test_bad_explicit_turn_limit_is_rejected_not_reinterpreted(monkeypatch, configured):
    monkeypatch.setattr(subagent_types, "_read_max_turns_override", lambda name: configured)
    with pytest.raises(ValueError):
        subagent_types.resolve_max_turns("general")


@pytest.mark.parametrize("runtime,expected", [
    (None, 900), ({}, 900), ({"task": {}}, 900),
    ({"taskTimeoutSeconds": None}, None), ({"taskTimeoutSeconds": 0}, None),
    ({"taskTimeoutSeconds": 100000}, 100000),
    ({"taskTimeoutSeconds": 0, "agentTaskTimeoutSeconds": 8}, None),
    ({"agentTaskTimeoutSeconds": None}, None), ({"agentTaskTimeoutSeconds": 0}, None),
    ({"agentTaskTimeoutSeconds": 8}, 8),
    ({"task": {"timeoutSeconds": 5}}, 5),
    ({"task": {"timeoutSeconds": None}}, None),
    ({"task": {"timeoutSeconds": 0}}, None),
])
def test_timeout_defaults_only_when_no_setting_is_present(runtime, expected):
    assert task_timeout_seconds(ToolContext(cwd=".", _runtime=runtime)) == expected


def test_environment_zero_cancels_runtime_timeout(monkeypatch):
    monkeypatch.setenv("MINICODE_TASK_TIMEOUT_SECONDS", "0")
    assert task_timeout_seconds(ToolContext(cwd=".", _runtime={"taskTimeoutSeconds": 8})) is None


@pytest.mark.parametrize("runtime,expected", [({}, 900), ({"taskTimeoutSeconds": None}, None)])
def test_empty_environment_preserves_runtime_or_default(monkeypatch, runtime, expected):
    monkeypatch.setenv("MINICODE_TASK_TIMEOUT_SECONDS", "")
    assert task_timeout_seconds(ToolContext(cwd=".", _runtime=runtime)) == expected


def test_environment_duration_overrides_explicit_unlimited(monkeypatch):
    monkeypatch.setenv("MINICODE_TASK_TIMEOUT_SECONDS", "12")
    assert task_timeout_seconds(ToolContext(cwd=".", _runtime={"taskTimeoutSeconds": None})) == 12


@pytest.mark.parametrize("value", [True, -1, float("inf"), float("nan"), "bad"])
def test_invalid_timeout_does_not_enable_execution(value):
    with pytest.raises(ValueError):
        task_timeout_seconds(ToolContext(cwd=".", _runtime={"taskTimeoutSeconds": value}))


@pytest.mark.parametrize("own_seconds,parent_seconds", [(None, None), (None, 30), (0, None), (0, 30), (60, 30), (5, None)])
def test_actual_workflow_dispatch_only_keeps_existing_deadlines(tmp_path, monkeypatch, own_seconds, parent_seconds):
    captured = []
    now = time.monotonic()
    parent = None if parent_seconds is None else now + parent_seconds

    def workflow(value, context, **kwargs):
        captured.append(context)
        return ToolResult(ok=True, output="dispatch observed")

    monkeypatch.setattr(task, "run_workflow", workflow)
    result = task._run({"agent_type": "workflow", "description": "observe", "prompt": "Observe dispatch."},
        ToolContext(cwd=str(tmp_path), _runtime={"taskTimeoutSeconds": own_seconds},
                    _task_deadline_monotonic=parent, _task_timeout_seconds=parent_seconds))
    assert result.ok and len(captured) == 1
    actual = captured[0]._task_deadline_monotonic
    if own_seconds in (None, 0):
        assert actual == parent
    else:
        assert now < actual <= time.monotonic() + own_seconds
        if parent is not None:
            assert actual <= parent


@pytest.mark.parametrize("parent_seconds", [None, 30, 1800])
def test_unconfigured_workflow_dispatch_bounds_entire_task(tmp_path, monkeypatch, parent_seconds):
    captured = []
    now = time.monotonic()
    parent = None if parent_seconds is None else now + parent_seconds

    def workflow(value, context, **kwargs):
        captured.append(context)
        return ToolResult(ok=True, output="dispatch observed")

    monkeypatch.setattr(task, "run_workflow", workflow)
    result = task._run(
        {"agent_type": "workflow", "description": "observe", "prompt": "Observe dispatch."},
        ToolContext(cwd=str(tmp_path), _runtime={},
                    _task_deadline_monotonic=parent, _task_timeout_seconds=parent_seconds),
    )
    assert result.ok and len(captured) == 1
    context = captured[0]
    if parent_seconds == 30:
        assert context._task_deadline_monotonic == parent
        assert context._task_timeout_seconds == 30
    else:
        assert now + 900 <= context._task_deadline_monotonic <= time.monotonic() + 900
        assert context._task_timeout_seconds == 900


def test_expired_ancestor_with_unlimited_timeout_still_raises(tmp_path):
    context = ToolContext(cwd=str(tmp_path), _runtime={"taskTimeoutSeconds": None},
                          _task_deadline_monotonic=time.monotonic() - 1,
                          _task_timeout_seconds=None)
    with pytest.raises(TaskDeadlineExceeded, match="deadline exceeded"):
        raise_task_deadline(context)
    with pytest.raises(TaskDeadlineExceeded):
        WorkflowWorkspaceTransaction(str(tmp_path), deadline_monotonic=context._task_deadline_monotonic,
                                     timeout_seconds=None)


def test_cancellation_still_precedes_unlimited_workspace_work(tmp_path):
    token = TurnCancellationToken("turn_" + "e" * 32)
    token.request()
    context = ToolContext(cwd=str(tmp_path), _cancellation_token=token)
    with pytest.raises(TurnCancellationRequested):
        raise_task_deadline(context)
    with pytest.raises(TurnCancellationRequested):
        WorkflowWorkspaceTransaction(str(tmp_path), timeout_seconds=None,
                                     operation_checkpoint=token.raise_if_requested)
