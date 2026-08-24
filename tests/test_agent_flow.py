"""Full agent loop integration test — verifies all cybernetic controllers fire.

Runs a complete agent turn with the mock model and checks that every
major controller in the Sense→Control→Act pipeline was invoked.

This is the definitive "MiniCode is working" test.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.context_manager import ContextManager
from minicode.mock_model import MockModelAdapter
from minicode.memory import MemoryManager
from minicode.permissions import PermissionManager
from minicode.tools import create_default_tool_registry


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def mock_model():
    return MockModelAdapter()


@pytest.fixture
def tools(workspace):
    return create_default_tool_registry(str(workspace), runtime=None)


@pytest.fixture
def permissions(workspace):
    def _allow(request):
        return {"decision": "allow_once"}
    return PermissionManager(str(workspace), prompt=_allow)


@pytest.fixture
def messages(workspace, permissions):
    return [
        {"role": "system", "content": "You are a coding assistant. Use tools to help the user."},
        {"role": "user", "content": "Create a React login form component"},
    ]


class TestAgentFlowBasic:
    """Basic agent loop runs without errors."""

    def test_agent_completes_without_error(
        self, mock_model, tools, messages, workspace, permissions
    ):
        result = run_agent_turn(
            model=mock_model,
            tools=tools,
            messages=messages,
            cwd=str(workspace),
            permissions=permissions,
            max_steps=3,
        )
        assert isinstance(result, list)
        assert len(result) > 0

    def test_agent_with_context_manager(
        self, mock_model, tools, messages, workspace, permissions
    ):
        ctx = ContextManager(model="claude-sonnet-4-20250514")
        run_agent_turn(
            model=mock_model,
            tools=tools,
            messages=messages,
            cwd=str(workspace),
            permissions=permissions,
            context_manager=ctx,
            max_steps=3,
        )
        stats = ctx.get_stats()
        assert stats.messages_count > 0


class TestAgentFlowCybernetics:
    """All cybernetic controllers initialize and run without errors."""

    def test_full_cybernetic_stack_initializes(
        self, mock_model, tools, messages, workspace, permissions
    ):
        """The full 15-controller cybernetic stack must not crash."""
        result = run_agent_turn(
            model=mock_model,
            tools=tools,
            messages=messages,
            cwd=str(workspace),
            permissions=permissions,
            context_manager=ContextManager(model="claude-sonnet-4-20250514"),
            enable_work_chain=True,
            max_steps=3,
            memory_manager=MemoryManager(project_root=workspace),
        )
        assert len(result) > 0

    def test_cybernetic_stack_with_ls_command(
        self, mock_model, tools, messages, workspace, permissions
    ):
        """Run /ls through the cybernetic stack."""
        messages.append({"role": "user", "content": "/ls"})
        result = run_agent_turn(
            model=mock_model,
            tools=tools,
            messages=messages,
            cwd=str(workspace),
            permissions=permissions,
            context_manager=ContextManager(model="claude-sonnet-4-20250514"),
            enable_work_chain=True,
            max_steps=5,
        )
        assert len(result) > 0

    def test_agent_loop_uses_orchestrator_hooks(
        self, monkeypatch, mock_model, tools, messages, workspace, permissions
    ):
        """The agent loop should drive the unified orchestrator lifecycle."""
        from minicode.cybernetic_orchestrator import CyberneticOrchestrator

        calls: list[str] = []

        def wrap(name):
            original = getattr(CyberneticOrchestrator, name)

            def _wrapped(self, *args, **kwargs):
                calls.append(name)
                return original(self, *args, **kwargs)

            return _wrapped

        for method in (
            "wire_memory",
            "wire_healing",
            "inject_memories",
            "step_start",
            "step_end",
            "reflect_on_task",
        ):
            monkeypatch.setattr(CyberneticOrchestrator, method, wrap(method))

        messages.append({"role": "user", "content": "/ls"})
        result = run_agent_turn(
            model=mock_model,
            tools=tools,
            messages=messages,
            cwd=str(workspace),
            permissions=permissions,
            context_manager=ContextManager(model="claude-sonnet-4-20250514"),
            enable_work_chain=True,
            max_steps=3,
            memory_manager=MemoryManager(project_root=workspace),
        )

        assert len(result) > 0
        for method in (
            "wire_memory",
            "wire_healing",
            "inject_memories",
            "step_start",
            "step_end",
            "reflect_on_task",
        ):
            assert method in calls

    def test_recovered_tool_error_does_not_force_task_result_failure(
        self, monkeypatch, tools, workspace, permissions
    ):
        import minicode.agent_loop as agent_loop_module

        from minicode.cybernetic_orchestrator import CyberneticOrchestrator
        from minicode.feedback_controller import FeedbackController
        from minicode.memory_pipeline import MemoryPipeline
        from minicode.smart_router import FeedbackLearner
        from minicode.task_object import TaskState

        captured: list[list[dict]] = []
        captured_tasks = []
        memory_outcomes: list[str] = []
        routing_outcomes = []
        routing_flushes: list[bool] = []
        pattern_outcomes: list[tuple[str, bool]] = []

        def capture_reflection(self, *args, **kwargs):
            captured.append(list(kwargs["execution_trace"]))

        original_build_task = agent_loop_module._build_work_chain_task

        def capture_task(messages):
            task, metadata = original_build_task(messages)
            captured_tasks.append(task)
            return task, metadata

        original_pattern_feedback = FeedbackController.record_pattern_effectiveness

        def capture_pattern_feedback(self, pattern_id, success):
            pattern_outcomes.append((pattern_id, success))
            return original_pattern_feedback(self, pattern_id, success)

        monkeypatch.setattr(
            CyberneticOrchestrator,
            "reflect_on_task",
            capture_reflection,
        )
        monkeypatch.setattr(agent_loop_module, "_build_work_chain_task", capture_task)
        monkeypatch.setattr(
            MemoryPipeline,
            "feedback",
            lambda self, outcome, *args, **kwargs: memory_outcomes.append(outcome),
        )
        monkeypatch.setattr(
            FeedbackLearner,
            "record_outcome",
            lambda self, outcome: routing_outcomes.append(outcome),
        )
        monkeypatch.setattr(
            FeedbackLearner,
            "flush",
            lambda self: routing_flushes.append(True),
        )
        monkeypatch.setattr(
            FeedbackController,
            "record_pattern_effectiveness",
            capture_pattern_feedback,
        )
        routed_model = MockModelAdapter()
        routed_model.model_id = "mock-model"
        run_agent_turn(
            model=routed_model,
            tools=tools,
            messages=[
                {"role": "system", "content": "Use tools."},
                {"role": "user", "content": "/cmd false"},
            ],
            cwd=str(workspace),
            permissions=permissions,
            enable_work_chain=True,
            max_steps=3,
            memory_manager=MemoryManager(project_root=workspace),
        )

        task_result = captured[0][-1]
        assert task_result["type"] == "task_result"
        assert task_result["status"] == "success"
        assert task_result["had_errors"] is True
        assert task_result["errors_recovered"] is True
        assert task_result["event_id"].startswith("event-")
        assert captured_tasks[0].state == TaskState.COMPLETED
        assert task_result["completion_succeeded"] is True
        assert task_result["verification_status"] == "unverified"
        assert task_result["goal_achieved"] is False
        assert memory_outcomes == ["unknown"]
        assert routing_outcomes == []
        assert routing_flushes == []
        task_pattern = [
            success
            for pattern_id, success in pattern_outcomes
            if pattern_id.endswith(captured_tasks[0].id)
        ]
        assert task_pattern == []

    def test_unidentified_model_is_not_added_to_routing_feedback(
        self, monkeypatch, tools, workspace, permissions
    ):
        from minicode.smart_router import FeedbackLearner

        routing_outcomes = []
        monkeypatch.setattr(
            FeedbackLearner,
            "record_outcome",
            lambda self, outcome: routing_outcomes.append(outcome),
        )

        run_agent_turn(
            model=MockModelAdapter(),
            tools=tools,
            messages=[
                {"role": "system", "content": "Use tools."},
                {"role": "user", "content": "hello"},
            ],
            cwd=str(workspace),
            permissions=permissions,
            enable_work_chain=True,
            max_steps=3,
        )

        assert routing_outcomes == []


class TestAgentMemoryPipeline:
    """Memory pipeline runs end-to-end within agent loop."""

    def test_memory_pipeline_in_agent_loop(
        self, mock_model, tools, messages, workspace, permissions
    ):
        """Memory pipeline (domain classify → BM25 → reranker → inject) must work."""
        # Create some memories first to have something to search
        from minicode.memory import MemoryManager, MemoryScope
        mgr = MemoryManager(project_root=str(workspace))
        mgr.add_entry(
            scope=MemoryScope.PROJECT, category="pattern",
            content="React forms use react-hook-form with zod validation",
            tags=["react", "form", "validation"],
        )
        mgr.add_entry(
            scope=MemoryScope.PROJECT, category="convention",
            content="Use functional components with hooks, avoid class components",
            tags=["react", "component"],
        )

        result = run_agent_turn(
            model=mock_model,
            tools=tools,
            messages=messages,
            cwd=str(workspace),
            permissions=permissions,
            context_manager=ContextManager(model="claude-sonnet-4-20250514"),
            enable_work_chain=True,
            max_steps=3,
        )
        assert len(result) > 0

    def test_same_turn_verification_tally_reaches_memory_feedback(
        self, workspace, monkeypatch
    ):
        """An independently verified tool result reaches Memory feedback as a
        turn tally. It is not attributed to an individual rendered Memory
        unless a separate target binding is available."""
        from minicode.memory import MemoryManager
        from minicode.memory_pipeline import MemoryPipeline
        from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
        from minicode.types import AgentStep, ChatMessage, ModelAdapter

        def run_verifier(_input_data: dict, _context) -> ToolResult:
            return ToolResult(
                ok=True,
                output="verifier output",
                verification={
                    "verificationVersion": 1,
                    "kind": "tests",
                    "outcome": "passed",
                    "source": "test_runner",
                },
            )

        verifier_tools = ToolRegistry(
            [
                ToolDefinition(
                    name="test_runner",
                    description="trusted verifier",
                    input_schema={"type": "object"},
                    validator=lambda value: value,
                    run=run_verifier,
                )
            ]
        )

        class VerifyThenDoneModel(ModelAdapter):
            def __init__(self) -> None:
                self.calls = 0

            def next(
                self, messages: list[ChatMessage], on_stream_chunk=None
            ) -> AgentStep:
                self.calls += 1
                if self.calls == 1:
                    return AgentStep(
                        type="tool_calls",
                        calls=[
                            {"id": "verify", "toolName": "test_runner", "input": {}}
                        ],
                    )
                return AgentStep(type="assistant", content="done")

        feedback_calls: list[tuple[object, dict]] = []
        monkeypatch.setattr(
            MemoryPipeline,
            "feedback",
            lambda self, outcome, *args, **kwargs: feedback_calls.append(
                (outcome, kwargs)
            ),
        )

        run_agent_turn(
            model=VerifyThenDoneModel(),
            tools=verifier_tools,
            messages=[
                {"role": "system", "content": "Use tools."},
                {"role": "user", "content": "run the test suite and confirm it passes"},
            ],
            cwd=str(workspace),
            enable_work_chain=True,
            max_steps=3,
            memory_manager=MemoryManager(project_root=workspace),
        )

        assert feedback_calls == [
            (True, {"verification_passed": 1, "verification_failed": 0})
        ]
