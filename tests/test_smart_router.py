from __future__ import annotations

import os

from minicode.smart_router import FeedbackLearner, SmartRouter, TaskOutcome


def _record_outcomes(
    router: SmartRouter,
    *,
    task_text: str,
    model: str,
    success: bool,
    count: int = 3,
) -> None:
    for _ in range(count):
        router.learner.record_outcome(
            TaskOutcome(
                task_text=task_text,
                assigned_model=model,
                success=success,
                duration_ms=100.0,
                cost_usd=0.0,
                tool_errors=0,
                model_switches=0,
            )
        )


def test_recorded_outcome_invalidates_cached_model_score() -> None:
    learner = FeedbackLearner()

    assert learner.get_model_score("model-a") == 0.5

    learner.record_outcome(
        TaskOutcome(
            task_text="review persistent memory",
            assigned_model="model-a",
            success=True,
            duration_ms=100.0,
            cost_usd=0.0,
            tool_errors=1,
            model_switches=0,
        )
    )

    assert learner.get_model_score("model-a") == 1.0


def test_smart_router_uses_three_observations_to_rerank_within_static_tier() -> None:
    router = SmartRouter()
    _record_outcomes(
        router,
        task_text="review update code",
        model="claude-sonnet-4-20250514",
        success=False,
    )
    _record_outcomes(
        router,
        task_text="review update code",
        model="gpt-4o",
        success=True,
    )

    decision, switch = router.route_and_switch(
        "review update code",
        current_model="claude-sonnet-4-20250514",
    )

    assert switch is None
    assert decision.tier_name == "balanced"
    assert decision.selected_model == "gpt-4o"
    assert "learned rerank" in decision.reasoning


def test_smart_router_does_not_rerank_from_only_two_observations() -> None:
    router = SmartRouter()
    _record_outcomes(
        router,
        task_text="review update code",
        model="claude-sonnet-4-20250514",
        success=False,
        count=2,
    )
    _record_outcomes(
        router,
        task_text="review update code",
        model="gpt-4o",
        success=True,
        count=2,
    )

    decision, _ = router.route_and_switch(
        "review update code",
        current_model="claude-sonnet-4-20250514",
    )

    assert decision.selected_model == "claude-sonnet-4-20250514"
    assert "learned rerank" not in decision.reasoning


def test_smart_router_does_not_rerank_when_only_one_candidate_is_observed() -> None:
    router = SmartRouter()
    _record_outcomes(
        router,
        task_text="review update code",
        model="gpt-4o",
        success=True,
    )

    decision, _ = router.route_and_switch(
        "review update code",
        current_model="claude-sonnet-4-20250514",
    )

    assert decision.selected_model == "claude-sonnet-4-20250514"
    assert "learned rerank" not in decision.reasoning


def test_smart_router_does_not_transfer_feedback_across_task_profiles() -> None:
    router = SmartRouter()
    _record_outcomes(
        router,
        task_text="analyze update code",
        model="claude-sonnet-4-20250514",
        success=False,
    )
    _record_outcomes(
        router,
        task_text="analyze update code",
        model="gpt-4o",
        success=True,
    )

    decision, _ = router.route_and_switch(
        "review update code",
        current_model="claude-sonnet-4-20250514",
    )

    assert decision.selected_model == "claude-sonnet-4-20250514"
    assert "learned rerank" not in decision.reasoning


def test_persisted_feedback_influences_a_fresh_router(tmp_path) -> None:
    feedback_path = tmp_path / "router-feedback.json"
    first_router = SmartRouter(feedback_path=feedback_path)
    _record_outcomes(
        first_router,
        task_text="review update code",
        model="claude-sonnet-4-20250514",
        success=False,
    )
    _record_outcomes(
        first_router,
        task_text="review update code",
        model="gpt-4o",
        success=True,
    )
    first_router.learner.flush()
    if os.name != "nt":
        assert feedback_path.stat().st_mode & 0o777 == 0o600

    fresh_router = SmartRouter(feedback_path=feedback_path)
    decision, _ = fresh_router.route_and_switch(
        "review update code",
        current_model="claude-sonnet-4-20250514",
    )

    assert decision.selected_model == "gpt-4o"
    assert "learned rerank" in decision.reasoning


def test_forced_model_is_not_overridden_by_learned_rerank() -> None:
    router = SmartRouter()
    _record_outcomes(
        router,
        task_text="review update code",
        model="claude-sonnet-4-20250514",
        success=False,
    )
    _record_outcomes(
        router,
        task_text="review update code",
        model="gpt-4o",
        success=True,
    )
    router.force_model("gpt-4o-mini")

    decision, _ = router.route_and_switch(
        "review update code",
        current_model="claude-sonnet-4-20250514",
    )

    assert decision.tier_name == "forced"
    assert decision.selected_model == "gpt-4o-mini"
    assert "learned rerank" not in decision.reasoning
