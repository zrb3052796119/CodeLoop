"""Deadline feedback must survive edits and remain separate from read nudges."""

import time

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.coding_progress import CodingProgressTracker
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelAdapter


def test_edit_does_not_hide_deadline_or_replenish_time_notices():
    tracker = CodingProgressTracker(enabled=True)
    tracker.observe(successful_read=False, changed=True, verified=False)
    first = tracker.before_request(None, 100, remaining_steps=30, remaining_seconds=170)
    assert first.reason == "implementation_deadline_pressure"
    assert first.read_batches == 0
    assert first.remaining_seconds == 170
    assert "structured result" in first.text
    assert "separate Verifier" in first.text
    assert "already complete" in first.text
    assert "make the smallest" not in first.text
    assert "should delegate" not in first.text
    assert tracker.before_request(None, 100, remaining_seconds=150) is None
    tracker.authoritative_delivery(changed=True)
    last = tracker.before_request(None, 100, remaining_seconds=80)
    assert last.reason == "implementation_deadline_pressure"
    tracker.observe(successful_read=False, changed=True, verified=True)
    assert tracker.before_request(None, 100, remaining_seconds=20) is None
    assert tracker._notice_count == 2


def test_time_warning_does_not_consume_read_or_call_pressure_quota():
    tracker = CodingProgressTracker(enabled=True)
    for _ in range(3):
        for _ in range(4):
            tracker.observe(successful_read=True, changed=False, verified=False)
        assert tracker.before_request(None, 100, remaining_steps=30) is not None
    assert tracker.before_request(None, 100, remaining_seconds=170) is not None
    assert tracker.before_request(None, 100, remaining_steps=6, remaining_seconds=160).reason == "implementation_budget_pressure"
    assert tracker.before_request(None, 100, remaining_steps=3, remaining_seconds=150).reason == "implementation_budget_pressure"
    assert tracker.before_request(None, 100, remaining_steps=2, remaining_seconds=80).reason == "implementation_deadline_pressure"
    assert tracker._notice_count == 7
    assert tracker.before_request(None, 100, remaining_steps=1, remaining_seconds=1) is None


@pytest.mark.parametrize("seconds", [None, float("nan"), float("inf"), 181])
def test_absent_invalid_or_distant_deadline_does_not_nudge_without_reading(seconds):
    tracker = CodingProgressTracker(enabled=True)
    assert tracker.before_request(None, 100, remaining_seconds=seconds) is None


def test_read_only_turn_never_receives_deadline_implementation_nudge():
    tracker = CodingProgressTracker(enabled=False)
    assert tracker.before_request(None, 100, remaining_seconds=1) is None


class DeadlineMessageModel(ModelAdapter):
    def __init__(self):
        self.seen = []

    def next(self, messages, on_stream_chunk=None):
        self.seen = [dict(message) for message in messages]
        return AgentStep(type="assistant", content="Blocked: required source is unavailable. No changes made.")


def test_real_loop_passes_monotonic_remaining_time_to_model(tmp_path):
    model = DeadlineMessageModel()
    run_agent_turn(
        model=model, tools=ToolRegistry([read_file_tool, edit_file_tool]),
        messages=[{"role": "user", "content": "Fix the requested source defect."}],
        cwd=str(tmp_path), implementation_task=True, enable_work_chain=False,
        max_steps=20, deadline_monotonic=time.monotonic() + 70,
    )
    notices = [m for m in model.seen if m.get("_coding_progress")]
    assert len(notices) == 1
    assert "Remaining execution time:" in notices[0]["content"]
    assert "precise blocker" in notices[0]["content"]
    assert list(tmp_path.iterdir()) == []


def test_real_loop_deadline_after_edit_requests_delivery_not_another_workflow(tmp_path, monkeypatch):
    import minicode.agent_loop as loop

    clock = [time.monotonic()]
    deadline = clock[0] + 200
    monkeypatch.setattr(loop.time, "monotonic", lambda: clock[0])
    (tmp_path / "app.py").write_text("value = 1\n")

    class EditingModel(ModelAdapter):
        def __init__(self):
            self.turn = 0
            self.final_messages = []

        def next(self, messages, on_stream_chunk=None):
            self.turn += 1
            if self.turn == 1:
                return AgentStep(type="tool_calls", calls=[{
                    "id": "read", "toolName": "read_file", "input": {"path": "app.py"},
                }])
            if self.turn == 2:
                clock[0] += 140
                return AgentStep(type="tool_calls", calls=[{
                    "id": "edit", "toolName": "edit_file", "input": {
                        "path": "app.py", "old": "value = 1", "new": "value = 2",
                    },
                }])
            self.final_messages = messages
            return AgentStep(type="assistant", content="Changed app.py; verification remains incomplete.")

    model = EditingModel()
    run_agent_turn(
        model=model, tools=ToolRegistry([read_file_tool, edit_file_tool]),
        messages=[{"role": "user", "content": "Change app.py value from 1 to 2."}],
        cwd=str(tmp_path), implementation_task=True, enable_work_chain=False,
        max_steps=20, deadline_monotonic=deadline,
        enable_implementation_phases=False,  # independently exercise advisory deadline evidence
    )
    assert (tmp_path / "app.py").read_text() == "value = 2\n"
    notices = [m["content"] for m in model.final_messages if m.get("_coding_progress")]
    assert len(notices) == 1
    assert "already complete" in notices[0]
    assert "acknowledged workflow receipt" in notices[0]
    assert "should delegate" not in notices[0]
    assert "without an observed code change" not in notices[0]
