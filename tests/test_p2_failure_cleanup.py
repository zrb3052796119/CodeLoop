"""Adjacent invariants for the residual P2 failure-path repairs."""
from __future__ import annotations

from pathlib import Path

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_compactor import (
    ContextCompactor,
    MicrocompactState,
    ReadDedupManager,
)
from minicode.context_manager import ContextManager
from minicode.memory_pipeline import MemoryPipeline
from minicode.project_facts import ProjectFactsStore
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.types import AgentStep


def test_failure_settlement_is_exact_and_idempotent() -> None:
    budget = AgentTurnBudget(max_total_tokens=10_000, max_model_calls=5)
    first = budget.reserve_model_call(400)
    second = budget.reserve_model_call(600)

    assert budget.fail_model_call(first) is True
    assert budget.fail_model_call(first) is False
    snapshot = budget.snapshot()

    assert snapshot.used_model_calls == 2
    assert snapshot.used_total_tokens == 0
    assert snapshot.reserved_total_tokens == 600

    budget.record_model_call(
        input_tokens=550,
        output_tokens=25,
        reservation=second,
    )
    snapshot = budget.snapshot()
    assert snapshot.reserved_total_tokens == 0
    assert snapshot.used_total_tokens == 575


def test_main_agent_provider_failure_releases_reservation(tmp_path: Path) -> None:
    class FailingModel:
        def next(self, _messages, **_kwargs):
            raise ConnectionError("synthetic provider outage")

    budget = AgentTurnBudget(max_total_tokens=10_000, max_model_calls=5)
    output = run_agent_turn(
        model=FailingModel(),
        tools=ToolRegistry([]),
        messages=[{"role": "user", "content": "probe"}],
        cwd=str(tmp_path),
        enable_work_chain=False,
        agent_budget=budget,
    )

    assert "Network error" in output[-1]["content"]
    assert budget.snapshot().reserved_total_tokens == 0


def test_dedup_liveness_reconciles_even_when_new_dedup_is_disabled(
    tmp_path: Path,
) -> None:
    compactor = ContextCompactor(workspace=tmp_path)
    compactor._microcompact._state = MicrocompactState(
        last_time_based_compact=0.0,
        time_based_interval=0.0,
        keep_recent_tool_results=5,
    )
    compactor.read_dedup.register_read("demo.py", "source", 0)
    messages = [
        {
            "role": "tool_result",
            "toolName": "read_file" if index == 0 else "other",
            "toolUseId": f"call-{index}",
            "content": "source" if index == 0 else f"result-{index}",
        }
        for index in range(7)
    ]

    compactor.process_request(
        messages,
        enable_tool_budget=False,
        enable_read_dedup=False,
        enable_auto_compact=False,
    )

    assert not compactor.read_dedup.should_dedup("demo.py", "source")


def test_agent_registers_dedup_source_at_full_tool_result(monkeypatch) -> None:
    registered_indices: list[int] = []
    original_register = ReadDedupManager.register_read

    def capture_register(self, file_path, content, message_index):
        registered_indices.append(message_index)
        return original_register(self, file_path, content, message_index)

    monkeypatch.setattr(ReadDedupManager, "register_read", capture_register)

    class ScriptedModel:
        def __init__(self) -> None:
            self.calls = 0

        def next(self, _messages, **_kwargs):
            steps = [
                AgentStep(
                    type="tool_calls",
                    calls=[
                        {
                            "id": "read-1",
                            "toolName": "read_file",
                            "input": {"path": "demo.py"},
                        }
                    ],
                ),
                AgentStep(type="assistant", content="done"),
            ]
            step = steps[self.calls]
            self.calls += 1
            return step

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="read probe",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _value, _context: ToolResult(
                    ok=True,
                    output="full source",
                ),
            )
        ]
    )
    messages = run_agent_turn(
        model=ScriptedModel(),
        tools=registry,
        messages=[
            {"role": "system", "content": "base"},
            {"role": "user", "content": "read demo.py"},
        ],
        cwd=".",
        context_manager=ContextManager(model="fake"),
    )

    assert len(registered_indices) == 1
    source = messages[registered_indices[0]]
    assert source["role"] == "tool_result"
    assert source["toolName"] == "read_file"
    assert source["content"] == "full source"


def test_project_facts_do_not_require_memory_or_lesson_cooldown(
    tmp_path: Path,
) -> None:
    ProjectFactsStore(tmp_path).observe_dependencies(["facts-only-package"])
    pipeline = MemoryPipeline(memory_manager=None)
    pipeline.initialize(workspace_path=str(tmp_path))
    first = pipeline.inject(
        "first task",
        [],
        [{"role": "system", "content": "base"}],
    )
    second = pipeline.inject(
        "second task during lesson cooldown",
        [],
        [{"role": "system", "content": "base"}],
    )

    assert "facts-only-package" in first[0]["content"]
    assert "facts-only-package" in second[0]["content"]
