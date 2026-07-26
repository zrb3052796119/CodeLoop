"""Tests for intelligent agent router."""

from __future__ import annotations

import sys

sys.path.insert(0, r"d:\Desktop\minicode\py-src")

from minicode.agent_router import (
    AgentRouter,
    TaskComplexity,
    extract_task_profile,
    get_agent_router,
    reset_agent_router,
)


def test_simple_task_classification():
    """Test that simple tasks are classified correctly."""
    profile = extract_task_profile("List all files in the project")
    assert profile.complexity == TaskComplexity.SIMPLE
    assert not profile.requires_coding
    print("   Simple task classification: PASS")


def test_moderate_task_classification():
    """Test that moderate tasks are classified correctly."""
    profile = extract_task_profile("Create a function to sort a list")
    assert profile.complexity in (TaskComplexity.MODERATE, TaskComplexity.SIMPLE)
    assert profile.requires_coding
    print("   Moderate task classification: PASS")


def test_complex_task_classification():
    """Test that complex tasks are classified correctly."""
    profile = extract_task_profile(
        "Design and build a python distributed task scheduling system "
        "with multiple agents working in parallel across different expertise domains"
    )
    assert profile.complexity in (TaskComplexity.COMPLEX, TaskComplexity.CRITICAL)
    assert profile.requires_coding
    assert profile.requires_reasoning
    print("   Complex task classification: PASS")


def test_critical_task_classification():
    """Test that critical tasks are classified correctly."""
    profile = extract_task_profile("Fix security vulnerability in production immediately")
    assert profile.complexity == TaskComplexity.CRITICAL
    print("   Critical task classification: PASS")


def test_routing_simple_task():
    """Test routing a simple task."""
    reset_agent_router()
    router = get_agent_router()
    router.force_model = None  # Ensure routing is enabled
    
    decision = router.route_task("List files in the project")
    
    assert decision.selected_model is not None
    assert decision.tier_name is not None
    assert decision.profile.complexity == TaskComplexity.SIMPLE
    print(f"   Routing simple task -> {decision.selected_model} ({decision.tier_name}): PASS")


def test_routing_complex_task():
    """Test routing a complex task."""
    reset_agent_router()
    router = get_agent_router()
    router.force_model = None
    
    decision = router.route_task(
        "Design and build a python distributed task scheduling system with multiple agents"
    )
    
    assert decision.selected_model is not None
    assert decision.profile.complexity in (TaskComplexity.COMPLEX, TaskComplexity.CRITICAL)
    print(f"   Routing complex task -> {decision.selected_model} ({decision.tier_name}): PASS")


def test_force_model():
    """Test forced model selection."""
    reset_agent_router()
    router = get_agent_router()
    router.force_model_selection("test-model")
    
    decision = router.route_task("Simple task")
    
    assert decision.selected_model == "test-model"
    assert decision.tier_name == "forced"
    print("   Force model selection: PASS")


def test_routing_stats():
    """Test routing statistics."""
    reset_agent_router()
    router = get_agent_router()
    router.force_model = None
    
    router.route_task("Simple task 1")
    router.route_task("Simple task 2")
    router.route_task("Complex distributed system design")
    
    stats = router.get_routing_stats()
    
    assert stats["total_decisions"] == 3
    assert stats["avg_estimated_cost"] >= 0
    print(f"   Routing stats: {stats['total_decisions']} decisions, "
          f"avg cost ${stats['avg_estimated_cost']:.4f}: PASS")


def test_budget_tracking():
    """Test budget tracking."""
    reset_agent_router()
    router = AgentRouter(budget_per_hour=0.01)  # Very low budget
    
    decision = router.route_task("List files")
    
    # Should still route, but may fall back to cheapest tier
    assert decision.selected_model is not None
    
    stats = router.get_routing_stats()
    assert stats["total_decisions"] == 1
    print(f"   Budget tracking: PASS")


def test_keyword_extraction():
    """Test keyword extraction from task."""
    profile = extract_task_profile(
        "Create a python function to implement a sorting algorithm"
    )
    
    assert len(profile.keywords) > 0
    assert profile.requires_coding
    assert "create" in profile.keywords or "python" in profile.keywords
    print(f"   Keyword extraction ({len(profile.keywords)} keywords): PASS")


def test_dangerous_task_detection():
    """Test dangerous keyword detection."""
    profile = extract_task_profile("Delete all files in the production server")
    
    assert profile.is_dangerous
    print("   Dangerous task detection: PASS")


def test_creativity_detection():
    """Test creativity keyword detection."""
    profile = extract_task_profile("Brainstorm creative ideas for a unique design concept")
    
    assert profile.requires_creativity
    print("   Creativity detection: PASS")


if __name__ == "__main__":
    test_simple_task_classification()
    test_moderate_task_classification()
    test_complex_task_classification()
    test_critical_task_classification()
    test_routing_simple_task()
    test_routing_complex_task()
    test_force_model()
    test_routing_stats()
    test_budget_tracking()
    test_keyword_extraction()
    test_dangerous_task_detection()
    test_creativity_detection()
    print("\n All agent router tests passed!")
