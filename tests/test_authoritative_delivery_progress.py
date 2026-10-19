"""Delivered authority retires old inspection debt without owning execution."""

from dataclasses import replace

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.coding_progress import CodingProgressTracker
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelAdapter


def _read_batches(tracker, count):
    for _ in range(count):
        tracker.observe(successful_read=True, changed=False, verified=False)


@pytest.mark.parametrize("changed", [False, True])
def test_authoritative_delivery_keeps_all_notice_quotas_and_counts_only_observed_changes(changed):
    tracker = CodingProgressTracker(enabled=True)
    full = AgentTurnBudget(max_total_tokens=500_000, max_model_calls=40).snapshot()
    for _ in range(3):
        _read_batches(tracker, 4)
        assert tracker.before_request(full, 20_000).reason == "inspection_without_delivery"
    _read_batches(tracker, 4)
    assert tracker.before_request(full, 20_000) is None
    assert tracker.before_request(replace(full, used_total_tokens=350_000), 20_000).reason == "implementation_budget_pressure"
    scarce = replace(full, used_total_tokens=435_000)
    assert tracker.before_request(scarce, 20_000).reason == "implementation_budget_pressure"
    quotas = (tracker._notice_count, tracker._routine_notice_count, tracker._pressure_level)

    tracker.authoritative_delivery(changed=changed)

    assert tracker.read_batches == tracker._last_notice_batch == 0
    assert tracker.mutations == int(changed)
    assert (tracker._notice_count, tracker._routine_notice_count, tracker._pressure_level) == quotas
    _read_batches(tracker, 20)
    assert tracker.before_request(scarce, 20_000, remaining_steps=1) is None


@pytest.mark.parametrize("broken_observer", [False, True])
def test_delivered_child_retires_old_reads_after_ack_and_observer_failure_cannot_stop_parent(
    tmp_path, monkeypatch, broken_observer,
):
    target = tmp_path / "app.py"
    target.write_text("value = 0\n")
    receipt = "COMMITTED: app.py; verification passed; exact frozen receipt."
    acknowledged = []
    observations = []

    def observe_delivery(self, *, changed):
        observations.append({
            "acknowledged": list(acknowledged),
            "read_batches": self.read_batches,
            "changed": changed,
            "mutations_before": self.mutations,
        })
        raise RuntimeError("fixture optional steering failure")

    if broken_observer:
        monkeypatch.setattr(CodingProgressTracker, "authoritative_delivery", observe_delivery)

    def commit(_inputs, _context):
        target.write_text("value = 1\n")
        return ToolResult(
            ok=True, output=receipt, _must_deliver=True, _continue_parent=True,
            _delivery_ack=lambda: acknowledged.append(True),
        )

    class Probe(ModelAdapter):
        def __init__(self):
            self.calls = 0
            self.prior_notices = 0
            self.after_commit_notices = None
            self.receipt_seen = False

        def next(self, messages, on_stream_chunk=None):
            self.calls += 1
            notices = [m for m in messages if m.get("_coding_progress")]
            if self.calls <= 5:
                return AgentStep(type="tool_calls", calls=[{
                    "id": f"r{self.calls}", "toolName": "read_file",
                    "input": {"path": "app.py"},
                }])
            if self.calls == 6:
                self.prior_notices = len(notices)
                return AgentStep(type="tool_calls", calls=[{
                    "id": "commit", "toolName": "task", "input": {},
                }])
            self.after_commit_notices = notices
            self.receipt_seen = any(
                m.get("role") == "tool_result" and m.get("content") == receipt
                for m in messages
            )
            return AgentStep(type="assistant", content="The requested change is committed and verified.")

    model = Probe()
    messages = run_agent_turn(
        model=model, tools=ToolRegistry([
            read_file_tool,
            ToolDefinition(
                name="task", description="host commit fixture",
                input_schema={"type": "object"}, validator=lambda data: data, run=commit,
            ),
        ]),
        messages=[{"role": "user", "content": "Fix app.py so value is 1."}],
        cwd=str(tmp_path), enable_work_chain=False, max_steps=9, agent_depth=1,
        agent_budget=AgentTurnBudget(max_total_tokens=500_000, max_model_calls=40),
    )
    assert model.calls == 7 and model.receipt_seen
    assert messages[-1]["content"] == "The requested change is committed and verified."
    assert acknowledged == [True]
    assert target.read_text() == "value = 1\n"
    assert sum(m.get("role") == "tool_result" and m.get("content") == receipt for m in messages) == 1
    if broken_observer:
        assert len(observations) == 1
        assert observations[0]["acknowledged"] == [True]
        assert observations[0]["read_batches"] == 5
        # A committed task is not itself byte-change evidence for this observer.
        assert observations[0]["changed"] is False
    else:
        assert len(model.after_commit_notices) == model.prior_notices
