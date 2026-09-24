from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

import minicode.memory_hybrid_runtime as hybrid_runtime
import scripts.memory_hybrid_resolver_pressure_evaluator as evaluator
from minicode.evaluation_one_shot import EvaluationOneShotReceipt
from minicode.types import AgentStep, StepDiagnostics
from scripts.memory_hybrid_qwen_v2_evaluator import (
    _write_json_exclusive,
    build_resolver_pressure_receipt,
    validate_resolver_pressure_report,
)


def _one_shot_claim() -> dict[str, object]:
    return {
        "schema_version": 1,
        "dataset_id": evaluator.DATASET_ID,
        "holdout_sha256": evaluator.EXPECTED_HOLDOUT_SHA256,
        "output_artifact": evaluator.HYBRID_RESOLVER_PRESSURE_ARTIFACT,
        "marker_path": evaluator.ONE_SHOT_MARKER_NAME,
        "claimed_at_utc": "2026-09-03T00:00:00Z",
    }


def test_main_validates_holdout_before_claiming_one_shot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holdout = tmp_path / "holdout.json"
    output = tmp_path / "report.json"
    order: list[str] = []
    monkeypatch.setattr(evaluator, "DEFAULT_HOLDOUT", holdout)
    monkeypatch.setattr(evaluator, "DEFAULT_OUTPUT", output)
    monkeypatch.setattr(evaluator, "implementation_freeze", lambda: {})
    monkeypatch.setattr(
        sys,
        "argv",
        ["resolver-pressure", "--acknowledge-one-shot"],
    )

    def reject_holdout(_path: Path) -> None:
        order.append("holdout_preflight")
        raise ValueError("synthetic schema failure")

    def unexpected_claim(*_args, **_kwargs) -> None:
        order.append("one_shot_claim")
        raise AssertionError("invalid holdout must not consume one-shot")

    monkeypatch.setattr(evaluator, "load_frozen_holdout", reject_holdout)
    monkeypatch.setattr(evaluator, "claim_evaluation_run", unexpected_claim)

    with pytest.raises(ValueError, match="synthetic schema failure"):
        evaluator.main()

    assert order == ["holdout_preflight"]


def test_main_canary_failure_does_not_claim_or_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holdout_path = tmp_path / "holdout.json"
    output = tmp_path / "report.json"
    holdout = {
        "dataset_id": evaluator.DATASET_ID,
        "holdout_sha256": evaluator.EXPECTED_HOLDOUT_SHA256,
        "manifest_sha256": evaluator.EXPECTED_MANIFEST_SHA256,
        "case_count": evaluator.EXPECTED_CASE_COUNT,
    }
    order: list[str] = []
    monkeypatch.setattr(evaluator, "DEFAULT_HOLDOUT", holdout_path)
    monkeypatch.setattr(evaluator, "DEFAULT_OUTPUT", output)
    monkeypatch.setattr(evaluator, "implementation_freeze", lambda: {})
    monkeypatch.setattr(
        evaluator,
        "load_frozen_holdout",
        lambda _path: (evaluator.validate_dataset(_dataset()), holdout),
    )
    monkeypatch.setattr(
        evaluator,
        "create_exact_subagent_verifier",
        lambda *_args, **_kwargs: object(),
    )

    def reject_canary(_model) -> None:
        order.append("route_canary")
        raise evaluator.ResolverRouteCanaryError("provider_or_transport_error")

    def unexpected_claim(*_args, **_kwargs) -> None:
        order.append("one_shot_claim")
        raise AssertionError("failed canary must not consume one-shot")

    monkeypatch.setattr(evaluator, "run_fixed_resolver_route_canary", reject_canary)
    monkeypatch.setattr(evaluator, "claim_evaluation_run", unexpected_claim)
    monkeypatch.setattr("minicode.config.load_runtime_config", lambda _root: {})
    monkeypatch.setattr("minicode.env_file.apply_env_file", lambda _paths: None)
    monkeypatch.setattr(
        evaluator,
        "build_hybrid_promotion_verifier_runtime",
        lambda runtime: runtime,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["resolver-pressure", "--acknowledge-one-shot"],
    )

    with pytest.raises(SystemExit, match="provider_or_transport_error"):
        evaluator.main()

    assert order == ["route_canary"]
    assert not output.exists()


def test_post_claim_failure_report_is_content_free_and_non_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freeze = {"schema_version": "test", "files": {}, "fingerprint": "a" * 64}
    monkeypatch.setattr(evaluator, "implementation_freeze", lambda: freeze)
    holdout = {
        "dataset_id": evaluator.DATASET_ID,
        "holdout_sha256": evaluator.EXPECTED_HOLDOUT_SHA256,
        "manifest_sha256": evaluator.EXPECTED_MANIFEST_SHA256,
        "case_count": evaluator.EXPECTED_CASE_COUNT,
    }

    report = evaluator.build_post_claim_failure_report(
        holdout=holdout,
        freeze=freeze,
        one_shot_claim=_one_shot_claim(),
        failure_stage="evaluate_resolver",
        failure_kind="provider_or_transport_error",
    )

    assert evaluator.validate_post_claim_failure_report(report) is report
    assert report["production_authority"] is False
    assert report["acceptance_gate"] == {"passed": False}
    serialized = json.dumps(report, ensure_ascii=False)
    assert "synthetic query object" not in serialized
    assert "synthetic memory object" not in serialized


def test_main_persists_failure_after_one_shot_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holdout_path = tmp_path / "holdout.json"
    output = tmp_path / "report.json"
    freeze = {"schema_version": "test", "files": {}, "fingerprint": "a" * 64}
    holdout = {
        "dataset_id": evaluator.DATASET_ID,
        "holdout_sha256": evaluator.EXPECTED_HOLDOUT_SHA256,
        "manifest_sha256": evaluator.EXPECTED_MANIFEST_SHA256,
        "case_count": evaluator.EXPECTED_CASE_COUNT,
    }
    monkeypatch.setattr(evaluator, "DEFAULT_HOLDOUT", holdout_path)
    monkeypatch.setattr(evaluator, "DEFAULT_OUTPUT", output)
    monkeypatch.setattr(evaluator, "implementation_freeze", lambda: freeze)
    monkeypatch.setattr(
        evaluator,
        "load_frozen_holdout",
        lambda _path: (evaluator.validate_dataset(_dataset()), holdout),
    )
    models: list[_RejectingModel] = []

    def fresh_model(*_args, **_kwargs):
        model = _RejectingModel()
        models.append(model)
        return model

    monkeypatch.setattr(
        evaluator,
        "create_exact_subagent_verifier",
        fresh_model,
    )
    monkeypatch.setattr(
        evaluator,
        "claim_evaluation_run",
        lambda *_args, **_kwargs: EvaluationOneShotReceipt(**_one_shot_claim()),
    )
    monkeypatch.setattr(
        evaluator,
        "evaluate_resolver",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            evaluator.ResolverProviderTransportFailure(1)
        ),
    )
    monkeypatch.setattr(
        "minicode.config.load_runtime_config",
        lambda _root: {},
    )
    monkeypatch.setattr("minicode.env_file.apply_env_file", lambda _paths: None)
    monkeypatch.setattr(
        evaluator,
        "build_hybrid_promotion_verifier_runtime",
        lambda runtime: runtime,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["resolver-pressure", "--acknowledge-one-shot"],
    )

    with pytest.raises(SystemExit) as raised:
        evaluator.main()

    assert raised.value.code == 2
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["schema_version"] == evaluator.POST_CLAIM_FAILURE_SCHEMA_VERSION
    assert written["failure_stage"] == "evaluate_resolver"
    assert written["failure_kind"] == "provider_or_transport_error"
    assert written["production_authority"] is False
    assert "secret" not in output.read_text(encoding="utf-8")
    assert len(models) == 2
    assert models[0] is not models[1]


def _dataset() -> dict[str, object]:
    relations = list(evaluator._CONTESTED_REASON)
    items = []
    for relation in relations:
        for index in range(evaluator.EXPECTED_CASES_PER_CATEGORY):
            suffix = f"{relation}-{index}"
            items.append(
                {
                    "id": f"case-{suffix}",
                    "query": f"synthetic query object {suffix}",
                    "current_files": [f"src/{suffix}.py"],
                    "active_domains": ["testing"],
                    "entry": {
                        "id": f"memory-{suffix}",
                        "memory_text": f"synthetic memory object {suffix}",
                        "metadata": {},
                        "domains": ["testing"],
                        "stored_files": [],
                    },
                    "expected_relevant": False,
                    "polarity": "negative",
                    "relation": relation,
                }
            )
    return {
        "schema_version": "1.0.0",
        "dataset_id": evaluator.DATASET_ID,
        "created_on": "2026-09-03",
        "sealed": True,
        "item_count": len(items),
        "items": items,
    }


class _RejectingModel:
    runtime: ClassVar[dict[str, str]] = {"model": evaluator.DEFAULT_VERIFIER_MODEL}

    def next(self, messages, **_kwargs):
        pairs = json.loads(messages[-1]["content"])["pairs"]
        rows = []
        for pair in pairs:
            rows.append(
                {
                    "id": pair["id"],
                    "decision": "irrelevant",
                    "confidence": 0.99,
                    "queryGrounded": True,
                    "objectMatch": False,
                    "relationSupported": False,
                    "supportKind": "none",
                    "disqualifier": pair["challenger"]["reasonCode"],
                    "queryEvidenceId": pair["queryEvidenceSegments"][0]["id"],
                    "memoryEvidenceId": pair["memoryEvidenceSegments"][0]["id"],
                }
            )
        return AgentStep(
            type="assistant",
            content=json.dumps({"resolutions": rows}),
            usage=SimpleNamespace(input_tokens=100, output_tokens=40),
            diagnostics=StepDiagnostics(stopReason="stop", blockTypes=["text"]),
        )


class _AdmittingModel(_RejectingModel):
    def next(self, messages, **_kwargs):
        step = super().next(messages, **_kwargs)
        payload = json.loads(step.content)
        row = payload["resolutions"][0]
        row.update(
            {
                "decision": "relevant",
                "objectMatch": True,
                "relationSupported": True,
                "supportKind": "semantic_equivalence",
                "disqualifier": "none",
            }
        )
        step.content = json.dumps(payload)
        return step


class _OneMalformedBatchModel(_RejectingModel):
    def __init__(self, *, failure_kind: str) -> None:
        self.calls = 0
        self.failure_kind = failure_kind

    def next(self, messages, **kwargs):
        self.calls += 1
        step = super().next(messages, **kwargs)
        if self.calls != 2:
            return step
        if self.failure_kind == "json":
            step.content = '{"resolutions": ['
        elif self.failure_kind == "strict_protocol":
            payload = json.loads(step.content)
            payload["resolutions"][0]["unexpected"] = True
            step.content = json.dumps(payload)
        elif self.failure_kind == "envelope":
            step.diagnostics = StepDiagnostics(
                stopReason="content_filter", blockTypes=["text"]
            )
        else:  # pragma: no cover - test helper contract
            raise AssertionError(self.failure_kind)
        return step


class _OneProviderFailureBatchModel(_RejectingModel):
    def __init__(self) -> None:
        self.calls = 0

    def next(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("Bearer provider-secret quota exhausted")
        return super().next(messages, **kwargs)


def test_validate_dataset_requires_balanced_all_negative_panel() -> None:
    dataset = _dataset()
    assert evaluator.validate_dataset(dataset) is dataset
    dataset["items"][0]["expected_relevant"] = True
    with pytest.raises(ValueError, match="item identity"):
        evaluator.validate_dataset(dataset)


def test_fixed_route_canary_is_content_free_and_uses_one_call() -> None:
    class CapturingModel(_RejectingModel):
        def __init__(self) -> None:
            self.messages = None

        def next(self, messages, **kwargs):
            self.messages = messages
            return super().next(messages, **kwargs)

    model = CapturingModel()
    receipt = evaluator.run_fixed_resolver_route_canary(model)

    assert receipt == {
        "passed": True,
        "request_sha256": receipt["request_sha256"],
        "model_call_count": 1,
        "usage_available": True,
    }
    assert len(receipt["request_sha256"]) == 64
    assert model.messages is not None
    serialized_messages = json.dumps(model.messages, ensure_ascii=False)
    serialized_receipt = json.dumps(receipt, ensure_ascii=False)
    assert "synthetic query object" not in serialized_messages
    assert "synthetic memory object" not in serialized_messages
    assert "checkout retry limit" not in serialized_receipt
    assert "thumbnail cache" not in serialized_receipt


def test_provider_failure_is_not_mislabeled_as_protocol_failure() -> None:
    dataset = evaluator.validate_dataset(_dataset())

    with pytest.raises(evaluator.ResolverProviderTransportFailure) as raised:
        evaluator.evaluate_resolver(dataset, _OneProviderFailureBatchModel())

    assert raised.value.batch_ordinal == 2
    assert "provider-secret" not in str(raised.value)


def test_response_protocol_error_remains_a_value_error() -> None:
    assert issubclass(hybrid_runtime.HybridResponseProtocolError, ValueError)


def test_exact_zero_event_upper_bound_is_below_five_percent() -> None:
    bound = evaluator.one_sided_exact_binomial_upper(0, 60)
    assert bound == pytest.approx(0.0487029133)
    assert bound < 0.05


def test_resolver_pressure_batches_every_case_and_stays_content_free() -> None:
    dataset = evaluator.validate_dataset(_dataset())
    result = evaluator.evaluate_resolver(dataset, _RejectingModel())
    assert result["counts"] == {
        "case_count": 60,
        "raw_relevant_count": 0,
        "approved_count": 0,
        "protocol_failure_batches": 0,
        "protocol_failure_cases": 0,
    }
    assert result["usage"] == {
        "model_call_count": 3,
        "input_tokens": 300,
        "output_tokens": 120,
    }
    assert all(
        len(row["case_id"]) == 64
        and len(row["entry_id"]) == 64
        and set(row["case_id"] + row["entry_id"]) <= set("0123456789abcdef")
        for row in result["cases"]
    )
    assert all(row["protocol_failure"] is False for row in result["cases"])
    report = evaluator.build_report(
        dataset=dataset,
        holdout={
            "dataset_id": evaluator.DATASET_ID,
            "holdout_sha256": evaluator.EXPECTED_HOLDOUT_SHA256,
            "manifest_sha256": evaluator.EXPECTED_MANIFEST_SHA256,
            "case_count": 60,
        },
        freeze={"schema_version": "test", "files": {}, "fingerprint": "a" * 64},
        one_shot_claim=_one_shot_claim(),
        verifier_model=evaluator.DEFAULT_VERIFIER_MODEL,
        result=result,
    )
    assert report["acceptance_gate"]["passed"] is True
    serialized = json.dumps(report, ensure_ascii=False)
    assert "synthetic query object" not in serialized
    assert "synthetic memory object" not in serialized


@pytest.mark.parametrize("failure_kind", ["envelope", "json", "strict_protocol"])
def test_protocol_failure_is_fail_closed_reported_and_blocks_promotion(
    failure_kind: str,
    tmp_path,
) -> None:
    dataset = evaluator.validate_dataset(_dataset())
    result = evaluator.evaluate_resolver(
        dataset,
        _OneMalformedBatchModel(failure_kind=failure_kind),
    )

    assert result["counts"] == {
        "case_count": 60,
        "raw_relevant_count": 0,
        "approved_count": 0,
        "protocol_failure_batches": 1,
        "protocol_failure_cases": 20,
    }
    assert result["usage"] == {
        "model_call_count": 3,
        "input_tokens": 300 if failure_kind != "envelope" else 200,
        "output_tokens": 120 if failure_kind != "envelope" else 80,
    }
    failure_rows = [row for row in result["cases"] if row["protocol_failure"]]
    assert len(failure_rows) == 20
    assert all(
        row["decision"] == "irrelevant"
        and row["confidence"] == 0.0
        and row["support_kind"] == "none"
        and row["approved"] is False
        for row in failure_rows
    )

    report = evaluator.build_report(
        dataset=dataset,
        holdout={
            "dataset_id": evaluator.DATASET_ID,
            "holdout_sha256": evaluator.EXPECTED_HOLDOUT_SHA256,
            "manifest_sha256": evaluator.EXPECTED_MANIFEST_SHA256,
            "case_count": 60,
        },
        freeze=evaluator.implementation_freeze(),
        one_shot_claim=_one_shot_claim(),
        verifier_model=evaluator.DEFAULT_VERIFIER_MODEL,
        result=result,
    )
    assert report["acceptance_gate"]["passed"] is False
    assert report["acceptance_gate"]["checks"]["protocol_failure_batches_zero"] is False
    assert report["acceptance_gate"]["checks"]["protocol_failure_cases_zero"] is False
    assert validate_resolver_pressure_report(report) is report
    output = tmp_path / f"{failure_kind}-failure-report.json"
    _write_json_exclusive(output, report)
    written = json.loads(output.read_text())
    assert written["acceptance_gate"]["passed"] is False
    assert written["resolver_pressure"]["counts"]["protocol_failure_batches"] == 1
    with pytest.raises(ValueError, match="did not pass"):
        build_resolver_pressure_receipt(
            report,
            artifact=evaluator.HYBRID_RESOLVER_PRESSURE_ARTIFACT,
        )
    serialized = json.dumps(report, ensure_ascii=False)
    assert "synthetic query object" not in serialized
    assert "synthetic memory object" not in serialized


def test_resolver_pressure_uses_shared_pair_bound_batch_builder(monkeypatch) -> None:
    assert evaluator._build_resolver_batch is hybrid_runtime._build_resolver_batch
    production_builder = evaluator._build_resolver_batch
    observed_batches: list[list[dict[str, object]]] = []

    def spy_builder(items, *, decisions, audits):
        pairs, expected = production_builder(
            items,
            decisions=decisions,
            audits=audits,
        )
        observed_batches.append(pairs)
        return pairs, expected

    monkeypatch.setattr(evaluator, "_build_resolver_batch", spy_builder)

    dataset = evaluator.validate_dataset(_dataset())
    result = evaluator.evaluate_resolver(dataset, _RejectingModel())

    assert result["counts"]["case_count"] == 60
    assert [len(batch) for batch in observed_batches] == [20, 20, 20]
    pair_ids = [str(pair["id"]) for batch in observed_batches for pair in batch]
    assert len(pair_ids) == len(set(pair_ids)) == 60
    evidence_ids = [
        str(segment["id"])
        for batch in observed_batches
        for pair in batch
        for side in ("queryEvidenceSegments", "memoryEvidenceSegments")
        for segment in pair[side]
    ]
    assert len(evidence_ids) == len(set(evidence_ids))


def test_any_raw_resolver_admission_fails_the_pressure_gate() -> None:
    dataset = evaluator.validate_dataset(_dataset())
    result = evaluator.evaluate_resolver(dataset, _AdmittingModel())
    assert result["counts"]["raw_relevant_count"] == 3
    report = evaluator.build_report(
        dataset=dataset,
        holdout={
            "dataset_id": evaluator.DATASET_ID,
            "holdout_sha256": evaluator.EXPECTED_HOLDOUT_SHA256,
            "manifest_sha256": evaluator.EXPECTED_MANIFEST_SHA256,
            "case_count": 60,
        },
        freeze={"schema_version": "test", "files": {}, "fingerprint": "a" * 64},
        one_shot_claim=_one_shot_claim(),
        verifier_model=evaluator.DEFAULT_VERIFIER_MODEL,
        result=result,
    )
    assert report["acceptance_gate"]["passed"] is False
    assert report["acceptance_gate"]["checks"]["raw_relevant_count_zero"] is False
