"""Tests for Multi-Agent Orchestration system.

Tests the core multi-agent components without requiring LLM calls
by using mock agent factories.
"""

from __future__ import annotations

import sys
sys.path.insert(0, r"d:\Desktop\minicode\py-src")

from minicode.multi_agent.types import (
    AgentRole,
    AgentMessage,
    AgentResult,
    AgentStatus,
    MessageType,
)
from minicode.multi_agent.shared_memory import SharedMemory
from minicode.multi_agent.message_queue import MessageQueue
from minicode.multi_agent.role_analyzer import RoleAnalyzer
from minicode.multi_agent.adaptive_workflow import AdaptiveWorkflow
from minicode.multi_agent.patterns import (
    SequentialPattern,
    ParallelPattern,
    HierarchicalPattern,
    ConsensusPattern,
    ToolMediatedPattern,
)
from minicode.multi_agent.orchestrator import Orchestrator


# ---------------------------------------------------------------------------
# Mock agent factory for testing
# ---------------------------------------------------------------------------

class MockAgent:
    """Mock agent for testing."""
    def __init__(self, agent_id: str, role: AgentRole, shared_memory, message_queue):
        self.agent_id = agent_id
        self.role = role
        self.shared_memory = shared_memory
        self.message_queue = message_queue

    def run(self, task: str) -> str:
        import time
        time.sleep(0.001)  # Small delay to ensure duration_ms > 0
        return f"[{self.agent_id}] Completed: {task[:50]}..."


def mock_agent_factory(agent_id: str, role: AgentRole, shared_memory, message_queue):
    return MockAgent(agent_id, role, shared_memory, message_queue)


# ---------------------------------------------------------------------------
# SharedMemory tests
# ---------------------------------------------------------------------------

def test_shared_memory_basic():
    """Test basic shared memory operations."""
    mem = SharedMemory()
    
    mem.write("key1", "value1", "agent1")
    assert mem.read("key1") == "value1"
    
    mem.write("key2", {"data": 123}, "agent2")
    assert mem.read("key2") == {"data": 123}


def test_shared_memory_delete():
    """Test shared memory delete."""
    mem = SharedMemory()
    mem.write("key", "value", "agent1")
    
    assert mem.delete("key", "agent1")
    assert mem.read("key") is None
    assert not mem.delete("key", "agent1")


def test_shared_memory_history():
    """Test shared memory history tracking."""
    mem = SharedMemory()
    mem.write("key1", "v1", "agent1")
    mem.write("key2", "v2", "agent2")
    mem.read("key1")
    
    history = mem.get_history(limit=10)
    assert len(history) == 2
    assert history[0].agent_id == "agent1"
    assert history[1].agent_id == "agent2"


def test_shared_memory_subscribe():
    """Test shared memory subscription."""
    mem = SharedMemory()
    events = []
    
    def callback(key, value, agent_id):
        events.append((key, value, agent_id))
    
    mem.subscribe("key1", callback)
    mem.write("key1", "value1", "agent1")
    
    assert len(events) == 1
    assert events[0] == ("key1", "value1", "agent1")


# ---------------------------------------------------------------------------
# MessageQueue tests
# ---------------------------------------------------------------------------

def test_message_queue_basic():
    """Test basic message queue operations."""
    mq = MessageQueue()
    mq.register_agent("agent1")
    mq.register_agent("agent2")
    
    msg = AgentMessage(
        msg_type=MessageType.TASK,
        from_agent="agent1",
        to_agent="agent2",
        content="Hello",
    )
    
    assert mq.send("agent2", msg)
    received = mq.receive("agent2", timeout=0.1)
    
    assert received is not None
    assert received.content == "Hello"
    assert received.from_agent == "agent1"


def test_message_queue_broadcast():
    """Test message broadcast."""
    mq = MessageQueue()
    mq.register_agent("agent1")
    mq.register_agent("agent2")
    mq.register_agent("agent3")
    
    msg = AgentMessage(
        msg_type=MessageType.BROADCAST,
        from_agent="agent1",
        to_agent="all",
        content="Broadcast message",
    )
    
    count = mq.broadcast(msg)
    assert count == 2  # agent2 and agent3, not agent1
    
    received2 = mq.receive("agent2", timeout=0.1)
    received3 = mq.receive("agent3", timeout=0.1)
    
    assert received2 is not None
    assert received3 is not None


def test_message_queue_unregister():
    """Test agent unregistration."""
    mq = MessageQueue()
    mq.register_agent("agent1")
    mq.unregister_agent("agent1")
    
    msg = AgentMessage(
        msg_type=MessageType.TASK,
        from_agent="agent2",
        to_agent="agent1",
        content="Hello",
    )
    
    assert not mq.send("agent1", msg)


# ---------------------------------------------------------------------------
# RoleAnalyzer tests
# ---------------------------------------------------------------------------

def test_role_analyzer_research():
    """Test role analysis for research task."""
    analyzer = RoleAnalyzer()
    roles = analyzer.analyze("Research the latest AI developments", max_roles=2)
    
    assert len(roles) >= 1
    assert any("research" in r.name.lower() for r in roles)


def test_role_analyzer_code():
    """Test role analysis for coding task."""
    analyzer = RoleAnalyzer()
    roles = analyzer.analyze("Implement a new feature in Python", max_roles=2)
    
    assert len(roles) >= 1
    assert any("code" in r.name.lower() for r in roles)


def test_role_analyzer_test():
    """Test role analysis for testing task."""
    analyzer = RoleAnalyzer()
    roles = analyzer.analyze("Write tests for the authentication module", max_roles=2)
    
    assert len(roles) >= 1
    assert any("test" in r.name.lower() for r in roles)


def test_role_analyzer_default():
    """Test role analysis with no matching keywords."""
    analyzer = RoleAnalyzer()
    roles = analyzer.analyze("xyz abc 123", max_roles=2)
    
    # Should return default roles (research + code)
    assert len(roles) == 2


def test_role_analyzer_max_roles():
    """Test role analysis respects max_roles."""
    analyzer = RoleAnalyzer()
    roles = analyzer.analyze(
        "Research, code, test, review, and deploy a new feature",
        max_roles=3,
    )
    
    assert len(roles) <= 3


# ---------------------------------------------------------------------------
# Orchestration Pattern tests
# ---------------------------------------------------------------------------

def test_sequential_pattern():
    """Test sequential execution pattern."""
    pattern = SequentialPattern()
    roles = [
        AgentRole(name="ResearchAgent", description="Research"),
        AgentRole(name="CodeAgent", description="Code"),
    ]
    
    trace = pattern.execute("Test task", roles, mock_agent_factory)
    
    assert trace.pattern == "sequential"
    assert len(trace.results) == 2
    assert all(r.success for r in trace.results)
    assert trace.duration_ms > 0


def test_parallel_pattern():
    """Test parallel execution pattern."""
    pattern = ParallelPattern()
    roles = [
        AgentRole(name="Agent1", description="Agent 1"),
        AgentRole(name="Agent2", description="Agent 2"),
        AgentRole(name="Agent3", description="Agent 3"),
    ]
    
    trace = pattern.execute("Test task", roles, mock_agent_factory)
    
    assert trace.pattern == "parallel"
    assert len(trace.results) == 3
    assert all(r.success for r in trace.results)


def test_hierarchical_pattern():
    """Test hierarchical execution pattern."""
    pattern = HierarchicalPattern()
    roles = [
        AgentRole(name="ManagerAgent", description="Manager"),
        AgentRole(name="WorkerAgent1", description="Worker 1"),
        AgentRole(name="WorkerAgent2", description="Worker 2"),
    ]
    
    trace = pattern.execute("Test task", roles, mock_agent_factory)
    
    assert trace.pattern == "hierarchical"
    assert len(trace.results) >= 3  # Manager + 2 workers + review


def test_consensus_pattern():
    """Test consensus execution pattern."""
    pattern = ConsensusPattern()
    roles = [
        AgentRole(name="Agent1", description="Agent 1"),
        AgentRole(name="Agent2", description="Agent 2"),
    ]
    
    trace = pattern.execute("Test task", roles, mock_agent_factory, max_rounds=2)
    
    assert trace.pattern == "consensus"
    assert len(trace.results) >= 2


def test_tool_mediated_pattern():
    """Test tool-mediated execution pattern."""
    pattern = ToolMediatedPattern()
    roles = [
        AgentRole(name="Agent1", description="Agent 1"),
        AgentRole(name="Agent2", description="Agent 2"),
    ]
    
    trace = pattern.execute("Test task", roles, mock_agent_factory)
    
    assert trace.pattern == "tool_mediated"
    assert len(trace.results) == 2
    
    # Check shared memory was used
    final_results = pattern.shared_memory.read("results")
    assert final_results is not None


# ---------------------------------------------------------------------------
# AdaptiveWorkflow tests
# ---------------------------------------------------------------------------

def test_adaptive_workflow_no_adjustment():
    """Test adaptive workflow with no issues."""
    from minicode.multi_agent.types import ExecutionTrace
    
    adaptive = AdaptiveWorkflow()
    trace = ExecutionTrace(task="test", pattern="sequential")
    
    adjustment = adaptive.monitor(trace)
    assert adjustment is None


def test_adaptive_workflow_slow_agents():
    """Test adaptive workflow detects slow agents."""
    from minicode.multi_agent.types import ExecutionTrace, AgentResult
    
    adaptive = AdaptiveWorkflow(slow_threshold_ms=100)
    trace = ExecutionTrace(task="test", pattern="sequential")
    trace.results.append(AgentResult(
        agent_id="agent1",
        role="test",
        status=AgentStatus.COMPLETED,
        output="",
        duration_ms=200,  # Above threshold
    ))
    
    adjustment = adaptive.monitor(trace)
    assert adjustment is not None
    assert "slow" in adjustment.reason.lower()
    assert adjustment.action == "add_specialist"


def test_adaptive_workflow_high_error_rate():
    """Test adaptive workflow detects high error rate."""
    from minicode.multi_agent.types import ExecutionTrace, AgentResult
    
    adaptive = AdaptiveWorkflow(error_threshold=0.3)
    trace = ExecutionTrace(task="test", pattern="sequential")
    trace.results.append(AgentResult(
        agent_id="agent1",
        role="test",
        status=AgentStatus.FAILED,
        output="",
        error="Error",
    ))
    trace.results.append(AgentResult(
        agent_id="agent2",
        role="test",
        status=AgentStatus.COMPLETED,
        output="",
    ))
    
    adjustment = adaptive.monitor(trace)
    assert adjustment is not None
    assert "error" in adjustment.reason.lower()


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------

def test_orchestrator_sequential():
    """Test orchestrator with sequential pattern."""
    orch = Orchestrator()
    orch.set_agent_factory(mock_agent_factory)
    
    trace = orch.execute("Test task", pattern="sequential", roles=[
        AgentRole(name="Agent1", description="Agent 1"),
    ])
    
    assert trace.pattern == "sequential"
    assert len(trace.results) == 1
    assert trace.results[0].success


def test_orchestrator_parallel():
    """Test orchestrator with parallel pattern."""
    orch = Orchestrator()
    orch.set_agent_factory(mock_agent_factory)
    
    trace = orch.execute("Test task", pattern="parallel", roles=[
        AgentRole(name="Agent1", description="Agent 1"),
        AgentRole(name="Agent2", description="Agent 2"),
    ])
    
    assert trace.pattern == "parallel"
    assert len(trace.results) == 2


def test_orchestrator_auto_roles():
    """Test orchestrator with auto-generated roles."""
    orch = Orchestrator()
    orch.set_agent_factory(mock_agent_factory)
    
    trace = orch.execute("Write code and tests", pattern="sequential")
    
    assert len(trace.roles) >= 1
    assert len(trace.results) >= 1


def test_orchestrator_get_final_output():
    """Test getting final output from trace."""
    orch = Orchestrator()
    orch.set_agent_factory(mock_agent_factory)
    
    trace = orch.execute("Test task", pattern="sequential", roles=[
        AgentRole(name="Agent1", description="Agent 1"),
    ])
    
    output = orch.get_final_output(trace)
    assert "Agent1" in output
    assert "Completed" in output


def test_orchestrator_pattern_names():
    """Test getting available pattern names."""
    orch = Orchestrator()
    names = orch.get_pattern_names()
    
    assert "sequential" in names
    assert "parallel" in names
    assert "hierarchical" in names
    assert "consensus" in names
    assert "tool_mediated" in names


def test_orchestrator_invalid_pattern():
    """Test orchestrator with invalid pattern."""
    orch = Orchestrator()
    orch.set_agent_factory(mock_agent_factory)
    
    try:
        orch.execute("Test task", pattern="invalid")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Unknown pattern" in str(e)


def test_orchestrator_no_factory():
    """Test orchestrator without agent factory."""
    orch = Orchestrator()
    
    try:
        orch.execute("Test task")
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "Agent factory not set" in str(e)


if __name__ == "__main__":
    import traceback
    
    tests = [
        # SharedMemory
        test_shared_memory_basic,
        test_shared_memory_delete,
        test_shared_memory_history,
        test_shared_memory_subscribe,
        # MessageQueue
        test_message_queue_basic,
        test_message_queue_broadcast,
        test_message_queue_unregister,
        # RoleAnalyzer
        test_role_analyzer_research,
        test_role_analyzer_code,
        test_role_analyzer_test,
        test_role_analyzer_default,
        test_role_analyzer_max_roles,
        # Patterns
        test_sequential_pattern,
        test_parallel_pattern,
        test_hierarchical_pattern,
        test_consensus_pattern,
        test_tool_mediated_pattern,
        # AdaptiveWorkflow
        test_adaptive_workflow_no_adjustment,
        test_adaptive_workflow_slow_agents,
        test_adaptive_workflow_high_error_rate,
        # Orchestrator
        test_orchestrator_sequential,
        test_orchestrator_parallel,
        test_orchestrator_auto_roles,
        test_orchestrator_get_final_output,
        test_orchestrator_pattern_names,
        test_orchestrator_invalid_pattern,
        test_orchestrator_no_factory,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {test.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
