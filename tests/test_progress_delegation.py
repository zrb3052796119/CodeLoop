"""Capability-aware progress advice through the real loop, without providers."""

from dataclasses import replace
import time

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.auto_mode import PermissionMode
from minicode.coding_progress import CodingProgressTracker
from minicode.permissions import PermissionManager
from minicode.tooling import ToolMetadata, ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.tools.task import task_tool
from minicode.types import AgentStep, ModelAdapter


def _delegation_notices(notices):
    return [text for text in notices if "delegat" in text.lower()]


class InspectionModel(ModelAdapter):
    def __init__(self, reads=12, on_request=None):
        self.reads = reads
        self.on_request = on_request
        self.requests = []

    def next(self, messages, on_stream_chunk=None):
        self.requests.append([
            message["content"] for message in messages if message.get("_coding_progress")
        ])
        number = len(self.requests)
        if self.on_request is not None:
            self.on_request(number)
        if number <= self.reads:
            return AgentStep(type="tool_calls", calls=[{
                "id": f"read-{number}", "toolName": "read_file",
                "input": {"path": "app.py"},
            }])
        return AgentStep(type="assistant", content="Investigation ended; no changes or verification completed.")


def _run_inspection(tmp_path, *, task=True, model=None, prompt="Fix the bug in app.py.",
                    budget=None, permissions=None, agent_depth=0, **kwargs):
    (tmp_path / "app.py").write_text("value = 0\n")
    definitions = [read_file_tool, edit_file_tool]
    if task is not False:
        definitions.append(task_tool if task is True else task)
    model = model or InspectionModel()
    run_agent_turn(
        model=model, tools=ToolRegistry(definitions),
        messages=[{"role": "user", "content": prompt}], cwd=str(tmp_path),
        enable_work_chain=False, max_steps=40, agent_depth=agent_depth,
        permissions=permissions,
        agent_budget=budget or AgentTurnBudget(max_total_tokens=1_000_000, max_model_calls=100),
        **kwargs,
    )
    assert len(model.requests) == model.reads + 1
    assert (tmp_path / "app.py").read_text() == "value = 0\n"
    return model.requests


@pytest.mark.parametrize("agent_depth", [0, 1])
def test_actual_implementation_loop_without_task_never_suggests_delegation(tmp_path, agent_depth):
    requests = _run_inspection(tmp_path, task=False, agent_depth=agent_depth)
    assert len(requests[-1]) == 3  # Ordinary implementation feedback remains.
    assert not _delegation_notices(requests[-1])


def test_actual_loop_suggests_once_after_eight_batches_even_for_one_file(tmp_path):
    requests = _run_inspection(tmp_path, runtime={"subagentRoutingEnabled": False})
    assert len(requests[4]) == 1
    assert not _delegation_notices(requests[4])
    suggestions = _delegation_notices(requests[8])
    assert len(suggestions) == 1
    assert "explore" in suggestions[0] and "workflow" in suggestions[0]
    assert "if" in suggestions[0].lower() and "otherwise" in suggestions[0].lower()
    assert "should delegate" not in suggestions[0]
    assert _delegation_notices(requests[-1]) == suggestions
    assert len(requests[-1]) == 3


@pytest.mark.parametrize("mode", ["readonly-request", "plan", "disabled-task", "nested"])
def test_actual_loop_does_not_suggest_unavailable_delegation(tmp_path, monkeypatch, mode):
    options = {}
    if mode == "readonly-request":
        options["prompt"] = "Review app.py; do not modify it."
    elif mode == "plan":
        permissions = PermissionManager(str(tmp_path))
        monkeypatch.setattr(permissions.auto_checker, "mode", PermissionMode.PLAN)
        options["permissions"] = permissions
    elif mode == "disabled-task":
        options["task"] = replace(task_tool, metadata=ToolMetadata(
            name="task", description=task_tool.description, is_enabled=False,
        ))
    elif mode == "nested":
        options["agent_depth"] = 2
    requests = _run_inspection(tmp_path, **options)
    assert not _delegation_notices(requests[-1])
    if mode in {"readonly-request", "plan"}:
        assert not requests[-1]


def test_actual_loop_rechecks_task_enabled_state_at_each_request(tmp_path):
    metadata = ToolMetadata(name="task", description=task_tool.description)

    def change_enabled(number):
        if number == 7:
            metadata.is_enabled = False
        elif number == 11:
            metadata.is_enabled = True

    model = InspectionModel(on_request=change_enabled)
    requests = _run_inspection(tmp_path, model=model, task=replace(task_tool, metadata=metadata))
    assert not _delegation_notices(requests[8])
    assert len(_delegation_notices(requests[12])) == 1


def test_actual_loop_preserves_call_pressure_feedback_without_delegation(tmp_path):
    requests = _run_inspection(tmp_path,
        budget=AgentTurnBudget(max_total_tokens=1_000_000, max_model_calls=14))
    assert not _delegation_notices(requests[-1])
    assert any("nearly exhausted" in notice for notice in requests[-1])


def test_actual_loop_preserves_deadline_feedback_without_delegation(tmp_path):
    requests = _run_inspection(tmp_path, deadline_monotonic=time.monotonic() + 170)
    assert not _delegation_notices(requests[-1])
    assert sum("[Coding deadline checkpoint]" in notice for notice in requests[-1]) == 1
    assert len(requests[-1]) == 4  # One deadline and the unchanged three routine notices.


def test_actual_bounded_loop_keeps_progress_without_delegation(tmp_path):
    requests = _run_inspection(tmp_path, bounded_implementation_phases=True)
    assert len(requests[-1]) == 3
    assert not _delegation_notices(requests[-1])


def _read(tracker, count):
    for _ in range(count):
        tracker.observe(successful_read=True, changed=False, verified=False)


@pytest.mark.parametrize("reset", ["delivery", "edit", "verified"])
def test_progress_reset_cannot_replenish_delegation_suggestion(reset):
    tracker = CodingProgressTracker(enabled=True)
    _read(tracker, 8)
    first = tracker.before_request(None, 100, can_delegate=True)
    assert _delegation_notices([first.text])
    if reset == "delivery":
        tracker.authoritative_delivery(changed=False)
    else:
        tracker.observe(successful_read=False, changed=reset == "edit", verified=reset == "verified")
    _read(tracker, 8)
    second = tracker.before_request(None, 100, can_delegate=True)
    assert second is not None and not _delegation_notices([second.text])
    assert tracker._routine_notice_count == 2


@pytest.mark.parametrize("remaining_steps,remaining_seconds,total_tokens", [
    (6, None, None), (None, 180, None), (None, None, 30_000),
])
def test_already_announced_pressure_also_suppresses_later_routine_suggestion(
        remaining_steps, remaining_seconds, total_tokens):
    tracker = CodingProgressTracker(enabled=True)
    budget = AgentTurnBudget(max_total_tokens=total_tokens).snapshot()
    _read(tracker, 8)
    first = tracker.before_request(budget, 1000, can_delegate=True,
        remaining_steps=remaining_steps, remaining_seconds=remaining_seconds)
    _read(tracker, 4)
    later = tracker.before_request(budget, 1000, can_delegate=True,
        remaining_steps=remaining_steps, remaining_seconds=remaining_seconds)
    assert first is not None and later is not None
    assert later.reason == "inspection_without_delivery"
    assert not _delegation_notices([first.text, later.text])


def test_delegation_does_not_consume_budget_or_deadline_reminder_allowances():
    tracker = CodingProgressTracker(enabled=True)
    notices = []
    for _ in range(3):
        _read(tracker, 4)
        notices.append(tracker.before_request(None, 100, remaining_steps=30, can_delegate=True))
    for steps, seconds in [(30, 170), (6, 160), (3, 150), (2, 80)]:
        notices.append(tracker.before_request(None, 100,
            remaining_steps=steps, remaining_seconds=seconds, can_delegate=True))
    assert all(notice is not None for notice in notices)
    assert [notice.reason for notice in notices] == [
        "inspection_without_delivery", "inspection_without_delivery", "inspection_without_delivery",
        "implementation_deadline_pressure", "implementation_budget_pressure",
        "implementation_budget_pressure", "implementation_deadline_pressure",
    ]
    assert len(_delegation_notices([notice.text for notice in notices])) == 1
    assert tracker.before_request(None, 100, remaining_steps=1,
        remaining_seconds=1, can_delegate=True) is None
