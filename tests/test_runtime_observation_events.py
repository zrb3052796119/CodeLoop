from __future__ import annotations

import hashlib
from types import SimpleNamespace
from pathlib import Path

from minicode.agent_loop import run_agent_turn
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_retrieval import MemoryRetrievalResult
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.run_events import (
    SkillUsageTracker,
    VerificationTracker,
    emit_memory_result_safely,
    emit_skill_routing_safely,
    project_skill_attribution_event,
    verification_corroboration,
)
from minicode.task_outcome import canonicalize_task_outcome
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.load_skill import create_load_skill_tool
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


class RepeatedSkillLoadModel(ModelAdapter):
    def __init__(self) -> None:
        self.calls = 0

    def next(
        self, messages: list[ChatMessage], on_stream_chunk=None
    ) -> AgentStep:
        self.calls += 1
        if self.calls <= 2:
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": f"load-{self.calls}",
                        "toolName": "load_skill",
                        "input": {"name": "memory-audit"},
                    }
                ],
            )
        return AgentStep(type="assistant", content="done")


class RecoveredErrorThenSkillModel(ModelAdapter):
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
                    {
                        "id": "failed-call",
                        "toolName": "missing_tool",
                        "input": {},
                    }
                ],
            )
        if self.calls == 2:
            return AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "load-call",
                        "toolName": "load_skill",
                        "input": {"name": "memory-audit"},
                    }
                ],
            )
        return AgentStep(type="assistant", content="recovered")


def _skill(index: int, *, name: str | None = None):
    return SimpleNamespace(
        qualified_name=name or f"project/skill-{index}",
        name=f"skill-{index}",
        source="project",
        directory="project",
        score=3.25 + index,
        description="password=skill-secret",
        content=f"# Skill {index}\npassword=content-secret",
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
        "routingVersion": 2,
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
                "contentDigest": hashlib.sha256(
                    f"# Skill {index}\npassword=content-secret".encode("utf-8")
                ).hexdigest(),
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
        "content-secret",
    ):
        assert forbidden not in serialized


def test_agent_emits_canonical_task_outcome_without_loading_a_skill(
    tmp_path: Path,
) -> None:
    sink = RecordingSink()

    run_agent_turn(
        model=FinalModel(),
        tools=ToolRegistry([]),
        messages=[
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "answer without a skill"},
        ],
        cwd=str(tmp_path),
        event_sink=sink,
        max_steps=1,
        enable_work_chain=False,
    )

    outcomes = [
        payload
        for event_type, payload in sink.events
        if event_type == "task.outcome"
    ]
    assert outcomes == [
        {
            "outcomeVersion": 1,
            "outcomeStatus": "success",
            "goalAchieved": True,
            "learningSuccess": True,
            "hadToolErrors": False,
            "errorsRecovered": False,
            "toolErrorCount": 0,
        }
    ]
    assert all(
        event_type != "skill.attributed"
        for event_type, _payload in sink.events
    )


def test_successful_load_skill_emits_identity_and_content_digest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    content = (
        "---\n"
        "name: memory-audit\n"
        "description: Audit persistent memory.\n"
        "---\n"
        "# Memory Audit\n"
    )
    skill_path = tmp_path / ".mini-code" / "skills" / "memory-audit" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(content, encoding="utf-8")
    sink = RecordingSink()

    result = create_load_skill_tool(str(tmp_path)).run(
        {"name": "memory-audit"},
        ToolContext(cwd=str(tmp_path), _event_sink=sink, _step=7),
    )

    assert result.ok
    assert sink.events == [
        (
            "skill.loaded",
            {
                "loadVersion": 1,
                "qualifiedName": "memory-audit",
                "source": "project",
                "directory": "",
                "contentDigest": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
        )
    ]
    serialized = str(sink.events)
    assert str(tmp_path) not in serialized
    assert "# Memory Audit" not in serialized


def test_agent_attributes_repeated_skill_load_once_to_canonical_outcome(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    content = (
        "---\n"
        "name: memory-audit\n"
        "description: Audit persistent memory.\n"
        "---\n"
        "# Memory Audit\n"
        "password=skill-content-secret\n"
    )
    skill_path = tmp_path / ".mini-code" / "skills" / "memory-audit" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(content, encoding="utf-8")
    sink = RecordingSink()

    run_agent_turn(
        model=RepeatedSkillLoadModel(),
        tools=ToolRegistry([create_load_skill_tool(str(tmp_path))]),
        messages=[
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "load the memory audit skill"},
        ],
        cwd=str(tmp_path),
        event_sink=sink,
        max_steps=4,
    )

    loaded = [
        payload
        for event_type, payload in sink.events
        if event_type == "skill.loaded"
    ]
    attributed = [
        payload
        for event_type, payload in sink.events
        if event_type == "skill.attributed"
    ]
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

    assert len(loaded) == 2
    assert attributed == [
        {
            "attributionVersion": 1,
            "attributionKind": "task_correlation",
            "outcomeStatus": "success",
            "goalAchieved": True,
            "hadToolErrors": False,
            "errorsRecovered": False,
            "toolErrorCount": 0,
            "loadedSkillCount": 1,
            "loadedSkills": [
                {
                    "qualifiedName": "memory-audit",
                    "source": "project",
                    "directory": "",
                    "contentDigest": digest,
                }
            ],
            "loadedSkillsTruncated": False,
        }
    ]
    serialized = str(attributed)
    assert str(tmp_path) not in serialized
    assert "skill-content-secret" not in serialized


def test_skill_attribution_separates_recovered_tool_error_from_task_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    content = (
        "---\n"
        "name: memory-audit\n"
        "description: Audit persistent memory.\n"
        "---\n"
        "# Memory Audit\n"
    )
    skill_path = tmp_path / ".mini-code" / "skills" / "memory-audit" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(content, encoding="utf-8")
    sink = RecordingSink()

    run_agent_turn(
        model=RecoveredErrorThenSkillModel(),
        tools=ToolRegistry([create_load_skill_tool(str(tmp_path))]),
        messages=[
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "recover and load the audit skill"},
        ],
        cwd=str(tmp_path),
        event_sink=sink,
        max_steps=4,
    )

    attributed = next(
        payload
        for event_type, payload in sink.events
        if event_type == "skill.attributed"
    )
    assert attributed["outcomeStatus"] == "success"
    assert attributed["goalAchieved"] is True
    assert attributed["hadToolErrors"] is True
    assert attributed["errorsRecovered"] is True
    assert attributed["toolErrorCount"] == 1


def test_skill_attribution_is_persisted_in_the_same_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    content = (
        "---\n"
        "name: memory-audit\n"
        "description: Audit persistent memory.\n"
        "---\n"
        "# Memory Audit\n"
    )
    skill_path = tmp_path / ".mini-code" / "skills" / "memory-audit" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(content, encoding="utf-8")
    journal = RunJournal(
        tmp_path,
        data_dir=tmp_path / "journal-home" / ".mini-code",
    )

    with observe_run(
        workspace=tmp_path,
        source="headless",
        title="Load an audit Skill",
        journal_factory=lambda _workspace: journal,
    ) as observation:
        run_id = observation.run_id
        run_agent_turn(
            model=RepeatedSkillLoadModel(),
            tools=ToolRegistry([create_load_skill_tool(str(tmp_path))]),
            messages=[
                {"role": "system", "content": "SYSTEM"},
                {"role": "user", "content": "load the memory audit skill"},
            ],
            cwd=str(tmp_path),
            event_sink=observation,
            max_steps=4,
        )

    assert run_id is not None
    events = journal.list_events(run_id, limit=100).items
    event_types = [event.type for event in events]
    assert event_types.count("skill.loaded") == 2
    assert max(
        index for index, event_type in enumerate(event_types)
        if event_type == "skill.loaded"
    ) < event_types.index("task.outcome")
    assert event_types.index("task.outcome") < event_types.index(
        "skill.attributed"
    )
    assert event_types[-1] == "run.completed"
    attributed = [
        event for event in events if event.type == "skill.attributed"
    ]
    assert len(attributed) == 1
    assert attributed[0].payload["loadedSkillCount"] == 1
    assert attributed[0].payload["outcomeStatus"] == "success"
    assert journal.get_run(run_id).status == "completed"


def test_skill_attribution_is_bounded_and_reports_truncation() -> None:
    tracker = SkillUsageTracker()
    for index in range(25):
        tracker.record(
            SimpleNamespace(
                qualified_name=f"project/skill-{index}",
                name=f"skill-{index}",
                source="project",
                directory="project",
                content=f"# Skill {index}",
            )
        )

    payload = project_skill_attribution_event(
        tracker,
        canonicalize_task_outcome("success", 0),
    )

    assert payload["loadedSkillCount"] == 20
    assert len(payload["loadedSkills"]) == 20
    assert payload["loadedSkillsTruncated"] is True


def test_verification_tracker_reduces_to_no_observation_by_default() -> None:
    tracker = VerificationTracker()
    assert tracker.snapshot() == (0, 0)
    assert verification_corroboration(*tracker.snapshot()) is None


def test_verification_tracker_ignores_malformed_or_incomplete_payloads() -> None:
    tracker = VerificationTracker()
    tracker.record(None)
    tracker.record({"outcome": "unknown"})
    tracker.record({"kind": "tests"})
    assert tracker.snapshot() == (0, 0)


def test_verification_tracker_any_failure_corroborates_negatively() -> None:
    tracker = VerificationTracker()
    tracker.record({"outcome": "passed"})
    tracker.record({"outcome": "passed"})
    tracker.record({"outcome": "failed"})
    assert tracker.snapshot() == (2, 1)
    assert verification_corroboration(*tracker.snapshot()) is False


def test_verification_tracker_all_passed_corroborates_positively() -> None:
    tracker = VerificationTracker()
    tracker.record({"outcome": "passed"})
    tracker.record({"outcome": "passed"})
    assert tracker.snapshot() == (2, 0)
    assert verification_corroboration(*tracker.snapshot()) is True


def test_verification_tracker_is_bounded() -> None:
    tracker = VerificationTracker(max_observations=3)
    for _ in range(10):
        tracker.record({"outcome": "passed"})
    assert tracker.snapshot() == (3, 0)


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


def test_memory_result_forwards_rendered_ids_to_a_sink_that_supports_it() -> None:
    class SinkWithMemoryBinding(RecordingSink):
        def __init__(self) -> None:
            super().__init__()
            self.recorded_ids: list[str] | None = None

        def record_rendered_memory_ids(self, entry_ids: list[str]) -> None:
            self.recorded_ids = list(entry_ids)

    result = MemoryRetrievalResult(
        rendered_ids=("project-1785082406796413000-b6ecf281",),
        no_match=False,
        no_match_reason="",
        controller_decision={"mode": "standard"},
    )
    sink = SinkWithMemoryBinding()

    emit_memory_result_safely(sink, result)

    assert sink.recorded_ids == ["project-1785082406796413000-b6ecf281"]


def test_memory_result_forwarding_ignores_a_sink_that_raises() -> None:
    class RaisingSink(RecordingSink):
        def record_rendered_memory_ids(self, entry_ids: list[str]) -> None:
            raise RuntimeError("boom")

    result = MemoryRetrievalResult(
        rendered_ids=("project-1785082406796413000-b6ecf281",),
        no_match=False,
        no_match_reason="",
        controller_decision={"mode": "standard"},
    )
    sink = RaisingSink()

    emit_memory_result_safely(sink, result)  # must not raise

    assert [event_type for event_type, _ in sink.events] == [
        "memory.retrieved",
        "memory.rendered",
    ]


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
