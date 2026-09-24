"""Curator LLM calls share the same Agent turn budget as the main loop."""

from __future__ import annotations

from pathlib import Path

from minicode.agent_budget import AgentTurnBudget
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_curator_agent import MemoryCuratorAgent
from minicode.types import AgentStep, ModelUsage


def _fully_link_project_memories(manager: MemoryManager) -> None:
    entries = tuple(manager.memories[MemoryScope.PROJECT].entries)
    for entry in entries:
        assert manager.set_related_memories(
            MemoryScope.PROJECT,
            entry.id,
            [other.id for other in entries if other.id != entry.id],
        )


def test_curator_skips_llm_when_shared_budget_exhausted(tmp_path: Path) -> None:
    manager = MemoryManager(project_root=tmp_path)
    manager.add_entry(
        scope=MemoryScope.PROJECT,
        category="architecture",
        content="Use repository pattern",
    )
    manager.add_entry(
        scope=MemoryScope.PROJECT,
        category="architecture",
        content="Keep domain model framework free",
    )
    manager.add_entry(
        scope=MemoryScope.PROJECT,
        category="architecture",
        content="Use a single data sink",
    )
    _fully_link_project_memories(manager)

    class FakeModel:
        def __init__(self) -> None:
            self.calls = 0

        def next(self, messages):
            self.calls += 1
            return AgentStep(
                type="assistant",
                content="Shared architecture insight across all related entries.",
                usage=ModelUsage(input_tokens=10, output_tokens=4, source="provider"),
            )

    model = FakeModel()
    curator = MemoryCuratorAgent(
        manager,
        model_adapter=model,
        workspace_path=str(tmp_path),
        max_insights_per_cycle=1,
    )
    budget = AgentTurnBudget(max_total_tokens=1)

    report = curator.run_cycle(force=True, agent_budget=budget)

    assert report.status == "completed"
    assert model.calls == 0


def test_curator_preserves_the_final_agent_model_call(tmp_path: Path) -> None:
    manager = MemoryManager(project_root=tmp_path)
    for content in (
        "Use repository pattern",
        "Keep domain model framework free",
        "Use a single data sink",
    ):
        manager.add_entry(
            scope=MemoryScope.PROJECT,
            category="architecture",
            content=content,
        )
    _fully_link_project_memories(manager)

    class FakeModel:
        def __init__(self) -> None:
            self.calls = 0

        def next(self, messages):
            self.calls += 1
            return AgentStep(
                type="assistant",
                content="Shared architecture insight across all related entries.",
            )

    model = FakeModel()
    curator = MemoryCuratorAgent(
        manager,
        model_adapter=model,
        workspace_path=str(tmp_path),
        max_insights_per_cycle=1,
    )
    budget = AgentTurnBudget(max_model_calls=1, max_total_tokens=10_000)

    report = curator.run_cycle(force=True, agent_budget=budget)

    assert report.status == "completed"
    assert model.calls == 0
    assert budget.snapshot().used_model_calls == 0


def test_curator_records_llm_usage_in_shared_budget(tmp_path: Path) -> None:
    manager = MemoryManager(project_root=tmp_path)
    manager.add_entry(
        scope=MemoryScope.PROJECT,
        category="architecture",
        content="Use repository pattern",
    )
    manager.add_entry(
        scope=MemoryScope.PROJECT,
        category="architecture",
        content="Keep domain model framework free",
    )
    manager.add_entry(
        scope=MemoryScope.PROJECT,
        category="architecture",
        content="Use a single data sink",
    )
    _fully_link_project_memories(manager)

    class FakeModel:
        def __init__(self) -> None:
            self.calls = 0

        def next(self, messages):
            self.calls += 1
            return AgentStep(
                type="assistant",
                content="Shared architecture insight across all related entries.",
                usage=ModelUsage(input_tokens=10, output_tokens=4, source="provider"),
            )

    model = FakeModel()
    curator = MemoryCuratorAgent(
        manager,
        model_adapter=model,
        workspace_path=str(tmp_path),
        max_insights_per_cycle=1,
    )
    budget = AgentTurnBudget(max_model_calls=2, max_total_tokens=10_000)

    curator.run_cycle(force=True, agent_budget=budget)

    snapshot = budget.snapshot()
    assert model.calls >= 1
    assert snapshot.used_model_calls >= 1
    assert snapshot.used_total_tokens >= 14
