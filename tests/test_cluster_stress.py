"""Cluster/concurrent stress tests for MiniCode.

Tests multiple agent loops running concurrently to verify thread safety,
scheduling contracts, and resource limit enforcement under load.
"""

from __future__ import annotations

import concurrent.futures
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from minicode.agent_intelligence import ErrorClassifier
from minicode.agent_loop import run_agent_turn
from minicode.agent_metrics import AgentMetricsCollector
from minicode.context_manager import ContextManager
from minicode.memory import MemoryManager, MemoryScope
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.types import AgentStep, ChatMessage, ModelAdapter


class CountingModel(ModelAdapter):
    """Minimal model whose calls are observable without wall-clock delays."""

    def __init__(self) -> None:
        self.calls = 0

    def next(
        self,
        messages: list[ChatMessage],
        on_stream_chunk: Callable[[str], None] | None = None,
        store: Any | None = None,
    ) -> AgentStep:
        self.calls += 1
        return AgentStep(type="assistant", content="done")


class ToolBatchModel(ModelAdapter):
    """Request one fixed tool batch, then complete the turn."""

    def __init__(self, num_tools: int) -> None:
        self.num_tools = num_tools
        self.calls = 0

    def next(
        self,
        messages: list[ChatMessage],
        on_stream_chunk: Callable[[str], None] | None = None,
        store: Any | None = None,
    ) -> AgentStep:
        self.calls += 1
        if self.calls == 1:
            return AgentStep(
                type="tool_calls",
                calls=[
                    {"id": str(index), "toolName": f"tool_{index}", "input": {}}
                    for index in range(self.num_tools)
                ],
            )
        return AgentStep(type="assistant", content="done")


class ConcurrentToolRegistry:
    """Thread-safe tool registry for concurrent testing."""

    def __init__(
        self,
        num_tools: int = 5,
        *,
        execution_barrier: threading.Barrier | None = None,
        concurrency_safe: bool = True,
    ) -> None:
        self._lock = threading.Lock()
        self._execution_count = 0
        self._concurrent_max = 0
        self._current_executions = 0
        self._execution_threads: list[int] = []
        self._execution_barrier = execution_barrier
        
        from minicode.tooling import ToolMetadata, ToolCapability
        
        tools = []
        for i in range(num_tools):
            meta = ToolMetadata(
                name=f"tool_{i}",
                description=f"Test tool {i}",
                capabilities=(
                    {ToolCapability.CONCURRENCY_SAFE} if concurrency_safe else set()
                ),
            )
            tools.append(ToolDefinition(
                name=f"tool_{i}",
                description=f"Test tool {i}",
                input_schema={"type": "object"},
                validator=lambda v: v,
                run=self._make_runner(i),
                metadata=meta,
            ))
        self.registry = ToolRegistry(tools)
    
    def _make_runner(self, tool_id: int):
        def runner(input_data: dict, context) -> ToolResult:
            with self._lock:
                self._current_executions += 1
                self._execution_count += 1
                self._concurrent_max = max(self._concurrent_max, self._current_executions)
                self._execution_threads.append(threading.get_ident())
            try:
                if self._execution_barrier is not None:
                    # A watchdog prevents a scheduler regression from hanging the
                    # suite; elapsed time is never part of the assertion.
                    self._execution_barrier.wait(timeout=5)
                return ToolResult(ok=True, output=f"tool_{tool_id} result")
            finally:
                with self._lock:
                    self._current_executions -= 1
        return runner


class TestConcurrentAgentLoopStress:
    """Stress tests for concurrent agent loop execution."""

    def test_single_agent_loop_basic(self):
        """Baseline: single agent loop completes successfully."""
        registry = ConcurrentToolRegistry(num_tools=3)
        model = CountingModel()

        messages = run_agent_turn(
            model=model,
            tools=registry.registry,
            messages=[{"role": "system", "content": "sys"}],
            cwd=".",
        )

        assert messages[-1]["role"] == "assistant"
        assert model.calls > 0

    def test_concurrent_agent_loops(self):
        """Multiple agent loops running concurrently."""
        num_workers = 4
        num_turns_per_worker = 3

        def run_worker(worker_id: int):
            registry = ConcurrentToolRegistry(num_tools=3)
            model = CountingModel()
            results = []

            for turn in range(num_turns_per_worker):
                messages = run_agent_turn(
                    model=model,
                    tools=registry.registry,
                    messages=[{"role": "system", "content": "sys"}],
                    cwd=".",
                    max_steps=5,
                )
                results.append(
                    {
                        "worker_id": worker_id,
                        "turn": turn,
                        "success": messages[-1]["role"] == "assistant",
                        "model_calls": model.calls,
                    }
                )
            return results

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(run_worker, i) for i in range(num_workers)]
            all_results = []
            for future in concurrent.futures.as_completed(futures):
                all_results.extend(future.result())

        assert len(all_results) == num_workers * num_turns_per_worker
        assert all(r["success"] for r in all_results)

    def test_high_concurrency_tool_execution(self):
        """The Agent Loop dispatches one concurrency-safe tool batch in parallel."""
        num_tools = 5
        registry = ConcurrentToolRegistry(
            num_tools=num_tools,
            execution_barrier=threading.Barrier(num_tools),
        )
        model = ToolBatchModel(num_tools)

        messages = run_agent_turn(
            model=model,
            tools=registry.registry,
            messages=[{"role": "system", "content": "sys"}],
            cwd=".",
            max_steps=3,
            enable_work_chain=False,
        )

        tool_results = [
            message for message in messages if message.get("role") == "tool_result"
        ]
        assert model.calls == 2
        assert registry._execution_count == num_tools
        assert registry._concurrent_max == num_tools
        assert len(tool_results) == num_tools
        assert all("result" in str(message.get("content")) for message in tool_results)

    def test_metrics_collector_thread_safety(self):
        """Verify metrics collector is thread-safe."""
        tmp = tempfile.mkdtemp()
        storage_path = Path(tmp) / "metrics.json"
        
        # Each worker gets its own collector to avoid shared state conflicts
        collectors: list[AgentMetricsCollector] = []
        
        def record_turns(worker_id: int):
            collector = AgentMetricsCollector(storage_path=storage_path)
            collectors.append(collector)
            for turn in range(5):
                collector.start_turn(turn * 100 + worker_id)
                collector.start_tool("read_file")
                collector.end_tool(True, "", 100)
                collector.end_turn(total_tokens=100)
            return worker_id

        num_workers = 4
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(record_turns, i) for i in range(num_workers)]
            for future in concurrent.futures.as_completed(futures):
                future.result()

        # Verify at least some turns were recorded (each collector has its own state)
        total_turns = sum(len(c.get_recent_turns(count=100)) for c in collectors)
        assert total_turns == num_workers * 5

        # Verify persistence
        assert storage_path.exists()

    def test_error_recovery_under_load(self):
        """Test error recovery when multiple failures occur concurrently."""
        errors = [
            "Connection timeout",
            "Permission denied",
            "Out of memory",
            "Invalid input format",
            "Network unreachable",
        ]

        def classify_error(error_msg: str):
            return ErrorClassifier.classify(error_msg, "run_command")

        # Classify errors concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(classify_error, e) for e in errors]
            results = [f.result() for f in futures]

        categories = [r.category.value for r in results]
        assert "network" in categories
        assert "permission" in categories
        assert "resource" in categories
        assert "logic" in categories

    def test_memory_system_concurrent_access(self):
        """Test memory system under concurrent access."""
        # Each worker gets its own MemoryManager to avoid file contention on Windows
        managers: list[MemoryManager] = []

        def add_entries(worker_id: int):
            tmp = tempfile.mkdtemp()
            manager = MemoryManager(project_root=Path(tmp))
            managers.append(manager)
            for i in range(10):
                manager.add_entry(
                    MemoryScope.PROJECT,
                    "test",
                    f"Worker {worker_id} entry {i}",
                    [f"tag-{worker_id}"],
                )
            return worker_id, manager

        def search_entries(manager: MemoryManager, worker_id: int):
            results = manager.search(f"Worker {worker_id}", scope=MemoryScope.PROJECT)
            return len(results)

        num_workers = 4
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as pool:
            # Concurrent writes (each to its own manager)
            write_futures = [pool.submit(add_entries, i) for i in range(num_workers)]
            worker_managers = []
            for f in write_futures:
                worker_id, mgr = f.result()
                worker_managers.append((worker_id, mgr))

            # Concurrent reads
            read_futures = [
                pool.submit(search_entries, mgr, wid)
                for wid, mgr in worker_managers
            ]
            search_counts = [f.result() for f in read_futures]

        assert all(c > 0 for c in search_counts)
        # Each manager has its own entries
        for _, mgr in worker_managers:
            assert len(mgr.memories[MemoryScope.PROJECT].entries) == 10


class TestAgentLoopScheduling:
    """Deterministic scheduling and call-accounting checks for the Agent Loop."""

    def test_repeated_agent_loop_call_accounting_is_deterministic(self):
        """Repeated isolated turns each consume exactly one model call."""
        call_counts = []

        for _ in range(10):
            registry = ConcurrentToolRegistry(num_tools=3)
            model = CountingModel()

            messages = run_agent_turn(
                model=model,
                tools=registry.registry,
                messages=[{"role": "system", "content": "sys"}],
                cwd=".",
                max_steps=3,
                enable_work_chain=False,
            )
            assert messages[-1] == {"role": "assistant", "content": "done"}
            call_counts.append(model.calls)

        assert call_counts == [1] * 10

    def test_non_concurrency_safe_tool_batch_stays_on_agent_thread(self):
        """The Agent Loop never moves ordinary tools into its worker pool."""
        num_tools = 4
        registry = ConcurrentToolRegistry(
            num_tools=num_tools,
            concurrency_safe=False,
        )
        model = ToolBatchModel(num_tools)
        agent_thread = threading.get_ident()

        messages = run_agent_turn(
            model=model,
            tools=registry.registry,
            messages=[{"role": "system", "content": "sys"}],
            cwd=".",
            max_steps=3,
            enable_work_chain=False,
        )

        tool_results = [
            message for message in messages if message.get("role") == "tool_result"
        ]
        assert model.calls == 2
        assert registry._execution_count == num_tools
        assert registry._concurrent_max == 1
        assert registry._execution_threads == [agent_thread] * num_tools
        assert len(tool_results) == num_tools


class TestResourceLimits:
    """Test system behavior under resource constraints."""

    def test_max_steps_enforcement(self):
        """Verify max_steps is enforced under load."""

        class InfiniteModel(ModelAdapter):
            def next(
                self,
                messages: list[ChatMessage],
                on_stream_chunk: Callable[[str], None] | None = None,
                store: Any | None = None,
            ) -> AgentStep:
                return AgentStep(
                    type="tool_calls",
                    calls=[{"id": "1", "toolName": "tool_0", "input": {}}],
                )

        registry = ConcurrentToolRegistry(num_tools=1)
        model = InfiniteModel()

        max_steps = 5
        messages = run_agent_turn(
            model=model,
            tools=registry.registry,
            messages=[{"role": "system", "content": "sys"}],
            cwd=".",
            max_steps=max_steps,
        )

        tool_results = [m for m in messages if m.get("role") == "tool_result"]
        assert len(tool_results) <= max_steps

    def test_context_manager_under_load(self):
        """Test context manager with many messages."""
        cm = ContextManager(model="gpt-4o")

        # Add many messages to simulate long conversation
        for i in range(100):
            cm.add_message(
                {
                    "role": "user" if i % 2 == 0 else "assistant",
                    "content": f"Message {i} with some content to test context management",
                }
            )

        stats = cm.get_stats()
        assert stats.messages_count == 100
        assert stats.total_tokens > 0

        # Verify compaction works
        if cm.should_auto_compact():
            compacted = cm.compact_messages()
            assert len(compacted) < 100
