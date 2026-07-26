from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import minicode.memory as memory_mod
from minicode.agent_reflection import ReflectionEngine
from minicode.reflection_llm import (
    LLMEligibilityDecision,
    LLMReflectionSynthesizer,
    ReflectionLLMConfig,
    StructuredGenerationResponse,
)
from minicode.reflection_shadow_metrics import (
    ReflectionShadowMetricsRecorder,
    deterministic_shadow_sample,
    load_shadow_metric_records,
    reflection_task_identifier,
    summarize_shadow_metrics,
)


class ScriptedClient:
    def __init__(self, response: StructuredGenerationResponse) -> None:
        self.response = response
        self.call_count = 0

    def generate_json(self, messages, *, timeout_seconds, max_output_tokens):
        del messages, timeout_seconds, max_output_tokens
        self.call_count += 1
        return self.response


def _llm_output() -> str:
    return json.dumps(
        {
            "task_summary": "Preserve parser API",
            "outcome": "success",
            "claims": [
                {
                    "claim_type": "constraint",
                    "semantic_key": "preserve_parse_api",
                    "statement": "Project constraint: Do not change the public parse API.",
                    "evidence_ids": ["event-1"],
                    "epistemic_status": "confirmed",
                    "applies_when": "When modifying parser interfaces.",
                    "limitations": [],
                    "verification_ids": [],
                    "related_error_ids": [],
                    "related_recovery_ids": [],
                }
            ],
        }
    )


def _trace() -> list[dict]:
    return [
        {
            "event_id": "event-1",
            "type": "user_constraint",
            "content": "Do not change the public parse API.",
        },
        {"event_id": "event-2", "type": "task_result", "status": "success"},
    ]


def _engine(
    mode: str,
    client: ScriptedClient,
    *,
    sample_rate: float,
    recorder=None,
) -> ReflectionEngine:
    config = ReflectionLLMConfig(
        mode=mode,  # type: ignore[arg-type]
        shadow_sample_rate=sample_rate,
        selection_strategy="replace",
    )
    return ReflectionEngine(
        llm_config=config,
        llm_synthesizer=LLMReflectionSynthesizer(client, config),
        shadow_metrics_recorder=recorder,
    )


def _comparison(**overrides):
    values = {
        "task_identifier": "0123456789abcdef",
        "eligibility_decision": LLMEligibilityDecision(
            True, ["user_correction"], ["event-1"], "high"
        ),
        "sampled": True,
        "sampled_out": False,
        "llm_called": True,
        "rule_claim_count": 1,
        "llm_claim_count": 2,
        "rule_valid_claim_count": 1,
        "llm_valid_claim_count": 1,
        "rule_value_decision": {"accepted": True},
        "llm_value_decision": {"accepted": False},
        "rule_durable_signals": ["stable_project_constraint"],
        "llm_durable_signals": [],
        "semantic_key_overlap": ["private_semantic_key"],
        "validator_issue_code_counts": {"invalid_evidence_reference": 2},
        "invalid_evidence_references": 2,
        "unsupported_claims": 0,
        "epistemic_mismatches": 0,
        "duplicate_semantic_keys": 0,
        "parse_schema_failure": False,
        "timeout_failure": False,
        "provider_failure": False,
        "fallback_reason": "llm_value_rejected",
        "latency_ms": 125.0,
        "input_tokens": 80,
        "output_tokens": 20,
        "cache_read_tokens": 12,
        "cache_creation_tokens": 3,
        "usage_source": "provider",
        "estimated_cost_usd": 0.001,
        "input_truncated": False,
        "input_safety_status": "safe",
        "output_safety_status": "safe",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_shadow_metrics_config_defaults_are_disabled_and_bounded() -> None:
    config = ReflectionLLMConfig.from_runtime({})

    assert config.shadow_metrics_enabled is False
    assert config.shadow_metrics_path is None
    assert config.shadow_sample_rate == 1.0
    assert config.shadow_max_records == 5_000
    assert config.shadow_max_file_bytes == 5 * 1024 * 1024


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (-1, 0.0),
        (0, 0.0),
        (0.2, 0.2),
        (1, 1.0),
        (5, 1.0),
        ("bad", 1.0),
        (float("nan"), 1.0),
        (float("inf"), 1.0),
    ],
)
def test_shadow_sample_rate_config_is_clamped(raw, expected) -> None:
    config = ReflectionLLMConfig.from_runtime({"reflectionShadowSampleRate": raw})
    assert config.shadow_sample_rate == expected


def test_deterministic_sampling_is_stable() -> None:
    task_id = reflection_task_identifier("stable task")
    assert deterministic_shadow_sample(task_id, 0.37) == deterministic_shadow_sample(
        task_id, 0.37
    )


def test_deterministic_sampling_has_exact_boundaries() -> None:
    assert deterministic_shadow_sample("task", 0.0) is False
    assert deterministic_shadow_sample("task", 1.0) is True
    assert deterministic_shadow_sample("task", -3.0) is False
    assert deterministic_shadow_sample("task", 3.0) is True


def test_shadow_sample_zero_never_calls_provider() -> None:
    client = ScriptedClient(StructuredGenerationResponse(text=_llm_output()))
    result = _engine("llm_shadow", client, sample_rate=0.0).reflect(
        "Preserve parser API", _trace()
    )

    assert client.call_count == 0
    assert result.shadow_comparison is not None
    assert result.shadow_comparison.sampled_out is True
    assert result.shadow_comparison.fallback_reason == "sampled_out"
    assert result.shadow_comparison.provider_failure is False
    assert result.shadow_comparison.timeout_failure is False


def test_sampled_out_is_written_as_non_provider_metrics_event(tmp_path) -> None:
    path = tmp_path / "shadow.jsonl"
    recorder = ReflectionShadowMetricsRecorder(path, model="m", provider="p")
    client = ScriptedClient(StructuredGenerationResponse(text=_llm_output()))

    _engine(
        "llm_shadow", client, sample_rate=0.0, recorder=recorder
    ).reflect("Preserve parser API", _trace())
    record = load_shadow_metric_records(path)[0]

    assert record["sampled"] is False
    assert record["sampled_out"] is True
    assert record["llm_called"] is False
    assert record["fallback_reason"] == "sampled_out"
    assert record["provider_failure"] is False


def test_metrics_distinguish_tool_call_failure(tmp_path) -> None:
    path = tmp_path / "shadow.jsonl"
    ReflectionShadowMetricsRecorder(path, model="m", provider="p").record(
        _comparison(fallback_reason="tool_call_rejected")
    )

    assert load_shadow_metric_records(path)[0]["tool_call_failure"] is True


def test_metrics_record_safe_parser_detail_without_semantic_key(tmp_path) -> None:
    path = tmp_path / "shadow.jsonl"
    ReflectionShadowMetricsRecorder(path, model="m", provider="p").record(
        _comparison(
            fallback_reason="invalid_semantic_key",
            parse_schema_failure=True,
            parser_failure_detail_code="semantic_key_contains_space",
        )
    )
    record = load_shadow_metric_records(path)[0]

    assert (
        record["parser_failure_detail_code"]
        == "semantic_key_contains_space"
    )
    assert "private_semantic_key" not in json.dumps(record)


def test_shadow_sample_one_calls_provider_once() -> None:
    client = ScriptedClient(StructuredGenerationResponse(text=_llm_output()))
    engine = _engine("llm_shadow", client, sample_rate=1.0)
    result = engine.reflect("Preserve parser API", _trace())
    engine.complete_shadow(result)

    assert client.call_count == 1
    assert result.shadow_comparison is not None
    assert result.shadow_comparison.sampled is True


def test_production_llm_mode_ignores_shadow_sample_rate() -> None:
    client = ScriptedClient(StructuredGenerationResponse(text=_llm_output()))
    result = _engine("llm", client, sample_rate=0.0).reflect(
        "Preserve parser API", _trace()
    )

    assert client.call_count == 1
    assert result.synthesis_source == "llm_replace"


def test_metrics_recorder_writes_allowlisted_jsonl(tmp_path) -> None:
    path = tmp_path / "metrics" / "shadow.jsonl"
    recorder = ReflectionShadowMetricsRecorder(
        path, model="deepseek-chat", provider="custom"
    )

    assert recorder.record(_comparison()) is True
    records = load_shadow_metric_records(path)

    assert len(records) == 1
    assert records[0]["mode"] == "llm_shadow"
    assert records[0]["llm_called"] is True
    assert records[0]["usage_source"] == "provider"
    assert records[0]["validator_issue_code_counts"] == {
        "invalid_evidence_reference": 2
    }
    assert "private_semantic_key" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "forbidden",
    [
        "task_description",
        "trace",
        "evidence",
        "claims",
        "path",
        "command",
        "prompt",
        "response",
        "memory",
        "statement",
    ],
)
def test_metrics_record_has_no_raw_content_fields(tmp_path, forbidden: str) -> None:
    path = tmp_path / "shadow.jsonl"
    ReflectionShadowMetricsRecorder(
        path, model="deepseek-chat", provider="custom"
    ).record(_comparison())

    assert forbidden not in json.loads(path.read_text(encoding="utf-8"))


def test_metrics_identifier_secret_is_redacted(tmp_path) -> None:
    path = tmp_path / "shadow.jsonl"
    recorder = ReflectionShadowMetricsRecorder(
        path,
        model="api_key=sk-synthetic-secret-value",
        provider="Bearer synthetic-secret-value",
    )

    assert recorder.record(_comparison()) is True
    text = path.read_text(encoding="utf-8")
    assert "synthetic-secret-value" not in text
    assert json.loads(text)["model"] == "redacted"


@pytest.mark.parametrize("name", ["memory.json", "MEMORY.md", "shadow.log"])
def test_metrics_path_cannot_target_formal_memory_or_unframed_log(
    tmp_path, name: str
) -> None:
    with pytest.raises(ValueError, match="must end in .jsonl"):
        ReflectionShadowMetricsRecorder(
            tmp_path / name, model="m", provider="p"
        )


def test_metrics_rotation_bounds_record_count(tmp_path) -> None:
    path = tmp_path / "shadow.jsonl"
    recorder = ReflectionShadowMetricsRecorder(
        path, model="m", provider="p", max_records=3
    )
    for index in range(8):
        recorder.record(_comparison(task_identifier=f"task-{index}"))

    records = load_shadow_metric_records(path)
    assert len(records) == 3
    assert len(records[-1]["task_identifier"]) == 16


def test_metrics_rotation_bounds_file_bytes(tmp_path) -> None:
    path = tmp_path / "shadow.jsonl"
    recorder = ReflectionShadowMetricsRecorder(
        path, model="m", provider="p", max_records=100, max_file_bytes=4_096
    )
    for index in range(30):
        recorder.record(_comparison(task_identifier=f"task-{index}"))

    assert path.stat().st_size <= 4_096
    assert load_shadow_metric_records(path)


def test_metrics_concurrent_writers_leave_valid_bounded_jsonl(tmp_path) -> None:
    path = tmp_path / "shadow.jsonl"
    recorders = [
        ReflectionShadowMetricsRecorder(
            path, model="m", provider="p", max_records=40
        )
        for _ in range(4)
    ]

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(
                recorders[index % 4].record,
                _comparison(task_identifier=f"task-{index}"),
            )
            for index in range(80)
        ]
        assert all(future.result() for future in futures)

    assert len(load_shadow_metric_records(path)) == 40
    assert all(json.loads(line) for line in path.read_text().splitlines())


def test_metrics_recorder_recovers_from_malformed_existing_line(tmp_path) -> None:
    path = tmp_path / "shadow.jsonl"
    path.write_text("{broken\n", encoding="utf-8")
    recorder = ReflectionShadowMetricsRecorder(path, model="m", provider="p")

    assert recorder.record(_comparison()) is True
    assert len(load_shadow_metric_records(path)) == 1
    assert "broken" not in path.read_text(encoding="utf-8")


def test_metrics_rotation_removes_preexisting_secret_line(tmp_path) -> None:
    path = tmp_path / "shadow.jsonl"
    path.write_text(
        '{"schema_version":1,"model":"api_key=sk-synthetic-secret-value"}\n',
        encoding="utf-8",
    )
    recorder = ReflectionShadowMetricsRecorder(path, model="m", provider="p")

    assert recorder.record(_comparison()) is True
    assert "synthetic-secret-value" not in path.read_text(encoding="utf-8")


def test_metrics_failure_never_breaks_shadow_completion() -> None:
    class BrokenRecorder:
        def record(self, comparison):
            del comparison
            raise OSError("disk unavailable")

    client = ScriptedClient(StructuredGenerationResponse(text=_llm_output()))
    result = _engine(
        "llm_shadow", client, sample_rate=1.0, recorder=BrokenRecorder()
    ).reflect("Preserve parser API", _trace())

    assert result.shadow_comparison is not None
    assert client.call_count == 1


def test_metrics_are_not_written_without_explicit_recorder(tmp_path) -> None:
    client = ScriptedClient(StructuredGenerationResponse(text=_llm_output()))
    _engine("llm_shadow", client, sample_rate=1.0).reflect(
        "Preserve parser API", _trace()
    )
    assert list(tmp_path.rglob("*.jsonl")) == []


def test_metrics_jsonl_is_not_loaded_as_memory(tmp_path, monkeypatch) -> None:
    from minicode.memory import MemoryManager, MemoryScope

    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", tmp_path / "global")
    path = tmp_path / ".mini-code-memory" / "reflection-shadow.jsonl"
    ReflectionShadowMetricsRecorder(path, model="m", provider="p").record(
        _comparison()
    )

    manager = MemoryManager(project_root=tmp_path)

    assert manager.memories[MemoryScope.PROJECT].entries == []
    assert manager.search("stable_project_constraint") == []


def test_curator_does_not_process_or_modify_metrics_jsonl(tmp_path, monkeypatch) -> None:
    from minicode.memory import MemoryManager
    from minicode.memory_curator_agent import MemoryCuratorAgent

    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", tmp_path / "global")
    path = tmp_path / ".mini-code-memory" / "reflection-shadow.jsonl"
    ReflectionShadowMetricsRecorder(path, model="m", provider="p").record(
        _comparison()
    )
    before = path.read_bytes()
    manager = MemoryManager(project_root=tmp_path)

    MemoryCuratorAgent(
        manager,
        workspace_path=str(tmp_path),
        max_insights_per_cycle=0,
    ).run_cycle(force=True)

    assert path.read_bytes() == before


def test_summary_reports_rates_latency_usage_and_cost() -> None:
    records = [
        {
            "eligible": True,
            "sampled": True,
            "llm_called": True,
            "fallback_reason": None,
            "llm_value_accepted": True,
            "rule_value_accepted": True,
            "parse_schema_failure": False,
            "timeout_failure": False,
            "provider_failure": False,
            "latency_ms": 100,
            "input_tokens": 10,
            "output_tokens": 4,
            "usage_source": "provider",
            "estimated_cost_usd": 0.01,
        },
        {
            "eligible": True,
            "sampled": True,
            "llm_called": True,
            "fallback_reason": "provider_timeout",
            "llm_value_accepted": None,
            "rule_value_accepted": True,
            "parse_schema_failure": False,
            "timeout_failure": True,
            "provider_failure": False,
            "latency_ms": 300,
            "input_tokens": 20,
            "output_tokens": 0,
            "usage_source": "estimated",
            "estimated_cost_usd": 0.02,
        },
    ]

    summary = summarize_shadow_metrics(records)

    assert summary["eligibility_rate"] == 1.0
    assert summary["eligible_count"] == 2
    assert summary["sampled_count"] == 2
    assert summary["call_count"] == 2
    assert summary["call_rate"] == 1.0
    assert summary["timeout_rate"] == 0.5
    assert summary["latency_ms"] == {"average": 200.0, "median": 200.0, "p95": 300.0}
    assert summary["tokens"] == {"input": 30, "output": 4}
    assert summary["usage_sources"] == {"estimated": 1, "provider": 1}
    assert summary["estimated_cost_usd"] == pytest.approx(0.03)


def test_summary_cli_skips_malformed_records_and_writes_both_formats(
    tmp_path,
) -> None:
    from scripts.summarize_reflection_shadow import main

    source = tmp_path / "shadow.jsonl"
    recorder = ReflectionShadowMetricsRecorder(source, model="m", provider="p")
    recorder.record(_comparison())
    with source.open("a", encoding="utf-8") as handle:
        handle.write("{broken\n")
    output = tmp_path / "summary.json"
    markdown = tmp_path / "summary.md"

    assert main([str(source), "--output", str(output), "--markdown", str(markdown)]) == 0
    assert json.loads(output.read_text())["record_count"] == 1
    assert "Reflection Shadow Metrics" in markdown.read_text()
