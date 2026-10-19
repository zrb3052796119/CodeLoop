"""Auxiliary requests share the same finite input-schema admission boundary."""
from pathlib import Path
import pytest
from minicode.agent_budget import AgentTurnBudget
from minicode.context_compactor import LLMSummaryGenerator
from minicode.context_manager import estimate_messages_tokens
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_curator_agent import MemoryCuratorAgent
from minicode.types import AgentStep, ModelUsage
from test_request_schema_soft_landing_review import _catalog, _schemas_price


@pytest.mark.parametrize('finite', [True, False])
def test_compaction_prices_its_shared_adapter_catalog(finite: bool) -> None:
    registry = _catalog()
    schema_cost = _schemas_price(registry)
    budget = AgentTurnBudget(max_total_tokens=schema_cost - 1 if finite else None, max_model_calls=2)
    calls = []

    class Model:
        tools = registry

        def next(self, messages, **options):
            assert not options.get('disable_tools')
            message_cost = estimate_messages_tokens(messages)
            assert message_cost < schema_cost - 1
            calls.append(message_cost + schema_cost)
            return AgentStep(type='assistant', content='The task and answer are preserved in this complete local summary.',
                usage=ModelUsage(input_tokens=calls[-1], output_tokens=16, source='provider'))

    summary = LLMSummaryGenerator(Model(), agent_budget=budget).summarize([
        {'role': 'user', 'content': 'task one'}, {'role': 'assistant', 'content': 'answer one'},
        {'role': 'user', 'content': 'task two'}, {'role': 'assistant', 'content': 'answer two'}])
    if finite:
        assert calls == [], 'A summary request must not spend a catalog that exceeds the shared balance.'
        assert summary is None
        assert budget.snapshot().used_total_tokens == 0
    else:
        assert len(calls) == 1
        assert summary is not None
        assert budget.snapshot().used_total_tokens == calls[0] + 16
    assert budget.snapshot().reserved_total_tokens == 0


@pytest.mark.parametrize('finite', [True, False])
def test_curator_next_prices_its_shared_adapter_catalog(tmp_path: Path, finite: bool) -> None:
    manager = MemoryManager(project_root=tmp_path)
    for content in ('Use repository pattern', 'Keep domain model framework free', 'Use a single data sink'):
        manager.add_entry(scope=MemoryScope.PROJECT, category='architecture', content=content)
    entries = tuple(manager.memories[MemoryScope.PROJECT].entries)
    for entry in entries:
        assert manager.set_related_memories(MemoryScope.PROJECT, entry.id,
            [other.id for other in entries if other.id != entry.id])
    registry = _catalog()
    schema_cost = _schemas_price(registry)
    budget = AgentTurnBudget(max_total_tokens=schema_cost - 1 if finite else None, max_model_calls=2)
    calls = []

    class Model:
        tools = registry

        def next(self, messages, **options):
            assert not options.get('disable_tools')
            message_cost = estimate_messages_tokens(messages)
            assert message_cost < schema_cost - 1
            calls.append(message_cost + schema_cost)
            return AgentStep(type='assistant', content='Shared architecture insight across all related entries.',
                usage=ModelUsage(input_tokens=calls[-1], output_tokens=12, source='provider'))

    report = MemoryCuratorAgent(manager, model_adapter=Model(), workspace_path=str(tmp_path),
        max_insights_per_cycle=1).run_cycle(force=True, agent_budget=budget)
    assert report.status == 'completed'
    if finite:
        assert calls == [], 'Curation must not spend a catalog that exceeds the shared balance.'
        assert budget.snapshot().used_total_tokens == 0
    else:
        assert len(calls) == 1
        assert budget.snapshot().used_total_tokens == calls[0] + 12
    assert budget.snapshot().reserved_total_tokens == 0
