from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from minicode.agent_loop import run_agent_turn
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_retrieval import MemoryRetrievalResult
from minicode.run_events import (
    emit_memory_result_safely,
    emit_skill_routing_safely,
)
from minicode.tooling import ToolRegistry
from minicode.types import AgentStep, ChatMessage, ModelAdapter


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def emit(self, event_type: str, *, step=None, payload=None) -> None:
        self.events.append((event_type, payload))


class FinalModel(ModelAdapter):
    def next(
        self, messages: list[ChatMessage], on_stream_chunk=None
    ) -> AgentStep:
        return AgentStep(type="assistant", content="done")


def _skill(index: int, *, name: str | None = None):
    return SimpleNamespace(
        qualified_name=name or f"project/skill-{index}",
        name=f"skill-{index}",
        source="project",
        directory="project",
        score=3.25 + index,
        description="password=skill-secret",
        path="/Users/example/private/SKILL.md",
        reasons=["keyword:password=prompt-secret"],
        tools=["run_command"],
    )


def test_skill_projection_is_versioned_bounded_and_strictly_safe() -> None:
    selected = [_skill(index) for index in range(24)]
    selected.insert(3, _skill(99, name="../password=unsafe-name"))
    routing = SimpleNamespace(
        intent_type="code",
        action_type="update",
        total_skills=42,
        selected=selected,
        selected_skills=selected,
        used_fallback=False,
        tool_affinity={"secret": 1.0},
    )
    sink = RecordingSink()

    emit_skill_routing_safely(sink, routing)

    assert sink.events[0][0] == "skill.routed"
    payload = sink.events[0][1]
    assert payload == {
        "routingVersion": 1,
        "intentType": "code",
        "actionType": "update",
        "totalSkills": 42,
        "selectedCount": 25,
        "selected": [
            {
                "qualifiedName": f"project/skill-{index}",
                "source": "project",
                "directory": "project",
                "score": 3.25 + index,
            }
            for index in range(20)
        ],
        "selectedTruncated": True,
        "usedFallback": False,
    }
    serialized = str(payload)
    for forbidden in (
        "skill-secret",
        "prompt-secret",
        "/Users/",
        "run_command",
        "tool_affinity",
        "reasons",
        "description",
    ):
        assert forbidden not in serialized


def test_memory_projection_uses_only_final_counts_and_safe_enums() -> None:
    result = MemoryRetrievalResult(
        candidate_ids=("secret-candidate-1", "secret-candidate-2", "secret-candidate-3"),
        selected_ids=("secret-selected",),
        rendered_ids=("secret-rendered",),
        suppressed_ids=("secret-suppressed-1", "secret-suppressed-2"),
        no_match=False,
        no_match_reason="",
        controller_decision={"mode": "standard", "reasons": ["password=secret"]},
        total_tokens=186,
        query_hash="secret-query-hash",
        diagnostics={"query": "password=prompt-secret"},
    )
    sink = RecordingSink()

    emit_memory_result_safely(sink, result)

    assert sink.events == [
        (
            "memory.retrieved",
            {
                "retrievalVersion": 1,
                "candidateCount": 3,
                "selectedCount": 1,
                "suppressedCount": 2,
                "noMatch": False,
                "noMatchReason": None,
            },
        ),
        (
            "memory.rendered",
            {
                "renderVersion": 1,
                "renderedCount": 1,
                "totalTokens": 186,
                "controllerMode": "standard",
                "injected": True,
            },
        ),
    ]
    serialized = str(sink.events)
    for forbidden in (
        "secret-candidate",
        "secret-selected",
        "secret-rendered",
        "secret-suppressed",
        "secret-query-hash",
        "prompt-secret",
    ):
        assert forbidden not in serialized


def test_no_sink_or_no_retrieval_result_emits_nothing() -> None:
    routing = SimpleNamespace(
        intent_type="code",
        action_type="read",
        total_skills=0,
        selected=[],
        selected_skills=[],
        used_fallback=True,
    )
    sink = RecordingSink()

    emit_skill_routing_safely(None, routing)
    emit_memory_result_safely(None, MemoryRetrievalResult())
    emit_memory_result_safely(sink, None)

    assert sink.events == []


def test_runtime_projection_rejects_invalid_enums_counts_and_nonfinite_scores() -> None:
    sink = RecordingSink()
    invalid_score = _skill(1)
    invalid_score.score = float("inf")
    nan_score = _skill(2)
    nan_score.score = float("nan")
    emit_skill_routing_safely(
        sink,
        SimpleNamespace(
            intent_type="code",
            action_type="read",
            total_skills=2,
            selected_skills=[invalid_score, nan_score],
            used_fallback=False,
        ),
    )

    assert sink.events == [
        (
            "skill.routed",
            {
                "routingVersion": 1,
                "intentType": "code",
                "actionType": "read",
                "totalSkills": 2,
                "selectedCount": 2,
                "selected": [],
                "selectedTruncated": True,
                "usedFallback": False,
            },
        )
    ]

    for invalid_routing in (
        SimpleNamespace(
            intent_type="password=illegal",
            action_type="read",
            total_skills=0,
            selected_skills=[],
            used_fallback=False,
        ),
        SimpleNamespace(
            intent_type="code",
            action_type="read",
            total_skills=100_001,
            selected_skills=[],
            used_fallback=False,
        ),
    ):
        emit_skill_routing_safely(sink, invalid_routing)

    invalid_memory = MemoryRetrievalResult(
        controller_decision={"mode": "password=illegal"},
        total_tokens=-1,
    )
    emit_memory_result_safely(sink, invalid_memory)

    assert len(sink.events) == 1


def test_agent_loop_observes_the_single_real_memory_injection_before_model(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(project_root=tmp_path)
    entry = manager.add_entry(
        scope=MemoryScope.PROJECT,
        category="architecture",
        content="Checkout total rounding happens after decimal line aggregation.",
        tags=["checkout", "total", "rounding"],
    )
    assert entry is not None
    search_calls: list[str] = []
    original_search = manager.search

    def observed_search(query: str, *args, **kwargs):
        search_calls.append(query)
        return original_search(query, *args, **kwargs)

    manager.search = observed_search  # type: ignore[method-assign]
    sink = RecordingSink()

    messages = run_agent_turn(
        model=FinalModel(),
        tools=ToolRegistry([]),
        messages=[
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "fix checkout total rounding"},
        ],
        cwd=str(tmp_path),
        memory_manager=manager,
        event_sink=sink,
        max_steps=2,
    )

    event_types = [event_type for event_type, _payload in sink.events]
    assert event_types[:3] == [
        "memory.retrieved",
        "memory.rendered",
        "model.started",
    ]
    assert event_types.count("memory.retrieved") == 1
    assert event_types.count("memory.rendered") == 1
    assert search_calls == ["fix checkout total rounding"]
    assert entry.retrieval_count == 1
    assert entry.injection_count == 1
    system = next(message["content"] for message in messages if message["role"] == "system")
    assert system.count(entry.content) == 1


def test_failing_sink_does_not_change_memory_prompt_counters_or_result(
    tmp_path: Path,
) -> None:
    class FailingSink:
        def emit(self, *_args, **_kwargs) -> None:
            raise RuntimeError("password=observer-secret")

    def run_case(workspace: Path, sink):
        workspace.mkdir()
        manager = MemoryManager(project_root=workspace)
        entry = manager.add_entry(
            scope=MemoryScope.PROJECT,
            category="architecture",
            content="Checkout total rounding happens after decimal line aggregation.",
            tags=["checkout", "total", "rounding"],
        )
        assert entry is not None
        result = run_agent_turn(
            model=FinalModel(),
            tools=ToolRegistry([]),
            messages=[
                {"role": "system", "content": "SYSTEM"},
                {"role": "user", "content": "fix checkout total rounding"},
            ],
            cwd=str(workspace),
            memory_manager=manager,
            event_sink=sink,
            max_steps=2,
        )
        return (
            result,
            entry.retrieval_count,
            entry.injection_count,
            entry.success_count,
            entry.failure_count,
        )

    baseline = run_case(tmp_path / "baseline", None)
    observed = run_case(tmp_path / "observed", FailingSink())

    assert observed == baseline
