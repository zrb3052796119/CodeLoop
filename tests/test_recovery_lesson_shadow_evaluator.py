"""Shadow evaluation budgets and metrics, without any provider or memory I/O."""
from __future__ import annotations

import json

import pytest

from minicode.reflection_llm import (
    ReflectionLLMConfig, StructuredClientFactoryResult, StructuredGenerationResponse,
)
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.turn_cancellation import TurnCancellationRequested
from scripts import evaluate_recovery_lesson_shadow as shadow


def _dataset():
    return shadow.load_dataset(shadow.DEFAULT_DATASET)


def _config(**kwargs):
    return ReflectionLLMConfig(mode="llm_shadow", model="scripted-contract", **kwargs)


class EmptyClient:
    def __init__(self):
        self.calls = 0

    def generate_json(self, messages, *, timeout_seconds, max_output_tokens):
        self.calls += 1
        evidence = json.loads(messages[-1]["content"])["task_evidence"]
        return StructuredGenerationResponse(text=json.dumps({
            "task_summary": evidence["task_description"], "outcome": evidence["task"]["final_outcome"],
            "claims": [],
        }))


def test_offline_dataset_runs_ten_scripted_episodes_without_runtime_or_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline mode must not resolve runtime clients")

    monkeypatch.setattr(shadow, "load_runtime_config", forbidden)
    monkeypatch.setattr(shadow, "create_structured_generation_client", forbidden)
    manifest, cases = _dataset()
    report = shadow.evaluate_cases(cases, manifest=manifest, config=_config())

    assert report["mode"] == "scripted_contract"
    assert report["memory_writes"] is False
    summary = report["summary"]
    assert summary["episode_count"] == 10
    assert summary["scripted_calls"] == 10
    assert summary["provider_calls"] == 0
    assert summary["zero_episode_precheck_count"] == 2
    assert summary["precheck_mismatch_count"] == 0
    assert summary["structurally_admitted_lesson_count"] == 6
    assert summary["same_episode_rule_lesson_count"] == 6
    assert summary["candidate_confirmed_recovery_count"] == 0
    assert summary["pipeline_completed"] is True
    assert report["semantic_quality"]["verdict"] == "not_established"
    multi = next(case for case in report["cases"] if case["category"] == "multi_episode")
    assert len(multi["episodes"]) == 2
    lesson_ids = [claim["claim_id"] for item in multi["episodes"] for claim in item["outcome"]["lessons"]]
    assert len(set(lesson_ids)) == 2
    assert all(item["semantic_review"]["status"] == "not_model_quality_evidence" for item in multi["episodes"])


@pytest.mark.parametrize("limit", [0, 1, 3])
def test_global_budget_counts_across_cases_and_skips_without_a_provider_call(limit):
    manifest, cases = _dataset()
    client = EmptyClient()
    report = shadow.evaluate_cases(cases, manifest=manifest, config=_config(),
                                  execute=True, client=client, max_calls=limit)

    assert client.calls == limit
    assert report["summary"]["provider_calls"] == limit
    assert report["summary"]["scripted_calls"] == 0
    assert report["summary"]["budget_skipped_episode_count"] == 10 - limit
    assert report["summary"]["provider_failure_count"] == 0
    assert report["summary"]["pipeline_completed"] is False
    for metric in report["summary"]["usage"].values():
        assert metric["total"] is None
        assert metric["known_subtotal"] is None
        assert metric["unknown_count"] == limit


def test_provider_failures_consume_calls_without_turning_unknown_usage_into_zero():
    manifest, cases = _dataset()

    class PartialUsageClient(EmptyClient):
        def generate_json(self, messages, *, timeout_seconds, max_output_tokens):
            if self.calls:
                self.calls += 1
                raise TimeoutError("temporary provider failure")
            response = super().generate_json(messages, timeout_seconds=timeout_seconds,
                                             max_output_tokens=max_output_tokens)
            return StructuredGenerationResponse(text=response.text, input_tokens=40,
                                                output_tokens=8, estimated_cost_usd=0.001,
                                                usage_source="provider")

    client = PartialUsageClient()
    report = shadow.evaluate_cases(cases[:2], manifest=manifest, config=_config(),
                                  execute=True, client=client, max_calls=2)
    assert client.calls == report["summary"]["provider_calls"] == 2
    assert report["summary"]["provider_failure_count"] == 1
    assert report["summary"]["pipeline_completed"] is False
    usage = report["summary"]["usage"]["input_tokens"]
    assert usage == {"total": None, "known_subtotal": 40, "known_count": 1, "unknown_count": 1}
    assert report["summary"]["usage"]["estimated_cost_usd"]["total"] is None
    assert report["summary"]["usage"]["estimated_cost_usd"]["known_subtotal"] == 0.001


def test_malformed_response_remains_bounded_redacted_and_reviewable():
    manifest, cases = _dataset()

    class MalformedClient:
        def generate_json(self, messages, **kwargs):
            return StructuredGenerationResponse(text="sk-abcdefghijklmnopqrstuvwxyz0123456789 " + "中" * 100)

    report = shadow.evaluate_cases(cases[:1], manifest=manifest,
                                  config=_config(max_output_bytes=90),
                                  execute=True, client=MalformedClient(), max_calls=1)
    episode = report["cases"][0]["episodes"][0]
    assert episode["outcome"]["generated_claims"] == []
    assert episode["outcome"]["failure_code"] is not None
    physical = episode["outcome"]["physical_call"]
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in physical["raw_response_text"]
    assert len(physical["raw_response_text"].encode("utf-8")) <= 90
    assert physical["raw_response_truncated"] is True
    assert physical["raw_response_sanitized"] is True
    assert report["summary"]["pipeline_completed"] is False
    assert episode["semantic_review"]["status"] == "pending"


def test_execute_cli_preserves_disabled_remote_policy(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(shadow, "load_runtime_config", lambda _: {
        "model": "glm-5.3-flash", "reflectionModel": "glm-5.3-flash",
        "allowRemoteReflectionModel": False,
    })

    def factory(runtime, config):
        captured["allow_remote"] = config.allow_remote_model
        return StructuredClientFactoryResult(None, "remote_model_not_allowed", True)

    monkeypatch.setattr(shadow, "create_structured_generation_client", factory)
    output = tmp_path / "report.json"
    with pytest.raises(SystemExit) as exc:
        shadow.main(["--execute", "--output", str(output)])
    assert exc.value.code == 2
    assert captured["allow_remote"] is False
    assert not output.exists()


def test_offline_cli_does_not_load_credentials_and_respects_case_selection(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline CLI must not load runtime or credentials")

    monkeypatch.setattr(shadow, "load_runtime_config", forbidden)
    output = tmp_path / "report.json"
    assert shadow.main(["--case-id", "auth-expiry", "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert [item["case_id"] for item in report["cases"]] == ["auth-expiry"]
    assert report["summary"]["scripted_calls"] == 1
    assert report["summary"]["provider_calls"] == 0


def test_dataset_contract_drift_is_not_sent_to_a_model():
    manifest, cases = _dataset()
    altered = {**cases[0], "expected_episode_count": 2}
    client = EmptyClient()
    report = shadow.evaluate_cases([altered], manifest=manifest, config=_config(),
                                  execute=True, client=client)
    assert client.calls == 0
    assert report["summary"]["precheck_mismatch_count"] == 1
    assert report["summary"]["pipeline_completed"] is False


def test_provider_local_deadline_retains_partial_case_and_continues_next_case():
    manifest, cases = _dataset()
    multi = next(case for case in cases if case["category"] == "multi_episode")

    class DeadlineClient(EmptyClient):
        def generate_json(self, messages, *, timeout_seconds, max_output_tokens):
            if self.calls == 1:
                self.calls += 1
                raise ModelCallDeadlineExceeded("provider local deadline")
            return super().generate_json(messages, timeout_seconds=timeout_seconds,
                                         max_output_tokens=max_output_tokens)

    report = shadow.evaluate_cases([multi, cases[0]], manifest=manifest, config=_config(),
                                  execute=True, client=DeadlineClient(), max_calls=3)
    first, second = report["cases"][0]["episodes"]
    assert first["outcome"]["failure_code"] == "outcome_unavailable_case_interrupted"
    assert first["outcome"]["outcome_available"] is False
    assert first["outcome"]["generated_claims"] == []
    assert first["outcome"]["physical_call"]["raw_response_text"]
    assert second["outcome"]["failure_code"] == "provider_deadline_exceeded"
    assert report["cases"][1]["episodes"][0]["outcome"]["failure_code"] is None
    assert report["summary"]["provider_calls"] == 3
    assert report["summary"]["provider_failure_count"] == 1
    assert report["summary"]["unavailable_outcome_count"] == 2
    assert report["summary"]["pipeline_completed"] is False
    record = first["outcome"]["physical_call"]
    assert len(record["messages_sha256"]) == 64
    assert record["request_parameters"] == {"timeout_seconds": 15.0, "max_output_tokens": 1200}
    assert len(report["dataset_content_sha256"]) == 64


def test_cancellation_returns_partial_report_without_starting_later_cases():
    manifest, cases = _dataset()

    class CancelClient:
        def generate_json(self, messages, **kwargs):
            raise TurnCancellationRequested("user cancelled")

    report = shadow.evaluate_cases(cases[:2], manifest=manifest, config=_config(),
                                  execute=True, client=CancelClient(), max_calls=2)
    assert report["interrupted"] is True
    assert report["summary"]["provider_calls"] == 1
    assert report["summary"]["processed_case_count"] == 1
    assert report["summary"]["unprocessed_case_count"] == 1
    assert report["unprocessed_case_ids"] == [cases[1]["case_id"]]
    assert report["summary"]["pipeline_completed"] is False
