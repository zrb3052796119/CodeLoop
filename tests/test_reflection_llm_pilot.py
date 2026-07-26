from __future__ import annotations

import json
from pathlib import Path

import pytest

from minicode.reflection_llm import (
    LLMSynthesisAttempt,
    StructuredGenerationResponse,
    get_reflection_output_schema,
    get_reflection_prompt,
)
from minicode.reflection_replay import (
    SyntheticResponseCaptureWriter,
    load_synthetic_response_capture,
)
from minicode.reflection_shadow_metrics import reflection_task_identifier
from scripts.reflection_llm_evaluator import load_holdout_dataset
from scripts.run_reflection_llm_pilot import (
    deterministic_case_order,
    main,
    render_markdown,
    run_pilot,
)


HOLDOUT = Path(__file__).parent / "fixtures" / "reflection_llm_holdout"


class EnvelopeEchoClient:
    def __init__(self, failures: list[BaseException] | None = None) -> None:
        self.calls = 0
        self.failures = list(failures or [])

    def generate_json(self, messages, *, timeout_seconds, max_output_tokens):
        del timeout_seconds, max_output_tokens
        self.calls += 1
        if self.failures:
            failure = self.failures.pop(0)
            raise failure
        envelope = json.loads(messages[1]["content"])["task_evidence"]
        return StructuredGenerationResponse(
            text=json.dumps(
                {
                    "task_summary": envelope["task_description"],
                    "outcome": envelope["task"]["final_outcome"],
                    "claims": [],
                }
            ),
            input_tokens=123,
            output_tokens=17,
            usage_source="provider",
            latency_ms=9.0,
        )


class FixedClaimClient:
    def __init__(self, *, semantic_key: str = "event_schema_compatibility") -> None:
        self.calls = 0
        self.messages = []
        self.semantic_key = semantic_key

    def generate_json(self, messages, *, timeout_seconds, max_output_tokens):
        del timeout_seconds, max_output_tokens
        self.calls += 1
        self.messages.append(messages)
        envelope = json.loads(messages[1]["content"])["task_evidence"]
        return StructuredGenerationResponse(
            text=json.dumps(
                {
                    "task_summary": envelope["task_description"],
                    "outcome": envelope["task"]["final_outcome"],
                    "claims": [
                        {
                            "claim_type": "constraint",
                            "semantic_key": self.semantic_key,
                            "statement": "The serialized event schema must remain backward compatible.",
                            "evidence_ids": ["event-1"],
                            "epistemic_status": "confirmed",
                            "applies_when": "When changing serialized event storage.",
                            "limitations": [],
                            "verification_ids": [],
                            "related_error_ids": [],
                            "related_recovery_ids": [],
                        }
                    ],
                }
            ),
            input_tokens=321,
            output_tokens=64,
            usage_source="provider",
            latency_ms=11.0,
        )


def test_pilot_case_selection_is_deterministic() -> None:
    cases = load_holdout_dataset(HOLDOUT)
    first = [case["case_id"] for case in deterministic_case_order(cases, "seed")]
    second = [case["case_id"] for case in deterministic_case_order(cases, "seed")]
    other = [case["case_id"] for case in deterministic_case_order(cases, "other")]

    assert first == second
    assert first != other


def test_pilot_dry_run_makes_no_provider_calls() -> None:
    cases = load_holdout_dataset(HOLDOUT)
    client = EnvelopeEchoClient()

    report = run_pilot(
        cases,
        execute=False,
        client=client,
        max_calls=5,
        seed="dry",
        case_ids=None,
        model="deepseek-chat",
        provider="custom",
    )

    assert client.calls == 0
    assert report["pilot_kind"] == "dry_run"
    assert report["call_count"] == 0
    assert report["skip_reasons"] == {"dry_run": len(cases)}


@pytest.mark.parametrize("max_calls", [0, 1, 3, 10])
def test_pilot_never_exceeds_configured_remote_call_cap(max_calls: int) -> None:
    cases = load_holdout_dataset(HOLDOUT)
    client = EnvelopeEchoClient()

    report = run_pilot(
        cases,
        execute=True,
        client=client,
        max_calls=max_calls,
        seed="cap",
        case_ids=None,
        model="deepseek-chat",
        provider="custom",
    )

    assert client.calls <= max_calls
    assert report["call_count"] == client.calls
    if max_calls <= 3:
        assert client.calls == max_calls


@pytest.mark.parametrize("max_calls", [-1, 11, 100])
def test_pilot_rejects_values_beyond_absolute_call_cap(max_calls: int) -> None:
    with pytest.raises(ValueError, match="max_calls"):
        run_pilot(
            [],
            execute=True,
            client=EnvelopeEchoClient(),
            max_calls=max_calls,
            seed="cap",
            case_ids=None,
            model="deepseek-chat",
            provider="custom",
        )


def test_pilot_ignores_scripted_provider_failure_fixture() -> None:
    cases = load_holdout_dataset(HOLDOUT)
    case = next(
        item for item in cases if item["llm_script"]["kind"] == "provider_error"
    )
    client = EnvelopeEchoClient()

    report = run_pilot(
        [case],
        execute=True,
        client=client,
        max_calls=1,
        seed="provider",
        case_ids={case["case_id"]},
        model="deepseek-chat",
        provider="custom",
    )

    assert client.calls == 1
    assert report["cases"][0]["parser_success"] is True
    assert report["fallback_reasons"] != {"provider_error": 1}


def test_pilot_continues_after_provider_failure() -> None:
    cases = load_holdout_dataset(HOLDOUT)[:4]
    client = EnvelopeEchoClient([RuntimeError("provider down")])

    report = run_pilot(
        cases,
        execute=True,
        client=client,
        max_calls=2,
        seed="continue",
        case_ids=None,
        model="deepseek-chat",
        provider="custom",
    )

    assert client.calls == 2
    assert report["fallback_reasons"].get("provider_error") == 1
    assert sum(case["parser_success"] for case in report["cases"]) == 1


def test_pilot_report_contains_no_raw_holdout_trace_or_script() -> None:
    cases = load_holdout_dataset(HOLDOUT)
    report = run_pilot(
        cases[:2],
        execute=True,
        client=EnvelopeEchoClient(),
        max_calls=2,
        seed="privacy",
        case_ids=None,
        model="deepseek-chat",
        provider="custom",
    )
    serialized = json.dumps(report, ensure_ascii=False)

    assert "llm_script" not in serialized
    assert "task_description" not in serialized
    assert "execution_trace" not in serialized
    for case in cases[:2]:
        for event in case["trace"]:
            content = event.get("content") or event.get("message")
            if content:
                assert str(content) not in serialized


def test_pilot_reports_real_usage_source_and_tokens() -> None:
    case = next(
        item
        for item in load_holdout_dataset(HOLDOUT)
        if item["case_id"] == "holdout-project-constraint-005"
    )
    report = run_pilot(
        [case],
        execute=True,
        client=EnvelopeEchoClient(),
        max_calls=1,
        seed="usage",
        case_ids=None,
        model="deepseek-chat",
        provider="custom",
    )

    assert report["usage_sources"] == {"provider": 1}
    assert report["tokens"]["input"] == 123
    assert report["tokens"]["output"] == 17


def test_pilot_report_redacts_secret_like_model_and_seed_identifiers() -> None:
    report = run_pilot(
        [],
        execute=False,
        client=None,
        max_calls=0,
        seed="token=synthetic-secret-value",
        case_ids=None,
        model="api_key=sk-synthetic-secret-value",
        provider="Bearer synthetic-secret-value",
    )

    serialized = json.dumps(report)
    assert "synthetic-secret-value" not in serialized
    assert report["model"] == "redacted"
    assert report["provider"] == "redacted"
    assert report["seed"] == "redacted"


def test_pilot_optional_persistence_validation_is_temporary() -> None:
    case = load_holdout_dataset(HOLDOUT)[0]
    report = run_pilot(
        [case],
        execute=False,
        client=None,
        max_calls=0,
        seed="temporary",
        case_ids=None,
        model="none",
        provider="none",
        validate_persistence=True,
    )

    validation = report["temporary_persistence_validation"]
    assert validation["temporary_only"] is True
    assert validation["attempted"] == 1


def test_pilot_cli_defaults_to_dry_run_and_writes_reports(tmp_path) -> None:
    output = tmp_path / "pilot.json"
    markdown = tmp_path / "pilot.md"

    assert main(
        [
            "--dataset",
            str(HOLDOUT),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ]
    ) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["pilot_kind"] == "dry_run"
    assert report["call_count"] == 0
    assert "Reflection LLM Real-Provider Pilot" in markdown.read_text(encoding="utf-8")
    assert list(tmp_path.glob("*.jsonl")) == []


def test_pilot_cli_requires_explicit_remote_opt_in(tmp_path) -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "--execute",
                "--output",
                str(tmp_path / "pilot.json"),
                "--markdown",
                str(tmp_path / "pilot.md"),
            ]
        )


def test_pilot_cli_rejects_more_than_ten_calls_before_execution() -> None:
    with pytest.raises(SystemExit):
        main(["--execute", "--allow-remote", "--max-calls", "11"])


@pytest.mark.parametrize(
    "prompt_version",
    ["baseline", "calibrated", "calibrated_verbose", "calibrated_compact"],
)
def test_pilot_uses_explicit_prompt_and_schema_version(prompt_version: str) -> None:
    case = next(
        item
        for item in load_holdout_dataset(HOLDOUT)
        if item["case_id"] == "holdout-project-constraint-005"
    )
    client = FixedClaimClient()

    report = run_pilot(
        [case],
        execute=True,
        client=client,
        max_calls=1,
        seed="prompt-version",
        case_ids=None,
        model="deepseek-chat",
        provider="custom",
        prompt_version=prompt_version,
    )

    assert report["prompt_version"] == prompt_version
    assert client.messages[0][0]["content"] == get_reflection_prompt(prompt_version)
    payload = json.loads(client.messages[0][1]["content"])
    assert payload["required_output_schema"] == get_reflection_output_schema(
        prompt_version
    )


def test_pilot_reports_structural_claim_diagnostics_without_raw_key() -> None:
    case = next(
        item
        for item in load_holdout_dataset(HOLDOUT)
        if item["case_id"] == "holdout-project-constraint-005"
    )
    client = FixedClaimClient()
    report = run_pilot(
        [case],
        execute=True,
        client=client,
        max_calls=1,
        seed="structure",
        case_ids=None,
        model="deepseek-chat",
        provider="custom",
    )
    record = report["cases"][0]

    assert record["candidate_claim_count"] == 1
    assert record["candidate_claim_type_counts"] == {"constraint": 1}
    assert record["candidate_claim_types"] == ["constraint"]
    assert record["candidate_epistemic_status_counts"] == {"confirmed": 1}
    assert record["candidate_reference_counts"]["evidence"] == 1
    assert record["valid_claim_count"] == 1
    assert record["rejected_claim_count"] == 0
    assert record["accepted_claim_type_counts"] == {"constraint": 1}
    assert record["accepted_claim_types"] == ["constraint"]
    assert record["value_accepted"] is True
    assert record["matched_expected_claim_count"] == record["matched_claim_count"]
    assert len(record["candidate_semantic_key_hashes"][0]) == 16
    assert "event_schema_compatibility" not in json.dumps(report)

    markdown = render_markdown(report)
    assert "Valid/rejected types" in markdown
    assert "References" in markdown
    assert "stable_project_constraint" in markdown


def test_pilot_reports_safe_semantic_key_failure_detail_only() -> None:
    case = next(
        item
        for item in load_holdout_dataset(HOLDOUT)
        if item["case_id"] == "holdout-project-constraint-005"
    )
    report = run_pilot(
        [case],
        execute=True,
        client=FixedClaimClient(semantic_key="event schema compatibility"),
        max_calls=1,
        seed="invalid-key",
        case_ids=None,
        model="deepseek-chat",
        provider="custom",
    )
    record = report["cases"][0]

    assert record["parser_failure_code"] == "invalid_semantic_key"
    assert (
        record["parser_failure_detail_code"]
        == "semantic_key_contains_space"
    )
    assert report["parser_failure_detail_codes"] == {
        "semantic_key_contains_space": 1
    }
    assert "event schema compatibility" not in json.dumps(report)


def test_pilot_capture_records_only_the_observed_synthetic_response(
    tmp_path: Path,
) -> None:
    case = next(
        item
        for item in load_holdout_dataset(HOLDOUT)
        if item["case_id"] == "holdout-project-constraint-005"
    )
    writer = SyntheticResponseCaptureWriter(
        tmp_path / "responses.jsonl",
        dataset_root=HOLDOUT,
    )
    report = run_pilot(
        [case],
        execute=True,
        client=FixedClaimClient(),
        max_calls=1,
        seed="capture",
        case_ids=None,
        model="deepseek-chat",
        provider="custom",
        prompt_version="baseline",
        capture_writer=writer,
    )
    records = load_synthetic_response_capture(writer.path)

    assert report["capture_record_count"] == 1
    assert len(records) == 1
    assert records[0]["case_id"] == case["case_id"]
    assert records[0]["prompt_version"] == "baseline"
    assert records[0]["task_identifier"] == reflection_task_identifier(
        case["task_description"]
    )


def test_pilot_cli_replays_capture_without_loading_provider_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = next(
        item
        for item in load_holdout_dataset(HOLDOUT)
        if item["case_id"] == "holdout-project-constraint-005"
    )
    response = FixedClaimClient().generate_json(
        [
            {"role": "system", "content": "unused"},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task_evidence": {
                            "task_description": case["task_description"],
                            "task": {"final_outcome": "success"},
                        }
                    }
                ),
            },
        ],
        timeout_seconds=1,
        max_output_tokens=100,
    )
    capture = tmp_path / "capture.jsonl"
    writer = SyntheticResponseCaptureWriter(capture, dataset_root=HOLDOUT)
    assert writer.record(
        case_id=case["case_id"],
        task_identifier=reflection_task_identifier(case["task_description"]),
        model="deepseek-chat",
        provider="custom",
        prompt_version="baseline",
        response=response,
        attempt=LLMSynthesisAttempt(success=True),
    )
    import minicode.config

    monkeypatch.setattr(
        minicode.config,
        "load_runtime_config",
        lambda *_args, **_kwargs: pytest.fail("provider config must not load"),
    )
    output = tmp_path / "replay.json"
    markdown = tmp_path / "replay.md"

    assert main(
        [
            "--dataset",
            str(HOLDOUT),
            "--replay-responses",
            str(capture),
            "--max-calls",
            "1",
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ]
    ) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["pilot_kind"] == "synthetic_replay"
    assert report["call_count"] == 1
    assert report["prompt_version"] == "baseline"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--capture-synthetic-responses", "--capture-path", "capture.jsonl"],
        ["--capture-path", "capture.jsonl"],
        ["--replay-responses", "capture.jsonl", "--execute", "--allow-remote"],
        ["--replay-responses", "capture.jsonl", "--validate-persistence"],
    ],
)
def test_pilot_cli_rejects_unsafe_capture_or_replay_combinations(
    arguments: list[str],
) -> None:
    with pytest.raises(SystemExit):
        main(arguments)
