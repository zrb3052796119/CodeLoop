"""Stress tests for multi-agent orchestration, reflection, and reach tools.

Tests system stability under high load and concurrent execution.
"""

from __future__ import annotations

import sys
import time
import concurrent.futures

sys.path.insert(0, r"d:\Desktop\minicode\py-src")

from minicode.multi_agent.orchestrator import Orchestrator
from minicode.multi_agent.patterns import SequentialPattern, ParallelPattern
from minicode.multi_agent.types import AgentRole
from minicode.agent_reflection import ReflectionEngine
from minicode.tools.reach_tools import _get_cached, _set_cached, clear_reach_cache


class FastMockAgent:
    """Fast mock agent for stress testing."""

    def __init__(self, agent_id, role, sm, mq):
        self.agent_id = agent_id
        self.role = role
        self.shared_memory = sm
        self.message_queue = mq

    def run(self, task: str) -> str:
        return f"[{self.agent_id}] Done"


def mock_factory(agent_id, role, sm, mq):
    return FastMockAgent(agent_id, role, sm, mq)


# ---------------------------------------------------------------------------
# Stress Test 1: High-frequency orchestration
# ---------------------------------------------------------------------------

def test_stress_sequential_many_agents():
    """Test sequential pattern with many agents."""
    print("\n=== Stress Test: Sequential with 20 agents ===")
    pattern = SequentialPattern()
    roles = [AgentRole(name=f"Agent{i}", description=f"Task {i}") for i in range(20)]

    start = time.time()
    trace = pattern.execute("Stress test task", roles, mock_factory)
    duration = time.time() - start

    assert len(trace.results) == 20
    assert trace.success_rate == 1.0
    print(f"  Agents: {len(trace.results)} | Duration: {duration*1000:.1f}ms | Rate: 100%")
    print("   PASS")


def test_stress_parallel_many_agents():
    """Test parallel pattern with many agents."""
    print("\n=== Stress Test: Parallel with 20 agents ===")
    pattern = ParallelPattern()
    roles = [AgentRole(name=f"Agent{i}", description=f"Task {i}") for i in range(20)]

    start = time.time()
    trace = pattern.execute("Stress test task", roles, mock_factory)
    duration = time.time() - start

    assert len(trace.results) == 20
    assert trace.success_rate == 1.0
    print(f"  Agents: {len(trace.results)} | Duration: {duration*1000:.1f}ms | Rate: 100%")
    print("   PASS")


# ---------------------------------------------------------------------------
# Stress Test 2: Concurrent orchestration runs
# ---------------------------------------------------------------------------

def test_stress_concurrent_orchestration():
    """Test multiple orchestration runs concurrently."""
    print("\n=== Stress Test: 10 concurrent orchestration runs ===")

    def run_orchestration(run_id: int):
        orch = Orchestrator()
        orch.set_agent_factory(mock_factory)
        trace = orch.execute(
            task=f"Concurrent task {run_id}",
            pattern="parallel",
            max_roles=5,
        )
        return trace

    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(run_orchestration, i) for i in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
    duration = time.time() - start

    total_agents = sum(len(r.results) for r in results)
    total_success = sum(r.success_rate for r in results)
    avg_success = total_success / len(results)

    print(f"  Runs: {len(results)} | Total Agents: {total_agents} | Avg Success: {avg_success:.0%} | Duration: {duration*1000:.1f}ms")
    assert avg_success == 1.0
    print("   PASS")


# ---------------------------------------------------------------------------
# Stress Test 3: Reflection under load
# ---------------------------------------------------------------------------

def test_stress_reflection_many_traces():
    """Test reflection engine with many traces."""
    print("\n=== Stress Test: 100 reflections ===")
    engine = ReflectionEngine()

    traces = []
    for i in range(100):
        if i % 3 == 0:
            # Error trace
            trace = [
                {"type": "assistant", "content": f"Task {i}"},
                {"type": "error", "content": "Error", "tool_name": "cmd"},
            ]
        else:
            # Success trace
            trace = [
                {"type": "assistant", "content": f"Task {i}"},
                {"type": "tool_call", "tool_name": "read_file"},
                {"type": "assistant", "content": f"Done {i}"},
            ]
        traces.append(trace)

    start = time.time()
    reflections = []
    for i, trace in enumerate(traces):
        r = engine.reflect(f"Task {i}", trace)
        reflections.append(r)
    duration = time.time() - start

    success_count = sum(1 for r in reflections if r.success)
    avg_confidence = sum(r.confidence for r in reflections) / len(reflections)

    print(f"  Reflections: {len(reflections)} | Success: {success_count} | Avg Confidence: {avg_confidence:.2f} | Duration: {duration*1000:.1f}ms")
    assert len(reflections) == 100
    print("   PASS")


# ---------------------------------------------------------------------------
# Stress Test 4: Cache under load
# ---------------------------------------------------------------------------

def test_stress_cache_many_entries():
    """Test cache with many entries."""
    print("\n=== Stress Test: 1000 cache operations ===")
    clear_reach_cache()

    start = time.time()

    # Write 500 entries
    for i in range(500):
        _set_cached(f"key_{i}", f"value_{i}" * 100)

    # Read 500 entries
    hits = 0
    for i in range(500):
        if _get_cached(f"key_{i}") is not None:
            hits += 1

    # Read 500 missing entries
    misses = 0
    for i in range(500, 1000):
        if _get_cached(f"key_{i}") is None:
            misses += 1

    duration = time.time() - start

    print(f"  Writes: 500 | Hits: {hits} | Misses: {misses} | Duration: {duration*1000:.1f}ms")
    assert hits == 500
    assert misses == 500
    print("   PASS")


# ---------------------------------------------------------------------------
# Stress Test 5: Mixed workload
# ---------------------------------------------------------------------------

def test_stress_mixed_workload():
    """Test mixed workload: orchestration + reflection + cache."""
    print("\n=== Stress Test: Mixed workload (5 iterations) ===")

    def mixed_task(iteration: int):
        # Orchestration
        orch = Orchestrator()
        orch.set_agent_factory(mock_factory)
        trace = orch.execute(f"Task {iteration}", "sequential", max_roles=3)

        # Reflection
        engine = ReflectionEngine()
        exec_trace = [
            {"type": "assistant", "content": f"Task {iteration}"},
            {"type": "tool_call", "tool_name": "read_file"},
        ]
        reflection = engine.reflect(f"Task {iteration}", exec_trace)

        # Cache
        _set_cached(f"mixed_{iteration}", f"result_{iteration}")
        cached = _get_cached(f"mixed_{iteration}")

        return {
            "agents": len(trace.results),
            "success": trace.success_rate,
            "reflection_confidence": reflection.confidence,
            "cache_hit": cached == f"result_{iteration}",
        }

    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(mixed_task, i) for i in range(5)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
    duration = time.time() - start

    total_agents = sum(r["agents"] for r in results)
    avg_confidence = sum(r["reflection_confidence"] for r in results) / len(results)
    all_cache_hits = all(r["cache_hit"] for r in results)

    print(f"  Iterations: {len(results)} | Total Agents: {total_agents} | Avg Confidence: {avg_confidence:.2f} | Cache Hits: {all_cache_hits} | Duration: {duration*1000:.1f}ms")
    assert all(r["success"] == 1.0 for r in results)
    assert all_cache_hits
    print("   PASS")


if __name__ == "__main__":
    print("=" * 70)
    print("MINICODE STRESS TEST SUITE")
    print("=" * 70)

    test_stress_sequential_many_agents()
    test_stress_parallel_many_agents()
    test_stress_concurrent_orchestration()
    test_stress_reflection_many_traces()
    test_stress_cache_many_entries()
    test_stress_mixed_workload()

    print("\n" + "=" * 70)
    print("ALL STRESS TESTS PASSED!")
    print("=" * 70)
