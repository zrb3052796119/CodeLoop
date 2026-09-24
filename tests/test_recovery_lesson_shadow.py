"""The episode learner observes after production selection and persistence."""
from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import minicode.memory as memory_mod
from minicode.agent_budget import AgentTurnBudget
from minicode.agent_reflection import ReflectionEngine
from minicode.config import load_runtime_config
from minicode.cybernetic_orchestrator import CyberneticOrchestrator
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.recovery_lesson import RecoveryLessonAgent, extract_recovery_episodes
from minicode.reflection_evidence import TraceEvidenceExtractor
from minicode.reflection_llm import (
    ReflectionLLMConfig,
    StructuredClientFactoryResult,
    StructuredGenerationResponse,
)
from minicode.reflection_shadow_metrics import (
    ReflectionShadowMetricsRecorder,
    build_recovery_shadow_summary,
    load_shadow_metric_records,
    summarize_shadow_metrics,
)
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken


TASK = "Repair report timeout"


def _trace(*, constraint: bool = False) -> list[dict]:
    events = [{
        "event_id": "constraint", "type": "user_constraint",
        "content": "Do not change the public parse API.",
    }] if constraint else []
    for call_id, timeout, success in (("failed", 1, False), ("retry", 120, True)):
        events.extend([
            {"event_id": f"{call_id}-call", "type": "tool_call", "call_id": call_id,
             "tool_name": "run_command", "input": {
                 "command": "python", "args": ["scripts/report.py"], "timeout": timeout,
             }},
            {"event_id": f"{call_id}-result", "type": "tool_result", "call_id": call_id,
             "tool_name": "run_command", "status": "success" if success else "error",
             "is_error": not success, "output_summary": "report created" if success else
             "Command timed out after 1 seconds (process group terminated)."},
        ])
    events.append({"event_id": "done", "type": "task_result", "status": "success"})
    return events


def _warning_output(trace: list[dict]) -> str:
    evidence = TraceEvidenceExtractor().extract(TASK, trace)
    error = evidence.errors[0]
    return json.dumps({"task_summary": TASK, "outcome": evidence.outcome, "claims": [{
        "claim_type": "warning", "semantic_key": "report_timeout_observation",
        "statement": "The report timed out under a one-second budget; the later retry succeeded.",
        "evidence_ids": list(error.source_event_ids), "epistemic_status": "inferred",
        "applies_when": "When running the report under a short timeout.",
        "limitations": ["One observation does not prove that the budget caused recovery."],
        "verification_ids": [], "related_error_ids": [error.error_id],
        "related_recovery_ids": [], "evidence_quotes": ["Command timed out after 1 seconds"],
    }]})


def _verified_trace() -> list[dict]:
    trace = _trace()
    for item in trace:
        if item.get("type") == "tool_call":
            item["input"] = {"command": "pytest", "args": ["tests/test_auth.py"]}
    trace[1]["output_summary"] = "AssertionError: Expired token was accepted"
    trace[3]["output_summary"] = "auth expiry regression: 1 passed"
    trace[2:2] = [
        {"event_id": "edit-call", "type": "tool_call", "call_id": "edit", "tool_name": "edit_file",
         "files_changed": ["src/auth.py"], "input": {"path": "src/auth.py",
            "old": "token = lookup(token)", "new": "check_expiry(token); token = lookup(token)"}},
        {"event_id": "edit-result", "type": "tool_result", "call_id": "edit", "tool_name": "edit_file",
         "files_changed": ["src/auth.py"], "status": "success", "output_summary": "updated"},
    ]
    return trace


class _Client:
    def __init__(self, response: str | Exception, callback=None):
        self.response = response
        self.callback = callback
        self.calls = 0

    def generate_json(self, messages, *, timeout_seconds, max_output_tokens):
        self.calls += 1
        if self.callback:
            self.callback()
        if isinstance(self.response, Exception):
            raise self.response
        return StructuredGenerationResponse(
            text=self.response, input_tokens=17, output_tokens=9,
            cache_read_tokens=0, cache_creation_tokens=0, usage_source="provider",
        )


def _engine(
    client=None, *, enabled=True, mode="llm_shadow", sample=1.0,
    recorder=None, agent=None,
) -> ReflectionEngine:
    config = ReflectionLLMConfig(
        mode=mode, recovery_lesson_shadow_enabled=enabled, shadow_sample_rate=sample,
    )
    if agent is None and client is not None:
        agent = RecoveryLessonAgent(client, config, max_model_calls=config.recovery_lesson_max_calls)
    # The independent episode branch must also run when the old whole-task
    # client/gate is unavailable. Production remains the rule branch.
    return ReflectionEngine(
        llm_config=config, recovery_lesson_agent=agent, shadow_metrics_recorder=recorder,
    )


@pytest.mark.parametrize("mode,enabled", [("rule", True), ("llm", True), ("llm_shadow", False)])
def test_recovery_shadow_requires_mode_and_explicit_opt_in(mode, enabled) -> None:
    client = _Client(_warning_output(_trace()))
    engine = _engine(client, mode=mode, enabled=enabled)
    reflection = engine.reflect(TASK, _trace())
    engine.complete_shadow(reflection)

    assert client.calls == 0
    assert reflection.recovery_lesson_shadow_summary is None


def test_timeout_candidate_is_independently_eligible_without_rule_recovery() -> None:
    client = _Client(_warning_output(_trace()))
    engine = _engine(client)
    reflection = engine.reflect(TASK, _trace(), defer_shadow=True)
    production = copy.deepcopy(reflection.to_memory_entry())
    assert reflection.task_evidence.recoveries == []
    assert reflection.task_evidence.verification == []

    engine.complete_shadow(reflection)
    summary = reflection.recovery_lesson_shadow_summary

    assert client.calls == 1
    assert summary.eligible and summary.sampled
    assert summary.candidate_episode_count == 1
    assert summary.llm_called == 1
    assert summary.valid_claim_count == 1
    assert summary.observation_count == 1
    assert summary.persistable_claim_count == 0
    assert summary.input_tokens == 17 and summary.output_tokens == 9
    assert summary.estimated_cost_usd is None
    assert reflection.to_memory_entry() == production
    assert reflection.structured_claims == []
    assert reflection.selection_source == "rule"


def test_even_persistable_shadow_lessons_never_enter_production_selection() -> None:
    trace = _verified_trace()
    evidence = TraceEvidenceExtractor().extract(TASK, trace)
    recovery = evidence.recoveries[0]
    error = next(item for item in evidence.errors if item.error_id in recovery.related_error_ids)
    checks = [item for item in evidence.verification if item.result == "passed"]
    response = json.dumps({"task_summary": TASK, "outcome": evidence.outcome, "claims": [{
        "claim_type": "recovery", "semantic_key": "shadow_expiry_before_lookup",
        "statement": "Run expiry validation before token lookup when an expired token is accepted.",
        "epistemic_status": "confirmed", "applies_when": "When validating token expiry.",
        "limitations": ["Only the cited targeted check passed."],
        "evidence_ids": list(dict.fromkeys([*error.source_event_ids, *recovery.event_ids, *checks[0].event_ids])),
        "related_error_ids": [error.error_id], "related_recovery_ids": [recovery.recovery_id],
        "verification_ids": [checks[0].verification_id],
        "evidence_quotes": [error.message, recovery.action],
    }]})
    engine = _engine(_Client(response))
    result = engine.reflect(TASK, trace, defer_shadow=True)
    before = copy.deepcopy(result.to_memory_entry())
    selected_ids = list(result.final_persistable_claim_ids)
    engine.complete_shadow(result)

    assert result.recovery_lesson_shadow_summary.persistable_claim_count == 1, result.recovery_lesson_shadow_summary.to_dict()
    assert result.to_memory_entry() == before
    assert result.final_persistable_claim_ids == selected_ids
    assert all(not item.claim_id.startswith("episode-") for item in result.structured_claims)


def test_sampling_rejects_episode_without_provider_call() -> None:
    client = _Client(_warning_output(_trace()))
    reflection = _engine(client, sample=0.0).reflect(TASK, _trace())

    assert client.calls == 0
    assert reflection.recovery_lesson_shadow_summary.sampled_out
    assert reflection.recovery_lesson_shadow_summary.fallback_reason == "sampled_out"


def test_no_episode_or_unavailable_client_never_calls_provider() -> None:
    client = _Client("unused")
    trace = [{"event_id": "done", "type": "task_result", "status": "success"}]
    no_episode = _engine(client).reflect(TASK, trace)
    unavailable = _engine().reflect(TASK, _trace())

    assert client.calls == 0
    assert no_episode.recovery_lesson_shadow_summary.fallback_reason == "no_recovery_episode"
    assert unavailable.recovery_lesson_shadow_summary.fallback_reason == "model_unavailable"


def test_budget_decline_is_zero_calls_and_preserves_rule_output() -> None:
    trace = _trace(constraint=True)
    client = _Client(_warning_output(trace))
    engine = _engine(client)
    reflection = engine.reflect(TASK, trace, defer_shadow=True)
    before = copy.deepcopy(reflection.to_memory_entry())
    budget = AgentTurnBudget(max_model_calls=1)
    engine.complete_shadow(reflection, agent_budget=budget)

    assert client.calls == 0
    summary = reflection.recovery_lesson_shadow_summary
    assert summary.llm_called == 0
    assert summary.failure_code_counts == {"agent_budget_exhausted": 1}
    assert summary.input_tokens == 0
    assert reflection.to_memory_entry() == before
    assert budget.snapshot().used_model_calls == 0


def test_successful_episode_charges_the_same_shared_budget() -> None:
    client = _Client(_warning_output(_trace()))
    budget = AgentTurnBudget(max_total_tokens=10_000)
    result = _engine(client).reflect(TASK, _trace(), agent_budget=budget)

    assert client.calls == 1
    assert budget.snapshot().used_model_calls == 1
    assert budget.snapshot().used_total_tokens == 26
    assert result.recovery_lesson_shadow_summary.input_tokens == 17
    assert result.recovery_lesson_shadow_summary.output_tokens == 9


@pytest.mark.parametrize("failure", [RuntimeError("private provider detail"), TimeoutError("private timeout detail")])
def test_provider_failure_does_not_change_the_rule_result(failure) -> None:
    client = _Client(failure)
    engine = _engine(client)
    reflection = engine.reflect(TASK, _trace(constraint=True), defer_shadow=True)
    before = copy.deepcopy(reflection.to_memory_entry())
    engine.complete_shadow(reflection)

    assert client.calls == 1
    summary = reflection.recovery_lesson_shadow_summary
    assert summary.llm_called == 1
    assert sum(summary.failure_code_counts.values()) == 1
    assert "private" not in json.dumps(summary.to_dict())
    assert reflection.to_memory_entry() == before


def test_unexpected_shadow_failure_is_isolated_and_not_retried() -> None:
    class BrokenAgent:
        calls = 0

        def attempt_lessons(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("/private/customer/secret.txt")

    agent = BrokenAgent()
    engine = _engine(agent=agent)
    reflection = engine.reflect(TASK, _trace(constraint=True), defer_shadow=True)
    before = copy.deepcopy(reflection.to_memory_entry())
    engine.complete_shadow(reflection)
    engine.complete_shadow(reflection)

    assert agent.calls == 1
    assert reflection.recovery_lesson_shadow_summary.fallback_reason == "recovery_shadow_error"
    assert reflection.to_memory_entry() == before


def test_completion_and_metrics_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "metrics.jsonl"
    recorder = ReflectionShadowMetricsRecorder(path, model="local-model", provider="local")
    client = _Client(_warning_output(_trace()))
    engine = _engine(client, recorder=recorder)
    reflection = engine.reflect(TASK, _trace(), defer_shadow=True)
    engine.complete_shadow(reflection)
    summary = reflection.recovery_lesson_shadow_summary
    engine.complete_shadow(reflection)

    assert client.calls == 1
    assert reflection.recovery_lesson_shadow_summary is summary
    records = load_shadow_metric_records(path)
    assert len([item for item in records if item.get("record_type") == "recovery_lesson_shadow"]) == 1
    aggregate = summarize_shadow_metrics(records)
    assert aggregate["record_count"] == 1  # Old whole-task metrics stay separate.
    assert aggregate["recovery_lesson_shadow"]["call_count"] == 1
    assert aggregate["recovery_lesson_shadow"]["estimated_cost_usd"] is None


@pytest.mark.parametrize("kind", ["cancel", "deadline"])
def test_control_signals_propagate_once_without_provider_call(kind: str) -> None:
    client = _Client(_warning_output(_trace()))
    engine = _engine(client)
    reflection = engine.reflect(TASK, _trace(), defer_shadow=True)
    token = TurnCancellationToken("turn_" + "a" * 32)
    if kind == "cancel":
        token.request()
        expected = TurnCancellationRequested
        kwargs = {"cancellation_token": token}
    else:
        expected = ModelCallDeadlineExceeded
        kwargs = {"deadline_monotonic": time.monotonic() - 1}
    with pytest.raises(expected):
        engine.complete_shadow(reflection, **kwargs)
    engine.complete_shadow(reflection, **kwargs)

    assert client.calls == 0


def test_metrics_failure_is_isolated_and_logs_are_content_free(caplog) -> None:
    class BrokenRecorder:
        def record(self, comparison):
            raise RuntimeError("private old metrics")

        def record_recovery(self, summary):
            raise RuntimeError("private recovery metrics")

    client = _Client(_warning_output(_trace()))
    with caplog.at_level(logging.INFO, logger="minicode.agent_reflection"):
        reflection = _engine(client, recorder=BrokenRecorder()).reflect(TASK, _trace())

    assert reflection.recovery_lesson_shadow_summary.llm_called == 1
    assert TASK not in caplog.text
    assert "scripts/report.py" not in caplog.text
    assert "private" not in caplog.text


def test_pipeline_commits_rule_before_recovery_shadow_and_never_stores_observation(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", tmp_path / "home" / ".mini-code")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory = MemoryManager(project_root=workspace)
    trace = _trace(constraint=True)
    seen_entries = []

    def after_persistence():
        entries = memory.memories[MemoryScope.PROJECT].entries
        seen_entries.append(copy.deepcopy([
            (item.content, item.approval_status, item.metadata) for item in entries
        ]))

    client = _Client(_warning_output(trace), callback=after_persistence)
    pipeline = MemoryPipeline(memory)
    pipeline.initialize(workspace_path=str(workspace), enable_reranker=False, reflection_engine=_engine(client))
    entry_id = pipeline.write(TASK, trace)

    assert entry_id is not None
    assert client.calls == 1
    assert len(seen_entries) == 1
    entries = memory.memories[MemoryScope.PROJECT].entries
    assert seen_entries[0]
    assert seen_entries[0] == [(item.content, item.approval_status, item.metadata) for item in entries]
    assert any("Do not change the public parse API" in item.content and item.approval_status == "approved" for item in entries)
    metadata = json.dumps([item.metadata for item in entries])
    assert "recovery_lesson_shadow" not in metadata
    assert "report_timeout_observation" not in metadata
    assert all(claim["claim_id"].startswith("claim-") for item in entries
               for claim in item.metadata["structured_reflection"]["claims"])


def test_summary_and_metrics_exclude_all_content_and_preserve_unknown_usage(tmp_path) -> None:
    evidence = TraceEvidenceExtractor().extract(TASK, _trace())
    episodes = extract_recovery_episodes(evidence)
    outcomes = [SimpleNamespace(
        attempted=True, model_called=True, failure_code=None, validation_issues=[],
        claim_count=1, latency_ms=12, input_tokens=None, output_tokens=8,
        estimated_cost_usd=None, generated_claims=[{"statement": "PRIVATE CONTENT"}],
    ), SimpleNamespace(
        attempted=False, model_called=False, failure_code="episode_call_limit",
        validation_issues=[], claim_count=0, latency_ms=None,
    )]
    result = SimpleNamespace(outcomes=outcomes, lessons=["PRIVATE CONTENT"], observations=[])
    summary = build_recovery_shadow_summary("PRIVATE TASK", episodes, result, eligible=True, sampled=True)
    assert summary.input_tokens is None
    assert summary.output_tokens == 8
    assert summary.estimated_cost_usd is None
    assert summary.persistable_claim_count == 1
    assert summary.latency_ms == 12
    assert "PRIVATE" not in json.dumps(summary.to_dict())
    recorder = ReflectionShadowMetricsRecorder(tmp_path / "metrics.jsonl", model="local", provider="local", max_records=2)
    for _ in range(3):
        assert recorder.record_recovery(summary)
    records = load_shadow_metric_records(recorder.path)
    assert len(records) == 2
    encoded = recorder.path.read_text()
    assert "PRIVATE" not in encoded
    assert "scripts/report.py" not in encoded
    assert "Command timed out" not in encoded
    assert records[0]["input_tokens"] is None


@pytest.mark.parametrize("raw,expected", [(None, 2), (0, 1), (99, 10), ("bad", 2), (4, 4)])
def test_recovery_shadow_config_defaults_and_bounds(raw, expected) -> None:
    config = ReflectionLLMConfig.from_runtime({"recoveryLessonMaxCalls": raw})
    assert config.recovery_lesson_shadow_enabled is False
    assert config.recovery_lesson_max_calls == expected
    assert ReflectionLLMConfig(recovery_lesson_max_calls=99).recovery_lesson_max_calls == 10


def test_runtime_config_reads_recovery_settings_and_environment(monkeypatch) -> None:
    monkeypatch.setattr("minicode.config.load_effective_settings", lambda cwd=None: {
        "model": "claude-test", "env": {"ANTHROPIC_API_KEY": "test"},
        "recoveryLessonShadowEnabled": True, "recoveryLessonMaxCalls": 3,
    })
    monkeypatch.delenv("MINI_CODE_RECOVERY_LESSON_SHADOW_ENABLED", raising=False)
    monkeypatch.delenv("MINI_CODE_RECOVERY_LESSON_MAX_CALLS", raising=False)
    runtime = load_runtime_config()
    assert runtime["recoveryLessonShadowEnabled"] is True
    assert runtime["recoveryLessonMaxCalls"] == 3
    monkeypatch.setenv("MINI_CODE_RECOVERY_LESSON_SHADOW_ENABLED", "false")
    monkeypatch.setenv("MINI_CODE_RECOVERY_LESSON_MAX_CALLS", "99")
    runtime = load_runtime_config()
    assert runtime["recoveryLessonShadowEnabled"] is False
    assert runtime["recoveryLessonMaxCalls"] == 10


@pytest.mark.parametrize("mode,enabled,created", [("llm_shadow", True, True), ("llm_shadow", False, False), ("llm", True, False)])
def test_orchestrator_reuses_approved_client_only_for_opted_in_shadow(monkeypatch, tmp_path, mode, enabled, created) -> None:
    client = _Client(_warning_output(_trace()))
    monkeypatch.setattr("minicode.reflection_llm.create_structured_generation_client", lambda runtime, config:
        StructuredClientFactoryResult(client=client, unavailable_reason=None, is_remote=False))
    orchestrator = CyberneticOrchestrator()
    orchestrator._workspace = str(tmp_path)
    orchestrator.initialize(MagicMock(model_id="local"), MagicMock(), {
        "reflectionSynthesizerMode": mode, "recoveryLessonShadowEnabled": enabled,
        "recoveryLessonMaxCalls": 3,
    })
    agent = orchestrator.reflection._recovery_lesson_agent
    assert (agent is not None) is created
    if agent is not None:
        assert agent._synthesizer._client is orchestrator.reflection._llm_synthesizer._client is client
    assert client.calls == 0
