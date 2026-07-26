"""Tests for agent reflection system."""

from __future__ import annotations

import sys

sys.path.insert(0, r"d:\Desktop\minicode\py-src")

from minicode.agent_reflection import ReflectionEngine, ReflectionResult


def test_reflection_success():
    """Test reflection on successful execution."""
    engine = ReflectionEngine()

    trace = [
        {"type": "assistant", "content": "I will analyze the codebase structure."},
        {"type": "tool_call", "tool_name": "list_files"},
        {"type": "tool_call", "tool_name": "read_file"},
        {"type": "assistant", "content": "Analysis complete. The project uses a modular architecture."},
    ]

    reflection = engine.reflect("Analyze project structure", trace)

    assert reflection.success is True
    assert reflection.confidence > 0.5
    assert len(reflection.key_decisions) > 0
    assert len(reflection.lessons_learned) > 0
    print("   Success reflection test passed")


def test_reflection_with_errors():
    """Test reflection on failed execution."""
    engine = ReflectionEngine()

    trace = [
        {"type": "assistant", "content": "I will run the tests."},
        {"type": "tool_call", "tool_name": "run_command"},
        {"type": "error", "content": "Command failed: pytest not found", "tool_name": "run_command"},
        {"type": "error", "content": "Alternative approach also failed", "tool_name": "run_command"},
    ]

    reflection = engine.reflect("Run test suite", trace)

    assert reflection.success is False
    assert len(reflection.errors_encountered) == 2
    # suggested_improvements may be empty if conditions not met
    assert isinstance(reflection.suggested_improvements, list)
    print("   Error reflection test passed")


def test_reflection_to_memory():
    """Test reflection memory entry format."""
    reflection = ReflectionResult(
        task_summary="Test task",
        success=True,
        key_decisions=["Used grep to find patterns"],
        errors_encountered=[],
        lessons_learned=["Grep is efficient for pattern matching"],
        suggested_improvements=[],
        confidence=0.9,
    )

    entry = reflection.to_memory_entry()

    assert entry["category"] == "reflection"
    assert "self-reflection" in entry["tags"]
    assert "success" in entry["tags"]
    assert entry["metadata"]["confidence"] == 0.9
    print("   Memory entry test passed")


def test_reflection_confidence_calculation():
    """Test confidence score calculation."""
    engine = ReflectionEngine()

    # Success with no errors = high confidence
    trace_success = [
        {"type": "assistant", "content": "Done."},
        {"type": "tool_call", "tool_name": "read_file"},
    ]
    r1 = engine.reflect("Task", trace_success)
    assert r1.confidence >= 0.7

    # Failure with errors = lower confidence
    trace_fail = [
        {"type": "error", "content": "Error 1"},
        {"type": "error", "content": "Error 2"},
        {"type": "error", "content": "Error 3"},
    ]
    r2 = engine.reflect("Task", trace_fail)
    assert r2.confidence < 0.5
    print("   Confidence calculation test passed")


def test_reflection_decision_extraction():
    """Test key decision extraction."""
    engine = ReflectionEngine()

    trace = [
        {"type": "assistant", "content": "I will use the grep tool to search for patterns."},
        {"type": "assistant", "content": "I decide to refactor the code into smaller functions."},
        {"type": "assistant", "content": "Plain status update without decision."},
    ]

    reflection = engine.reflect("Refactor code", trace)
    assert len(reflection.key_decisions) >= 2
    print("   Decision extraction test passed")


if __name__ == "__main__":
    test_reflection_success()
    test_reflection_with_errors()
    test_reflection_to_memory()
    test_reflection_confidence_calculation()
    test_reflection_decision_extraction()
    print("\n All reflection tests passed!")
