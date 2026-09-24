"""A provider timeout ends one reflection call, not the parent or next episode."""

from __future__ import annotations

import json

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.recovery_lesson import RecoveryLessonAgent
from minicode.reflection_evidence import TraceEvidenceExtractor
from minicode.reflection_llm import (
    ModelAdapterStructuredGenerationClient,
    ReflectionLLMConfig,
)
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from minicode.types import AgentStep, ModelUsage
from scripts import evaluate_recovery_lesson_shadow as shadow


class _Clock:
    now = 100.0

    def monotonic(self) -> float:
        return self.now


class _ControlledAdapter:
    runtime = {"model": "deadline-test"}

    def __init__(self, clock: _Clock, behaviors: list[str]) -> None:
        self.clock = clock
        self.behaviors = list(behaviors)
        self.calls = 0
        self._thinking_blocks = ["parent-only-state"]

    def next(self, messages, *, cancellation_token=None, deadline_monotonic=None):
        assert self._thinking_blocks == []
        self._thinking_blocks = ["request-only-state"]
        self.calls += 1
        behavior = self.behaviors.pop(0)
        if behavior == "deadline":
            # The real call_model_next post-call checkpoint detects this.
            self.clock.now = deadline_monotonic + 0.001
        elif behavior == "past_parent":
            self.clock.now += 400
        elif behavior == "early_deadline":
            raise ModelCallDeadlineExceeded("bounded provider stopped")
        elif behavior == "cancel":
            cancellation_token.request()
            raise ModelCallDeadlineExceeded("provider stopped during cancellation")
        try:
            task = json.loads(messages[-1]["content"])["task_evidence"]
            content = json.dumps({
                "task_summary": task["task_description"],
                "outcome": task["task"]["final_outcome"],
                "claims": [],
            })
        except (KeyError, json.JSONDecodeError):
            content = "{}"
        return AgentStep(
            type="assistant", kind="final", content=content,
            usage=ModelUsage(input_tokens=30, output_tokens=10, source="provider"),
        )


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    # reflection_llm and model_call_control intentionally share the same
    # monotonic source. No network call or wall-clock sleep is involved.
    monkeypatch.setattr("minicode.reflection_llm.time.monotonic", clock.monotonic)
    return clock


@pytest.mark.parametrize("parent_deadline", [None, 300.0])
def test_request_local_deadline_is_a_provider_timeout(clock, parent_deadline) -> None:
    adapter = _ControlledAdapter(clock, ["deadline"])
    client = ModelAdapterStructuredGenerationClient(adapter)

    with pytest.raises(TimeoutError) as failure:
        client.generate_json(
            [{"role": "user", "content": "{}"}], timeout_seconds=15,
            max_output_tokens=100, deadline_monotonic=parent_deadline,
        )

    assert type(failure.value) is TimeoutError
    assert adapter.calls == 1
    assert adapter._thinking_blocks == ["parent-only-state"]


@pytest.mark.parametrize(
    "behavior,parent_deadline",
    [("deadline", 105.0), ("early_deadline", 105.0), ("past_parent", 300.0)],
)
def test_parent_deadline_remains_a_control_signal(clock, behavior, parent_deadline) -> None:
    adapter = _ControlledAdapter(clock, [behavior])

    with pytest.raises(ModelCallDeadlineExceeded):
        ModelAdapterStructuredGenerationClient(adapter).generate_json(
            [{"role": "user", "content": "{}"}], timeout_seconds=15,
            max_output_tokens=100, deadline_monotonic=parent_deadline,
        )

    assert adapter.calls == 1
    assert adapter._thinking_blocks == ["parent-only-state"]


def _multi_case():
    manifest, cases = shadow.load_dataset(shadow.DEFAULT_DATASET)
    case = next(case for case in cases if case["category"] == "multi_episode")
    return manifest, cases, case


def test_local_timeout_records_one_failed_call_and_continues_next_episode(clock) -> None:
    _, _, case = _multi_case()
    evidence = TraceEvidenceExtractor().extract(case["task_description"], case["trace"])
    adapter = _ControlledAdapter(clock, ["deadline", "success"])
    agent = RecoveryLessonAgent(
        ModelAdapterStructuredGenerationClient(adapter), ReflectionLLMConfig(), max_model_calls=2,
    )
    budget = AgentTurnBudget(max_model_calls=3)

    result = agent.attempt_lessons(case["task_description"], evidence, agent_budget=budget)

    assert adapter.calls == result.llm_called == 2
    assert [outcome.failure_code for outcome in result.outcomes] == ["provider_timeout", None]
    assert [outcome.model_called for outcome in result.outcomes] == [True, True]
    assert result.outcomes[0].input_tokens is None
    assert result.outcomes[0].estimated_cost_usd is None
    assert result.outcomes[1].input_tokens == 30
    assert budget.snapshot().used_model_calls == 2
    assert adapter._thinking_blocks == ["parent-only-state"]


@pytest.mark.parametrize("control", ["deadline", "cancel"])
def test_parent_control_aborts_remaining_episodes(clock, control) -> None:
    _, _, case = _multi_case()
    evidence = TraceEvidenceExtractor().extract(case["task_description"], case["trace"])
    adapter = _ControlledAdapter(clock, [control, "success"])
    agent = RecoveryLessonAgent(ModelAdapterStructuredGenerationClient(adapter), ReflectionLLMConfig())
    token = TurnCancellationToken("turn_" + "a" * 32)
    kwargs = {"cancellation_token": token}
    if control == "deadline":
        kwargs["deadline_monotonic"] = 105.0
    expected = ModelCallDeadlineExceeded if control == "deadline" else TurnCancellationRequested

    with pytest.raises(expected):
        agent.attempt_lessons(case["task_description"], evidence, **kwargs)

    assert adapter.calls == 1
    assert adapter._thinking_blocks == ["parent-only-state"]


def test_evaluator_keeps_all_episode_outcomes_after_local_timeout(clock) -> None:
    manifest, cases, multi = _multi_case()
    single = next(case for case in cases if case["expected_episode_count"] == 1)
    adapter = _ControlledAdapter(clock, ["deadline", "success", "success"])

    report = shadow.evaluate_cases(
        [multi, single], manifest=manifest, config=ReflectionLLMConfig(),
        execute=True, client=ModelAdapterStructuredGenerationClient(adapter), max_calls=3,
    )

    assert adapter.calls == report["summary"]["provider_calls"] == 3
    episodes = [episode for case in report["cases"] for episode in case["episodes"]]
    assert [episode["outcome"]["failure_code"] for episode in episodes] == [
        "provider_timeout", None, None,
    ]
    assert all(episode["outcome"]["outcome_available"] for episode in episodes)
    assert all("execution_failure" not in case for case in report["cases"])
    assert report["summary"]["provider_failure_count"] == 1
    assert report["summary"]["usage"]["input_tokens"]["total"] is None
    assert report["summary"]["usage"]["input_tokens"]["known_subtotal"] == 60
