"""Integration tests for multi-agent orchestration."""

from __future__ import annotations

import sys

sys.path.insert(0, r"d:\Desktop\minicode\py-src")

from minicode.multi_agent.orchestrator import Orchestrator
from minicode.multi_agent.patterns import SequentialPattern, ParallelPattern
from minicode.multi_agent.shared_memory import SharedMemory
from minicode.multi_agent.message_queue import MessageQueue
from minicode.multi_agent.types import AgentRole


class MockAgent:
    """Mock agent for testing."""

    def __init__(self, agent_id, role, shared_memory, message_queue):
        self.agent_id = agent_id
        self.role = role
        self.shared_memory = shared_memory
        self.message_queue = message_queue

    def run(self, task: str) -> str:
        self.shared_memory.write(
            f"result_{self.agent_id}",
            {"task": task, "agent": self.agent_id},
            self.agent_id,
        )
        return f"[{self.agent_id}] Completed: {task[:50]}..."


def test_sequential_pattern():
    """Test sequential execution."""
    pattern = SequentialPattern()
    roles = [
        AgentRole(name="researcher", description="Research task"),
        AgentRole(name="writer", description="Write report"),
    ]

    trace = pattern.execute(
        "Test task", roles,
        lambda agent_id, role, sm, mq: MockAgent(agent_id, role, sm, mq)
    )

    assert len(trace.results) == 2
    assert trace.results[0].status.value == "completed"
    assert trace.results[1].status.value == "completed"
    print("   Sequential pattern test passed")


def test_parallel_pattern():
    """Test parallel execution."""
    pattern = ParallelPattern()
    roles = [
        AgentRole(name="analyzer1", description="Analyze part 1"),
        AgentRole(name="analyzer2", description="Analyze part 2"),
    ]

    trace = pattern.execute(
        "Test task", roles,
        lambda agent_id, role, sm, mq: MockAgent(agent_id, role, sm, mq)
    )

    assert len(trace.results) == 2
    assert all(r.status.value == "completed" for r in trace.results)
    print("   Parallel pattern test passed")


def test_orchestrator_integration():
    """Test full orchestrator with mock factory."""
    orchestrator = Orchestrator()
    orchestrator.set_agent_factory(
        lambda agent_id, role, sm, mq: MockAgent(agent_id, role, sm, mq)
    )

    trace = orchestrator.execute(
        task="Analyze this codebase",
        pattern="sequential",
        max_roles=2,
    )

    assert trace.pattern == "sequential"
    assert len(trace.results) > 0
    print("   Orchestrator integration test passed")


def test_shared_memory():
    """Test shared memory operations."""
    mem = SharedMemory()

    mem.write("key1", "value1", "agent1")
    assert mem.read("key1") == "value1"

    mem.write("key2", {"data": 123}, "agent2")
    assert mem.read("key2") == {"data": 123}

    mem.delete("key1", "agent1")
    assert mem.read("key1") is None
    print("   Shared memory test passed")


def test_multi_agent_shortcut():
    """Test /multi command shortcut parsing."""
    from minicode.local_tool_shortcuts import parse_local_tool_shortcut

    result = parse_local_tool_shortcut("/multi sequential analyze project")
    assert result is not None
    assert result["toolName"] == "multi_agent_orchestrate"
    assert result["input"]["pattern"] == "sequential"
    assert result["input"]["task"] == "analyze project"

    result2 = parse_local_tool_shortcut("/multi parallel task1 task2 task3")
    assert result2 is not None
    assert result2["input"]["pattern"] == "parallel"
    assert result2["input"]["task"] == "task1 task2 task3"
    print("   Multi-agent shortcut test passed")


if __name__ == "__main__":
    test_sequential_pattern()
    test_parallel_pattern()
    test_orchestrator_integration()
    test_shared_memory()
    test_multi_agent_shortcut()
    print("\n All multi-agent integration tests passed!")
