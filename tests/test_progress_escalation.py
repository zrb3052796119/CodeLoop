from dataclasses import replace
from minicode.agent_budget import AgentTurnBudget
from minicode.coding_progress import CodingProgressTracker


def reading(tracker, count=4):
    for _ in range(count):
        tracker.observe(successful_read=True, changed=False, verified=False)


def test_three_routine_notices_do_not_consume_late_budget_escalations():
    tracker = CodingProgressTracker(enabled=True)
    base = AgentTurnBudget(max_total_tokens=500_000, max_model_calls=40).snapshot()
    for _ in range(3):
        reading(tracker)
        assert tracker.before_request(base, 20_000).reason == 'inspection_without_delivery'
    reading(tracker)
    assert tracker.before_request(base, 20_000) is None
    notice = tracker.before_request(replace(base, used_total_tokens=350_000), 20_000)
    assert notice.reason == 'implementation_budget_pressure'
    assert notice.estimated_requests_left == 6
    assert tracker.before_request(replace(base, used_total_tokens=350_000), 20_000) is None
    notice = tracker.before_request(replace(base, used_total_tokens=435_000), 20_000)
    assert notice.reason == 'implementation_budget_pressure'
    assert notice.estimated_requests_left == 2
    assert 'precise blocker' in notice.text
    assert tracker.before_request(replace(base, used_total_tokens=480_000), 20_000) is None
    assert tracker._notice_count == 5


def test_half_budget_remaining_does_not_claim_window_is_nearly_exhausted():
    tracker = CodingProgressTracker(enabled=True)
    base = AgentTurnBudget(max_total_tokens=500_000).snapshot()
    reading(tracker, 3)
    assert tracker.before_request(replace(base, used_total_tokens=250_000), 20_000) is None


def test_child_local_step_limit_triggers_with_large_shared_budget():
    tracker = CodingProgressTracker(enabled=True)
    base = AgentTurnBudget(max_total_tokens=500_000, max_model_calls=40).snapshot()
    reading(tracker, 3)
    notice = tracker.before_request(base, 3_000, remaining_steps=6)
    assert notice.reason == 'implementation_budget_pressure'
    assert notice.estimated_requests_left == 6
    assert tracker.before_request(base, 3_000, remaining_steps=5) is None
    assert tracker.before_request(base, 3_000, remaining_steps=3).estimated_requests_left == 3


def test_mutations_do_not_reset_global_escalation_quota():
    tracker = CodingProgressTracker(enabled=True)
    reading(tracker, 3)
    assert tracker.before_request(None, 100, remaining_steps=3) is not None
    tracker.observe(successful_read=False, changed=True, verified=False)
    reading(tracker, 3)
    assert tracker.before_request(None, 100, remaining_steps=2) is None


def test_read_only_controller_never_escalates():
    tracker = CodingProgressTracker(enabled=False)
    reading(tracker, 20)
    assert tracker.before_request(None, 100, remaining_steps=1) is None
