"""Tests for intelligent agent routing system."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

from minicode.agent_router import AgentRouter, RoutingDecision, TaskComplexity, extract_task_profile
from minicode.model_registry import BUILTIN_MODELS, Provider
from minicode.model_switcher import ModelSwitcher, SwitchResult, detect_provider_name
from minicode.smart_router import FeedbackLearner, SmartRouter, TaskOutcome, get_smart_router, reset_smart_router


class TestTaskClassification:

    def test_simple_task(self):
        profile = extract_task_profile("list all files in the directory")
        assert profile.complexity in (TaskComplexity.SIMPLE, TaskComplexity.MODERATE)
        assert profile.requires_coding is False

    def test_coding_task(self):
        profile = extract_task_profile("create a Python function that sorts a list using merge sort")
        assert profile.requires_coding is True
        assert profile.complexity in (TaskComplexity.MODERATE, TaskComplexity.COMPLEX)

    def test_complex_task(self):
        profile = extract_task_profile(
            "design and build a microservice architecture for the platform "
            "with API gateway, authentication, and database integration"
        )
        assert profile.complexity in (TaskComplexity.COMPLEX, TaskComplexity.CRITICAL)
        assert profile.requires_coding is True

    def test_critical_task(self):
        profile = extract_task_profile(
            "URGENT: security breach detected in production, need immediate fix"
        )
        assert profile.complexity == TaskComplexity.CRITICAL
        assert profile.deadline_urgent is True
        assert profile.is_dangerous is True

    def test_dangerous_task(self):
        profile = extract_task_profile("delete all files in the production database")
        assert profile.is_dangerous is True

    def test_creative_task(self):
        profile = extract_task_profile("design a unique and innovative landing page")
        assert profile.requires_creativity is True


class TestAgentRouter:

    def test_simple_task_routes_to_cheap_model(self):
        router = AgentRouter()
        decision = router.route_task("list files")
        assert decision.selected_model in ("claude-haiku-3-20240307", "gpt-4o-mini")

    def test_complex_task_routes_to_powerful_model(self):
        router = AgentRouter()
        decision = router.route_task(
            "architect a distributed system with fault tolerance"
        )
        assert decision.selected_model in (
            "claude-opus-4-20250514",
            "claude-sonnet-4-20250514",
        )

    def test_forced_model_overrides_routing(self):
        router = AgentRouter(force_model="gpt-4o")
        decision = router.route_task("list files")
        assert decision.selected_model == "gpt-4o"
        assert decision.tier_name == "forced"

    def test_unforce_model(self):
        router = AgentRouter(force_model="gpt-4o")
        router.force_model_selection(None)
        decision = router.route_task("list files")
        assert decision.tier_name != "forced"

    def test_routing_stats(self):
        router = AgentRouter()
        router.route_task("list files")
        router.route_task("build a complex system")
        stats = router.get_routing_stats()
        assert stats["total_decisions"] == 2
        assert "complexity_distribution" in stats
        assert "model_distribution" in stats


class TestModelSwitcher:

    def _make_tools(self):
        return MagicMock()

    def test_switch_to_known_model(self):
        tools = self._make_tools()
        switcher = ModelSwitcher(
            current_model="claude-sonnet-4-20250514",
            current_runtime={"model": "claude-sonnet-4-20250514"},
            current_tools=tools,
        )
        result = switcher.switch_to("claude-haiku-3-20240307", reason="test")
        assert result.success is True
        assert result.old_model == "claude-sonnet-4-20250514"
        assert result.new_model == "claude-haiku-3-20240307"
        assert switcher.current_model == "claude-haiku-3-20240307"

    def test_switch_to_unknown_model_fails(self):
        tools = self._make_tools()
        switcher = ModelSwitcher(
            current_model="claude-sonnet-4-20250514",
            current_runtime={},
            current_tools=tools,
            available_models={"claude-sonnet-4-20250514": MagicMock()},
        )
        result = switcher.switch_to("unknown-model-x", reason="test")
        assert result.success is False
        assert len(result.errors) > 0

    def test_switch_history(self):
        tools = self._make_tools()
        switcher = ModelSwitcher(
            current_model="claude-sonnet-4-20250514",
            current_runtime={"model": "claude-sonnet-4-20250514"},
            current_tools=tools,
        )
        switcher.switch_to("claude-haiku-3-20240307", reason="test1")
        switcher.switch_to("gpt-4o-mini", reason="test2")
        history = switcher.get_switch_history()
        assert len(history) == 2
        assert history[0]["reason"] == "test1"
        assert switcher.switch_count == 2

    def test_get_current_adapter(self):
        tools = self._make_tools()
        switcher = ModelSwitcher(
            current_model="claude-sonnet-4-20250514",
            current_runtime={"model": "claude-sonnet-4-20250514"},
            current_tools=tools,
        )
        assert switcher.get_current_adapter() is None
        switcher.switch_to("claude-haiku-3-20240307", reason="test")
        assert switcher.get_current_adapter() is not None


class TestFeedbackLearner:

    def test_record_outcome(self, tmp_path):
        storage = tmp_path / "feedback.json"
        learner = FeedbackLearner(storage_path=storage)
        learner.record_outcome(TaskOutcome(
            task_text="test task",
            assigned_model="claude-sonnet-4-20250514",
            success=True,
            duration_ms=1000.0,
            cost_usd=0.05,
            tool_errors=0,
            model_switches=0,
        ))
        assert storage.exists()
        report = learner.get_performance_report()
        assert report["total_tasks"] == 1
        assert "claude-sonnet-4-20250514" in report["models"]

    def test_model_scoring(self):
        learner = FeedbackLearner()
        for _ in range(5):
            learner.record_outcome(TaskOutcome(
                task_text="task",
                assigned_model="good-model",
                success=True,
                duration_ms=500,
                cost_usd=0.01,
                tool_errors=0,
                model_switches=0,
            ))
        for _ in range(5):
            learner.record_outcome(TaskOutcome(
                task_text="task",
                assigned_model="bad-model",
                success=False,
                duration_ms=5000,
                cost_usd=0.50,
                tool_errors=3,
                model_switches=1,
            ))
        good_score = learner.get_model_score("good-model")
        bad_score = learner.get_model_score("bad-model")
        assert good_score > bad_score

    def test_unknown_model_score(self):
        learner = FeedbackLearner()
        assert learner.get_model_score("unknown") == 0.5

    def test_persistence(self, tmp_path):
        storage = tmp_path / "feedback.json"
        learner1 = FeedbackLearner(storage_path=storage)
        learner1.record_outcome(TaskOutcome(
            task_text="persistent task",
            assigned_model="claude-sonnet-4-20250514",
            success=True,
            duration_ms=100,
            cost_usd=0.01,
            tool_errors=0,
            model_switches=0,
        ))
        learner2 = FeedbackLearner(storage_path=storage)
        report = learner2.get_performance_report()
        assert report["total_tasks"] == 1

    def test_best_model_selection(self):
        learner = FeedbackLearner()
        for model in ["claude-haiku-3-20240307", "claude-sonnet-4-20250514", "claude-opus-4-20250514"]:
            for _ in range(3):
                learner.record_outcome(TaskOutcome(
                    task_text="task",
                    assigned_model=model,
                    success=True,
                    duration_ms=1000,
                    cost_usd=0.05,
                    tool_errors=0,
                    model_switches=0,
                ))
        best = learner.get_best_model_for_task_type(
            "create a python function",
            ["claude-haiku-3-20240307", "claude-sonnet-4-20250514"],
        )
        assert best in ["claude-haiku-3-20240307", "claude-sonnet-4-20250514"]


class TestSmartRouter:

    def test_route_and_switch(self, tmp_path):
        tools = MagicMock()
        switcher = ModelSwitcher(
            current_model="claude-sonnet-4-20250514",
            current_runtime={"model": "claude-sonnet-4-20250514"},
            current_tools=tools,
        )
        storage = tmp_path / "feedback.json"
        router = SmartRouter(switcher=switcher, feedback_path=storage)
        decision, switch_result = router.route_and_switch(
            task_text="list files",
            current_model="claude-sonnet-4-20250514",
        )
        assert decision is not None
        assert decision.selected_model in ("claude-haiku-3-20240307", "gpt-4o-mini")

    def test_record_and_report(self, tmp_path):
        tools = MagicMock()
        switcher = ModelSwitcher(
            current_model="claude-sonnet-4-20250514",
            current_runtime={"model": "claude-sonnet-4-20250514"},
            current_tools=tools,
        )
        storage = tmp_path / "feedback.json"
        router = SmartRouter(switcher=switcher, feedback_path=storage)
        router.record_task_outcome(
            task_text="list files",
            success=True,
            cost_usd=0.01,
            tool_errors=0,
        )
        report = router.get_performance_report()
        assert "routing_stats" in report
        assert "model_performance" in report
        assert report["model_performance"]["total_tasks"] == 1

    def test_force_model(self):
        router = SmartRouter()
        router.force_model("gpt-4o")
        decision, _ = router.route_and_switch(
            task_text="anything",
            current_model="gpt-4o",
        )
        assert decision.selected_model == "gpt-4o"

    def test_global_singleton(self, tmp_path):
        reset_smart_router()
        storage = tmp_path / "feedback.json"
        router1 = get_smart_router(feedback_path=storage)
        router2 = get_smart_router()
        assert router1 is router2
        reset_smart_router()
        router3 = get_smart_router()
        assert router3 is not router1


class TestDetectProviderName:

    def test_anthropic(self):
        assert detect_provider_name("claude-sonnet-4-20250514") == "anthropic"

    def test_openai(self):
        assert detect_provider_name("gpt-4o") == "openai"

    def test_openrouter(self):
        assert detect_provider_name("openrouter/auto") == "openrouter"
